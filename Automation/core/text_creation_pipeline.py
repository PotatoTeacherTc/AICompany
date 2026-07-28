import json
from pathlib import Path
import re
import time
import uuid

from core.artifact_manager import ArtifactManager
from core.base_pipeline import BasePipeline
from core.result import PipelineResult
from core.status import PipelineStatus
from core.structured_logging import LogLevel, safe_log
from providers.factory import ProviderFactory
from providers.text import TextGenerationRequest, TextGenerationResult, TEXT_TASK_TYPES


_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")
_SCHEMAS = {
    "LYRICS": {
        "title", "theme_summary", "lyrics", "sections", "language",
        "safe_metadata",
    },
    "CONTENT_PLAN": {
        "title", "concept", "target_audience", "content_outline",
        "visual_direction", "publishing_summary",
    },
    "VIDEO_SCRIPT": {"title", "scenes"},
    "TITLE_DESCRIPTION": {"title", "description", "tags"},
}


class TextCreationPipeline(BasePipeline):
    def __init__(
        self,
        root,
        provider=None,
        selection=None,
        artifact_manager=None,
        execution_history=None,
        logger=None,
        maximum_output_size=12000,
    ):
        super().__init__("Text Creation Pipeline")
        selection = selection or (
            ProviderFactory.text_from_environment() if provider is None else None
        )
        self.provider = ProviderFactory.ensure_provider_allowed(
            provider or selection.provider
        )
        self.model = getattr(selection, "default_model", None)
        self.timeout_seconds = getattr(selection, "timeout_seconds", 30.0)
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifacts = artifact_manager or ArtifactManager()
        self.history = execution_history
        self.logger = logger
        if (
            not isinstance(maximum_output_size, int)
            or not 256 <= maximum_output_size <= 100000
        ):
            raise ValueError("maximum_output_size is invalid")
        self.maximum_output_size = maximum_output_size

    def run(self, task):
        workspace_id = getattr(task, "workspace_id", None)
        task_type = str(getattr(task, "task_type", "") or "").upper()
        mission_id = (
            task.parameters.get("mission_id")
            if isinstance(getattr(task, "parameters", None), dict)
            else None
        )
        try:
            _identifier(workspace_id, "workspace_id")
            _identifier(mission_id, "mission_id")
            if task_type not in TEXT_TASK_TYPES:
                raise ValueError("unsupported text task type")
            instruction = getattr(task, "task_text", None)
            if not isinstance(instruction, str) or not instruction.strip():
                raise ValueError("generation instruction is required")
            safe_log(
                self.logger, "TEXT_GENERATION_STARTED", self.name,
                workspace_id=workspace_id, mission_id=mission_id,
                execution_id=getattr(task, "id", None), status="RUNNING",
                provider=self.provider.__class__.__name__, model=self.model,
                metadata={"task_type": task_type},
            )
            started = time.monotonic()
            generated = self.provider.generate_text(TextGenerationRequest(
                workspace_id,
                mission_id,
                task_type,
                instruction,
                context={},
                output_format="json",
                maximum_output_size=self.maximum_output_size,
                model=self.model,
                timeout_seconds=self.timeout_seconds,
            ))
            if time.monotonic() - started > self.timeout_seconds:
                raise TimeoutError()
            if not isinstance(generated, TextGenerationResult):
                raise ValueError("text provider returned invalid result")
            content = self._content(
                generated.output_text, task_type, instruction,
                generated.provider,
            )
            project = self._project(workspace_id, mission_id, task_type)
            artifact_file = project / f"{task_type.lower()}.json"
            artifact_file.write_text(
                json.dumps(content, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            artifact = self.artifacts.register_file(
                artifact_file,
                "TEXT",
                self.name,
                workspace_id=workspace_id,
                mission_id=mission_id,
                stage=task_type,
            )
            usage = self._usage(generated)
            if (
                usage is not None
                and usage.get("estimated_cost_usd", 0) > 0
            ):
                raise ValueError("paid provider usage is disabled")
            result = self._result(
                task, task_type, workspace_id, mission_id, content,
                generated, usage, [self._safe_artifact(artifact)],
            )
            self._history(task, result, task_type)
            safe_log(
                self.logger, "TEXT_GENERATION_COMPLETED", self.name,
                workspace_id=workspace_id, mission_id=mission_id,
                execution_id=getattr(task, "id", None), status="SUCCESS",
                provider=generated.provider, model=generated.model, usage=usage,
                metadata={
                    "task_type": task_type,
                    "artifact_id": artifact.get("artifact_id"),
                },
            )
            return result
        except Exception as error:
            result = self._failure(
                task, task_type, workspace_id, mission_id, error
            )
            self._history(task, result, task_type or "TEXT")
            safe_log(
                self.logger, "TEXT_GENERATION_FAILED", self.name,
                level=LogLevel.ERROR,
                workspace_id=workspace_id if isinstance(workspace_id, str) else None,
                mission_id=mission_id if isinstance(mission_id, str) else None,
                execution_id=getattr(task, "id", None), status="FAILED",
                error=f"TextProviderError: {type(error).__name__}",
                metadata={"task_type": task_type},
            )
            return result

    def _project(self, workspace_id, mission_id, task_type):
        project = (
            self.root / workspace_id / mission_id
            / f"{task_type.lower()}-{uuid.uuid4().hex[:8]}"
        ).resolve()
        workspace_root = (self.root / workspace_id).resolve()
        if workspace_root != project and workspace_root not in project.parents:
            raise ValueError("text artifact path escaped workspace")
        project.mkdir(parents=True, exist_ok=False)
        return project

    @classmethod
    def _content(cls, output, task_type, instruction, provider):
        if not isinstance(output, str) or not output.strip():
            raise ValueError("text provider returned empty output")
        try:
            content = json.loads(output)
        except json.JSONDecodeError as error:
            if provider != "ollama":
                raise ValueError("text provider returned malformed output") from error
            content = cls._fallback_content(output, task_type)
        if not isinstance(content, dict) or not _SCHEMAS[task_type].issubset(content):
            if provider != "ollama":
                raise ValueError("text provider output schema is invalid")
            content = cls._fallback_content(output, task_type)
        if not isinstance(content.get("title"), str) or not content["title"].strip():
            raise ValueError("generated title is invalid")
        if task_type == "LYRICS" and (
            not isinstance(content.get("lyrics"), str)
            or not isinstance(content.get("sections"), dict)
        ):
            raise ValueError("lyrics output schema is invalid")
        if cls._contains_unsafe_content(content, instruction):
            raise ValueError("generated content is unsafe")
        return content

    @staticmethod
    def _fallback_content(output, task_type):
        if task_type == "LYRICS":
            return {
                "title": "Local creative result",
                "theme_summary": "Locally generated Korean lyrics",
                "lyrics": output,
                "sections": {"generated": output},
                "language": "ko",
                "safe_metadata": {"normalized": True},
            }
        if task_type == "CONTENT_PLAN":
            return {
                "title": "Local creative result",
                "concept": output,
                "target_audience": "Local creative audience",
                "content_outline": [output],
                "visual_direction": "Defined in generated content",
                "publishing_summary": "Locally generated content plan",
            }
        if task_type == "VIDEO_SCRIPT":
            return {
                "title": "Local creative result",
                "scenes": [{"scene": 1, "summary": output}],
            }
        return {
            "title": "Local creative result",
            "description": output,
            "tags": [],
        }

    @classmethod
    def _contains_unsafe_content(cls, value, instruction):
        sensitive_keys = {
            "prompt", "objective", "api_key", "apikey", "authorization",
            "cookie", "oauth_token", "password", "secret", "token",
        }
        if isinstance(value, dict):
            return any(
                str(key).lower().replace("-", "_") in sensitive_keys
                or cls._contains_unsafe_content(nested, instruction)
                for key, nested in value.items()
            )
        if isinstance(value, list):
            return any(
                cls._contains_unsafe_content(item, instruction)
                for item in value
            )
        if isinstance(value, str):
            source = instruction.strip() if isinstance(instruction, str) else ""
            if source and source in value:
                return True
            return bool(re.search(
                r"(?:[A-Za-z]:\\|/(?:home|Users|var|tmp|etc)/|"
                r"(?:api[_-]?key|oauth[_-]?token|authorization|cookie|"
                r"password|secret|token)\s*[:=])",
                value,
                re.IGNORECASE,
            ))
        return False

    @staticmethod
    def _usage(generated):
        usage = generated.usage
        if usage is None:
            return None
        source = usage if isinstance(usage, dict) else {
            key: getattr(usage, key)
            for key in (
                "input_tokens", "output_tokens", "total_tokens",
                "estimated_cost_usd",
            ) if hasattr(usage, key)
        }
        result = {
            "provider": generated.provider,
            "model": generated.model,
        }
        for key in (
            "input_tokens", "output_tokens", "total_tokens",
            "estimated_cost_usd",
        ):
            if key in source and source[key] is not None:
                result[key] = source[key]
        return result

    def _result(
        self, task, task_type, workspace_id, mission_id, content,
        generated, usage, artifacts,
    ):
        result = PipelineResult(
            PipelineStatus.SUCCESS,
            self.name,
            "Text creation",
            task_type,
            data={
                "workspace_id": workspace_id,
                "mission_id": mission_id,
                "provider": generated.provider,
                "model": generated.model,
                "provider_usage": usage,
                "title": content["title"],
                "generation_mode": (
                    "fake" if generated.provider == "fake-text" else "local"
                ),
                "task_redacted": True,
            },
            artifacts=artifacts,
        ).to_dict()
        result["task_id"] = getattr(task, "id", None)
        return result

    def _failure(self, task, task_type, workspace_id, mission_id, error):
        result = PipelineResult(
            PipelineStatus.FAILED,
            self.name,
            "Text creation",
            task_type or None,
            data={
                "workspace_id": (
                    workspace_id if isinstance(workspace_id, str) else None
                ),
                "mission_id": (
                    mission_id if isinstance(mission_id, str) else None
                ),
                "task_redacted": True,
            },
            error=f"TextCreationError: {type(error).__name__}",
        ).to_dict()
        result["task_id"] = getattr(task, "id", None)
        return result

    @staticmethod
    def _safe_artifact(artifact):
        return {
            key: artifact[key] for key in (
                "artifact_id", "artifact_type", "mime_type", "filename",
                "size", "created_at", "producer_pipeline", "workspace_id",
                "mission_id", "stage", "status", "internal_ref",
            ) if key in artifact
        }

    def _history(self, task, result, task_type):
        if self.history is None:
            return
        try:
            self.history.record_content_stage(task, result, task_type)
        except Exception:
            pass


def _identifier(value, field_name):
    if (
        not isinstance(value, str)
        or not value.strip()
        or not _SAFE_ID.fullmatch(value.strip())
    ):
        raise ValueError(f"{field_name} contains unsupported characters")
    return value.strip()
