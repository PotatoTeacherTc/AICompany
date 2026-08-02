from pathlib import Path
from datetime import datetime, timezone
import json
import re

from core.blog_package import BlogPackageOrchestrator, BlogPackageRequest, BlogPackageService
from core.completed_audio_intake import AudioInputLocator, AudioInputValidator, MusicProjectAudioLinkService
from core.content_brief_orchestration import ContentBriefRequest, ContentBriefService, ContentProjectOrchestrator, ContentProjectRepository
from core.image_package import ImagePackageOrchestrator, ImagePackageRequest
from core.music_planning import MusicPlanningRequest, MusicPlanningService
from core.naver_blog_publishing import NAVER_PUBLICATION_KIND, NaverBlogPublishingAssistant, NaverPublishingRequest
from core.status import PipelineStatus
from core.video_package import VideoPackageOrchestrator, VideoPackageRequest
from core.youtube_publishing import GoogleYouTubeOAuthFlow, GoogleYouTubeProvider, YouTubePublishingService
from providers.factory import ProviderFactory


_UPLOAD_NAME = re.compile(r"^[A-Za-z0-9가-힣 _.-]{1,180}$")


class ProductContentRunner:
    """One shared DI composition for the approved @2-@9 local workflow."""

    def __init__(self, root, states, artifacts, history, usage, environment,
                 youtube_connections=None, youtube_provider=None, naver_browser=None):
        self.root = Path(root).resolve(); self.root.mkdir(parents=True, exist_ok=True)
        self.states, self.artifacts, self.history, self.usage = states, artifacts, history, usage
        self.environment = dict(environment)
        self.projects = ContentProjectRepository(states)
        self.youtube_connections = youtube_connections
        self.youtube_provider = youtube_provider or GoogleYouTubeProvider()
        self.naver_browser = naver_browser

    def upload_audio(self, workspace_id, project_id, filename, content):
        if not isinstance(filename, str) or not _UPLOAD_NAME.fullmatch(filename) or Path(filename).suffix.lower() not in {".mp3", ".wav", ".flac", ".m4a"}:
            raise ValueError("invalid audio filename")
        if not isinstance(content, bytes) or not content or len(content) > 250 * 1024 * 1024:
            raise ValueError("invalid audio content")
        locator = AudioInputLocator(self.root / "inputs")
        directory = locator.workspace_directory(workspace_id, create=True)
        target = directory / filename
        if target.exists(): raise ValueError("audio upload already exists")
        try:
            target.write_bytes(content)
            return MusicProjectAudioLinkService(
                locator, AudioInputValidator(), self.artifacts, self.states, self.history
            ).import_audio(workspace_id, project_id, filename)
        finally:
            try: target.unlink(missing_ok=True)
            except OSError: pass

    def __call__(self, stage, workspace_id, product_id, request_text, record=None):
        record = record or {}; results = record.get("results") or {}
        if stage == "PLANNING":
            if not request_text: return self._failed("REQUEST_NOT_AVAILABLE")
            selection = ProviderFactory.text_from_environment(self.environment)
            value = MusicPlanningService(
                self.root / "work", selection=selection,
                artifact_manager=self.artifacts, execution_history=self.history,
            ).run(MusicPlanningRequest(workspace_id, request_text))
            data = value.get("data") or {}
            return self._output(value, "WAITING_FOR_INPUT", {
                "project_id": data.get("mission_id"), "provider": data.get("provider"),
                "model": data.get("model"), "next_action": data.get("next_action"),
            })
        project_id = (results.get("planning") or {}).get("project_id")
        if not project_id: return self._failed("MUSIC_PROJECT_NOT_FOUND")
        if stage == "MUSIC":
            link = MusicProjectAudioLinkService(
                AudioInputLocator(self.root / "inputs"), AudioInputValidator(),
                self.artifacts, self.states, self.history,
            ).get_link(workspace_id, project_id)
            if link is None: return {"status": "WAITING_FOR_INPUT", "safe_error": "AUDIO_INPUT_REQUIRED"}
            selection = ProviderFactory.text_from_environment(self.environment)
            value = ContentProjectOrchestrator(
                self.root / "work", ContentBriefService(selection=selection), self.projects,
                self.states, self.artifacts, self.history, self.usage,
            ).run(ContentBriefRequest(workspace_id, project_id))
            data = value.get("data") or {}
            return self._output(value, "COMPLETED", {"content_project_id": data.get("content_project_id")})
        content_id = (results.get("music") or {}).get("content_project_id")
        if not content_id: return self._failed("CONTENT_PROJECT_NOT_FOUND")
        if stage == "IMAGE":
            value = ImagePackageOrchestrator(
                self.root / "work", ProviderFactory.image_from_environment(self.environment),
                self.projects, self.states, self.artifacts, self.history, self.usage,
            ).run(ImagePackageRequest(workspace_id, content_id, seed=1000))
            return self._output(value, "COMPLETED", {"content_project_id": content_id})
        if stage == "BLOG":
            value = BlogPackageOrchestrator(
                self.root / "work", BlogPackageService(selection=ProviderFactory.text_from_environment(self.environment)),
                self.projects, self.states, self.artifacts, self.history, self.usage,
            ).run(BlogPackageRequest(workspace_id, content_id))
            return self._output(value, "COMPLETED", {"content_project_id": content_id})
        if stage == "VIDEO":
            value = VideoPackageOrchestrator(
                self.root / "work", ProviderFactory.video_from_environment(self.environment),
                self.projects, self.states, self.artifacts, self.history, self.usage,
            ).run(VideoPackageRequest(workspace_id, content_id, f"video-{product_id}"))
            return self._output(value, "COMPLETED", {"content_project_id": content_id})
        if stage == "YOUTUBE":
            if self.youtube_connections is None: return {"status": "CONNECTION_REQUIRED", "safe_error": "YOUTUBE_CONNECTION_REQUIRED"}
            connections = self.youtube_connections.repository.list(workspace_id)
            connected = next((item for item in connections if item.status == "CONNECTED"), None)
            if connected is None: return {"status": "CONNECTION_REQUIRED", "safe_error": "YOUTUBE_CONNECTION_REQUIRED"}
            try: self._refresh_youtube(connected)
            except Exception: return {"status": "CONNECTION_REQUIRED", "safe_error": "YOUTUBE_RECONNECT_REQUIRED"}
            value = YouTubePublishingService(
                self.youtube_provider, self.youtube_connections, self.projects,
                self.states, self.artifacts, self.history, self.usage,
            ).publish(workspace_id, content_id, connected.connection_id, f"youtube-{product_id}", "private")
            return self._output(value, "COMPLETED", {"published_url": (value.get("data") or {}).get("published_url")})
        if stage == "NAVER":
            if self.naver_browser is None: return {"status": "CONNECTION_REQUIRED", "safe_error": "NAVER_CONNECTION_REQUIRED"}
            assistant = NaverBlogPublishingAssistant(
                self.naver_browser, self.states, self.artifacts, self.history,
                self.root / "work" / "naver", self.usage,
            )
            request = NaverPublishingRequest(workspace_id, content_id, "Potato Company", False, 900)
            current = self.states.get(NAVER_PUBLICATION_KIND, content_id, workspace_id)
            value = (assistant.complete_after_confirmation(request)
                     if isinstance(current, dict) and current.get("status") == "USER_CONFIRM_REQUIRED"
                     else assistant.run(request))
            status = (value.get("data") or {}).get("status") or value.get("status")
            return self._output(value, "PUBLISHED" if status == "PUBLISHED" else status, {"published_url": (value.get("data") or {}).get("published_url")})
        return self._failed("STAGE_UNSUPPORTED")

    def _refresh_youtube(self, connection):
        token = self.youtube_connections.token(connection.workspace_id, connection.connection_id)
        try: expires = datetime.fromisoformat(token.get("expires_at", ""))
        except (TypeError, ValueError): expires = datetime.min.replace(tzinfo=timezone.utc)
        if expires.tzinfo is None: expires = expires.replace(tzinfo=timezone.utc)
        if expires > datetime.now(timezone.utc): return
        client_file = self.environment.get("AICOMPANY_GOOGLE_CLIENT_SECRET_FILE")
        if not client_file: raise ValueError("client configuration unavailable")
        config = json.loads(Path(client_file).read_text(encoding="utf-8"))
        refreshed = GoogleYouTubeOAuthFlow().refresh(config, token.get("refresh_token"))
        self.youtube_connections.refresh(connection.workspace_id, connection.connection_id, refreshed)

    @staticmethod
    def _output(value, success_status, result):
        status = value.get("status")
        if not value.get("error") and status not in {PipelineStatus.FAILED, PipelineStatus.TIMED_OUT, PipelineStatus.CANCELLED}:
            return {"status": success_status, "artifacts": value.get("artifacts") or [], "result": result}
        error = str(value.get("error") or "")
        code = error.split(":", 1)[-1].strip() if ":" in error else "STAGE_FAILED"
        return {"status": "FAILED", "safe_error": code if code.replace("_", "").isalnum() else "STAGE_FAILED"}

    @staticmethod
    def _failed(code): return {"status": "FAILED", "safe_error": code}
