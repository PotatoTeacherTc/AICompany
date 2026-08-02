import json
import sys
import tempfile
import unittest
from pathlib import Path

from core.artifact_manager import ArtifactManager
from core.artifact_repository import FileArtifactRepository
from core.content_brief_orchestration import ContentProject, ContentProjectRepository
from core.execution_history import ExecutionHistory
from core.object_storage import ArtifactStorageAdapter, LocalStorageProvider
from core.persistence import JsonStateRepository
from core.secure_token_store import FakeSecureTokenStore, SecureTokenStoreError, WindowsLocalSecureTokenStore
from core.status import PipelineStatus
from core.usage_engine import UsageEngine
from core.video_package import VIDEO_PACKAGE_KIND
from core.youtube_publishing import (CONNECTION_KIND, PUBLICATION_KIND, YOUTUBE_SCOPE,
    YouTubeConnectionRepository, YouTubeConnectionService, YouTubeFoundationError,
    YouTubeOAuthClient, YouTubePublishingService)
from providers.content_media import FakeYouTubeProvider


def token(marker="marker-a"):
    return {"access_token": marker + "-access", "refresh_token": marker + "-refresh",
            "expires_at": "2026-08-02T12:00:00+00:00", "token_type": "Bearer",
            "granted_scopes": (YOUTUBE_SCOPE,)}


class SecureTokenStoreTests(unittest.TestCase):
    def test_fake_crud_binding_tamper_and_shared_restart(self):
        records = {}; first = FakeSecureTokenStore(records)
        reference = first.put("workspace-a", "connection-a", token("secret-one"))
        self.assertNotIn("secret", reference); self.assertEqual("secret-one-access", first.get("workspace-a", "connection-a", reference)["access_token"])
        with self.assertRaises(SecureTokenStoreError): first.get("workspace-b", "connection-a", reference)
        with self.assertRaises(SecureTokenStoreError): first.get("workspace-a", "connection-a", reference[:-1] + "0")
        second = FakeSecureTokenStore(records); second.replace("workspace-a", "connection-a", reference, token("secret-two"))
        self.assertEqual("secret-two-refresh", first.get("workspace-a", "connection-a", reference)["refresh_token"])
        self.assertTrue(second.delete("workspace-a", "connection-a", reference)); self.assertFalse(first.exists("workspace-a", "connection-a", reference))

    @unittest.skipUnless(sys.platform == "win32", "Windows Credential Manager smoke")
    def test_windows_credential_manager_actual_put_get_replace_delete(self):
        store = WindowsLocalSecureTokenStore(); connection = "smoke-" + __import__("secrets").token_hex(8)
        reference = store.put("workspace-smoke", connection, token("aicompany-smoke-secret"))
        try:
            restored = WindowsLocalSecureTokenStore().get("workspace-smoke", connection, reference)
            self.assertEqual("aicompany-smoke-secret-refresh", restored["refresh_token"])
            store.replace("workspace-smoke", connection, reference, token("aicompany-smoke-replaced"))
            self.assertEqual("aicompany-smoke-replaced-access", store.get("workspace-smoke", connection, reference)["access_token"])
        finally: store.delete("workspace-smoke", connection, reference)
        self.assertFalse(store.exists("workspace-smoke", connection, reference))

    def test_non_windows_has_no_plaintext_fallback(self):
        if sys.platform != "win32":
            with self.assertRaisesRegex(SecureTokenStoreError, "UNSUPPORTED_SECURE_STORE"): WindowsLocalSecureTokenStore()


class OAuthFoundationTests(unittest.TestCase):
    def test_state_pkce_minimal_scope_loopback_and_single_use(self):
        oauth = YouTubeOAuthClient(clock=lambda: 100)
        session, url = oauth.start("workspace-a", "client-a", port=8765)
        self.assertIn("youtube.upload", url); self.assertNotIn("force-ssl", url); self.assertNotEqual(session.code_verifier, session.code_challenge)
        value = oauth.consume_callback(session.session_id, session.state, "code-a"); self.assertEqual(session.code_verifier, value["code_verifier"])
        with self.assertRaisesRegex(YouTubeFoundationError, "CALLBACK_REUSED"): oauth.consume_callback(session.session_id, session.state, "code-a")
    def test_state_mismatch_and_external_redirect_are_blocked(self):
        oauth = YouTubeOAuthClient(clock=lambda: 100); session, _ = oauth.start("workspace-a", "client-a")
        with self.assertRaisesRegex(YouTubeFoundationError, "STATE_MISMATCH"): oauth.consume_callback(session.session_id, "wrong", "code")
        with self.assertRaisesRegex(YouTubeFoundationError, "REDIRECT_INVALID"): oauth.start("workspace-a", "client-a", "192.168.0.2")


class PublishingFoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.state_path = self.root / "state.json"; self.states = JsonStateRepository(self.state_path)
        repository = FileArtifactRepository(self.root / "artifacts.json", self.root / "storage")
        self.artifacts = ArtifactManager(repository, ArtifactStorageAdapter(LocalStorageProvider(self.root / "storage"), repository))
        self.projects = ContentProjectRepository(self.states); self.tokens = FakeSecureTokenStore(); self.connections = YouTubeConnectionService(YouTubeConnectionRepository(self.states), self.tokens)
        self.connection = self.connections.connect("workspace-a", "channel-a", "Safe Channel", token("never-persist"))
        self._project()
        class HistoryRepository:
            def __init__(repo): repo.records = []
            def load(repo): return list(repo.records)
            def save(repo, records): repo.records = list(records)
        self.history_repository = HistoryRepository()
        self.service = YouTubePublishingService(FakeYouTubeProvider(), self.connections, self.projects, self.states, self.artifacts, history=ExecutionHistory(repository=self.history_repository), usage=UsageEngine(self.states))
    def tearDown(self): self.temp.cleanup()
    def _artifact(self, name, content, kind, mime_name=None):
        path = self.root / (mime_name or name); path.write_bytes(content)
        return self.artifacts.register_file(path, kind, "test", workspace_id="workspace-a", mission_id="music-a", task_id="content-a")
    def _project(self):
        video = self._artifact("video.mp4", b"video" * 100, "VIDEO_MP4")
        draft = self._artifact("youtube_package.json", json.dumps({"title": "Title", "description": "Description", "tags": ["music"], "category": "Music", "visibility_draft": "private"}).encode(), "YOUTUBE_PACKAGE_DRAFT")
        thumb = self._artifact("thumb.png", b"\x89PNG\r\n\x1a\n" + b"x" * 100, "YOUTUBE_THUMBNAIL_SOURCE")
        now = "2026-08-02T00:00:00+00:00"; self.projects.save(ContentProject("content-a", "workspace-a", "music-a", "plan", "audio", PipelineStatus.READY_FOR_CONTENT, 4, "brief", "exec", now, now,
            completed_steps=("IMAGE_PACKAGE", "BLOG_PACKAGE", "VIDEO_PACKAGE"), pending_steps=("YOUTUBE_PACKAGE", "PUBLISHING")))
        self.states.save(VIDEO_PACKAGE_KIND, "content-a", "workspace-a", {"status": "COMPLETED", "artifact_ids": [video["artifact_id"], draft["artifact_id"]], "thumbnail_artifact_id": thumb["artifact_id"]})
    def test_connection_persists_only_reference_refresh_and_revoke(self):
        text = self.state_path.read_text(encoding="utf-8")
        self.assertNotIn("never-persist", text); self.assertIn("token_reference", text)
        restored = YouTubeConnectionRepository(JsonStateRepository(self.state_path)).get("workspace-a", self.connection.connection_id)
        self.assertEqual(self.connection.token_reference, restored.token_reference)
        refreshed = self.connections.refresh("workspace-a", self.connection.connection_id, token("rotated")); self.assertEqual(1, refreshed.revision)
        revoked = self.connections.disconnect("workspace-a", self.connection.connection_id); self.assertEqual("REVOKED", revoked.status); self.assertIsNone(revoked.token_reference)
    def test_fake_resumable_processing_thumbnail_publication_and_restart(self):
        result = self.service.publish("workspace-a", "content-a", self.connection.connection_id, "key-a")
        self.assertEqual("SUCCESS", result["status"]); self.assertEqual("private", result["data"]["privacy_status"]); self.assertEqual("succeeded", result["data"]["processing_status"])
        self.assertTrue(result["data"]["video_id"]); self.assertEqual("APPLIED", result["data"]["thumbnail_status"])
        self.assertEqual(1, len(self.history_repository.records)); self.assertNotIn("never-persist", repr(self.history_repository.records))
        project = self.projects.get("workspace-a", "content-a"); self.assertIn("YOUTUBE_PACKAGE", project.completed_steps); self.assertEqual(("PUBLISHING",), project.pending_steps)
        restored_states = JsonStateRepository(self.state_path); restored = restored_states.get(PUBLICATION_KIND, "content-a", "workspace-a"); self.assertEqual(result["data"]["video_id"], restored["video_id"])
        replay = self.service.publish("workspace-a", "content-a", self.connection.connection_id, "key-a"); self.assertTrue(replay["data"]["idempotent_replay"])
    def test_private_workspace_and_idempotency_are_enforced(self):
        self.assertIn("PRIVATE_REQUIRED", self.service.publish("workspace-a", "content-a", self.connection.connection_id, "key", "public")["error"])
        self.assertIn("CONTENT_PROJECT_NOT_FOUND", self.service.publish("workspace-b", "content-a", self.connection.connection_id, "key")["error"])
        self.service.publish("workspace-a", "content-a", self.connection.connection_id, "first")
        self.assertIn("IDEMPOTENCY_CONFLICT", self.service.publish("workspace-a", "content-a", self.connection.connection_id, "second")["error"])


if __name__ == "__main__": unittest.main()
