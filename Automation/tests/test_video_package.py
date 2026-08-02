import hashlib
import json
import shutil
import tempfile
import unittest
import wave
from pathlib import Path

from core.artifact_manager import ArtifactManager
from core.artifact_repository import FileArtifactRepository
from core.blog_package import BLOG_PACKAGE_KIND
from core.completed_audio_intake import MUSIC_AUDIO_LINK_KIND, MusicProjectAudioLink
from core.content_brief_orchestration import ContentProject, ContentProjectRepository
from core.execution_history import ExecutionHistory
from core.image_package import IMAGE_PACKAGE_KIND
from core.object_storage import ArtifactStorageAdapter, LocalStorageProvider
from core.persistence import JsonStateRepository
from core.status import PipelineStatus
from core.usage_engine import UsageEngine
from core.video_package import VIDEO_PACKAGE_KIND, VideoPackageOrchestrator, VideoPackageRequest
from providers.content_media import FFmpegVideoProvider, _deterministic_png
from providers.factory import ProviderSelection


class MemoryHistoryRepository:
    def __init__(self): self.records = []
    def load(self): return list(self.records)
    def save(self, value): self.records = list(value)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg CLI is required")
class VideoPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.states = JsonStateRepository(self.root / "state.json")
        repository = FileArtifactRepository(self.root / "artifacts.json", self.root / "storage")
        self.artifacts = ArtifactManager(repository, ArtifactStorageAdapter(LocalStorageProvider(self.root / "storage"), repository))
        self.projects = ContentProjectRepository(self.states); self.history_repo = MemoryHistoryRepository()
        self.orchestrator = VideoPackageOrchestrator(
            self.root / "work", ProviderSelection(FFmpegVideoProvider(), "ffmpeg-h264-aac", 60),
            self.projects, self.states, self.artifacts, ExecutionHistory(repository=self.history_repo), UsageEngine(self.states),
        )
        self._project("workspace-a", "content-a")

    def tearDown(self): self.temp.cleanup()

    def _register(self, path, kind, workspace="workspace-a", content="content-a", metadata=None):
        return self.artifacts.register_file(path, kind, "test", workspace_id=workspace, mission_id="music-a", task_id=content, metadata=metadata)

    def _project(self, workspace, content):
        audio_path = self.root / f"{content}.wav"
        with wave.open(str(audio_path), "wb") as output:
            output.setnchannels(1); output.setsampwidth(2); output.setframerate(8000); output.writeframes(b"\0\0" * 9600)
        audio = self._register(audio_path, "MUSIC_SOURCE_AUDIO", workspace, content)
        cover_path = self.root / f"{content}-cover.png"; cover_path.write_bytes(_deterministic_png(320, 180, 4))
        thumb_path = self.root / f"{content}-thumb.png"; thumb_path.write_bytes(_deterministic_png(320, 180, 5))
        cover = self._register(cover_path, "CONTENT_COVER_IMAGE", workspace, content, {"purpose": "COVER"})
        thumb = self._register(thumb_path, "YOUTUBE_THUMBNAIL_SOURCE", workspace, content, {"purpose": "YOUTUBE_THUMBNAIL_SOURCE"})
        blog_path = self.root / f"{content}-blog.json"; blog_path.write_text(json.dumps({
            "title": "다시 걷는 밤", "excerpt": "음악과 이미지로 회복의 흐름을 소개합니다.",
            "call_to_action": "영상을 검토해 주세요.", "tags": ["음악", "회복"],
        }, ensure_ascii=False), encoding="utf-8")
        blog = self._register(blog_path, "BLOG_PACKAGE", workspace, content)
        now = "2026-08-02T00:00:00+00:00"
        self.projects.save(ContentProject(content, workspace, "music-a", "plan-a", audio["artifact_id"], PipelineStatus.READY_FOR_CONTENT, 3, "brief-a", "exec-a", now, now,
            completed_steps=("MUSIC_PLAN", "AUDIO_INPUT", "CONTENT_BRIEF", "IMAGE_PACKAGE", "BLOG_PACKAGE"),
            pending_steps=("VIDEO_PACKAGE", "YOUTUBE_PACKAGE", "PUBLISHING")))
        self.states.save(MUSIC_AUDIO_LINK_KIND, "music-a", workspace, MusicProjectAudioLink("music-a", workspace, audio["artifact_id"], audio_path.name, "wav", 1.2, hashlib.sha256(audio_path.read_bytes()).hexdigest(), now, PipelineStatus.INPUT_READY, "ready").to_dict())
        self.states.save(IMAGE_PACKAGE_KIND, content, workspace, {"status": "COMPLETED", "images": [
            {"artifact_id": cover["artifact_id"], "purpose": "COVER"}, {"artifact_id": thumb["artifact_id"], "purpose": "YOUTUBE_THUMBNAIL_SOURCE"}]})
        self.states.save(BLOG_PACKAGE_KIND, content, workspace, {"status": "COMPLETED", "blog_package_id": f"blog-{content}", "artifact_ids": [blog["artifact_id"]]})

    def test_real_ffmpeg_mp4_artifacts_history_usage_and_workspace(self):
        result = self.orchestrator.run(VideoPackageRequest("workspace-a", "content-a", "same"))
        self.assertEqual(PipelineStatus.SUCCESS, result["status"]); self.assertEqual(5, len(result["artifacts"]))
        self.assertEqual("ffmpeg", result["data"]["provider"]); self.assertAlmostEqual(1.2, result["data"]["duration_seconds"], delta=.6)
        kinds = {item["artifact_type"] for item in result["artifacts"]}
        self.assertEqual({"VIDEO_MP4", "YOUTUBE_PACKAGE_DRAFT", "YOUTUBE_DESCRIPTION", "YOUTUBE_TAGS", "VIDEO_MANIFEST"}, kinds)
        video = next(item for item in result["artifacts"] if item["artifact_type"] == "VIDEO_MP4")
        content = self.artifacts.storage_adapter.read("workspace-a", video["artifact_id"])
        self.assertGreater(len(content), 1000); self.assertIsNone(self.artifacts.get(video["artifact_id"], "workspace-b"))
        self.assertEqual(1, len(self.history_repo.records)); self.assertIsNotNone(UsageEngine(self.states).get("video-package-content-a", "workspace-a"))
        self.assertNotIn(str(self.root), repr(result))

    def test_youtube_draft_has_no_upload_and_states_remain_pending(self):
        result = self.orchestrator.run(VideoPackageRequest("workspace-a", "content-a"))
        package_artifact = next(item for item in result["artifacts"] if item["artifact_type"] == "YOUTUBE_PACKAGE_DRAFT")
        package = json.loads(self.artifacts.storage_adapter.read("workspace-a", package_artifact["artifact_id"]))
        self.assertEqual("NOT_UPLOADED", package["upload_status"]); self.assertEqual("private", package["visibility_draft"])
        self.assertTrue(package["thumbnail_artifact_id"]); self.assertNotIn("url", repr(package).lower())
        project = self.projects.get("workspace-a", "content-a")
        self.assertIn("VIDEO_PACKAGE", project.completed_steps); self.assertEqual(("YOUTUBE_PACKAGE", "PUBLISHING"), project.pending_steps)

    def test_idempotent_restart_does_not_render_again(self):
        first = self.orchestrator.run(VideoPackageRequest("workspace-a", "content-a", "key")); before = len(self.artifacts.list("workspace-a"))
        self.orchestrator = VideoPackageOrchestrator(self.root / "work", ProviderSelection(FFmpegVideoProvider(), "ffmpeg-h264-aac", 60), self.projects, self.states, self.artifacts, ExecutionHistory(repository=self.history_repo), UsageEngine(self.states))
        second = self.orchestrator.run(VideoPackageRequest("workspace-a", "content-a", "key"))
        self.assertEqual(first["data"]["artifact_ids"], second["data"]["artifact_ids"]); self.assertTrue(second["data"]["idempotent_replay"]); self.assertEqual(before, len(self.artifacts.list("workspace-a")))

    def test_workspace_and_prerequisite_failures_are_safe(self):
        foreign = self.orchestrator.run(VideoPackageRequest("workspace-b", "content-a")); self.assertIn("WORKSPACE_MISMATCH", foreign["error"])
        project = self.projects.get("workspace-a", "content-a"); self.projects.save(ContentProject(**{**project.to_dict(), "revision": 4, "completed_steps": ("IMAGE_PACKAGE",)}), expected_revision=3)
        failed = self.orchestrator.run(VideoPackageRequest("workspace-a", "content-a")); self.assertIn("PACKAGE_PREREQUISITE_INCOMPLETE", failed["error"])


if __name__ == "__main__": unittest.main()
