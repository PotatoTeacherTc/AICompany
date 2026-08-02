from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from html import escape
import hashlib
import json
from pathlib import Path
import re
import tempfile
import threading

from core.artifact_manager import ArtifactManager
from core.content_brief_orchestration import ContentProjectRepository
from core.image_package import IMAGE_PACKAGE_KIND
from core.persistence import StateRepository
from core.result import PipelineResult
from core.status import PipelineStatus
from core.structured_logging import LogLevel, safe_log
from core.task import Task
from providers.factory import ProviderFactory
from providers.text import TextGenerationRequest, TextGenerationResult, TextProviderError


BLOG_PACKAGE_KIND = "blog_package"
BLOG_PROMPT_VERSION = "blog-package-v1"
BLOG_SCHEMA_VERSION = "1.0"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")
_SAFE_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_OPTION = 2000
_MAX_BODY = 6000


def _string():
    return {"type": "string"}


def _strings():
    return {"type": "array", "items": _string()}


BLOG_GENERATION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "title": _string(), "alternative_titles": _strings(),
        "excerpt": _string(), "meta_description": _string(),
        "primary_keyword": _string(), "secondary_keywords": _strings(),
        "tags": _strings(), "target_audience": _string(), "tone": _string(),
        "language": _string(),
        "sections": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"heading": _string(), "body": _string()},
            "required": ["heading", "body"],
        }},
        "call_to_action": _string(), "warnings": _strings(),
        "assumptions": _strings(), "next_action": _string(),
    },
}
BLOG_GENERATION_SCHEMA["required"] = list(BLOG_GENERATION_SCHEMA["properties"])


class BlogPackageError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class BlogPackageRequest:
    workspace_id: str
    content_project_id: str
    target_platform: str = "generic_blog"
    language: str | None = None
    tone: str | None = None
    article_length: str | None = None
    audience_override: str | None = None
    call_to_action: str | None = None
    additional_notes: str | None = None
    idempotency_key: str | None = None
    correlation_id: str | None = None

    def validate(self):
        _identifier(self.workspace_id, "workspace_id")
        _identifier(self.content_project_id, "content_project_id")
        _identifier(self.target_platform, "target_platform")
        for name in ("language", "tone", "article_length", "audience_override", "call_to_action", "additional_notes"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > _MAX_OPTION):
                raise ValueError(f"{name} is invalid")
        for name in ("idempotency_key", "correlation_id"):
            value = getattr(self, name)
            if value is not None:
                _identifier(value, name)
        return self


@dataclass(frozen=True)
class ArticleSection:
    section_id: str
    heading: str
    level: int
    body: str
    image_reference: str | None
    alt_text: str | None
    caption: str | None
    order: int


@dataclass(frozen=True)
class ImagePlacement:
    artifact_id: str
    purpose: str
    section_id: str
    alt_text: str
    caption: str
    order: int


@dataclass(frozen=True)
class BlogPackage:
    blog_package_id: str
    workspace_id: str
    content_project_id: str
    title: str
    alternative_titles: tuple[str, ...]
    slug: str
    excerpt: str
    meta_description: str
    primary_keyword: str
    secondary_keywords: tuple[str, ...]
    tags: tuple[str, ...]
    target_audience: str
    tone: str
    language: str
    estimated_reading_minutes: int
    article_sections: tuple[ArticleSection, ...]
    image_placements: tuple[ImagePlacement, ...]
    call_to_action: str
    seo_checks: tuple[str, ...]
    warnings: tuple[str, ...]
    assumptions: tuple[str, ...]
    next_action: str
    schema_version: str
    created_at: str

    def to_dict(self):
        return asdict(self)


class BlogPackageService:
    def __init__(self, provider=None, selection=None):
        selection = selection or (ProviderFactory.text_from_environment() if provider is None else None)
        if provider is not None:
            self.provider = ProviderFactory.ensure_provider_allowed(provider)
            self.model, self.timeout = None, 30.0
        else:
            self.provider = ProviderFactory.ensure_provider_allowed(selection.provider)
            self.model, self.timeout = selection.default_model, selection.timeout_seconds

    def generate(self, request, brief, context):
        instruction = _prompt(request, brief, context)
        generated = self.provider.generate_text(TextGenerationRequest(
            request.workspace_id, request.content_project_id, "BLOG_PACKAGE",
            instruction, output_format="json", maximum_output_size=30000,
            model=self.model, timeout_seconds=self.timeout,
            response_schema=BLOG_GENERATION_SCHEMA,
        ))
        if not isinstance(generated, TextGenerationResult):
            raise BlogPackageError("INVALID_PROVIDER_RESULT")
        try:
            value = json.loads(generated.output_text)
        except (TypeError, json.JSONDecodeError):
            raise BlogPackageError("JSON_PARSE_FAILED") from None
        return _package(request, value, context), generated, instruction


class BlogPackageOrchestrator:
    def __init__(self, work_root, service, project_repository, state_repository,
                 artifact_manager, execution_history=None, usage_engine=None,
                 logger=None):
        self.root = Path(work_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.service = service
        self.projects = project_repository
        self.states = state_repository
        self.artifacts = artifact_manager
        self.history = execution_history
        self.usage = usage_engine
        self.logger = logger
        if not isinstance(project_repository, ContentProjectRepository):
            raise TypeError("project_repository must be ContentProjectRepository")
        if not isinstance(state_repository, StateRepository):
            raise TypeError("state_repository must implement StateRepository")
        if not isinstance(artifact_manager, ArtifactManager):
            raise TypeError("artifact_manager must be ArtifactManager")
        self._lock = threading.Lock()

    def run(self, request):
        if not isinstance(request, BlogPackageRequest):
            return self._failure(None, None, "INVALID_REQUEST")
        try:
            request.validate()
        except Exception:
            return self._failure(None, request, "INVALID_REQUEST")
        with self._lock:
            return self._run_locked(request)

    def _run_locked(self, request):
        task = _task(request)
        project = self.projects.get(request.workspace_id, request.content_project_id)
        if project is None:
            return self._failure(task, request, self._missing_code(request))
        if project.status != PipelineStatus.READY_FOR_CONTENT:
            return self._failure(task, request, "CONTENT_PROJECT_NOT_READY", project)
        if "IMAGE_PACKAGE" not in project.completed_steps:
            return self._failure(task, request, "IMAGE_PACKAGE_NOT_COMPLETED", project)
        existing = self.states.get(BLOG_PACKAGE_KIND, project.content_project_id, request.workspace_id)
        if isinstance(existing, dict) and existing.get("status") == "COMPLETED":
            artifacts = self._existing_artifacts(existing, request.workspace_id)
            if len(artifacts) != 5:
                return self._failure(task, request, "BLOG_ARTIFACT_MISSING", project)
            return self._success(task, project, existing, artifacts, replay=True)
        digest = _digest(request.idempotency_key)
        if isinstance(existing, dict) and existing.get("status") == "RUNNING":
            return self._failure(task, request, "DUPLICATE_EXECUTION", project)
        if isinstance(existing, dict) and existing.get("idempotency_digest") not in {None, digest}:
            return self._failure(task, request, "IDEMPOTENCY_CONFLICT", project)
        try:
            brief, image_manifest, images = self._inputs(project)
            record = {
                "blog_package_id": _blog_id(project.content_project_id),
                "workspace_id": project.workspace_id,
                "content_project_id": project.content_project_id,
                "music_project_id": project.music_project_id,
                "status": "RUNNING", "artifact_ids": [],
                "idempotency_digest": digest, "created_at": _now(), "updated_at": _now(),
            }
            self.states.save(BLOG_PACKAGE_KIND, project.content_project_id, project.workspace_id, record)
            safe_log(self.logger, "BLOG_PACKAGE_STARTED", "BlogPackageOrchestrator",
                     workspace_id=project.workspace_id, mission_id=project.music_project_id,
                     execution_id=record["blog_package_id"], status="RUNNING")
            package, generated, prompt = self.service.generate(request, brief, {
                "blog_package_id": record["blog_package_id"],
                "image_manifest_artifact_id": image_manifest["artifact_id"],
                "images": images,
            })
            created = self._save_artifacts(project, package, generated, prompt,
                                           image_manifest["artifact_id"])
            record.update({
                "status": "COMPLETED", "artifact_ids": [item["artifact_id"] for item in created],
                "provider": generated.provider, "model": generated.model,
                "title": package.title, "image_count": len(package.image_placements),
                "updated_at": _now(),
            })
            try:
                self.states.save(BLOG_PACKAGE_KIND, project.content_project_id, project.workspace_id, record)
            except Exception:
                self._discard(created, project.workspace_id)
                raise BlogPackageError("BLOG_STATE_SAVE_FAILED") from None
            ready = replace(
                project, revision=project.revision + 1, updated_at=_now(), failed_step=None,
                completed_steps=tuple(dict.fromkeys(project.completed_steps + ("BLOG_PACKAGE",))),
                pending_steps=tuple(step for step in project.pending_steps if step != "BLOG_PACKAGE"),
            )
            try:
                self.projects.save(ready, expected_revision=project.revision)
            except Exception:
                self._discard(created, project.workspace_id)
                record.update({"status": "FAILED", "artifact_ids": [], "updated_at": _now()})
                self.states.save(BLOG_PACKAGE_KIND, project.content_project_id, project.workspace_id, record)
                raise BlogPackageError("PROJECT_SAVE_FAILED") from None
            usage = _usage(generated)
            result = self._success(task, ready, record, created, usage=usage)
            self._history(task, result, request.correlation_id)
            if self.usage is not None and usage is not None:
                self.usage.record_safe(
                    request.workspace_id, f"blog-package-{project.content_project_id}", usage,
                    mission_id=project.music_project_id,
                    usage_id=f"blog-package-{project.content_project_id}",
                )
            return result
        except Exception as error:
            code = error.code if isinstance(error, (BlogPackageError, TextProviderError)) else type(error).__name__
            try:
                failed = self.states.get(BLOG_PACKAGE_KIND, project.content_project_id, request.workspace_id)
                if isinstance(failed, dict):
                    failed.update({"status": "FAILED", "updated_at": _now()})
                    self.states.save(BLOG_PACKAGE_KIND, project.content_project_id, project.workspace_id, failed)
            except Exception:
                pass
            return self._failure(task, request, code, project)

    def get(self, workspace_id, content_project_id):
        _identifier(workspace_id, "workspace_id")
        _identifier(content_project_id, "content_project_id")
        return self.states.get(BLOG_PACKAGE_KIND, content_project_id, workspace_id)

    def _inputs(self, project):
        brief_artifact = self.artifacts.get(project.brief_artifact_id, project.workspace_id)
        image_record = self.states.get(IMAGE_PACKAGE_KIND, project.content_project_id, project.workspace_id)
        if brief_artifact is None or brief_artifact.get("artifact_type") != "CONTENT_BRIEF":
            raise BlogPackageError("CONTENT_BRIEF_NOT_FOUND")
        if not isinstance(image_record, dict) or image_record.get("status") != "COMPLETED":
            raise BlogPackageError("IMAGE_PACKAGE_NOT_COMPLETED")
        manifest = self.artifacts.get(image_record.get("manifest_artifact_id"), project.workspace_id)
        if manifest is None or manifest.get("artifact_type") != "IMAGE_PACKAGE_MANIFEST":
            raise BlogPackageError("IMAGE_MANIFEST_NOT_FOUND")
        brief = self._read_json(brief_artifact, project.workspace_id, "CONTENT_BRIEF_INVALID")
        manifest_value = self._read_json(manifest, project.workspace_id, "IMAGE_MANIFEST_INVALID")
        placements = []
        for item in manifest_value.get("images", []):
            if not isinstance(item, dict) or item.get("purpose") not in {"COVER", "BLOG_INLINE"}:
                continue
            artifact = self.artifacts.get(item.get("artifact_id"), project.workspace_id)
            if artifact is None or artifact.get("status") != "AVAILABLE":
                raise BlogPackageError("IMAGE_ARTIFACT_UNAVAILABLE")
            placements.append({"artifact_id": artifact["artifact_id"], "purpose": item["purpose"]})
        if not {item["purpose"] for item in placements}.issuperset({"COVER", "BLOG_INLINE"}):
            raise BlogPackageError("REQUIRED_IMAGE_MISSING")
        return brief, manifest, placements

    def _read_json(self, artifact, workspace_id, code):
        if self.artifacts.storage_adapter is None:
            raise BlogPackageError("ARTIFACT_STORAGE_UNAVAILABLE")
        content = self.artifacts.storage_adapter.read(workspace_id, artifact["artifact_id"])
        try:
            value = json.loads(content.decode("utf-8"))
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            raise BlogPackageError(code) from None
        if not isinstance(value, dict):
            raise BlogPackageError(code)
        return value

    def _save_artifacts(self, project, package, generated, prompt, manifest_id):
        metadata = {
            "content_project_id": project.content_project_id,
            "blog_package_id": package.blog_package_id,
            "schema_version": BLOG_SCHEMA_VERSION, "provider": generated.provider,
            "model": generated.model, "prompt_version": BLOG_PROMPT_VERSION,
            "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "source_brief_artifact_id": project.brief_artifact_id,
            "image_manifest_artifact_id": manifest_id,
        }
        image_manifest = {"schema_version": BLOG_SCHEMA_VERSION,
                          "blog_package_id": package.blog_package_id,
                          "images": [asdict(item) for item in package.image_placements]}
        seo = {"title": package.title, "slug": package.slug,
               "meta_description": package.meta_description,
               "primary_keyword": package.primary_keyword,
               "secondary_keywords": list(package.secondary_keywords),
               "tags": list(package.tags), "seo_checks": list(package.seo_checks)}
        values = (
            ("blog_package.json", json.dumps(package.to_dict(), ensure_ascii=False, indent=2), "BLOG_PACKAGE"),
            ("blog_article.md", _markdown(package), "BLOG_ARTICLE_MARKDOWN"),
            ("blog_article.html", _html(package), "BLOG_ARTICLE_HTML"),
            ("blog_seo_metadata.json", json.dumps(seo, ensure_ascii=False, indent=2), "BLOG_SEO_METADATA"),
            ("blog_image_manifest.json", json.dumps(image_manifest, ensure_ascii=False, indent=2), "BLOG_IMAGE_MANIFEST"),
        )
        created = []
        with tempfile.TemporaryDirectory(dir=self.root) as temporary:
            try:
                for filename, content, artifact_type in values:
                    path = Path(temporary) / filename
                    path.write_text(content, encoding="utf-8")
                    created.append(self.artifacts.register_file(
                        path, artifact_type, "Blog Package Orchestrator",
                        workspace_id=project.workspace_id, mission_id=project.music_project_id,
                        task_id=project.content_project_id, stage="BLOG_PACKAGE", metadata=metadata,
                    ))
            except Exception:
                self._discard(created, project.workspace_id)
                raise BlogPackageError("ARTIFACT_SAVE_FAILED") from None
        return created

    def _existing_artifacts(self, record, workspace_id):
        return [item for item in (self.artifacts.get(value, workspace_id) for value in record.get("artifact_ids", [])) if item and item.get("status") == "AVAILABLE"]

    def _success(self, task, project, record, artifacts, usage=None, replay=False):
        result = PipelineResult(
            PipelineStatus.SUCCESS, "Blog Package Orchestrator", "Blog package", "BLOG_PACKAGE",
            data={"workspace_id": project.workspace_id, "mission_id": project.music_project_id,
                  "content_project_id": project.content_project_id,
                  "blog_package_id": record["blog_package_id"], "blog_package_status": "COMPLETED",
                  "title": record.get("title"), "artifact_ids": record["artifact_ids"],
                  "image_count": record.get("image_count"), "provider": record.get("provider"),
                  "model": record.get("model"), "provider_usage": usage,
                  "pending_steps": list(project.pending_steps),
                  "next_action": "Review and edit the package before manual publishing.",
                  "idempotent_replay": replay, "task_redacted": True},
            artifacts=[_safe_artifact(item) for item in artifacts],
        ).to_dict()
        result["task_id"] = task.id
        return result

    def _failure(self, task, request, code, project=None):
        result = PipelineResult(
            PipelineStatus.FAILED, "Blog Package Orchestrator", "Blog package", "BLOG_PACKAGE",
            data={"workspace_id": getattr(request, "workspace_id", None),
                  "content_project_id": getattr(request, "content_project_id", None),
                  "mission_id": getattr(project, "music_project_id", None),
                  "blog_package_status": "FAILED", "task_redacted": True},
            error=f"BlogPackageError: {code}",
        ).to_dict()
        result["task_id"] = getattr(task, "id", None)
        if task is not None:
            self._history(task, result, getattr(request, "correlation_id", None))
        safe_log(self.logger, "BLOG_PACKAGE_FAILED", "BlogPackageOrchestrator",
                 level=LogLevel.ERROR, workspace_id=getattr(request, "workspace_id", None),
                 execution_id=getattr(request, "content_project_id", None), status="FAILED",
                 error=f"BlogPackageError: {code}")
        return result

    def _history(self, task, result, correlation_id):
        result.setdefault("data", {})["stages"] = {"BLOG_PACKAGE": result["data"].get("blog_package_status"), "correlation_id": correlation_id}
        if self.history is not None:
            try:
                self.history.record_content_stage(task, result, "BLOG_PACKAGE")
            except Exception:
                pass

    def _discard(self, artifacts, workspace_id):
        for artifact in artifacts:
            try:
                self.artifacts.discard_managed_artifact(artifact["artifact_id"], workspace_id)
            except Exception:
                pass

    def _missing_code(self, request):
        return "WORKSPACE_MISMATCH" if any(item.get("task_id") == request.content_project_id and item.get("workspace_id") != request.workspace_id for item in self.artifacts.list(None)) else "CONTENT_PROJECT_NOT_FOUND"


def _package(request, value, context):
    if not isinstance(value, dict) or set(value) != set(BLOG_GENERATION_SCHEMA["required"]):
        raise BlogPackageError("SCHEMA_VALIDATION_FAILED")
    value = _clean_generated(value)
    strings = ("title", "excerpt", "meta_description", "primary_keyword", "target_audience", "tone", "language", "call_to_action", "next_action")
    if any(not isinstance(value.get(key), str) or not value[key].strip() for key in strings):
        raise BlogPackageError("SCHEMA_VALIDATION_FAILED")
    sections = value.get("sections")
    if not isinstance(sections, list) or not 3 <= len(sections) <= 12:
        raise BlogPackageError("SECTION_COUNT_INVALID")
    if any(not isinstance(item, dict) or set(item) != {"heading", "body"} or not all(isinstance(item[k], str) and item[k].strip() for k in item) or len(item["body"]) > _MAX_BODY for item in sections):
        raise BlogPackageError("SECTION_INVALID")
    for key, limit in (("alternative_titles", 5), ("secondary_keywords", 10), ("tags", 12), ("warnings", 10), ("assumptions", 10)):
        items = value.get(key)
        if not isinstance(items, list) or len(items) > limit or any(not isinstance(item, str) or not item.strip() for item in items):
            raise BlogPackageError("SCHEMA_VALIDATION_FAILED")
    if not value["alternative_titles"] or not value["secondary_keywords"] or not value["tags"]:
        raise BlogPackageError("SEO_FIELDS_INVALID")
    if not 30 <= len(value["meta_description"]) <= 180:
        raise BlogPackageError("META_DESCRIPTION_INVALID")
    slug = _slug(value["title"])
    placements = []
    cover = next(item for item in context["images"] if item["purpose"] == "COVER")
    inline = next(item for item in context["images"] if item["purpose"] == "BLOG_INLINE")
    section_models = []
    for index, item in enumerate(sections, 1):
        image = cover if index == 1 else inline if index == min(3, len(sections)) else None
        section_id = f"section-{index}"
        alt = f"{value['title']} - {item['heading']}" if image else None
        caption = item["heading"] if image else None
        section_models.append(ArticleSection(section_id, item["heading"], 2, item["body"], image["artifact_id"] if image else None, alt, caption, index))
        if image:
            placements.append(ImagePlacement(image["artifact_id"], image["purpose"], section_id, alt, caption, len(placements) + 1))
    words = sum(len(item.body.split()) for item in section_models)
    return BlogPackage(
        context["blog_package_id"], request.workspace_id, request.content_project_id,
        value["title"].strip(), tuple(value["alternative_titles"]), slug,
        value["excerpt"].strip(), value["meta_description"].strip(),
        value["primary_keyword"].strip(), tuple(value["secondary_keywords"]),
        tuple(value["tags"]), (request.audience_override or value["target_audience"]).strip(),
        (request.tone or value["tone"]).strip(), (request.language or value["language"]).strip(),
        max(1, (words + 199) // 200), tuple(section_models), tuple(placements),
        (request.call_to_action or value["call_to_action"]).strip(),
        ("title_present", "primary_keyword_present", "meta_description_length", "image_references_valid"),
        tuple(value["warnings"]), tuple(value["assumptions"]), value["next_action"].strip(),
        BLOG_SCHEMA_VERSION, _now(),
    )


def _prompt(request, brief, context):
    safe_brief = {key: brief.get(key) for key in ("project_title", "core_message", "content_goal", "target_audience", "emotional_arc", "mood_keywords", "blog_direction", "blog_requirements", "seo_primary_keywords", "seo_secondary_keywords", "assumptions", "source_summary")}
    options = {key: getattr(request, key) for key in ("target_platform", "language", "tone", "article_length", "audience_override", "call_to_action", "additional_notes")}
    images = [{"artifact_id": item["artifact_id"], "purpose": item["purpose"]} for item in context["images"]]
    return ("Create a factual, editable blog package from only the approved brief. Do not invent releases, artist history, chart results, listener reactions, platform registration, revenue, or links. Return only the supplied JSON Schema. "
            f"Approved brief: {json.dumps(safe_brief, ensure_ascii=False)}\nAvailable image references: {json.dumps(images)}\nOptions: {json.dumps(options, ensure_ascii=False)}")


def _markdown(package):
    lines = [f"# {package.title}", "", package.excerpt, ""]
    for section in package.article_sections:
        lines.extend([f"## {section.heading}", ""])
        if section.image_reference:
            lines.extend([f"![{section.alt_text}](artifact:{section.image_reference})", ""])
        lines.extend([section.body, ""])
    lines.extend([f"**다음 행동:** {package.call_to_action}", "", "태그: " + ", ".join(package.tags)])
    return "\n".join(lines)


def _html(package):
    parts = ["<article>", f"<h1>{escape(package.title)}</h1>", f"<p>{escape(package.excerpt)}</p>"]
    for section in package.article_sections:
        parts.append(f"<section><h2>{escape(section.heading)}</h2>")
        if section.image_reference:
            parts.append(f'<figure><img src="artifact:{escape(section.image_reference, quote=True)}" alt="{escape(section.alt_text, quote=True)}"><figcaption>{escape(section.caption)}</figcaption></figure>')
        parts.append(f"<p>{escape(section.body)}</p></section>")
    parts.extend([f"<p><strong>{escape(package.call_to_action)}</strong></p>", "</article>"])
    return "\n".join(parts)


def _slug(value):
    ascii_value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not ascii_value:
        ascii_value = "blog-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    if not _SAFE_SLUG.fullmatch(ascii_value) or len(ascii_value) > 96:
        raise BlogPackageError("SLUG_INVALID")
    return ascii_value


def _clean_generated(value):
    if isinstance(value, dict):
        return {key: _clean_generated(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_generated(item) for item in value]
    if not isinstance(value, str):
        return value
    clean = re.sub(r"<[^>]*>", "", value)
    clean = re.sub(r"(?i)\b(?:javascript|data)\s*:", "", clean)
    clean = re.sub(r"(?i)\bon[a-z]+\s*=", "", clean)
    return " ".join(clean.replace("\x00", "").split())


def _usage(generated):
    usage = generated.usage
    if usage is None:
        return None
    if hasattr(usage, "to_dict"):
        usage = usage.to_dict()
    elif hasattr(usage, "__dict__"):
        usage = dict(usage.__dict__)
    if not isinstance(usage, dict):
        return None
    result = {key: usage[key] for key in ("input_tokens", "output_tokens", "total_tokens", "estimated_cost_usd") if key in usage and usage[key] is not None}
    result.update({"provider": generated.provider, "model": generated.model})
    return result


def _safe_artifact(artifact):
    return {key: artifact[key] for key in ArtifactManager.METADATA_FIELDS if key in artifact}


def _task(request):
    task = Task("Blog package", {"mission_id": request.content_project_id}, workspace_id=request.workspace_id)
    task.task_type = "BLOG_PACKAGE"
    return task


def _identifier(value, name):
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ValueError(f"{name} is invalid")


def _blog_id(content_project_id):
    return f"blog-{content_project_id}"


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else None


def _now():
    return datetime.now(timezone.utc).isoformat()
