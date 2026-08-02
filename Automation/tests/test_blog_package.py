import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path

from core.artifact_manager import ArtifactManager
from core.artifact_repository import FileArtifactRepository
from core.blog_package import (
    BLOG_PACKAGE_KIND, BlogPackageOrchestrator, BlogPackageRequest,
    BlogPackageService,
)
from core.content_brief_orchestration import ContentProject, ContentProjectRepository
from core.execution_history import ExecutionHistory
from core.image_package import IMAGE_PACKAGE_KIND
from core.object_storage import ArtifactStorageAdapter, LocalStorageProvider
from core.persistence import JsonStateRepository
from core.status import PipelineStatus
from core.usage_engine import UsageEngine
from providers.factory import ProviderSelection
from providers.content_media import _deterministic_png
from providers.text import FakeTextProvider, TextGenerationResult


class MemoryHistoryRepository:
    def __init__(self): self.records = []
    def load(self): return list(self.records)
    def save(self, records): self.records = list(records)


class NoUsageProvider(FakeTextProvider):
    def generate_text(self, request):
        value = super().generate_text(request)
        return TextGenerationResult(value.provider, value.model, value.output_text, None)


class MaliciousProvider(FakeTextProvider):
    def generate_text(self, request):
        value = super().generate_text(request)
        payload = json.loads(value.output_text)
        payload["sections"][0]["body"] = '<script>alert(1)</script><img src=x onerror="steal()"> javascript: secret'
        return TextGenerationResult(value.provider, value.model, json.dumps(payload), value.usage)


class InvalidProvider(FakeTextProvider):
    def generate_text(self, request):
        return TextGenerationResult("fake-text", "fake", '{"title":"only"}', None)


class FailingHistory:
    def record_content_stage(self, *args): raise RuntimeError("private history")


class BlogPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state_file = self.root / "state.json"
        self.artifact_file = self.root / "artifacts.json"
        self.storage_root = self.root / "storage"
        self.history_repository = MemoryHistoryRepository()
        self._compose()
        self._ready_project("workspace-a", "content-a")

    def tearDown(self): self.temp.cleanup()

    def _compose(self, provider=None, history=None):
        self.states = JsonStateRepository(self.state_file)
        repository = FileArtifactRepository(self.artifact_file, self.storage_root)
        self.artifacts = ArtifactManager(repository, ArtifactStorageAdapter(LocalStorageProvider(self.storage_root), repository))
        self.projects = ContentProjectRepository(self.states)
        self.orchestrator = BlogPackageOrchestrator(
            self.root / "work", BlogPackageService(selection=ProviderSelection(provider or FakeTextProvider(), "fake-creative-v1", 5)),
            self.projects, self.states, self.artifacts,
            history if history is not None else ExecutionHistory(repository=self.history_repository),
            UsageEngine(self.states),
        )

    def _file(self, name, value, artifact_type, workspace, content_id, mission="music-a"):
        path = self.root / f"{workspace}-{content_id}-{name}"
        if isinstance(value, bytes): path.write_bytes(value)
        else: path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return self.artifacts.register_file(path, artifact_type, "test", workspace_id=workspace, mission_id=mission, task_id=content_id)

    def _ready_project(self, workspace, content_id):
        brief = self._file("brief.json", {
            "project_title": "다시 걷는 밤", "core_message": "상실 뒤의 회복",
            "content_goal": "음악 프로젝트 소개", "target_audience": "성인 음악 독자",
            "emotional_arc": ["상실", "회복"], "mood_keywords": ["warm", "hopeful"],
            "blog_direction": "사실에 근거한 에디토리얼", "blog_requirements": ["소개", "감상 포인트"],
            "seo_primary_keywords": ["회복 음악"], "seo_secondary_keywords": ["한국 발라드"],
            "assumptions": ["테스트 브리프"], "source_summary": "승인된 음악 계획과 오디오 metadata",
        }, "CONTENT_BRIEF", workspace, content_id)
        cover = self._file("cover.png", _deterministic_png(16, 16, 1), "CONTENT_COVER_IMAGE", workspace, content_id)
        inline = self._file("inline.png", _deterministic_png(16, 10, 2), "BLOG_INLINE_IMAGE", workspace, content_id)
        manifest = self._file("manifest.json", {
            "schema_version": "1.0", "images": [
                {"artifact_id": cover["artifact_id"], "purpose": "COVER"},
                {"artifact_id": inline["artifact_id"], "purpose": "BLOG_INLINE"},
            ]}, "IMAGE_PACKAGE_MANIFEST", workspace, content_id)
        now = "2026-08-02T00:00:00+00:00"
        project = ContentProject(
            content_id, workspace, "music-a", "plan-a", "audio-a",
            PipelineStatus.READY_FOR_CONTENT, 2, brief["artifact_id"], "execution-a",
            now, now, completed_steps=("MUSIC_PLAN", "AUDIO_INPUT", "CONTENT_BRIEF", "IMAGE_PACKAGE"),
            pending_steps=("BLOG_PACKAGE", "VIDEO_PACKAGE", "YOUTUBE_PACKAGE", "PUBLISHING"),
        )
        self.projects.save(project)
        self.states.save(IMAGE_PACKAGE_KIND, content_id, workspace, {
            "content_project_id": content_id, "workspace_id": workspace,
            "status": "COMPLETED", "manifest_artifact_id": manifest["artifact_id"],
        })
        return project

    def test_generates_structured_package_five_artifacts_state_usage_history(self):
        result = self.orchestrator.run(BlogPackageRequest("workspace-a", "content-a", idempotency_key="same"))
        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertEqual(5, len(result["artifacts"]))
        self.assertEqual(2, result["data"]["image_count"])
        self.assertEqual("COMPLETED", self.states.get(BLOG_PACKAGE_KIND, "content-a", "workspace-a")["status"])
        project = self.projects.get("workspace-a", "content-a")
        self.assertIn("BLOG_PACKAGE", project.completed_steps)
        self.assertEqual(("VIDEO_PACKAGE", "YOUTUBE_PACKAGE", "PUBLISHING"), project.pending_steps)
        self.assertEqual(1, len(self.history_repository.records))
        self.assertIsNotNone(UsageEngine(self.states).get("blog-package-content-a", "workspace-a"))
        self.assertNotIn(str(self.root), repr(result))

    def test_package_contract_markdown_html_seo_and_image_manifest(self):
        result = self.orchestrator.run(BlogPackageRequest("workspace-a", "content-a"))
        by_type = {item["artifact_type"]: item for item in result["artifacts"]}
        package = json.loads(self.artifacts.storage_adapter.read("workspace-a", by_type["BLOG_PACKAGE"]["artifact_id"]))
        self.assertGreaterEqual(len(package["article_sections"]), 3)
        self.assertEqual(2, len(package["image_placements"]))
        self.assertTrue(package["slug"])
        markdown = self.artifacts.storage_adapter.read("workspace-a", by_type["BLOG_ARTICLE_MARKDOWN"]["artifact_id"]).decode()
        html = self.artifacts.storage_adapter.read("workspace-a", by_type["BLOG_ARTICLE_HTML"]["artifact_id"]).decode()
        seo = json.loads(self.artifacts.storage_adapter.read("workspace-a", by_type["BLOG_SEO_METADATA"]["artifact_id"]))
        images = json.loads(self.artifacts.storage_adapter.read("workspace-a", by_type["BLOG_IMAGE_MANIFEST"]["artifact_id"]))
        self.assertIn("artifact:", markdown)
        self.assertIn("<article>", html)
        self.assertNotIn("<script", html.lower())
        self.assertTrue(seo["primary_keyword"])
        self.assertEqual(2, len(images["images"]))

    def test_html_escapes_active_content(self):
        self._compose(MaliciousProvider())
        result = self.orchestrator.run(BlogPackageRequest("workspace-a", "content-a"))
        html_artifact = next(item for item in result["artifacts"] if item["artifact_type"] == "BLOG_ARTICLE_HTML")
        html = self.artifacts.storage_adapter.read("workspace-a", html_artifact["artifact_id"]).decode()
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("<img src=x", html.lower())
        self.assertNotIn("javascript:", html.lower())

    def test_idempotent_replay_restart_and_concurrency(self):
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.orchestrator.run(BlogPackageRequest("workspace-a", "content-a", idempotency_key="key-a")))) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual([PipelineStatus.SUCCESS, PipelineStatus.SUCCESS], [item["status"] for item in results])
        self.assertTrue(any(item["data"]["idempotent_replay"] for item in results))
        before = len(self.artifacts.list("workspace-a"))
        self._compose()
        replay = self.orchestrator.run(BlogPackageRequest("workspace-a", "content-a", idempotency_key="key-a"))
        self.assertTrue(replay["data"]["idempotent_replay"])
        self.assertEqual(before, len(self.artifacts.list("workspace-a")))

    def test_workspace_missing_image_and_incomplete_image_are_rejected(self):
        foreign = self.orchestrator.run(BlogPackageRequest("workspace-b", "content-a"))
        self.assertIn("WORKSPACE_MISMATCH", foreign["error"])
        self.artifacts.archive(next(item["artifact_id"] for item in self.artifacts.list("workspace-a") if item["artifact_type"] == "BLOG_INLINE_IMAGE"), "workspace-a")
        missing = self.orchestrator.run(BlogPackageRequest("workspace-a", "content-a"))
        self.assertIn("IMAGE_ARTIFACT_UNAVAILABLE", missing["error"])
        self._ready_project("workspace-a", "content-incomplete")
        project = self.projects.get("workspace-a", "content-incomplete")
        self.projects.save(replace(project, completed_steps=tuple(step for step in project.completed_steps if step != "IMAGE_PACKAGE"), revision=3), expected_revision=2)
        incomplete = self.orchestrator.run(BlogPackageRequest("workspace-a", "content-incomplete"))
        self.assertIn("IMAGE_PACKAGE_NOT_COMPLETED", incomplete["error"])

    def test_schema_failure_is_safe_and_retryable(self):
        self._compose(InvalidProvider())
        failed = self.orchestrator.run(BlogPackageRequest("workspace-a", "content-a"))
        self.assertIn("SCHEMA_VALIDATION_FAILED", failed["error"])
        self.assertNotIn("only", repr(failed))
        self._compose()
        recovered = self.orchestrator.run(BlogPackageRequest("workspace-a", "content-a"))
        self.assertEqual(PipelineStatus.SUCCESS, recovered["status"])

    def test_final_state_failure_discards_partial_package_and_retry_recovers(self):
        baseline = len(self.artifacts.list("workspace-a"))
        original_save = self.states.save
        failed_once = {"value": False}
        def fail_completed(kind, entity_id, workspace_id, value):
            if kind == BLOG_PACKAGE_KIND and value.get("status") == "COMPLETED" and not failed_once["value"]:
                failed_once["value"] = True
                raise OSError("private state error")
            return original_save(kind, entity_id, workspace_id, value)
        self.states.save = fail_completed
        failed = self.orchestrator.run(BlogPackageRequest("workspace-a", "content-a"))
        self.assertIn("BLOG_STATE_SAVE_FAILED", failed["error"])
        self.assertEqual(baseline, len(self.artifacts.list("workspace-a")))
        recovered = self.orchestrator.run(BlogPackageRequest("workspace-a", "content-a"))
        self.assertEqual(PipelineStatus.SUCCESS, recovered["status"])

    def test_missing_usage_and_history_failure_do_not_change_success(self):
        self._compose(NoUsageProvider(), FailingHistory())
        result = self.orchestrator.run(BlogPackageRequest("workspace-a", "content-a"))
        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertIsNone(result["data"]["provider_usage"])

    def test_invalid_input_and_idempotency_conflict(self):
        invalid = self.orchestrator.run(BlogPackageRequest("../escape", "content-a"))
        self.assertEqual(PipelineStatus.FAILED, invalid["status"])
        self.orchestrator.run(BlogPackageRequest("workspace-a", "content-a", idempotency_key="first"))
        replay = self.orchestrator.run(BlogPackageRequest("workspace-a", "content-a", idempotency_key="different"))
        self.assertTrue(replay["data"]["idempotent_replay"])

    def test_cli_composition_fake_smoke(self):
        from main import run_blog_package
        cli_root = self.root / "cli"
        state_root = cli_root / "state"
        storage = cli_root / "artifacts"
        states = JsonStateRepository(state_root / "music-project-state.json")
        repository = FileArtifactRepository(state_root / "artifact-metadata.json", storage)
        artifacts = ArtifactManager(repository, ArtifactStorageAdapter(LocalStorageProvider(storage), repository))
        projects = ContentProjectRepository(states)
        def save(name, value, kind):
            path = self.root / ("cli-" + name)
            if isinstance(value, bytes): path.write_bytes(value)
            else: path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            return artifacts.register_file(path, kind, "test", workspace_id="workspace-cli", mission_id="music-cli", task_id="content-cli")
        brief = save("brief.json", {
            "project_title": "로컬 프로젝트", "core_message": "회복", "content_goal": "소개",
            "target_audience": "독자", "emotional_arc": ["회복"], "mood_keywords": ["hopeful"],
            "blog_direction": "안전한 소개", "blog_requirements": ["본문"],
            "seo_primary_keywords": ["음악"], "seo_secondary_keywords": ["창작"],
            "assumptions": ["테스트"], "source_summary": "승인된 자료",
        }, "CONTENT_BRIEF")
        cover = save("cover.png", _deterministic_png(16, 16, 1), "CONTENT_COVER_IMAGE")
        inline = save("inline.png", _deterministic_png(16, 10, 2), "BLOG_INLINE_IMAGE")
        manifest = save("manifest.json", {"images": [
            {"artifact_id": cover["artifact_id"], "purpose": "COVER"},
            {"artifact_id": inline["artifact_id"], "purpose": "BLOG_INLINE"},
        ]}, "IMAGE_PACKAGE_MANIFEST")
        now = "2026-08-02T00:00:00+00:00"
        projects.save(ContentProject(
            "content-cli", "workspace-cli", "music-cli", "plan-cli", "audio-cli",
            PipelineStatus.READY_FOR_CONTENT, 2, brief["artifact_id"], "execution-cli",
            now, now, completed_steps=("MUSIC_PLAN", "AUDIO_INPUT", "CONTENT_BRIEF", "IMAGE_PACKAGE"),
            pending_steps=("BLOG_PACKAGE", "VIDEO_PACKAGE", "YOUTUBE_PACKAGE", "PUBLISHING"),
        ))
        states.save(IMAGE_PACKAGE_KIND, "content-cli", "workspace-cli", {
            "content_project_id": "content-cli", "workspace_id": "workspace-cli",
            "status": "COMPLETED", "manifest_artifact_id": manifest["artifact_id"],
        })
        result = run_blog_package(
            "workspace-cli", "content-cli", root=cli_root,
            environment={"ALLOW_PAID_PROVIDER": "false", "AICOMPANY_TEXT_PROVIDER": "fake"},
            idempotency_key="cli",
        )
        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertNotIn(str(self.root), repr(result))


if __name__ == "__main__":
    unittest.main()
