from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import threading

from core.artifact_manager import ArtifactManager
from core.blog_package import BLOG_PACKAGE_KIND
from core.completed_audio_intake import MUSIC_AUDIO_LINK_KIND, MusicProjectAudioLink
from core.content_brief_orchestration import ContentProjectRepository
from core.image_package import IMAGE_PACKAGE_KIND
from core.persistence import StateRepository
from core.result import PipelineResult
from core.status import PipelineStatus
from core.structured_logging import LogLevel, safe_log
from core.task import Task
from providers.content_media import MediaGenerationResult, VideoGenerationRequest
from providers.factory import ProviderFactory


VIDEO_PACKAGE_KIND = "video_package"
VIDEO_SCHEMA_VERSION = "1.0"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")


class VideoPackageError(RuntimeError):
    def __init__(self, code): self.code = code; super().__init__(code)


@dataclass(frozen=True)
class VideoPackageRequest:
    workspace_id: str
    content_project_id: str
    idempotency_key: str | None = None
    correlation_id: str | None = None

    def validate(self):
        for value, name in ((self.workspace_id, "workspace_id"), (self.content_project_id, "content_project_id")):
            _identifier(value, name)
        for value, name in ((self.idempotency_key, "idempotency_key"), (self.correlation_id, "correlation_id")):
            if value is not None: _identifier(value, name)
        return self


class VideoPackageOrchestrator:
    def __init__(self, work_root, provider_selection, project_repository,
                 state_repository, artifact_manager, execution_history=None,
                 usage_engine=None, logger=None, probe_runner=None):
        self.root = Path(work_root).resolve(); self.root.mkdir(parents=True, exist_ok=True)
        self.provider = ProviderFactory.ensure_provider_allowed(provider_selection.provider)
        self.model = provider_selection.default_model or "configured-video-model"
        self.timeout = provider_selection.timeout_seconds
        self.projects, self.states, self.artifacts = project_repository, state_repository, artifact_manager
        self.history, self.usage, self.logger = execution_history, usage_engine, logger
        self.probe_runner = probe_runner or subprocess.run
        if not isinstance(project_repository, ContentProjectRepository): raise TypeError("project_repository must be ContentProjectRepository")
        if not isinstance(state_repository, StateRepository): raise TypeError("state_repository must implement StateRepository")
        if not isinstance(artifact_manager, ArtifactManager): raise TypeError("artifact_manager must be ArtifactManager")
        self._lock = threading.Lock()

    def run(self, request):
        if not isinstance(request, VideoPackageRequest): return self._failure(None, None, "INVALID_REQUEST")
        try: request.validate()
        except Exception: return self._failure(None, request, "INVALID_REQUEST")
        with self._lock: return self._run(request)

    def _run(self, request):
        task = _task(request)
        project = self.projects.get(request.workspace_id, request.content_project_id)
        if project is None: return self._failure(task, request, self._missing_code(request))
        if project.status != PipelineStatus.READY_FOR_CONTENT: return self._failure(task, request, "CONTENT_PROJECT_NOT_READY", project)
        if not {"IMAGE_PACKAGE", "BLOG_PACKAGE"}.issubset(project.completed_steps):
            return self._failure(task, request, "PACKAGE_PREREQUISITE_INCOMPLETE", project)
        existing = self.states.get(VIDEO_PACKAGE_KIND, project.content_project_id, project.workspace_id)
        if isinstance(existing, dict) and existing.get("status") == "COMPLETED":
            artifacts = self._existing(existing, project.workspace_id)
            if len(artifacts) == 5: return self._success(task, project, existing, artifacts, replay=True)
            return self._failure(task, request, "VIDEO_ARTIFACT_MISSING", project)
        if isinstance(existing, dict) and existing.get("status") == "RUNNING":
            return self._failure(task, request, "DUPLICATE_EXECUTION", project)
        record = {"video_package_id": f"video-{project.content_project_id}", "workspace_id": project.workspace_id,
                  "content_project_id": project.content_project_id, "status": "RUNNING", "artifact_ids": [],
                  "idempotency_digest": _digest(request.idempotency_key), "created_at": _now(), "updated_at": _now()}
        try:
            inputs = self._inputs(project)
            self.states.save(VIDEO_PACKAGE_KIND, project.content_project_id, project.workspace_id, record)
            safe_log(self.logger, "VIDEO_PACKAGE_STARTED", "VideoPackageOrchestrator", workspace_id=project.workspace_id,
                     mission_id=project.music_project_id, execution_id=record["video_package_id"], status="RUNNING")
            with tempfile.TemporaryDirectory(dir=self.root) as temporary:
                temp = Path(temporary)
                audio_path = self._materialize(inputs["audio"], project.workspace_id, temp / ("audio" + Path(inputs["audio"].get("filename", ".mp3")).suffix))
                image_path = self._materialize(inputs["cover"], project.workspace_id, temp / ("cover" + Path(inputs["cover"].get("filename", ".png")).suffix))
                render = temp / "render"; render.mkdir()
                generated = self.provider.generate_video(VideoGenerationRequest(
                    "Create the approved still-image music video", project.workspace_id, project.music_project_id,
                    str(render), input_artifacts=tuple(_safe_artifact(item) for item in (inputs["audio"], inputs["cover"])),
                    model=self.model, timeout_seconds=self.timeout, source_audio_path=str(audio_path),
                    source_image_path=str(image_path), duration_seconds=inputs["duration"],
                ))
                video_path = self._validate_generation(generated, render)
                technical = self._probe(video_path)
                self._validate_technical(technical, inputs["duration"])
                created = self._save(project, record, generated, video_path, technical, inputs, temp)
            record.update({"status": "COMPLETED", "artifact_ids": [item["artifact_id"] for item in created],
                           "provider": generated.provider, "model": generated.model,
                           "duration_seconds": technical["duration_seconds"], "thumbnail_artifact_id": inputs["thumbnail"]["artifact_id"],
                           "updated_at": _now()})
            try: self.states.save(VIDEO_PACKAGE_KIND, project.content_project_id, project.workspace_id, record)
            except Exception:
                self._discard(created, project.workspace_id); raise VideoPackageError("VIDEO_STATE_SAVE_FAILED") from None
            ready = replace(project, revision=project.revision + 1, updated_at=_now(), failed_step=None,
                            completed_steps=tuple(dict.fromkeys(project.completed_steps + ("VIDEO_PACKAGE",))),
                            pending_steps=tuple(step for step in project.pending_steps if step != "VIDEO_PACKAGE"))
            try: self.projects.save(ready, expected_revision=project.revision)
            except Exception:
                self._discard(created, project.workspace_id); record.update({"status": "FAILED", "artifact_ids": []});
                self.states.save(VIDEO_PACKAGE_KIND, project.content_project_id, project.workspace_id, record)
                raise VideoPackageError("PROJECT_SAVE_FAILED") from None
            usage = _usage(generated)
            result = self._success(task, ready, record, created, usage=usage)
            self._history(task, result, request.correlation_id)
            if self.usage is not None and usage is not None:
                self.usage.record_safe(project.workspace_id, f"video-package-{project.content_project_id}", usage,
                                       mission_id=project.music_project_id, usage_id=f"video-package-{project.content_project_id}")
            return result
        except Exception as error:
            code = error.code if isinstance(error, VideoPackageError) else "TIMEOUT" if isinstance(error, TimeoutError) else type(error).__name__
            try:
                failed = self.states.get(VIDEO_PACKAGE_KIND, project.content_project_id, project.workspace_id)
                if isinstance(failed, dict): failed.update({"status": "FAILED", "updated_at": _now()}); self.states.save(VIDEO_PACKAGE_KIND, project.content_project_id, project.workspace_id, failed)
            except Exception: pass
            return self._failure(task, request, code, project)

    def _inputs(self, project):
        link_value = self.states.get(MUSIC_AUDIO_LINK_KIND, project.music_project_id, project.workspace_id)
        try: link = MusicProjectAudioLink(**link_value)
        except (TypeError, ValueError): raise VideoPackageError("AUDIO_LINK_NOT_FOUND") from None
        audio = self.artifacts.get(link.audio_artifact_id, project.workspace_id)
        image_state = self.states.get(IMAGE_PACKAGE_KIND, project.content_project_id, project.workspace_id)
        blog_state = self.states.get(BLOG_PACKAGE_KIND, project.content_project_id, project.workspace_id)
        if audio is None or audio.get("status") != "AVAILABLE" or audio.get("artifact_type") != "MUSIC_SOURCE_AUDIO": raise VideoPackageError("AUDIO_ARTIFACT_UNAVAILABLE")
        if not isinstance(image_state, dict) or image_state.get("status") != "COMPLETED": raise VideoPackageError("IMAGE_PACKAGE_NOT_COMPLETED")
        if not isinstance(blog_state, dict) or blog_state.get("status") != "COMPLETED": raise VideoPackageError("BLOG_PACKAGE_NOT_COMPLETED")
        images = [self.artifacts.get(item.get("artifact_id"), project.workspace_id) for item in image_state.get("images", [])]
        cover = next((item for item in images if item and item.get("metadata", {}).get("purpose") == "COVER" and item.get("status") == "AVAILABLE"), None)
        thumbnail = next((item for item in images if item and item.get("metadata", {}).get("purpose") == "YOUTUBE_THUMBNAIL_SOURCE" and item.get("status") == "AVAILABLE"), None)
        if cover is None or thumbnail is None: raise VideoPackageError("IMAGE_ARTIFACT_UNAVAILABLE")
        blog_artifact = next((self.artifacts.get(value, project.workspace_id) for value in blog_state.get("artifact_ids", []) if (self.artifacts.get(value, project.workspace_id) or {}).get("artifact_type") == "BLOG_PACKAGE"), None)
        if blog_artifact is None: raise VideoPackageError("BLOG_ARTIFACT_UNAVAILABLE")
        blog = self._read_json(blog_artifact, project.workspace_id)
        return {"audio": audio, "cover": cover, "thumbnail": thumbnail, "blog": blog,
                "duration": float(link.duration_seconds), "blog_package_id": blog_state.get("blog_package_id")}

    def _materialize(self, artifact, workspace, destination):
        if self.artifacts.storage_adapter is None: raise VideoPackageError("ARTIFACT_STORAGE_UNAVAILABLE")
        content = self.artifacts.storage_adapter.read(workspace, artifact["artifact_id"])
        if not isinstance(content, bytes) or not content: raise VideoPackageError("ARTIFACT_CONTENT_UNAVAILABLE")
        destination.write_bytes(content); return destination

    def _read_json(self, artifact, workspace):
        try: return json.loads(self.artifacts.storage_adapter.read(workspace, artifact["artifact_id"]).decode("utf-8"))
        except Exception: raise VideoPackageError("BLOG_ARTIFACT_INVALID") from None

    def _validate_generation(self, generated, boundary):
        if not isinstance(generated, MediaGenerationResult) or len(generated.artifacts) != 1: raise VideoPackageError("PROVIDER_RESULT_INVALID")
        path = Path(generated.artifacts[0].path).resolve(); boundary = Path(boundary).resolve()
        if boundary not in path.parents or not path.is_file() or path.suffix.lower() != ".mp4" or path.is_symlink(): raise VideoPackageError("VIDEO_OUTPUT_INVALID")
        return path

    def _probe(self, path):
        command = ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate", "-of", "json", str(path)]
        try: result = self.probe_runner(command, capture_output=True, timeout=min(self.timeout, 30), check=False)
        except Exception: raise VideoPackageError("FFPROBE_FAILED") from None
        if result.returncode != 0: raise VideoPackageError("FFPROBE_FAILED")
        try:
            value = json.loads(result.stdout.decode("utf-8") if isinstance(result.stdout, bytes) else result.stdout)
            streams = value["streams"]; duration = float(value["format"]["duration"])
        except Exception: raise VideoPackageError("FFPROBE_INVALID") from None
        video = next((item for item in streams if item.get("codec_type") == "video"), None); audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
        return {"duration_seconds": round(duration, 3), "video_codec": video.get("codec_name") if video else None,
                "audio_codec": audio.get("codec_name") if audio else None, "width": video.get("width") if video else None,
                "height": video.get("height") if video else None, "fps": video.get("r_frame_rate") if video else None,
                "has_audio": audio is not None}

    @staticmethod
    def _validate_technical(value, expected):
        if value.get("video_codec") != "h264" or value.get("audio_codec") != "aac" or not value.get("has_audio"): raise VideoPackageError("VIDEO_CODEC_INVALID")
        if (value.get("width"), value.get("height")) != (1920, 1080) or value.get("fps") != "30/1": raise VideoPackageError("VIDEO_PROFILE_INVALID")
        if abs(value["duration_seconds"] - expected) > 0.6: raise VideoPackageError("VIDEO_DURATION_INVALID")

    def _save(self, project, record, generated, video_path, technical, inputs, temp):
        title = inputs["blog"].get("title", "Music project")
        description = inputs["blog"].get("excerpt", "") + "\n\n" + inputs["blog"].get("call_to_action", "")
        tags = list(inputs["blog"].get("tags", []))[:12]
        package = {"schema_version": VIDEO_SCHEMA_VERSION, "title": title, "description": description,
                   "tags": tags, "category": "Music", "visibility_draft": "private",
                   "chapters_draft": [{"timestamp": "00:00", "title": title}],
                   "pinned_comment_draft": inputs["blog"].get("call_to_action", ""),
                   "thumbnail_artifact_id": inputs["thumbnail"]["artifact_id"], "upload_status": "NOT_UPLOADED"}
        manifest = {"schema_version": VIDEO_SCHEMA_VERSION, "video_package_id": record["video_package_id"],
                    "content_project_id": project.content_project_id, "source_audio_artifact_id": inputs["audio"]["artifact_id"],
                    "cover_artifact_id": inputs["cover"]["artifact_id"], "thumbnail_artifact_id": inputs["thumbnail"]["artifact_id"],
                    "technical": technical, "checksum_sha256": hashlib.sha256(video_path.read_bytes()).hexdigest()}
        files = [(video_path, "VIDEO_MP4"), (temp / "youtube_package.json", "YOUTUBE_PACKAGE_DRAFT"),
                 (temp / "youtube_description.md", "YOUTUBE_DESCRIPTION"), (temp / "youtube_tags.json", "YOUTUBE_TAGS"),
                 (temp / "video_manifest.json", "VIDEO_MANIFEST")]
        files[1][0].write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
        files[2][0].write_text(description, encoding="utf-8")
        files[3][0].write_text(json.dumps({"tags": tags}, ensure_ascii=False, indent=2), encoding="utf-8")
        files[4][0].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        metadata = {"content_project_id": project.content_project_id, "video_package_id": record["video_package_id"],
                    "blog_package_id": inputs["blog_package_id"], "schema_version": VIDEO_SCHEMA_VERSION,
                    "provider": generated.provider, "model": generated.model, "source_audio_artifact_id": inputs["audio"]["artifact_id"],
                    "thumbnail_artifact_id": inputs["thumbnail"]["artifact_id"]}
        created = []
        try:
            for path, kind in files: created.append(self.artifacts.register_file(path, kind, "Video Package Orchestrator", workspace_id=project.workspace_id, mission_id=project.music_project_id, task_id=project.content_project_id, stage="VIDEO_PACKAGE", metadata=metadata))
        except Exception:
            self._discard(created, project.workspace_id); raise VideoPackageError("ARTIFACT_SAVE_FAILED") from None
        return created

    def _existing(self, record, workspace): return [item for item in (self.artifacts.get(value, workspace) for value in record.get("artifact_ids", [])) if item and item.get("status") == "AVAILABLE"]
    def _success(self, task, project, record, artifacts, usage=None, replay=False):
        result = PipelineResult(PipelineStatus.SUCCESS, "Video Package Orchestrator", "Video package", "VIDEO_PACKAGE",
            data={"workspace_id": project.workspace_id, "mission_id": project.music_project_id, "content_project_id": project.content_project_id,
                  "video_package_id": record["video_package_id"], "video_package_status": "COMPLETED", "provider": record.get("provider"),
                  "model": record.get("model"), "duration_seconds": record.get("duration_seconds"), "thumbnail_artifact_id": record.get("thumbnail_artifact_id"),
                  "artifact_ids": record.get("artifact_ids"), "provider_usage": usage, "pending_steps": list(project.pending_steps),
                  "next_action": "Review the MP4 and YouTube package; upload is not implemented.", "idempotent_replay": replay, "task_redacted": True},
            artifacts=[_safe_artifact(item) for item in artifacts]).to_dict(); result["task_id"] = task.id; return result
    def _failure(self, task, request, code, project=None):
        result = PipelineResult(PipelineStatus.FAILED, "Video Package Orchestrator", "Video package", "VIDEO_PACKAGE",
            data={"workspace_id": getattr(request, "workspace_id", None), "content_project_id": getattr(request, "content_project_id", None),
                  "mission_id": getattr(project, "music_project_id", None), "video_package_status": "FAILED", "task_redacted": True},
            error=f"VideoPackageError: {code}").to_dict(); result["task_id"] = getattr(task, "id", None)
        if task is not None: self._history(task, result, getattr(request, "correlation_id", None))
        safe_log(self.logger, "VIDEO_PACKAGE_FAILED", "VideoPackageOrchestrator", level=LogLevel.ERROR,
                 workspace_id=getattr(request, "workspace_id", None), status="FAILED", error=f"VideoPackageError: {code}")
        return result
    def _history(self, task, result, correlation):
        result.setdefault("data", {})["stages"] = {"VIDEO_PACKAGE": result["data"].get("video_package_status"), "correlation_id": correlation}
        if self.history:
            try: self.history.record_content_stage(task, result, "VIDEO_PACKAGE")
            except Exception: pass
    def _discard(self, artifacts, workspace):
        for item in artifacts:
            try: self.artifacts.discard_managed_artifact(item["artifact_id"], workspace)
            except Exception: pass
    def _missing_code(self, request): return "WORKSPACE_MISMATCH" if any(item.get("task_id") == request.content_project_id and item.get("workspace_id") != request.workspace_id for item in self.artifacts.list(None)) else "CONTENT_PROJECT_NOT_FOUND"


def _safe_artifact(value): return {key: value[key] for key in ArtifactManager.METADATA_FIELDS if key in value}
def _usage(generated):
    usage = generated.usage
    if usage is None: return None
    return {"provider": generated.provider, "model": generated.model, "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0), "total_tokens": getattr(usage, "total_tokens", 0),
            "estimated_cost_usd": getattr(usage, "estimated_cost_usd", None)}
def _task(request): task = Task("Video package", {"mission_id": request.content_project_id}, workspace_id=request.workspace_id); task.task_type = "VIDEO_PACKAGE"; return task
def _identifier(value, name):
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value): raise ValueError(f"{name} is invalid")
def _digest(value): return hashlib.sha256(value.encode()).hexdigest() if value else None
def _now(): return datetime.now(timezone.utc).isoformat()
