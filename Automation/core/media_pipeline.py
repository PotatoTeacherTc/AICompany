from datetime import datetime, timezone
from pathlib import Path
import re
import time

from core.artifact_manager import ArtifactManager
from core.base_pipeline import BasePipeline
from core.result import PipelineResult
from core.status import PipelineStatus
from providers.content_media import (
    ImageGenerationRequest,
    MediaGenerationResult,
    VideoGenerationRequest,
)
from providers.factory import ProviderFactory
from providers.pipeline_utils import provider_error


_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class MediaPipeline(BasePipeline):
    kind = None
    artifact_type = None

    def __init__(
        self,
        root,
        provider=None,
        provider_selection=None,
        artifact_manager=None,
        execution_history=None,
        model=None,
        timeout_seconds=None,
    ):
        super().__init__(f"{self.kind.title()} Pipeline")
        selection = provider_selection
        if selection is None and provider is None:
            selection = getattr(ProviderFactory, f"{self.kind}_from_environment")()
        self.provider = ProviderFactory.ensure_provider_allowed(
            provider or selection.provider
        )
        self.model = model if model is not None else getattr(selection, "default_model", None)
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else getattr(selection, "timeout_seconds", 30.0)
        )
        if not isinstance(self.timeout_seconds, (int, float)) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifact_manager = artifact_manager or ArtifactManager()
        self.execution_history = execution_history

    def run(self, task, input_artifacts=()):
        try:
            prompt, workspace_id, mission_id = self._validate(task)
            project_name = (
                f"{self.kind}_project_"
                + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            )
            project = (self.root / workspace_id / project_name).resolve()
            if self.root.resolve() not in project.parents:
                raise ValueError("output escaped configured root")
            project.mkdir(parents=True)
            references = self._validate_references(input_artifacts, workspace_id)
            request = self._request(
                prompt, workspace_id, mission_id, project, references
            )
            started = time.monotonic()
            generation = self._generate(request)
            if time.monotonic() - started > self.timeout_seconds:
                raise TimeoutError()
            if not isinstance(generation, MediaGenerationResult):
                raise ValueError("provider returned an invalid result")
            paths = self._paths(project, generation)
            records = self.artifact_manager.register_files(
                paths, self.artifact_type, self.name, workspace_id=workspace_id,
                mission_id=mission_id, stage=self.kind
            )
            artifacts = [self._safe_artifact(item) for item in records]
            data = {
                "workspace_id": workspace_id,
                "mission_id": mission_id,
                "provider": generation.provider,
                "model": generation.model,
                "provider_usage": self._usage(generation),
                "artifacts": artifacts,
                "input_artifact_ids": [
                    item.get("artifact_id") for item in references
                ],
                "task_redacted": True,
            }
            result = PipelineResult(
                PipelineStatus.SUCCESS, self.name, task, self.kind.upper(),
                data=data, artifacts=artifacts
            ).to_dict()
        except TimeoutError:
            result = PipelineResult(
                PipelineStatus.TIMED_OUT, self.name, task, self.kind.upper(),
                error="ProviderError: TimeoutError"
            ).to_dict()
        except Exception as error:
            result = PipelineResult(
                PipelineStatus.FAILED, self.name, task, self.kind.upper(),
                error=provider_error(error)
            ).to_dict()
        result["task"] = f"{self.kind.title()} generation"
        self._history(task, result)
        return result

    def _request(self, prompt, workspace_id, mission_id, project, references):
        common = dict(
            prompt=prompt, workspace_id=workspace_id, mission_id=mission_id,
            output_directory=str(project), model=self.model,
            timeout_seconds=self.timeout_seconds,
        )
        if self.kind == "image":
            return ImageGenerationRequest(**common)
        return VideoGenerationRequest(**common, input_artifacts=tuple(references))

    def _generate(self, request):
        return getattr(self.provider, f"generate_{self.kind}")(request)

    def _history(self, task, result):
        if self.execution_history is None:
            return
        try:
            self.execution_history.record_content_stage(task, result, self.kind.upper())
        except Exception:
            pass

    @staticmethod
    def _validate(task):
        prompt = getattr(task, "task_text", None)
        workspace_id = getattr(task, "workspace_id", None)
        mission_id = getattr(task, "parameters", {}).get("mission_id") or getattr(task, "id", None)
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be non-empty")
        for value in (workspace_id, mission_id):
            if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
                raise ValueError("scope contains unsupported characters")
        return prompt.strip(), workspace_id, mission_id

    @staticmethod
    def _validate_references(artifacts, workspace_id):
        values = tuple(artifacts or ())
        for artifact in values:
            if not isinstance(artifact, dict):
                raise ValueError("artifact reference must be metadata")
            if artifact.get("workspace_id") != workspace_id:
                raise ValueError("artifact workspace mismatch")
            if "path" in artifact:
                raise ValueError("artifact reference must not expose a path")
        return values

    @staticmethod
    def _paths(project, generation):
        paths = []
        for artifact in generation.artifacts:
            path = Path(artifact.path).resolve()
            if project != path and project not in path.parents:
                raise ValueError("provider artifact escaped project boundary")
            if not path.is_file():
                raise ValueError("provider artifact is missing")
            paths.append(path)
        if not paths:
            raise ValueError("provider returned no artifacts")
        return paths

    @staticmethod
    def _usage(generation):
        usage = generation.usage
        inputs = getattr(usage, "input_tokens", 0) or 0
        outputs = getattr(usage, "output_tokens", 0) or 0
        return {
            "provider": generation.provider,
            "model": generation.model,
            "input_tokens": inputs,
            "output_tokens": outputs,
            "total_tokens": getattr(usage, "total_tokens", inputs + outputs) or 0,
            "estimated_cost_usd": getattr(usage, "estimated_cost_usd", 0.0) or 0.0,
        }

    @staticmethod
    def _safe_artifact(artifact):
        return {
            key: artifact[key]
            for key in ArtifactManager.METADATA_FIELDS
            if key in artifact
        }


class ImagePipeline(MediaPipeline):
    kind = "image"
    artifact_type = "IMAGE"


class VideoPipeline(MediaPipeline):
    kind = "video"
    artifact_type = "VIDEO"
