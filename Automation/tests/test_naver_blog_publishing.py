import json
import tempfile
import unittest
from pathlib import Path

from core.artifact_manager import ArtifactManager
from core.artifact_repository import FileArtifactRepository
from core.blog_package import BLOG_PACKAGE_KIND
from core.execution_history import ExecutionHistory
from core.naver_blog_publishing import NAVER_PUBLICATION_KIND, NaverBlogPublishingAssistant, NaverPublishingRequest
from core.object_storage import ArtifactStorageAdapter, LocalStorageProvider
from core.persistence import JsonStateRepository
from core.usage_engine import UsageEngine
from providers.factory import ProviderFactory
from providers.naver_blog import FakeNaverBlogBrowser


class NaverPublishingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.state_path = self.root / "state.json"; self.states = JsonStateRepository(self.state_path)
        repository = FileArtifactRepository(self.root / "artifacts.json", self.root / "storage")
        self.artifacts = ArtifactManager(repository, ArtifactStorageAdapter(LocalStorageProvider(self.root / "storage"), repository))
        self._package("workspace-a", "content-a")
        class HistoryRepo:
            def __init__(repo): repo.records = []
            def load(repo): return list(repo.records)
            def save(repo, records): repo.records = list(records)
        self.history_repo = HistoryRepo()
    def tearDown(self): self.temp.cleanup()

    def _file(self, name, content, kind, workspace, project):
        path = self.root / (workspace + name); path.write_bytes(content)
        return self.artifacts.register_file(path, kind, "test", workspace_id=workspace, mission_id="music-a", task_id=project)
    def _package(self, workspace, project):
        image = self._file("image.png", b"\x89PNG\r\n\x1a\n" + b"x" * 50, "BLOG_INLINE_IMAGE", workspace, project)
        package = self._file("package.json", json.dumps({"blog_package_id": "blog-" + project, "title": "Safe title", "tags": ["one", "two"]}).encode(), "BLOG_PACKAGE", workspace, project)
        article = self._file("article.md", b"Safe body", "BLOG_ARTICLE_MARKDOWN", workspace, project)
        manifest = self._file("manifest.json", json.dumps({"images": [{"artifact_id": image["artifact_id"]}]}).encode(), "BLOG_IMAGE_MANIFEST", workspace, project)
        self.states.save(BLOG_PACKAGE_KIND, project, workspace, {"status": "COMPLETED", "artifact_ids": [package["artifact_id"], article["artifact_id"], manifest["artifact_id"]]})

    def test_fake_prepare_never_clicks_publish(self):
        browser = FakeNaverBlogBrowser(); assistant = NaverBlogPublishingAssistant(browser, self.states, self.artifacts, work_root=self.root / "work")
        result = assistant.run(NaverPublishingRequest("workspace-a", "content-a"))
        self.assertEqual("USER_CONFIRM_REQUIRED", result["status"])
        self.assertIn("OPEN_PUBLISH_SETTINGS", browser.actions)
        self.assertNotIn("CLICK_FINAL_PUBLISH", browser.actions)
        self.assertEqual("USER_CONFIRM_REQUIRED", self.states.get(NAVER_PUBLICATION_KIND, "content-a", "workspace-a")["status"])

    def test_manual_publication_records_url_history_and_restart(self):
        browser = FakeNaverBlogBrowser("https://blog.naver.com/safe_blog/123")
        assistant = NaverBlogPublishingAssistant(browser, self.states, self.artifacts,
            ExecutionHistory(repository=self.history_repo), self.root / "work", UsageEngine(self.states))
        result = assistant.run(NaverPublishingRequest("workspace-a", "content-a", wait_for_publication=True))
        self.assertEqual("SUCCESS", result["status"]); self.assertEqual("PUBLISHED", result["data"]["status"])
        self.assertEqual(1, len(self.history_repo.records)); self.assertNotIn("cookie", repr(result).lower())
        restarted = JsonStateRepository(self.state_path).get(NAVER_PUBLICATION_KIND, "content-a", "workspace-a")
        self.assertEqual("https://blog.naver.com/safe_blog/123", restarted["published_url"])
        receipt = self.artifacts.get(restarted["receipt_artifact_id"], "workspace-a")
        self.assertEqual("NAVER_PUBLICATION_RECEIPT", receipt["artifact_type"])
        self.assertTrue(self.states.list("usage", "workspace-a"))

    def test_workspace_bad_url_and_user_action_fail_safely(self):
        assistant = NaverBlogPublishingAssistant(FakeNaverBlogBrowser(), self.states, self.artifacts, work_root=self.root / "work")
        self.assertIn("BLOG_PACKAGE_INCOMPLETE", assistant.run(NaverPublishingRequest("workspace-b", "content-a"))["error"])
        bad = NaverBlogPublishingAssistant(FakeNaverBlogBrowser("https://evil.example/post"), self.states, self.artifacts, work_root=self.root / "work2")
        result = bad.run(NaverPublishingRequest("workspace-a", "content-a", wait_for_publication=True))
        self.assertIn("PUBLICATION_NOT_CONFIRMED", result["error"])

    def test_factory_is_fake_by_default_and_real_is_explicit(self):
        self.assertIsInstance(ProviderFactory.naver_blog_from_environment({}).provider, FakeNaverBlogBrowser)
        selection = ProviderFactory.naver_blog_from_environment({"AICOMPANY_NAVER_BLOG_PROVIDER": "playwright", "AICOMPANY_NAVER_PROFILE_DIR": str(self.root / "profile")})
        self.assertEqual("naver-smart-editor", selection.default_model)


if __name__ == "__main__": unittest.main()
