from datetime import datetime, timezone
from pathlib import Path
import re
import time

from config.settings import PROJECT_ROOT
from core.artifact_manager import ArtifactManager
from core.base_pipeline import BasePipeline
from core.result import PipelineResult
from core.status import PipelineStatus
from providers.factory import ProviderFactory
from providers.music import (
    GenericMusicProviderAdapter,
    MusicGenerationRequest,
    MusicGenerationResult,
    MusicProvider,
)
from providers.pipeline_utils import provider_error


_SAFE_SCOPE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class MusicPipeline(BasePipeline):
    def __init__(
        self,
        music_root=None,
        provider=None,
        artifact_manager=None,
        execution_history=None,
        provider_selection=None,
        model=None,
        timeout_seconds=None,
    ):
        super().__init__("Music Pipeline")
        self.music_root = Path(music_root or PROJECT_ROOT / "Music")
        selection = provider_selection
        if selection is None and provider is None:
            selection = ProviderFactory.music_from_environment()
        selected_provider = provider or selection.provider
        self.provider = (
            selected_provider
            if isinstance(selected_provider, MusicProvider)
            else GenericMusicProviderAdapter(selected_provider)
        )
        self.model = (
            model
            if model is not None
            else getattr(selection, "default_model", None)
        )
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else getattr(selection, "timeout_seconds", 30.0)
        )
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("music timeout_seconds must be positive")
        self.artifact_manager = artifact_manager or ArtifactManager()
        self.execution_history = execution_history
        self.music_root.mkdir(parents=True, exist_ok=True)

    def run(self, task):
        try:
            task_text, workspace_id, mission_id = self._validate_task(task)
            project_name = (
                "music_project_"
                + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            )
            project_path = (self.music_root / workspace_id / project_name).resolve()
            if self.music_root.resolve() not in project_path.parents:
                raise ValueError("music output must stay within configured root")
            project_path.mkdir(parents=True, exist_ok=False)
            request = MusicGenerationRequest(
                prompt=task_text,
                workspace_id=workspace_id,
                mission_id=mission_id,
                output_directory=str(project_path),
                model=self.model,
                timeout_seconds=self.timeout_seconds,
            )
            started_at = time.monotonic()
            generation = self.provider.generate_music(request)
            if time.monotonic() - started_at > self.timeout_seconds:
                raise TimeoutError("music provider exceeded timeout")
            if not isinstance(generation, MusicGenerationResult):
                raise ValueError("music provider returned an invalid result")

            generated_paths = self._validated_paths(project_path, generation)
            usage = self._normalize_usage(generation)
            metadata_file = project_path / "metadata.txt"
            metadata_file.write_text(
                "\n".join(
                    (
                        f"project_name: {project_name}",
                        f"workspace_id: {workspace_id}",
                        f"mission_id: {mission_id}",
                        f"provider: {generation.provider}",
                        f"model: {generation.model}",
                        f"created_at: {datetime.now(timezone.utc).isoformat()}",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            artifact_records = self.artifact_manager.register_files(
                [*generated_paths, metadata_file],
                "MUSIC",
                self.name,
                workspace_id=workspace_id,
            )
            safe_artifacts = [self._safe_artifact(item) for item in artifact_records]
            result = PipelineResult(
                status=PipelineStatus.SUCCESS,
                pipeline=self.name,
                task=task,
                task_type=task.task_type,
                data={
                    "project_name": project_name,
                    "workspace_id": workspace_id,
                    "mission_id": mission_id,
                    "provider": generation.provider,
                    "model": generation.model,
                    "generated_artifacts": safe_artifacts,
                    "provider_usage": usage,
                    "artifacts": safe_artifacts,
                    "task_redacted": True,
                },
                artifacts=safe_artifacts,
            ).to_dict()
        except TimeoutError:
            result = PipelineResult(
                PipelineStatus.TIMED_OUT,
                self.name,
                task,
                getattr(task, "task_type", "MUSIC"),
                error="ProviderError: TimeoutError",
            ).to_dict()
        except Exception as error:
            result = PipelineResult(
                PipelineStatus.FAILED,
                self.name,
                task,
                getattr(task, "task_type", "MUSIC"),
                error=provider_error(error),
            ).to_dict()
        result["task"] = "Music generation"
        self._record_history(task, result)
        return result

    def _record_history(self, task, result):
        if self.execution_history is None:
            return
        try:
            self.execution_history.record_music(task, result)
        except Exception:
            pass

    @staticmethod
    def _validate_task(task):
        task_text = getattr(task, "task_text", None)
        workspace_id = getattr(task, "workspace_id", None)
        parameters = getattr(task, "parameters", {})
        mission_id = parameters.get("mission_id") or getattr(task, "id", None)
        if not isinstance(task_text, str) or not task_text.strip():
            raise ValueError("music task must be a non-empty string")
        for value, field_name in (
            (workspace_id, "workspace_id"),
            (mission_id, "mission_id"),
        ):
            if not isinstance(value, str) or not _SAFE_SCOPE_ID.fullmatch(value):
                raise ValueError(f"{field_name} contains unsupported characters")
        return task_text.strip(), workspace_id, mission_id

    @staticmethod
    def _validated_paths(project_path, generation):
        paths = []
        for artifact in generation.artifacts:
            path = Path(artifact.path).resolve()
            if project_path != path and project_path not in path.parents:
                raise ValueError("music artifact escaped project boundary")
            if not path.is_file():
                raise ValueError("music provider artifact is missing")
            paths.append(path)
        if not paths:
            raise ValueError("music provider returned no artifacts")
        return paths

    @staticmethod
    def _normalize_usage(generation):
        usage = generation.usage
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        return {
            "provider": generation.provider,
            "model": generation.model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": getattr(
                usage, "total_tokens", input_tokens + output_tokens
            )
            or 0,
            "estimated_cost_usd": getattr(
                usage, "estimated_cost_usd", 0.0
            )
            or 0.0,
        }

    @staticmethod
    def _safe_artifact(artifact):
        return {
            key: artifact.get(key)
            for key in ArtifactManager.METADATA_FIELDS
            if key in artifact
        }
