from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import struct
import tempfile
import threading
import time
import zlib

from core.artifact_manager import ArtifactManager
from core.content_brief_orchestration import ContentProjectRepository
from core.persistence import StateRepository
from core.result import PipelineResult
from core.status import PipelineStatus
from core.structured_logging import LogLevel, safe_log
from core.task import Task
from providers.content_media import ImageGenerationRequest, MediaGenerationResult
from providers.factory import ProviderFactory


IMAGE_PACKAGE_KIND = "image_package"
IMAGE_PROMPT_VERSION = "image-package-v1"
IMAGE_WORKFLOW_VERSION = "checkpoint-basic-v1"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")
_PURPOSES = (
    ("COVER", 1024, 1024, "CONTENT_COVER_IMAGE"),
    ("YOUTUBE_THUMBNAIL_SOURCE", 1280, 720, "YOUTUBE_THUMBNAIL_SOURCE"),
    ("VIDEO_BACKGROUND", 1280, 720, "VIDEO_BACKGROUND_IMAGE"),
    ("BLOG_INLINE", 1200, 800, "BLOG_INLINE_IMAGE"),
)


class ImagePackageError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ImagePackageRequest:
    workspace_id: str
    content_project_id: str
    seed: int = 1000
    workflow_profile: str = "default"
    correlation_id: str | None = None

    def validate(self):
        _identifier(self.workspace_id, "workspace_id")
        _identifier(self.content_project_id, "content_project_id")
        _identifier(self.workflow_profile, "workflow_profile")
        if self.correlation_id is not None:
            _identifier(self.correlation_id, "correlation_id")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool) or not 0 <= self.seed < 2**63:
            raise ValueError("seed is invalid")
        return self


class ImagePromptFormatter:
    @staticmethod
    def build(brief, purpose):
        fields = (
            "visual_concept", "visual_style", "color_direction",
            "thumbnail_direction", "target_audience", "core_message",
        )
        values = [str(brief.get(key, "")).strip() for key in fields]
        requirements = brief.get("image_requirements") or []
        moods = brief.get("mood_keywords") or []
        prohibited = brief.get("prohibited_elements") or []
        if not all(values) or not all(isinstance(item, str) for item in requirements + moods + prohibited):
            raise ImagePackageError("CONTENT_BRIEF_INVALID")
        prompt = (
            f"Purpose: {purpose}. Visual concept: {values[0]}. Style: {values[1]}. "
            f"Color direction: {values[2]}. Thumbnail direction: {values[3]}. "
            f"Audience: {values[4]}. Core message: {values[5]}. "
            f"Mood: {', '.join(moods)}. Requirements: {', '.join(requirements)}. "
            "Original editorial composition; do not imitate a named living artist or studio."
        )
        negative = ", ".join(prohibited + ["embedded text", "watermark", "logo"])
        if len(prompt) > 8000 or len(negative) > 4000:
            raise ImagePackageError("IMAGE_PROMPT_TOO_LARGE")
        return prompt, negative


class ImagePackageOrchestrator:
    def __init__(self, work_root, provider_selection, project_repository,
                 state_repository, artifact_manager, execution_history=None,
                 usage_engine=None, logger=None, steps=4, guidance=3.5,
                 max_image_bytes=25_000_000):
        self.root = Path(work_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.selection = provider_selection
        self.provider = ProviderFactory.ensure_provider_allowed(provider_selection.provider)
        self.model = provider_selection.default_model or "configured-image-model"
        self.timeout = provider_selection.timeout_seconds
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
        if not isinstance(steps, int) or not 1 <= steps <= 50:
            raise ValueError("steps are invalid")
        if not isinstance(guidance, (int, float)) or not 0 <= guidance <= 30:
            raise ValueError("guidance is invalid")
        self.steps = steps
        self.guidance = float(guidance)
        self.max_image_bytes = max_image_bytes
        self._lock = threading.Lock()

    def run(self, request):
        if not isinstance(request, ImagePackageRequest):
            return self._failure(None, None, "INVALID_REQUEST")
        try:
            request.validate()
        except Exception:
            return self._failure(None, request, "INVALID_REQUEST")
        with self._lock:
            return self._run_locked(request)

    def smoke(self, request, width=512, height=512):
        """Generate one real-or-injected image without completing IMAGE_PACKAGE."""
        if not isinstance(request, ImagePackageRequest):
            return self._failure(None, None, "INVALID_REQUEST")
        try:
            request.validate()
        except Exception:
            return self._failure(None, request, "INVALID_REQUEST")
        task = _task(request)
        task.task_type = "IMAGE_PACKAGE_SMOKE"
        started = time.monotonic()
        try:
            if (
                not isinstance(width, int) or not isinstance(height, int)
                or not 64 <= width <= 2048 or not 64 <= height <= 2048
            ):
                raise ImagePackageError("SMOKE_DIMENSIONS_INVALID")
            project = self.projects.get(request.workspace_id, request.content_project_id)
            if project is None:
                raise ImagePackageError(self._missing_code(request))
            if project.status != PipelineStatus.READY_FOR_CONTENT:
                raise ImagePackageError("CONTENT_PROJECT_NOT_READY")
            brief = self._brief(project)
            prompt, negative = ImagePromptFormatter.build(brief, "COVER")
            with tempfile.TemporaryDirectory(dir=self.root) as temporary:
                generated = self.provider.generate_image(ImageGenerationRequest(
                    prompt, request.workspace_id, project.music_project_id,
                    temporary, model=self.model, timeout_seconds=self.timeout,
                    purpose="COVER", width=width, height=height, seed=request.seed,
                    steps=self.steps, guidance=self.guidance,
                    negative_prompt=negative,
                    workflow_profile=request.workflow_profile,
                ))
                path, info = self._validate_generation(
                    generated, temporary, width, height
                )
                artifact = self.artifacts.register_file(
                    path, "CONTENT_COVER_IMAGE", "Image Package Orchestrator",
                    workspace_id=request.workspace_id,
                    mission_id=project.music_project_id,
                    task_id=project.content_project_id,
                    stage="IMAGE_PACKAGE_SMOKE",
                    metadata={
                        "provider": generated.provider, "model": generated.model,
                        "prompt_version": IMAGE_PROMPT_VERSION,
                        "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                        "workflow_version": IMAGE_WORKFLOW_VERSION,
                        "content_project_id": project.content_project_id,
                        "purpose": "COVER", "width": info[1], "height": info[2],
                        "seed": request.seed, "steps": self.steps,
                        "guidance": self.guidance,
                        "checksum_sha256": info[3],
                    },
                )
            if self.artifacts.get(artifact["artifact_id"], request.workspace_id) is None:
                raise ImagePackageError("ARTIFACT_SAVE_FAILED")
            duration = round(time.monotonic() - started, 6)
            usage = {
                "provider": generated.provider, "model": generated.model,
                "estimated_cost_usd": 0.0,
            }
            result = PipelineResult(
                PipelineStatus.SUCCESS, "Image Package Orchestrator",
                "Image package smoke", "IMAGE_PACKAGE_SMOKE",
                data={
                    "workspace_id": request.workspace_id,
                    "mission_id": project.music_project_id,
                    "content_project_id": project.content_project_id,
                    "image_package_status": "SMOKE_COMPLETED",
                    "provider": generated.provider, "model": generated.model,
                    "provider_usage": usage, "format": info[0],
                    "width": info[1], "height": info[2],
                    "checksum_sha256": info[3], "duration_seconds": duration,
                    "artifact_id": artifact["artifact_id"],
                    "task_redacted": True,
                }, artifacts=[_safe_artifact(artifact)],
            ).to_dict()
            result["task_id"] = task.id
            self._history(task, result, "IMAGE_PACKAGE_SMOKE")
            if self.usage is not None:
                self.usage.record_safe(
                    request.workspace_id,
                    f"image-package-smoke-{project.content_project_id}", usage,
                    mission_id=project.music_project_id,
                    usage_id=f"image-package-smoke-{project.content_project_id}",
                )
            return result
        except Exception as error:
            code = error.code if isinstance(error, ImagePackageError) else type(error).__name__
            return self._failure(task, request, code)

    def _run_locked(self, request):
        task = _task(request)
        project = self.projects.get(request.workspace_id, request.content_project_id)
        if project is None:
            return self._failure(task, request, self._missing_code(request))
        if project.status != PipelineStatus.READY_FOR_CONTENT:
            return self._failure(task, request, "CONTENT_PROJECT_NOT_READY")
        existing = self.states.get(IMAGE_PACKAGE_KIND, project.content_project_id, request.workspace_id)
        if isinstance(existing, dict) and existing.get("status") == "COMPLETED":
            if self.artifacts.get(existing.get("manifest_artifact_id"), request.workspace_id) is None:
                return self._failure(task, request, "MANIFEST_ARTIFACT_MISSING", project)
            return self._existing(task, project, existing)
        brief = self._brief(project)
        record = self._start_record(project, request, existing)
        self._save(record)
        safe_log(self.logger, "IMAGE_PACKAGE_STARTED", "ImagePackageOrchestrator",
                 workspace_id=request.workspace_id, mission_id=project.music_project_id,
                 execution_id=project.content_project_id, status="RUNNING",
                 provider=self.provider.name, model=self.model)
        try:
            for index, (purpose, width, height, artifact_type) in enumerate(_PURPOSES):
                reusable = self._reusable(record, purpose, request.workspace_id)
                if reusable is not None:
                    continue
                prompt, negative = ImagePromptFormatter.build(brief, purpose)
                started = time.monotonic()
                with tempfile.TemporaryDirectory(dir=self.root) as temporary:
                    generated = self.provider.generate_image(ImageGenerationRequest(
                        prompt, request.workspace_id, project.music_project_id,
                        temporary, model=self.model, timeout_seconds=self.timeout,
                        purpose=purpose, width=width, height=height,
                        seed=request.seed + index, steps=self.steps,
                        guidance=self.guidance, negative_prompt=negative,
                        workflow_profile=request.workflow_profile,
                    ))
                    if time.monotonic() - started > self.timeout:
                        raise TimeoutError()
                    path, info = self._validate_generation(generated, temporary, width, height)
                    artifact = self.artifacts.register_file(
                        path, artifact_type, "Image Package Orchestrator",
                        workspace_id=request.workspace_id,
                        mission_id=project.music_project_id,
                        task_id=project.content_project_id, stage="IMAGE_PACKAGE",
                        metadata={
                            "provider": generated.provider, "model": generated.model,
                            "prompt_version": IMAGE_PROMPT_VERSION,
                            "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                            "workflow_version": IMAGE_WORKFLOW_VERSION,
                            "content_project_id": project.content_project_id,
                            "purpose": purpose, "width": info[1], "height": info[2],
                            "seed": request.seed + index, "steps": self.steps,
                            "guidance": self.guidance,
                            "checksum_sha256": info[3],
                        },
                    )
                record["images"].append({
                    "purpose": purpose, "artifact_id": artifact["artifact_id"],
                    "artifact_type": artifact_type, "width": info[1], "height": info[2],
                    "checksum_sha256": info[3], "generation_status": "COMPLETED",
                })
                record["updated_at"] = _now()
                try:
                    self._save(record)
                except Exception:
                    record["images"].pop()
                    self.artifacts.discard_managed_artifact(artifact["artifact_id"], request.workspace_id)
                    raise ImagePackageError("IMAGE_STATE_SAVE_FAILED") from None
            manifest = self._manifest(project, record)
            manifest_artifact = self.artifacts.get(record.get("manifest_artifact_id"), request.workspace_id)
            if manifest_artifact is None:
                manifest_artifact = self._save_manifest(project, manifest)
            record.update({
                "status": "COMPLETED", "manifest_artifact_id": manifest_artifact["artifact_id"],
                "updated_at": _now(), "failed_purpose": None,
                "execution_steps": _execution_steps("COMPLETED"),
            })
            self._save(record)
            ready = replace(
                project, revision=project.revision + 1, updated_at=_now(),
                completed_steps=tuple(dict.fromkeys(project.completed_steps + ("IMAGE_PACKAGE",))),
                pending_steps=tuple(step for step in project.pending_steps if step != "IMAGE_PACKAGE"),
                failed_step=None,
            )
            try:
                self.projects.save(ready, expected_revision=project.revision)
            except Exception:
                record.update({"status": "FAILED", "failed_purpose": "PROJECT_STATE", "updated_at": _now()})
                self._save(record)
                raise ImagePackageError("PROJECT_SAVE_FAILED") from None
            result = self._success(task, ready, record, manifest_artifact)
            self._history(task, result)
            if self.usage is not None:
                self.usage.record_safe(
                    request.workspace_id, f"image-package-{project.content_project_id}",
                    {"provider": self.provider.name, "model": self.model, "estimated_cost_usd": 0.0},
                    mission_id=project.music_project_id,
                    usage_id=f"image-package-{project.content_project_id}",
                )
            return result
        except Exception as error:
            code = error.code if isinstance(error, ImagePackageError) else type(error).__name__
            record.update({"status": "FAILED", "failed_purpose": self._next_purpose(record), "updated_at": _now()})
            record["execution_steps"] = _execution_steps("FAILED")
            try:
                self._save(record)
            except Exception:
                pass
            return self._failure(task, request, code, project)

    def _brief(self, project):
        artifact = self.artifacts.get(project.brief_artifact_id, project.workspace_id)
        if artifact is None or artifact.get("artifact_type") != "CONTENT_BRIEF":
            raise ImagePackageError("CONTENT_BRIEF_NOT_FOUND")
        adapter = self.artifacts.storage_adapter
        content = adapter.read(project.workspace_id, artifact["artifact_id"]) if adapter else None
        try:
            value = json.loads(content.decode("utf-8"))
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            raise ImagePackageError("CONTENT_BRIEF_INVALID") from None
        if not isinstance(value, dict):
            raise ImagePackageError("CONTENT_BRIEF_INVALID")
        return value

    def _validate_generation(self, generated, temporary, width, height):
        if not isinstance(generated, MediaGenerationResult) or len(generated.artifacts) != 1:
            raise ImagePackageError("PROVIDER_RESULT_INVALID")
        path = Path(generated.artifacts[0].path).resolve()
        boundary = Path(temporary).resolve()
        if boundary not in path.parents or not path.is_file() or path.is_symlink():
            raise ImagePackageError("OUTPUT_PATH_UNSAFE")
        content = path.read_bytes()
        if not 0 < len(content) <= self.max_image_bytes:
            raise ImagePackageError("IMAGE_SIZE_INVALID")
        kind, actual_width, actual_height = inspect_image(content)
        extension = path.suffix.lower()
        if extension not in {".png", ".jpg", ".jpeg", ".webp"} or (
            kind == "PNG" and extension != ".png"
        ) or (kind == "JPEG" and extension not in {".jpg", ".jpeg"}) or (
            kind == "WEBP" and extension != ".webp"
        ):
            raise ImagePackageError("IMAGE_EXTENSION_MISMATCH")
        if abs(actual_width / actual_height - width / height) > 0.03:
            raise ImagePackageError("IMAGE_ASPECT_RATIO_INVALID")
        return path, (kind, actual_width, actual_height, hashlib.sha256(content).hexdigest())

    def _start_record(self, project, request, existing):
        images = list(existing.get("images", ())) if isinstance(existing, dict) else []
        return {
            "content_project_id": project.content_project_id,
            "workspace_id": project.workspace_id,
            "music_project_id": project.music_project_id,
            "status": "RUNNING", "provider": self.provider.name, "model": self.model,
            "workflow_version": IMAGE_WORKFLOW_VERSION,
            "seed": request.seed, "images": images,
            "manifest_artifact_id": existing.get("manifest_artifact_id") if isinstance(existing, dict) else None,
            "failed_purpose": None,
            "execution_steps": _execution_steps("RUNNING"),
            "created_at": existing.get("created_at", _now()) if isinstance(existing, dict) else _now(),
            "updated_at": _now(),
        }

    def _reusable(self, record, purpose, workspace_id):
        for image in record["images"]:
            if image.get("purpose") == purpose and image.get("generation_status") == "COMPLETED":
                artifact = self.artifacts.get(image.get("artifact_id"), workspace_id)
                if artifact is not None and artifact.get("status") == "AVAILABLE":
                    return image
        record["images"] = [item for item in record["images"] if item.get("purpose") != purpose]
        return None

    def _manifest(self, project, record):
        return {
            "schema_version": "1.0", "content_project_id": project.content_project_id,
            "workspace_id": project.workspace_id, "provider": self.provider.name,
            "model": self.model, "workflow_version": IMAGE_WORKFLOW_VERSION,
            "images": list(record["images"]), "generation_status": "COMPLETED",
            "usage": {"estimated_cost_usd": 0.0}, "warnings": [], "created_at": _now(),
        }

    def _save_manifest(self, project, manifest):
        with tempfile.TemporaryDirectory(dir=self.root) as temporary:
            path = Path(temporary) / "image_package_manifest.json"
            path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            return self.artifacts.register_file(
                path, "IMAGE_PACKAGE_MANIFEST", "Image Package Orchestrator",
                workspace_id=project.workspace_id, mission_id=project.music_project_id,
                task_id=project.content_project_id, stage="IMAGE_PACKAGE",
                metadata={"provider": self.provider.name, "model": self.model,
                          "workflow_version": IMAGE_WORKFLOW_VERSION,
                          "content_project_id": project.content_project_id},
            )

    def _save(self, record):
        self.states.save(IMAGE_PACKAGE_KIND, record["content_project_id"], record["workspace_id"], record)

    def _missing_code(self, request):
        return "WORKSPACE_MISMATCH" if any(
            item.get("task_id") == request.content_project_id
            and item.get("workspace_id") != request.workspace_id
            for item in self.artifacts.list(None)
        ) else "CONTENT_PROJECT_NOT_FOUND"

    @staticmethod
    def _next_purpose(record):
        completed = {item.get("purpose") for item in record.get("images", ()) if item.get("generation_status") == "COMPLETED"}
        return next((purpose for purpose, *_ in _PURPOSES if purpose not in completed), "PROJECT_STATE")

    def _success(self, task, project, record, manifest):
        artifacts = [self.artifacts.get(item["artifact_id"], project.workspace_id) for item in record["images"]]
        artifacts.append(manifest)
        result = PipelineResult(
            PipelineStatus.SUCCESS, "Image Package Orchestrator", "Image package", "IMAGE_PACKAGE",
            data={
                "workspace_id": project.workspace_id, "mission_id": project.music_project_id,
                "content_project_id": project.content_project_id,
                "image_package_status": "COMPLETED", "provider": self.provider.name,
                "model": self.model, "provider_usage": {"provider": self.provider.name,
                "model": self.model, "estimated_cost_usd": 0.0},
                "image_artifact_ids": [item["artifact_id"] for item in record["images"]],
                "manifest_artifact_id": manifest["artifact_id"],
                "pending_steps": list(project.pending_steps),
                "next_action": "Proceed to the separately approved @6 blog package.",
                "task_redacted": True,
            }, artifacts=[_safe_artifact(item) for item in artifacts if item],
        ).to_dict()
        result["task_id"] = task.id
        return result

    def _existing(self, task, project, record):
        manifest = self.artifacts.get(record.get("manifest_artifact_id"), project.workspace_id)
        result = self._success(task, project, record, manifest)
        result["data"]["idempotent_replay"] = True
        return result

    def _failure(self, task, request, code, project=None):
        workspace = request.workspace_id if isinstance(request, ImagePackageRequest) else None
        content_id = request.content_project_id if isinstance(request, ImagePackageRequest) else None
        result = PipelineResult(
            PipelineStatus.FAILED, "Image Package Orchestrator", "Image package", "IMAGE_PACKAGE",
            data={"workspace_id": workspace, "content_project_id": content_id,
                  "mission_id": getattr(project, "music_project_id", None),
                  "image_package_status": "FAILED", "task_redacted": True},
            error=f"ImagePackageError: {code}",
        ).to_dict()
        result["task_id"] = getattr(task, "id", None)
        if task is not None:
            self._history(task, result, getattr(task, "task_type", "IMAGE_PACKAGE"))
        safe_log(self.logger, "IMAGE_PACKAGE_FAILED", "ImagePackageOrchestrator",
                 level=LogLevel.ERROR, workspace_id=workspace,
                 execution_id=content_id, status="FAILED",
                 error=f"ImagePackageError: {code}")
        return result

    def _history(self, task, result, task_type="IMAGE_PACKAGE"):
        if self.history is None:
            return
        result.setdefault("data", {})["stages"] = {
            "IMAGE_PACKAGE": result.get("data", {}).get("image_package_status")
        }
        try:
            self.history.record_content_stage(task, result, task_type)
        except Exception:
            pass


def inspect_image(content):
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return _inspect_png(content)
    if content.startswith(b"\xff\xd8") and content.endswith(b"\xff\xd9"):
        return _inspect_jpeg(content)
    if len(content) >= 30 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return _inspect_webp(content)
    raise ImagePackageError("IMAGE_FORMAT_INVALID")


def _inspect_png(content):
    offset, width, height, bit_depth, color_type, compressed = 8, None, None, None, None, b""
    ended = False
    while offset + 12 <= len(content):
        length = struct.unpack(">I", content[offset:offset + 4])[0]
        kind = content[offset + 4:offset + 8]
        data = content[offset + 8:offset + 8 + length]
        crc = content[offset + 8 + length:offset + 12 + length]
        if len(data) != length or len(crc) != 4 or zlib.crc32(kind + data) & 0xffffffff != struct.unpack(">I", crc)[0]:
            raise ImagePackageError("IMAGE_DECODE_FAILED")
        if kind == b"IHDR" and length == 13:
            width, height, bit_depth, color_type = struct.unpack(">IIBB", data[:10])
        elif kind == b"IDAT":
            compressed += data
        elif kind == b"IEND":
            ended = True
            break
        offset += 12 + length
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if not ended or not width or not height or channels is None or bit_depth not in {8, 16}:
        raise ImagePackageError("IMAGE_DECODE_FAILED")
    try:
        decoded = zlib.decompress(compressed)
    except zlib.error:
        raise ImagePackageError("IMAGE_DECODE_FAILED") from None
    expected = height * (1 + ((width * channels * bit_depth + 7) // 8))
    if len(decoded) != expected:
        raise ImagePackageError("IMAGE_DECODE_FAILED")
    return "PNG", width, height


def _inspect_jpeg(content):
    offset = 2
    while offset + 4 < len(content):
        if content[offset] != 0xff:
            offset += 1
            continue
        marker = content[offset + 1]
        offset += 2
        if marker in {0xd8, 0xd9}:
            continue
        length = struct.unpack(">H", content[offset:offset + 2])[0]
        if marker in {0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf}:
            height, width = struct.unpack(">HH", content[offset + 3:offset + 7])
            if width and height:
                return "JPEG", width, height
        if length < 2:
            break
        offset += length
    raise ImagePackageError("IMAGE_DECODE_FAILED")


def _inspect_webp(content):
    if struct.unpack("<I", content[4:8])[0] + 8 != len(content):
        raise ImagePackageError("IMAGE_DECODE_FAILED")
    kind = content[12:16]
    if kind == b"VP8X":
        width = 1 + int.from_bytes(content[24:27], "little")
        height = 1 + int.from_bytes(content[27:30], "little")
    elif kind == b"VP8L" and content[20] == 0x2f:
        bits = int.from_bytes(content[21:25], "little")
        width, height = (bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1
    else:
        raise ImagePackageError("IMAGE_DECODE_FAILED")
    return "WEBP", width, height


def _safe_artifact(artifact):
    return {key: artifact[key] for key in ArtifactManager.METADATA_FIELDS if key in artifact}


def _task(request):
    task = Task("Image package", {"mission_id": request.content_project_id}, workspace_id=request.workspace_id)
    task.task_type = "IMAGE_PACKAGE"
    return task


def _identifier(value, name):
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ValueError(f"{name} is invalid")
    return value


def _now():
    return datetime.now(timezone.utc).isoformat()


def _execution_steps(image_status):
    return {
        "IMAGE_PACKAGE": image_status,
        "BLOG_PACKAGE": "PENDING", "VIDEO_PACKAGE": "PENDING",
        "YOUTUBE_PACKAGE": "PENDING", "PUBLISHING": "PENDING",
    }
