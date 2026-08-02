from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import base64
import hashlib
import json
import secrets
import threading
from urllib.parse import urlencode, urlparse

from core.artifact_manager import ArtifactManager
from core.content_brief_orchestration import ContentProjectRepository
from core.persistence import StateRepository
from core.secure_token_store import SecureTokenStore, SecureTokenStoreError
from core.status import PipelineStatus
from core.task import Task
from core.video_package import VIDEO_PACKAGE_KIND
from providers.content_media import YouTubeProvider


YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
CONNECTION_KIND = "youtube_connection"
PUBLICATION_KIND = "youtube_publication"
_STATUSES = {"DISCONNECTED", "AUTHORIZATION_PENDING", "CONNECTED", "TOKEN_EXPIRED", "REVOKED", "ERROR"}


class YouTubeFoundationError(RuntimeError):
    def __init__(self, code): self.code = code; super().__init__(f"YouTubeFoundationError: {code}")


@dataclass(frozen=True)
class YouTubeConnection:
    connection_id: str; workspace_id: str; provider: str; channel_id: str | None
    safe_channel_title: str | None; granted_scopes: tuple[str, ...]; status: str
    token_reference: str | None; connected_at: str | None; refreshed_at: str | None
    revoked_at: str | None; revision: int; metadata: dict
    def validate(self):
        _id(self.connection_id); _id(self.workspace_id)
        if self.status not in _STATUSES or not isinstance(self.metadata, dict): raise ValueError("connection invalid")
        if any(key in asdict(self) for key in ("access_token", "refresh_token")): raise ValueError("token field forbidden")
        return self
    def to_dict(self): return asdict(self)
    @classmethod
    def from_dict(cls, value):
        try: return cls(**{**value, "granted_scopes": tuple(value.get("granted_scopes", ())) }).validate()
        except Exception: return None


class YouTubeConnectionRepository:
    def __init__(self, states):
        if not isinstance(states, StateRepository): raise TypeError("states must implement StateRepository")
        self.states = states
    def save(self, value): value.validate(); self.states.save(CONNECTION_KIND, value.connection_id, value.workspace_id, value.to_dict()); return value
    def get(self, workspace_id, connection_id): return YouTubeConnection.from_dict(self.states.get(CONNECTION_KIND, connection_id, workspace_id))
    def list(self, workspace_id): return [item for item in (YouTubeConnection.from_dict(value) for value in self.states.list(CONNECTION_KIND, workspace_id)) if item]


@dataclass(frozen=True)
class OAuthSession:
    session_id: str; workspace_id: str; state: str; code_verifier: str
    code_challenge: str; redirect_uri: str; expires_at_epoch: float; used: bool = False


class YouTubeOAuthClient:
    def __init__(self, clock=None): self.clock = clock or __import__("time").time; self._sessions = {}; self._lock = threading.Lock()
    def start(self, workspace_id, client_id, redirect_host="127.0.0.1", port=0, timeout_seconds=300):
        _id(workspace_id)
        if redirect_host not in {"127.0.0.1", "localhost"} or not isinstance(port, int) or not 0 <= port <= 65535: raise YouTubeFoundationError("REDIRECT_INVALID")
        if not isinstance(client_id, str) or not client_id.strip(): raise YouTubeFoundationError("CLIENT_CONFIG_INVALID")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        session = OAuthSession("oauth_" + secrets.token_hex(16), workspace_id, state, verifier, challenge,
                               f"http://{redirect_host}:{port}/oauth/callback", self.clock() + timeout_seconds)
        with self._lock: self._sessions[session.session_id] = session
        query = urlencode({"client_id": client_id, "redirect_uri": session.redirect_uri, "response_type": "code",
                           "scope": YOUTUBE_SCOPE, "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
                           "access_type": "offline", "include_granted_scopes": "false"})
        return session, "https://accounts.google.com/o/oauth2/v2/auth?" + query
    def consume_callback(self, session_id, state, code):
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.used: raise YouTubeFoundationError("CALLBACK_REUSED")
            if self.clock() > session.expires_at_epoch: raise YouTubeFoundationError("OAUTH_TIMEOUT")
            if not secrets.compare_digest(session.state, state): raise YouTubeFoundationError("STATE_MISMATCH")
            if not isinstance(code, str) or not code: raise YouTubeFoundationError("AUTHORIZATION_CANCELLED")
            self._sessions[session_id] = replace(session, used=True)
            return {"code": code, "code_verifier": session.code_verifier, "redirect_uri": session.redirect_uri}


class YouTubeConnectionService:
    def __init__(self, repository, token_store):
        if not isinstance(token_store, SecureTokenStore): raise TypeError("token_store must implement SecureTokenStore")
        self.repository, self.tokens = repository, token_store
    def connect(self, workspace_id, channel_id, channel_title, token_payload):
        _id(workspace_id); _id(channel_id)
        scopes = tuple(token_payload.get("granted_scopes", ()))
        if scopes != (YOUTUBE_SCOPE,): raise YouTubeFoundationError("SCOPE_INVALID")
        connection_id = "ytc_" + secrets.token_hex(16)
        reference = self.tokens.put(workspace_id, connection_id, token_payload)
        now = _now(); connection = YouTubeConnection(connection_id, workspace_id, "youtube", channel_id,
            _safe_title(channel_title), scopes, "CONNECTED", reference, now, now, None, 0, {})
        try: return self.repository.save(connection)
        except Exception: self.tokens.delete(workspace_id, connection_id, reference); raise YouTubeFoundationError("CONNECTION_SAVE_FAILED") from None
    def refresh(self, workspace_id, connection_id, token_payload):
        value = self._connected(workspace_id, connection_id)
        if tuple(token_payload.get("granted_scopes", ())) != (YOUTUBE_SCOPE,): raise YouTubeFoundationError("SCOPE_INVALID")
        self.tokens.replace(workspace_id, connection_id, value.token_reference, token_payload)
        return self.repository.save(replace(value, refreshed_at=_now(), revision=value.revision + 1))
    def disconnect(self, workspace_id, connection_id):
        value = self._connected(workspace_id, connection_id); self.tokens.delete(workspace_id, connection_id, value.token_reference)
        return self.repository.save(replace(value, status="REVOKED", token_reference=None, revoked_at=_now(), revision=value.revision + 1))
    def token(self, workspace_id, connection_id):
        value = self._connected(workspace_id, connection_id)
        try: return self.tokens.get(workspace_id, connection_id, value.token_reference)
        except SecureTokenStoreError: raise YouTubeFoundationError("TOKEN_UNAVAILABLE") from None
    def _connected(self, workspace_id, connection_id):
        value = self.repository.get(workspace_id, connection_id)
        if value is None: raise YouTubeFoundationError("CONNECTION_NOT_FOUND")
        if value.status != "CONNECTED" or not value.token_reference: raise YouTubeFoundationError("CONNECTION_NOT_CONNECTED")
        return value


@dataclass(frozen=True)
class YouTubePublication:
    publication_id: str; workspace_id: str; content_project_id: str; connection_id: str
    provider: str; channel_id: str; video_id: str; privacy_status: str
    upload_status: str; processing_status: str; thumbnail_status: str
    published_url: str; uploaded_at: str; processed_at: str | None
    artifact_references: tuple[str, ...]; idempotency_digest: str; revision: int
    warnings: tuple[str, ...]; next_action: str
    def to_dict(self): return asdict(self)
    @classmethod
    def from_dict(cls, value):
        try: return cls(**{**value, "artifact_references": tuple(value["artifact_references"]), "warnings": tuple(value["warnings"])})
        except Exception: return None


class YouTubePublishingService:
    def __init__(self, provider, connections, projects, states, artifacts, history=None, usage=None):
        if not isinstance(provider, YouTubeProvider): raise TypeError("provider must be YouTubeProvider")
        self.provider, self.connections, self.projects, self.states, self.artifacts = provider, connections, projects, states, artifacts
        self.history, self.usage, self._lock = history, usage, threading.Lock()
    def publish(self, workspace_id, content_project_id, connection_id, idempotency_key, privacy="private"):
        if privacy != "private": return self._failure(workspace_id, content_project_id, "PRIVATE_REQUIRED")
        for value in (workspace_id, content_project_id, connection_id, idempotency_key):
            try: _id(value)
            except Exception: return self._failure(workspace_id, content_project_id, "INVALID_REQUEST")
        with self._lock:
            digest = hashlib.sha256(idempotency_key.encode()).hexdigest()
            current = YouTubePublication.from_dict(self.states.get(PUBLICATION_KIND, content_project_id, workspace_id))
            if current:
                if current.idempotency_digest != digest: return self._failure(workspace_id, content_project_id, "IDEMPOTENCY_CONFLICT")
                return self._result(current, replay=True)
            try:
                project = self.projects.get(workspace_id, content_project_id)
                if project is None: raise YouTubeFoundationError("CONTENT_PROJECT_NOT_FOUND")
                if "VIDEO_PACKAGE" not in project.completed_steps or "YOUTUBE_PACKAGE" not in project.pending_steps: raise YouTubeFoundationError("VIDEO_PACKAGE_INCOMPLETE")
                connection = self.connections._connected(workspace_id, connection_id); token = self.connections.token(workspace_id, connection_id)
                video_state = self.states.get(VIDEO_PACKAGE_KIND, content_project_id, workspace_id)
                inputs = self._inputs(video_state, workspace_id)
                draft = json.loads(self.artifacts.storage_adapter.read(workspace_id, inputs["draft"]["artifact_id"]).decode())
                if draft.get("visibility_draft") != "private": raise YouTubeFoundationError("PRIVATE_REQUIRED")
                upload = self.provider.start_resumable_upload({"workspace_id": workspace_id, "content_project_id": content_project_id,
                    "privacy_status": "private", "video_artifact_id": inputs["video"]["artifact_id"], "metadata": draft}, token)
                video_id = upload.get("video_id")
                if not isinstance(video_id, str) or not video_id: raise YouTubeFoundationError("UPLOAD_RESULT_INVALID")
                processing = self.provider.poll_processing(video_id, token, 60)
                if processing.get("processing_status") != "succeeded": raise YouTubeFoundationError("PROCESSING_FAILED")
                thumbnail_content = self.artifacts.storage_adapter.read(workspace_id, inputs["thumbnail"]["artifact_id"])
                thumb = self.provider.set_thumbnail(video_id, thumbnail_content, inputs["thumbnail"]["mime_type"], token, 30)
                now = _now(); publication = YouTubePublication("ytp_" + secrets.token_hex(16), workspace_id, content_project_id,
                    connection_id, "youtube", connection.channel_id, video_id, "private", "UPLOADED", "succeeded",
                    thumb.get("thumbnail_status", "FAILED"), f"https://www.youtube.com/watch?v={video_id}", now, now,
                    (inputs["video"]["artifact_id"], inputs["draft"]["artifact_id"], inputs["thumbnail"]["artifact_id"]), digest, 0, (),
                    "Review the private video in YouTube Studio.")
                self.states.save(PUBLICATION_KIND, content_project_id, workspace_id, publication.to_dict())
                ready = replace(project, revision=project.revision + 1, updated_at=now,
                    completed_steps=tuple(dict.fromkeys(project.completed_steps + ("YOUTUBE_PACKAGE",))),
                    pending_steps=tuple(step for step in project.pending_steps if step != "YOUTUBE_PACKAGE"))
                self.projects.save(ready, expected_revision=project.revision)
                if self.usage: self.usage.record_safe(workspace_id, f"youtube-{content_project_id}", {"provider": "youtube", "model": "youtube-data-api-v3", "estimated_cost_usd": 0.0}, mission_id=project.music_project_id, usage_id=f"youtube-{content_project_id}")
                result = self._result(publication); self._history(workspace_id, content_project_id, project.music_project_id, result); return result
            except Exception as error:
                code = error.code if isinstance(error, YouTubeFoundationError) else type(error).__name__
                return self._failure(workspace_id, content_project_id, code)
    def _inputs(self, state, workspace):
        if not isinstance(state, dict) or state.get("status") != "COMPLETED": raise YouTubeFoundationError("VIDEO_PACKAGE_INCOMPLETE")
        values = [self.artifacts.get(item, workspace) for item in state.get("artifact_ids", ())]
        video = next((item for item in values if item and item.get("artifact_type") == "VIDEO_MP4" and item.get("status") == "AVAILABLE"), None)
        draft = next((item for item in values if item and item.get("artifact_type") == "YOUTUBE_PACKAGE_DRAFT" and item.get("status") == "AVAILABLE"), None)
        thumbnail = self.artifacts.get(state.get("thumbnail_artifact_id"), workspace)
        if not video or not draft or not thumbnail or thumbnail.get("status") != "AVAILABLE": raise YouTubeFoundationError("ARTIFACT_UNAVAILABLE")
        if video.get("mime_type") != "video/mp4" or thumbnail.get("mime_type") not in {"image/png", "image/jpeg"} or thumbnail.get("size", 0) > 2_000_000: raise YouTubeFoundationError("ARTIFACT_INVALID")
        return {"video": video, "draft": draft, "thumbnail": thumbnail}
    @staticmethod
    def _result(value, replay=False): return {"status": "SUCCESS", "pipeline": "YouTube Publishing Foundation", "artifacts": [], "data": {**value.to_dict(), "provider_usage": {"provider": "youtube", "model": "youtube-data-api-v3", "estimated_cost_usd": 0.0}, "idempotent_replay": replay}, "error": None}
    @staticmethod
    def _failure(workspace, project, code): return {"status": "FAILED", "data": {"workspace_id": workspace, "content_project_id": project}, "error": f"YouTubeFoundationError: {code}"}
    def _history(self, workspace, content_project, mission, result):
        if not self.history: return
        task = Task("YouTube publication", {"mission_id": mission}, workspace_id=workspace); task.task_type = "YOUTUBE_PACKAGE"
        result["data"].update({"mission_id": mission, "stages": {"YOUTUBE_PACKAGE": "COMPLETED"}})
        try: self.history.record_content_stage(task, result, "YOUTUBE_PACKAGE")
        except Exception: pass


def _id(value):
    if not isinstance(value, str) or not value or len(value) > 128 or not all(c.isalnum() or c in "._:-" for c in value): raise ValueError("invalid identifier")
def _safe_title(value):
    if not isinstance(value, str) or not value.strip(): raise YouTubeFoundationError("CHANNEL_INVALID")
    return value.strip()[:100]
def _now(): return datetime.now(timezone.utc).isoformat()
