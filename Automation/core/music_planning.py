from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
import time
import uuid

from core.artifact_manager import ArtifactManager
from core.result import PipelineResult
from core.status import PipelineStatus
from core.structured_logging import LogLevel, safe_log
from core.task import Task
from providers.factory import ProviderFactory
from providers.text import TextGenerationRequest, TextGenerationResult, TextProviderError


PROMPT_VERSION = "music-planning-v1"
SCHEMA_VERSION = "1.0"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")
_MAX_REQUEST = 6000


def _string():
    return {"type": "string"}


def _array(kind):
    return {"type": "array", "items": {"type": kind}}


@dataclass(frozen=True)
class MusicPlanningRequest:
    workspace_id: str
    user_request: str
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    language: str | None = None
    target_platform: str = "suno"
    genre_preferences: tuple[str, ...] = ()
    mood_preferences: tuple[str, ...] = ()
    vocal_preferences: tuple[str, ...] = ()
    reference_notes: str | None = None
    duration_preference: int | None = None
    explicit_content_allowed: bool = False
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        _identifier(self.workspace_id, "workspace_id")
        _identifier(self.request_id, "request_id")
        if not isinstance(self.user_request, str) or not self.user_request.strip():
            raise ValueError("user_request is required")
        if len(self.user_request) > _MAX_REQUEST:
            raise ValueError("user_request is too long")
        if not isinstance(self.metadata, dict):
            raise ValueError("metadata must be a dictionary")
        if self.target_platform.lower() != "suno":
            raise ValueError("target_platform must be suno")
        for name in ("genre_preferences", "mood_preferences", "vocal_preferences"):
            values = getattr(self, name)
            if not isinstance(values, (tuple, list)) or len(values) > 10 or not all(
                isinstance(value, str) and 0 < len(value.strip()) <= 100 for value in values
            ):
                raise ValueError(f"{name} is invalid")
        if self.duration_preference is not None and not 30 <= self.duration_preference <= 900:
            raise ValueError("duration_preference is invalid")
        object.__setattr__(self, "workspace_id", self.workspace_id.strip())
        object.__setattr__(self, "request_id", self.request_id.strip())
        object.__setattr__(self, "user_request", self.user_request.strip())
        object.__setattr__(self, "metadata", _safe_input_metadata(self.metadata))


@dataclass(frozen=True)
class MusicPlanningResult:
    title_candidates: tuple[str, ...]
    primary_title: str
    concept_summary: str
    target_listener: str
    genre: str
    subgenres: tuple[str, ...]
    mood: tuple[str, ...]
    tempo_bpm: int
    key_or_tonality: str
    time_signature: str
    song_structure: tuple[str, ...]
    instrumentation: tuple[str, ...]
    vocal_style: str
    language: str
    lyrical_theme: str
    lyrical_direction: str
    production_direction: str
    reference_style_notes: str
    negative_constraints: tuple[str, ...]
    suno_style_prompt: str
    suno_lyrics_prompt: str
    suno_exclude_prompt: str
    recommended_settings: dict
    variations: tuple[dict, ...]
    quality_checklist: tuple[str, ...]
    assumptions: tuple[str, ...]
    warnings: tuple[str, ...]
    next_action: str

    @classmethod
    def from_dict(cls, value):
        validate_music_plan(value)
        list_fields = {
            "title_candidates", "subgenres", "mood", "song_structure",
            "instrumentation", "negative_constraints", "variations",
            "quality_checklist", "assumptions", "warnings",
        }
        return cls(**{
            key: tuple(item) if key in list_fields else item
            for key, item in value.items()
        })

    def to_dict(self):
        return asdict(self)


MUSIC_PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title_candidates": {**_array("string"), "minItems": 2, "maxItems": 5},
        "primary_title": _string(), "concept_summary": _string(),
        "target_listener": _string(), "genre": _string(),
        "subgenres": _array("string"), "mood": _array("string"),
        "tempo_bpm": {"type": "integer", "minimum": 40, "maximum": 240}, "key_or_tonality": _string(),
        "time_signature": _string(), "song_structure": _array("string"),
        "instrumentation": _array("string"), "vocal_style": _string(),
        "language": _string(), "lyrical_theme": _string(),
        "lyrical_direction": _string(), "production_direction": _string(),
        "reference_style_notes": _string(),
        "negative_constraints": _array("string"),
        "suno_style_prompt": _string(), "suno_lyrics_prompt": _string(),
        "suno_exclude_prompt": _string(),
        "recommended_settings": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "duration_seconds": {"type": "integer", "minimum": 30, "maximum": 900},
                "explicit_content": {"type": "boolean"},
                "vocal_mode": _string(),
            },
            "required": ["duration_seconds", "explicit_content", "vocal_mode"],
        },
        "variations": {
            "type": "array", "minItems": 2, "maxItems": 3, "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "name": _string(), "style_prompt": _string(),
                    "direction": _string(),
                },
                "required": ["name", "style_prompt", "direction"],
            },
        },
        "quality_checklist": _array("string"),
        "assumptions": _array("string"), "warnings": _array("string"),
        "next_action": _string(),
    },
}
MUSIC_PLAN_SCHEMA["required"] = list(MUSIC_PLAN_SCHEMA["properties"])


class MusicPlanningPromptBuilder:
    @staticmethod
    def build(request):
        preferences = {
            "language": request.language,
            "target_platform": request.target_platform,
            "genre_preferences": list(request.genre_preferences),
            "mood_preferences": list(request.mood_preferences),
            "vocal_preferences": list(request.vocal_preferences),
            "reference_notes": request.reference_notes,
            "duration_preference": request.duration_preference,
            "explicit_content_allowed": request.explicit_content_allowed,
        }
        return (
            "You are an AI music planning employee. Convert the user intent into a "
            "complete, original music plan for manual use in Suno. Do not imitate a "
            "named artist or existing song. Preserve the intent, mark inferred choices "
            "in assumptions, and return only the supplied JSON Schema. Keep Suno inputs "
            "concise and platform-neutral when UI details are uncertain.\n"
            f"User intent: {request.user_request}\n"
            f"Optional preferences: {json.dumps(preferences, ensure_ascii=False)}"
        )


class SunoPackageFormatter:
    @staticmethod
    def build(plan):
        value = plan.to_dict()
        return {
            "title": plan.primary_title,
            "style_prompt": plan.suno_style_prompt,
            "lyrics_or_prompt": plan.suno_lyrics_prompt,
            "exclude_styles": plan.suno_exclude_prompt,
            "recommended_settings": dict(plan.recommended_settings),
            "variations": [dict(item) for item in plan.variations],
            "quality_checklist": list(plan.quality_checklist),
            "next_action": plan.next_action,
            "plan_summary": {
                key: value[key] for key in (
                    "genre", "mood", "tempo_bpm", "vocal_style",
                    "instrumentation", "song_structure",
                )
            },
        }

    @staticmethod
    def markdown(package):
        settings = package["recommended_settings"]
        variations = "\n".join(
            f"- {item['name']}: {item['style_prompt']} ({item['direction']})"
            for item in package["variations"]
        )
        checklist = "\n".join(f"- [ ] {item}" for item in package["quality_checklist"])
        return (
            f"# {package['title']}\n\n## Style Prompt\n{package['style_prompt']}\n\n"
            f"## Lyrics / Lyrics Prompt\n{package['lyrics_or_prompt']}\n\n"
            f"## Exclude Styles\n{package['exclude_styles']}\n\n"
            f"## Recommended Settings\n- Duration: {settings['duration_seconds']} seconds\n"
            f"- Explicit content: {settings['explicit_content']}\n- Vocal mode: {settings['vocal_mode']}\n\n"
            f"## Variations\n{variations}\n\n## Result Checklist\n{checklist}\n\n"
            f"## Next Action\n{package['next_action']}\n"
        )


class MusicPlanningService:
    def __init__(self, root, provider=None, selection=None, artifact_manager=None,
                 execution_history=None, logger=None, maximum_output_size=24000):
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
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifacts = artifact_manager or ArtifactManager()
        self.history = execution_history
        self.logger = logger
        self.maximum_output_size = maximum_output_size

    def run(self, request):
        task = None
        try:
            if not isinstance(request, MusicPlanningRequest):
                raise ValueError("request must use MusicPlanningRequest")
            task = Task("Music planning request", {"mission_id": request.request_id}, workspace_id=request.workspace_id)
            task.id, task.task_type = request.request_id, "MUSIC_PLAN"
            safe_log(self.logger, "MUSIC_PLANNING_STARTED", "MusicPlanningService",
                     workspace_id=request.workspace_id, mission_id=request.request_id,
                     execution_id=request.request_id, status="RUNNING",
                     metadata={"prompt_version": PROMPT_VERSION})
            started = time.monotonic()
            generated = self.provider.generate_text(TextGenerationRequest(
                request.workspace_id, request.request_id, "MUSIC_PLAN",
                MusicPlanningPromptBuilder.build(request), output_format="json",
                maximum_output_size=self.maximum_output_size, model=self.model,
                timeout_seconds=self.timeout_seconds, response_schema=MUSIC_PLAN_SCHEMA,
            ))
            if time.monotonic() - started > self.timeout_seconds:
                raise TimeoutError()
            if not isinstance(generated, TextGenerationResult):
                raise ValueError("text provider returned invalid result")
            try:
                plan = MusicPlanningResult.from_dict(
                    _normalize_music_plan(json.loads(generated.output_text))
                )
            except (TypeError, json.JSONDecodeError, ValueError):
                raise ValueError("music plan schema validation failed") from None
            package = SunoPackageFormatter.build(plan)
            files = self._write_files(request, plan, package)
            metadata = {
                "provider": generated.provider, "model": generated.model,
                "prompt_version": PROMPT_VERSION,
                "source_request_id": request.request_id,
                "schema_version": SCHEMA_VERSION,
            }
            artifacts = [self.artifacts.register_file(
                path, artifact_type, "Music Planning Service",
                workspace_id=request.workspace_id, mission_id=request.request_id,
                task_id=request.request_id, stage="MUSIC_PLANNING", metadata=metadata,
            ) for path, artifact_type in files]
            usage = _usage(generated)
            result = PipelineResult(
                PipelineStatus.WAITING_FOR_INPUT, "Music Planning Service",
                "Music planning", "MUSIC_PLAN",
                data={
                    "workspace_id": request.workspace_id,
                    "mission_id": request.request_id,
                    "provider": generated.provider, "model": generated.model,
                    "provider_usage": usage, "primary_title": plan.primary_title,
                    "next_action": plan.next_action, "prompt_version": PROMPT_VERSION,
                    "task_redacted": True,
                }, artifacts=[_safe_artifact(item) for item in artifacts],
            ).to_dict()
            result["task_id"] = request.request_id
            task.wait_for_input(result)
            self._history(task, result)
            safe_log(self.logger, "MUSIC_PLANNING_COMPLETED", "MusicPlanningService",
                     workspace_id=request.workspace_id, mission_id=request.request_id,
                     execution_id=request.request_id, status=PipelineStatus.WAITING_FOR_INPUT,
                     provider=generated.provider, model=generated.model, usage=usage,
                     metadata={"artifact_ids": [item["artifact_id"] for item in artifacts]})
            return result
        except Exception as error:
            workspace_id = request.workspace_id if isinstance(request, MusicPlanningRequest) else None
            request_id = request.request_id if isinstance(request, MusicPlanningRequest) else None
            result = PipelineResult(
                PipelineStatus.FAILED, "Music Planning Service", "Music planning",
                "MUSIC_PLAN", data={"workspace_id": workspace_id, "mission_id": request_id,
                                    "task_redacted": True},
                error=f"MusicPlanningError: {_error_code(error)}",
            ).to_dict()
            result["task_id"] = request_id
            if task is not None:
                task.fail(result)
                self._history(task, result)
            safe_log(self.logger, "MUSIC_PLANNING_FAILED", "MusicPlanningService",
                     level=LogLevel.ERROR, workspace_id=workspace_id, mission_id=request_id,
                     execution_id=request_id, status="FAILED",
                     error=f"MusicPlanningError: {_error_code(error)}")
            return result

    def _write_files(self, request, plan, package):
        project = (self.root / request.workspace_id / request.request_id).resolve()
        workspace_root = (self.root / request.workspace_id).resolve()
        if workspace_root != project and workspace_root not in project.parents:
            raise ValueError("workspace path escaped")
        project.mkdir(parents=True, exist_ok=False)
        values = (
            (project / "structured_music_plan.json", plan.to_dict(), "MUSIC_PLAN"),
            (project / "suno_creation_package.json", package, "SUNO_PACKAGE"),
        )
        for path, value, _ in values:
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown = project / "suno_creation_package.md"
        markdown.write_text(SunoPackageFormatter.markdown(package), encoding="utf-8")
        return [(path, kind) for path, _, kind in values] + [(markdown, "SUNO_PACKAGE")]

    def _history(self, task, result):
        if self.history is not None:
            try:
                self.history.record_content_stage(task, result, "MUSIC_PLAN")
            except Exception:
                pass


def validate_music_plan(value):
    required = set(MUSIC_PLAN_SCHEMA["required"])
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("music plan fields are invalid")
    strings = required - {
        "title_candidates", "subgenres", "mood", "tempo_bpm", "song_structure",
        "instrumentation", "negative_constraints", "recommended_settings",
        "variations", "quality_checklist", "assumptions", "warnings",
    }
    if any(not isinstance(value[key], str) or not value[key].strip() or len(value[key]) > 4000 for key in strings):
        raise ValueError("music plan string is invalid")
    if not 40 <= value["tempo_bpm"] <= 240:
        raise ValueError("tempo_bpm is invalid")
    for key in ("title_candidates", "subgenres", "mood", "song_structure", "instrumentation",
                "negative_constraints", "quality_checklist", "assumptions", "warnings"):
        items = value[key]
        if not isinstance(items, list) or len(items) > 20 or not all(isinstance(item, str) and item.strip() for item in items):
            raise ValueError(f"{key} is invalid")
    if not 2 <= len(value["title_candidates"]) <= 5 or value["primary_title"] not in value["title_candidates"]:
        raise ValueError("title selection is invalid")
    if not 2 <= len(value["variations"]) <= 3 or not all(
        isinstance(item, dict) and set(item) == {"name", "style_prompt", "direction"}
        and all(isinstance(part, str) and part.strip() for part in item.values())
        for item in value["variations"]
    ):
        raise ValueError("variations are invalid")
    settings = value["recommended_settings"]
    if not isinstance(settings, dict) or set(settings) != {"duration_seconds", "explicit_content", "vocal_mode"}:
        raise ValueError("recommended_settings are invalid")
    if (not isinstance(settings["duration_seconds"], int)
            or not 30 <= settings["duration_seconds"] <= 900
            or not isinstance(settings["explicit_content"], bool)
            or not isinstance(settings["vocal_mode"], str)
            or not settings["vocal_mode"].strip()):
        raise ValueError("recommended_settings are invalid")


def _normalize_music_plan(value):
    """Repair only the schema relationship JSON Schema cannot express."""
    if not isinstance(value, dict):
        return value
    primary = value.get("primary_title")
    candidates = value.get("title_candidates")
    if (
        isinstance(primary, str) and primary.strip()
        and isinstance(candidates, list) and 2 <= len(candidates) <= 5
        and all(isinstance(item, str) and item.strip() for item in candidates)
        and primary not in candidates
    ):
        value = dict(value)
        value["title_candidates"] = [primary, *candidates[1:]]
    return value


def _identifier(value, name):
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value.strip()):
        raise ValueError(f"{name} is invalid")


def _safe_input_metadata(value):
    allowed = {"source", "locale"}
    if set(value) - allowed:
        raise ValueError("metadata contains unsupported fields")
    return {key: item for key, item in value.items() if isinstance(item, (str, int, float, bool, type(None)))}


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


def _error_code(error):
    if isinstance(error, TextProviderError):
        return error.code
    names = {TimeoutError: "timeout", ConnectionError: "network_error"}
    for kind, code in names.items():
        if isinstance(error, kind):
            return code
    return type(error).__name__
