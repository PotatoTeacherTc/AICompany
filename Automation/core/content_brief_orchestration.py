from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile
import threading

from core.artifact_manager import ArtifactManager
from core.completed_audio_intake import MUSIC_AUDIO_LINK_KIND, MusicProjectAudioLink
from core.persistence import StateRepository
from core.result import PipelineResult
from core.status import PipelineStatus
from core.structured_logging import LogLevel, safe_log
from core.task import Task
from core.workflow_definition import StepDefinition, WorkflowDefinition
from providers.factory import ProviderFactory
from providers.text import TextGenerationRequest, TextGenerationResult, TextProviderError


CONTENT_PROJECT_KIND = "content_project"
CONTENT_BRIEF_PROMPT_VERSION = "content-brief-v1"
CONTENT_BRIEF_SCHEMA_VERSION = "1.0"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")
_MAX_INPUT = 2000
_PENDING_STEPS = (
    "IMAGE_PACKAGE", "BLOG_PACKAGE", "VIDEO_PACKAGE", "YOUTUBE_PACKAGE",
    "PUBLISHING",
)


def _string():
    return {"type": "string"}


def _strings():
    return {"type": "array", "minItems": 1, "maxItems": 20,
            "items": {"type": "string", "maxLength": 300}}


CONTENT_BRIEF_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        key: (_strings() if key in {
            "emotional_arc", "mood_keywords", "seo_primary_keywords",
            "seo_secondary_keywords", "title_keywords", "image_requirements",
            "blog_requirements", "video_requirements", "youtube_requirements",
            "prohibited_elements", "safety_notes", "assumptions", "next_steps",
        } else _string())
        for key in (
            "project_title", "core_message", "content_goal", "target_audience",
            "listener_profile", "emotional_arc", "mood_keywords",
            "visual_concept", "visual_style", "color_direction",
            "thumbnail_direction", "video_direction", "blog_direction",
            "youtube_direction", "seo_primary_keywords",
            "seo_secondary_keywords", "title_keywords", "image_requirements",
            "blog_requirements", "video_requirements", "youtube_requirements",
            "prohibited_elements", "safety_notes", "assumptions",
            "source_summary", "next_steps",
        )
    },
}
CONTENT_BRIEF_SCHEMA["required"] = list(CONTENT_BRIEF_SCHEMA["properties"])


class ContentBriefError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(f"ContentBriefError: {code}")


@dataclass(frozen=True)
class ContentBriefRequest:
    workspace_id: str
    music_project_id: str
    content_goal: str | None = None
    target_audience: str | None = None
    language: str | None = None
    additional_notes: str | None = None
    idempotency_key: str | None = None
    correlation_id: str | None = None

    def __post_init__(self):
        _identifier(self.workspace_id, "workspace_id")
        _identifier(self.music_project_id, "music_project_id")
        for name in (
            "content_goal", "target_audience", "language", "additional_notes"
        ):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or not value.strip()
                or len(value) > _MAX_INPUT
            ):
                raise ValueError(f"{name} is invalid")
        for name in ("idempotency_key", "correlation_id"):
            value = getattr(self, name)
            if value is not None:
                _identifier(value, name)


@dataclass(frozen=True)
class ContentBrief:
    project_title: str
    core_message: str
    content_goal: str
    target_audience: str
    listener_profile: str
    emotional_arc: tuple[str, ...]
    mood_keywords: tuple[str, ...]
    visual_concept: str
    visual_style: str
    color_direction: str
    thumbnail_direction: str
    video_direction: str
    blog_direction: str
    youtube_direction: str
    seo_primary_keywords: tuple[str, ...]
    seo_secondary_keywords: tuple[str, ...]
    title_keywords: tuple[str, ...]
    image_requirements: tuple[str, ...]
    blog_requirements: tuple[str, ...]
    video_requirements: tuple[str, ...]
    youtube_requirements: tuple[str, ...]
    prohibited_elements: tuple[str, ...]
    safety_notes: tuple[str, ...]
    assumptions: tuple[str, ...]
    source_summary: str
    next_steps: tuple[str, ...]

    @classmethod
    def from_dict(cls, value):
        validate_content_brief(value)
        return cls(**{
            key: tuple(item) if isinstance(item, list) else item
            for key, item in value.items()
        })

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class ContentProject:
    content_project_id: str
    workspace_id: str
    music_project_id: str
    music_plan_artifact_id: str
    source_audio_artifact_id: str
    status: str
    revision: int
    brief_artifact_id: str | None
    execution_plan_artifact_id: str | None
    created_at: str
    updated_at: str
    completed_steps: tuple[str, ...] = ()
    pending_steps: tuple[str, ...] = _PENDING_STEPS
    failed_step: str | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        try:
            project = cls(**{
                key: (tuple(value[key]) if key in {"completed_steps", "pending_steps"} else value[key])
                for key in cls.__dataclass_fields__ if key in value
            })
            project.validate()
            return project
        except (KeyError, TypeError, ValueError):
            return None

    def validate(self):
        for name in (
            "content_project_id", "workspace_id", "music_project_id",
            "music_plan_artifact_id", "source_audio_artifact_id",
        ):
            _identifier(getattr(self, name), name)
        if self.status not in {
            PipelineStatus.BRIEF_GENERATING, PipelineStatus.READY_FOR_CONTENT,
            PipelineStatus.FAILED,
        }:
            raise ValueError("invalid content project status")
        if not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("invalid content project revision")
        if not isinstance(self.metadata, dict):
            raise ValueError("invalid content project metadata")
        return self


class ContentProjectRepository:
    def __init__(self, state_repository):
        if not isinstance(state_repository, StateRepository):
            raise TypeError("state_repository must implement StateRepository")
        self.states = state_repository

    def get(self, workspace_id, content_project_id):
        return ContentProject.from_dict(self.states.get(
            CONTENT_PROJECT_KIND, content_project_id, workspace_id
        ))

    def get_by_music_project(self, workspace_id, music_project_id):
        content_id = _content_project_id(workspace_id, music_project_id)
        return self.get(workspace_id, content_id)

    def save(self, project, expected_revision=None):
        project.validate()
        current = self.get(project.workspace_id, project.content_project_id)
        if current is None:
            if expected_revision is not None:
                raise ContentBriefError("STALE_REVISION")
        elif expected_revision != current.revision:
            raise ContentBriefError("STALE_REVISION")
        self.states.save(
            CONTENT_PROJECT_KIND, project.content_project_id,
            project.workspace_id, project.to_dict(),
        )
        return project


class ContentBriefPromptBuilder:
    @staticmethod
    def build(request, music_plan, audio_metadata):
        safe_plan = {
            key: music_plan.get(key) for key in (
                "primary_title", "concept_summary", "target_listener", "genre",
                "subgenres", "mood", "tempo_bpm", "song_structure",
                "instrumentation", "vocal_style", "lyrical_theme",
                "production_direction", "negative_constraints", "assumptions",
            )
        }
        preferences = {
            "content_goal": request.content_goal,
            "target_audience": request.target_audience,
            "language": request.language,
            "additional_notes": request.additional_notes,
        }
        return (
            "Create one provider-neutral cross-channel content brief from the "
            "approved music plan and safe completed-audio metadata. Preserve the "
            "music plan when optional preferences conflict; record inferred or "
            "supplemental choices in assumptions. Do not imitate named artists, "
            "invent media analysis, or execute any downstream step. Return only "
            "the supplied JSON Schema.\n"
            f"Approved music plan: {json.dumps(safe_plan, ensure_ascii=False)}\n"
            f"Audio metadata: {json.dumps(audio_metadata, ensure_ascii=False)}\n"
            f"Optional content preferences: {json.dumps(preferences, ensure_ascii=False)}"
        )


class ContentExecutionPlanBuilder:
    @staticmethod
    def build(content_project_id, brief_artifact_id, audio_artifact_id):
        workflow = WorkflowDefinition(
            workflow_id=f"content-{content_project_id}",
            name="Content package plan", version="1.0.0",
            steps=(
                StepDefinition("IMAGE_PACKAGE", "IMAGE_PACKAGE"),
                StepDefinition("BLOG_PACKAGE", "BLOG_PACKAGE"),
                StepDefinition("VIDEO_PACKAGE", "VIDEO_PACKAGE", ("IMAGE_PACKAGE",)),
                StepDefinition("YOUTUBE_PACKAGE", "YOUTUBE_PACKAGE", ("VIDEO_PACKAGE",)),
                StepDefinition("PUBLISHING", "PUBLISHING", ("BLOG_PACKAGE", "YOUTUBE_PACKAGE")),
            ),
        ).validate()
        return {
            "workflow_id": workflow.workflow_id,
            "schema_version": 1,
            "steps": [{
                "step_id": step.step_id,
                "step_type": step.capability,
                "status": "PENDING",
                "dependencies": list(step.depends_on),
                "required_inputs": [brief_artifact_id, audio_artifact_id],
                "expected_outputs": [f"{step.step_id.lower()}_artifact"],
                "retryable": True,
                "metadata": {},
            } for step in workflow.steps],
        }


class ContentBriefService:
    def __init__(self, provider=None, selection=None):
        selection = selection or (ProviderFactory.text_from_environment() if provider is None else None)
        if provider is not None:
            self.provider = ProviderFactory.ensure_provider_allowed(provider)
            self.model, self.timeout_seconds = None, 30.0
        else:
            if getattr(selection.provider, "is_paid", False) and not selection.paid_allowed:
                raise ValueError("Paid provider is disabled by policy")
            self.provider = selection.provider
            self.model = selection.default_model
            self.timeout_seconds = selection.timeout_seconds

    def generate(self, request, music_plan, audio_metadata):
        generated = self.provider.generate_text(TextGenerationRequest(
            request.workspace_id, request.music_project_id, "CONTENT_BRIEF",
            ContentBriefPromptBuilder.build(request, music_plan, audio_metadata),
            output_format="json", maximum_output_size=24000,
            model=self.model, timeout_seconds=self.timeout_seconds,
            response_schema=CONTENT_BRIEF_SCHEMA,
        ))
        if not isinstance(generated, TextGenerationResult):
            raise ContentBriefError("INVALID_PROVIDER_RESULT")
        try:
            value = json.loads(generated.output_text)
            if isinstance(value, dict):
                value["next_steps"] = list(_PENDING_STEPS)
            brief = ContentBrief.from_dict(value)
        except (TypeError, json.JSONDecodeError, ValueError):
            raise ContentBriefError("SCHEMA_VALIDATION_FAILED") from None
        return brief, generated


class ContentProjectOrchestrator:
    def __init__(self, work_root, brief_service, project_repository,
                 state_repository, artifact_manager, execution_history,
                 usage_engine=None, logger=None):
        self.work_root = Path(work_root).resolve()
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.briefs = brief_service
        self.projects = project_repository
        self.states = state_repository
        self.artifacts = artifact_manager
        self.history = execution_history
        self.usage = usage_engine
        self.logger = logger
        self._lock = threading.Lock()

    def run(self, request):
        if not isinstance(request, ContentBriefRequest):
            return self._failure(None, None, "INVALID_REQUEST")
        with self._lock:
            return self._run_locked(request)

    def _run_locked(self, request):
        project = None
        generated_artifacts = []
        task = _task(request)
        try:
            current = self.projects.get_by_music_project(
                request.workspace_id, request.music_project_id
            )
            if current is not None and current.status == PipelineStatus.READY_FOR_CONTENT:
                return self._existing_result(task, current)
            if current is not None and current.status == PipelineStatus.BRIEF_GENERATING:
                raise ContentBriefError("DUPLICATE_EXECUTION")
            music_plan_artifact, audio_artifact, music_plan, audio_link = self._inputs(request)
            now = datetime.now(timezone.utc).isoformat()
            if current is None:
                project = ContentProject(
                    _content_project_id(request.workspace_id, request.music_project_id),
                    request.workspace_id, request.music_project_id,
                    music_plan_artifact["artifact_id"], audio_artifact["artifact_id"],
                    PipelineStatus.BRIEF_GENERATING, 0, None, None, now, now,
                    completed_steps=("MUSIC_PLAN", "AUDIO_INPUT"),
                    metadata={"idempotency_digest": _digest(request.idempotency_key)},
                )
                self.projects.save(project)
            else:
                project = replace(
                    current, status=PipelineStatus.BRIEF_GENERATING,
                    revision=current.revision + 1, updated_at=now,
                    failed_step=None,
                )
                self.projects.save(project, expected_revision=current.revision)
            safe_log(self.logger, "CONTENT_BRIEF_STARTED", "ContentProjectOrchestrator",
                     workspace_id=request.workspace_id,
                     mission_id=request.music_project_id,
                     execution_id=project.content_project_id,
                     status=PipelineStatus.BRIEF_GENERATING)
            brief, generated = self.briefs.generate(
                request, music_plan, audio_artifact.get("metadata", {})
            )
            generated_artifacts = self._artifacts(project, brief, generated)
            plan_artifact = next(
                item for item in generated_artifacts
                if item["artifact_type"] == "CONTENT_EXECUTION_PLAN"
            )
            brief_artifact = next(
                item for item in generated_artifacts
                if item["artifact_type"] == "CONTENT_BRIEF"
            )
            completed = tuple(dict.fromkeys(project.completed_steps + ("CONTENT_BRIEF",)))
            ready = replace(
                project, status=PipelineStatus.READY_FOR_CONTENT,
                revision=project.revision + 1,
                brief_artifact_id=brief_artifact["artifact_id"],
                execution_plan_artifact_id=plan_artifact["artifact_id"],
                updated_at=datetime.now(timezone.utc).isoformat(),
                completed_steps=completed, pending_steps=_PENDING_STEPS,
                metadata={
                    **project.metadata, "provider": generated.provider,
                    "model": generated.model,
                    "prompt_version": CONTENT_BRIEF_PROMPT_VERSION,
                    "source_checksum": audio_link.checksum_sha256,
                    "correlation_id": request.correlation_id,
                },
            )
            try:
                self.projects.save(ready, expected_revision=project.revision)
            except Exception:
                self._discard(generated_artifacts, request.workspace_id)
                self._mark_failed(project, "PROJECT_STATE")
                raise ContentBriefError("PROJECT_SAVE_FAILED") from None
            usage = _usage(generated)
            result = self._success(task, ready, brief, generated, usage, generated_artifacts)
            self._history(task, result, request.correlation_id)
            if self.usage is not None and usage is not None:
                self.usage.record_safe(
                    request.workspace_id, ready.content_project_id, usage,
                    mission_id=request.music_project_id,
                    usage_id=f"content-brief-{ready.content_project_id}",
                )
            safe_log(self.logger, "CONTENT_BRIEF_COMPLETED", "ContentProjectOrchestrator",
                     workspace_id=request.workspace_id,
                     mission_id=request.music_project_id,
                     execution_id=ready.content_project_id,
                     status=PipelineStatus.READY_FOR_CONTENT,
                     provider=generated.provider, model=generated.model,
                     usage=usage,
                     metadata={"artifact_ids": [item["artifact_id"] for item in generated_artifacts]})
            return result
        except Exception as error:
            if project is not None and project.status == PipelineStatus.BRIEF_GENERATING:
                self._mark_failed(project, "CONTENT_BRIEF")
            code = error.code if isinstance(error, (ContentBriefError, TextProviderError)) else type(error).__name__
            return self._failure(task, request, code)

    def get_project(self, workspace_id, content_project_id):
        return self.projects.get(workspace_id, content_project_id)

    def _inputs(self, request):
        link_value = self.states.get(
            MUSIC_AUDIO_LINK_KIND, request.music_project_id, request.workspace_id
        )
        try:
            link = MusicProjectAudioLink(**link_value)
        except (TypeError, ValueError):
            other_workspace = any(
                item.get("workspace_id") != request.workspace_id
                for item in self.artifacts.find(None, mission_id=request.music_project_id)
            )
            raise ContentBriefError(
                "WORKSPACE_MISMATCH" if other_workspace else "MUSIC_PROJECT_NOT_FOUND"
            )
        if link.status != PipelineStatus.INPUT_READY:
            raise ContentBriefError("PROJECT_NOT_INPUT_READY")
        music = self.artifacts.find(request.workspace_id, mission_id=request.music_project_id)
        plan = next((item for item in music if item.get("artifact_type") == "MUSIC_PLAN"), None)
        audio = self.artifacts.get(link.audio_artifact_id, request.workspace_id)
        if plan is None:
            raise ContentBriefError("MUSIC_PLAN_NOT_FOUND")
        if audio is None or audio.get("artifact_type") != "MUSIC_SOURCE_AUDIO":
            raise ContentBriefError("AUDIO_ARTIFACT_NOT_FOUND")
        if audio.get("status") == "MISSING":
            raise ContentBriefError("AUDIO_ARTIFACT_MISSING")
        if audio.get("mission_id") != request.music_project_id:
            raise ContentBriefError("AUDIO_PROJECT_MISMATCH")
        plan_content = self._read_json(plan, request.workspace_id)
        return plan, audio, plan_content, link

    def _read_json(self, artifact, workspace_id):
        adapter = self.artifacts.storage_adapter
        if adapter is None:
            raise ContentBriefError("ARTIFACT_STORAGE_UNAVAILABLE")
        content = adapter.read(workspace_id, artifact["artifact_id"])
        if not isinstance(content, bytes) or len(content) > 1_000_000:
            raise ContentBriefError("MUSIC_PLAN_UNAVAILABLE")
        try:
            value = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ContentBriefError("MUSIC_PLAN_INVALID") from None
        if not isinstance(value, dict):
            raise ContentBriefError("MUSIC_PLAN_INVALID")
        return value

    def _artifacts(self, project, brief, generated):
        metadata = {
            "content_project_id": project.content_project_id,
            "music_project_id": project.music_project_id,
            "source_audio_artifact_id": project.source_audio_artifact_id,
            "schema_version": CONTENT_BRIEF_SCHEMA_VERSION,
            "prompt_version": CONTENT_BRIEF_PROMPT_VERSION,
            "provider": generated.provider, "model": generated.model,
        }
        with tempfile.TemporaryDirectory(dir=self.work_root) as temporary:
            root = Path(temporary)
            brief_json = root / "content_brief.json"
            brief_md = root / "content_brief.md"
            plan_json = root / "content_execution_plan.json"
            brief_json.write_text(json.dumps(brief.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            brief_md.write_text(_brief_markdown(brief), encoding="utf-8")
            created = []
            try:
                for path, artifact_type in (
                    (brief_json, "CONTENT_BRIEF"),
                    (brief_md, "CONTENT_BRIEF_MARKDOWN"),
                ):
                    created.append(self.artifacts.register_file(
                        path, artifact_type, "Content Project Orchestrator",
                        workspace_id=project.workspace_id,
                        mission_id=project.music_project_id,
                        task_id=project.content_project_id,
                        stage="CONTENT_BRIEF", metadata=metadata,
                    ))
                execution = ContentExecutionPlanBuilder.build(
                    project.content_project_id, created[0]["artifact_id"],
                    project.source_audio_artifact_id,
                )
                plan_json.write_text(
                    json.dumps(execution, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                created.append(self.artifacts.register_file(
                    plan_json, "CONTENT_EXECUTION_PLAN",
                    "Content Project Orchestrator",
                    workspace_id=project.workspace_id,
                    mission_id=project.music_project_id,
                    task_id=project.content_project_id,
                    stage="CONTENT_BRIEF", metadata=metadata,
                ))
                return created
            except Exception:
                self._discard(created, project.workspace_id)
                raise ContentBriefError("ARTIFACT_SAVE_FAILED") from None

    def _mark_failed(self, project, step):
        try:
            current = self.projects.get(project.workspace_id, project.content_project_id)
            if current is None or current.status != PipelineStatus.BRIEF_GENERATING:
                return
            failed = replace(
                current, status=PipelineStatus.FAILED,
                revision=current.revision + 1,
                updated_at=datetime.now(timezone.utc).isoformat(),
                failed_step=step,
            )
            self.projects.save(failed, expected_revision=current.revision)
        except Exception:
            pass

    def _discard(self, artifacts, workspace_id):
        for artifact in artifacts:
            try:
                self.artifacts.discard_managed_artifact(
                    artifact["artifact_id"], workspace_id
                )
            except Exception:
                pass

    def _history(self, task, result, correlation_id):
        if self.history is None:
            return
        result["data"]["stages"] = {
            "content_project": "CREATED",
            "brief": result["status"],
            "correlation_id": correlation_id,
        }
        try:
            self.history.record_content_stage(task, result, "CONTENT_BRIEF")
        except Exception:
            pass

    @staticmethod
    def _success(task, project, brief, generated, usage, artifacts):
        result = PipelineResult(
            PipelineStatus.READY_FOR_CONTENT, "Content Project Orchestrator",
            "Content brief", "CONTENT_BRIEF",
            data={
                "workspace_id": project.workspace_id,
                "mission_id": project.music_project_id,
                "content_project_id": project.content_project_id,
                "music_project_id": project.music_project_id,
                "project_title": brief.project_title,
                "brief_artifact_id": project.brief_artifact_id,
                "execution_plan_artifact_id": project.execution_plan_artifact_id,
                "provider": generated.provider, "model": generated.model,
                "provider_usage": usage,
                "previous_status": PipelineStatus.INPUT_READY,
                "current_status": PipelineStatus.READY_FOR_CONTENT,
                "pending_steps": list(project.pending_steps),
                "next_action": "Begin the approved @5 image package using the shared brief.",
                "task_redacted": True,
            }, artifacts=[_safe_artifact(item) for item in artifacts],
        ).to_dict()
        result["task_id"] = task.id
        return result

    def _existing_result(self, task, project):
        artifacts = [
            item for artifact_id in (
                project.brief_artifact_id, project.execution_plan_artifact_id
            ) if artifact_id for item in [self.artifacts.get(artifact_id, project.workspace_id)]
            if item is not None
        ]
        result = PipelineResult(
            PipelineStatus.READY_FOR_CONTENT, "Content Project Orchestrator",
            "Content brief", "CONTENT_BRIEF",
            data={
                "workspace_id": project.workspace_id,
                "mission_id": project.music_project_id,
                "content_project_id": project.content_project_id,
                "music_project_id": project.music_project_id,
                "brief_artifact_id": project.brief_artifact_id,
                "execution_plan_artifact_id": project.execution_plan_artifact_id,
                "current_status": project.status,
                "pending_steps": list(project.pending_steps),
                "idempotent_replay": True, "task_redacted": True,
            }, artifacts=[_safe_artifact(item) for item in artifacts],
        ).to_dict()
        result["task_id"] = task.id
        return result

    def _failure(self, task, request, code):
        workspace_id = request.workspace_id if isinstance(request, ContentBriefRequest) else None
        music_id = request.music_project_id if isinstance(request, ContentBriefRequest) else None
        result = PipelineResult(
            PipelineStatus.FAILED, "Content Project Orchestrator",
            "Content brief", "CONTENT_BRIEF",
            data={
                "workspace_id": workspace_id, "mission_id": music_id,
                "music_project_id": music_id,
                "current_status": PipelineStatus.INPUT_READY,
                "task_redacted": True,
            }, error=f"ContentBriefError: {code}",
        ).to_dict()
        result["task_id"] = getattr(task, "id", None)
        if task is not None:
            task.fail(result)
            self._history(task, result, request.correlation_id)
        safe_log(self.logger, "CONTENT_BRIEF_FAILED", "ContentProjectOrchestrator",
                 level=LogLevel.ERROR, workspace_id=workspace_id,
                 mission_id=music_id,
                 execution_id=getattr(task, "id", None), status="FAILED",
                 error=f"ContentBriefError: {code}")
        return result


def validate_content_brief(value):
    required = set(CONTENT_BRIEF_SCHEMA["required"])
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("content brief fields are invalid")
    list_fields = {
        key for key, schema in CONTENT_BRIEF_SCHEMA["properties"].items()
        if schema["type"] == "array"
    }
    for key in required - list_fields:
        if not isinstance(value[key], str) or not value[key].strip() or len(value[key]) > 4000:
            raise ValueError(f"{key} is invalid")
    for key in list_fields:
        values = value[key]
        if not isinstance(values, list) or not 1 <= len(values) <= 20 or not all(
            isinstance(item, str) and item.strip() and len(item) <= 300
            for item in values
        ):
            raise ValueError(f"{key} is invalid")
    if tuple(value["next_steps"]) != _PENDING_STEPS:
        raise ValueError("next_steps are invalid")


def _task(request):
    task = Task("Content brief generation", {"mission_id": request.music_project_id}, workspace_id=request.workspace_id)
    task.id = _content_project_id(request.workspace_id, request.music_project_id)
    task.task_type = "CONTENT_BRIEF"
    return task


def _content_project_id(workspace_id, music_project_id):
    digest = hashlib.sha256(f"{workspace_id}:{music_project_id}".encode()).hexdigest()[:24]
    return f"content-{digest}"


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest() if isinstance(value, str) else None


def _identifier(value, name):
    if not isinstance(value, str) or not value.strip() or not _SAFE_ID.fullmatch(value.strip()):
        raise ValueError(f"{name} is invalid")


def _usage(generated):
    if generated.usage is None:
        return None
    source = generated.usage if isinstance(generated.usage, dict) else {
        key: getattr(generated.usage, key) for key in (
            "input_tokens", "output_tokens", "total_tokens", "estimated_cost_usd"
        ) if hasattr(generated.usage, key)
    }
    result = {"provider": generated.provider, "model": generated.model}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        if key in source and source[key] is not None:
            result[key] = source[key]
    if "estimated_cost_usd" in source:
        result["estimated_cost_usd"] = source["estimated_cost_usd"]
    return result


def _safe_artifact(value):
    return {key: value[key] for key in (
        "artifact_id", "artifact_type", "mime_type", "filename", "size",
        "created_at", "producer_pipeline", "workspace_id", "mission_id",
        "task_id", "stage", "status", "internal_ref", "metadata",
    ) if key in value}


def _brief_markdown(brief):
    return (
        f"# {brief.project_title}\n\n## Core Message\n{brief.core_message}\n\n"
        f"## Audience\n{brief.target_audience}\n\n## Visual Direction\n"
        f"{brief.visual_concept}\n\n## Blog Direction\n{brief.blog_direction}\n\n"
        f"## Video Direction\n{brief.video_direction}\n\n## YouTube Direction\n"
        f"{brief.youtube_direction}\n\n## Prohibited Elements\n"
        + "\n".join(f"- {item}" for item in brief.prohibited_elements)
        + "\n"
    )
