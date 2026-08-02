import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from core.artifact_manager import ArtifactManager
from core.artifact_repository import FileArtifactRepository
from core.completed_audio_intake import (
    AudioInputLocator, AudioInputValidator, AudioProbeResult,
    MusicProjectAudioLinkService,
)
from core.content_brief_orchestration import (
    CONTENT_BRIEF_SCHEMA, CONTENT_PROJECT_KIND, ContentBriefRequest,
    ContentBriefService, ContentProjectOrchestrator, ContentProjectRepository,
    validate_content_brief,
)
from core.execution_history import ExecutionHistory
from core.execution_history_repository import JsonFileExecutionHistoryRepository
from core.music_planning import MusicPlanningRequest, MusicPlanningService
from core.object_storage import ArtifactStorageAdapter, LocalStorageProvider
from core.persistence import JsonStateRepository, StateRepository
from core.status import PipelineStatus
from core.usage_engine import UsageEngine
from main import run_content_brief, run_music_import, run_music_plan
from providers.factory import ProviderFactory
from providers.text import FakeTextProvider, TextGenerationRequest, TextGenerationResult, TextProviderError


MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x08safe-audio"


class FakeProbe:
    def probe(self, _path):
        return AudioProbeResult("mp3", 15.0, "mp3", 44100, 2)


def fake_brief():
    result = FakeTextProvider().generate_text(TextGenerationRequest(
        "workspace-a", "music-a", "CONTENT_BRIEF", "safe",
        response_schema=CONTENT_BRIEF_SCHEMA,
    ))
    return json.loads(result.output_text)


class BriefProvider:
    is_paid = False

    def __init__(self, value=None, error=None, usage=None, delay=0):
        self.value = fake_brief() if value is None else value
        self.error = error
        self.usage = usage
        self.delay = delay
        self.calls = 0
        self.requests = []

    def generate_text(self, request):
        self.calls += 1
        self.requests.append(request)
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        output = self.value if isinstance(self.value, str) else json.dumps(self.value, ensure_ascii=False)
        return TextGenerationResult("brief-test", "brief-model", output, self.usage)


class ContentBriefOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.storage = self.root / "artifacts"
        self.state_root = self.root / "state"
        self.state_file = self.state_root / "project-state.json"
        self.artifact_file = self.state_root / "artifacts.json"
        self.history_file = self.state_root / "history.json"
        self.state = JsonStateRepository(self.state_file)
        self.repository = FileArtifactRepository(self.artifact_file, self.storage)
        self.history = ExecutionHistory(repository=JsonFileExecutionHistoryRepository(self.history_file))
        planning_artifacts = ArtifactManager(self.repository)
        self.music_id = "music-a"
        planned = MusicPlanningService(
            self.storage, provider=FakeTextProvider(),
            artifact_manager=planning_artifacts, execution_history=self.history,
        ).run(MusicPlanningRequest(
            "workspace-a", "private original request", request_id=self.music_id
        ))
        self.assertEqual(PipelineStatus.WAITING_FOR_INPUT, planned["status"])
        repository = FileArtifactRepository(self.artifact_file, self.storage)
        self.artifacts = ArtifactManager(
            repository,
            storage_adapter=ArtifactStorageAdapter(LocalStorageProvider(self.storage), repository),
        )
        locator = AudioInputLocator(self.root / "inputs")
        audio = locator.workspace_directory("workspace-a", create=True) / "song.mp3"
        audio.write_bytes(MP3)
        imported = MusicProjectAudioLinkService(
            locator, AudioInputValidator(FakeProbe()), self.artifacts,
            self.state, self.history,
        ).import_audio("workspace-a", self.music_id, "song")
        self.assertEqual(PipelineStatus.INPUT_READY, imported["status"])
        self.audio_id = imported["data"]["audio_artifact_id"]

    def tearDown(self):
        self.temp.cleanup()

    def orchestrator(self, provider=None, state=None, artifacts=None,
                     history=None, usage=None):
        state = state or self.state
        return ContentProjectOrchestrator(
            self.root / "work", ContentBriefService(provider=provider or FakeTextProvider()),
            ContentProjectRepository(state), state,
            artifacts or self.artifacts,
            self.history if history is None else history,
            UsageEngine(state) if usage is None else usage,
        )

    def request(self, workspace="workspace-a", **values):
        return ContentBriefRequest(
            workspace, self.music_id, idempotency_key="stable-key", **values
        )

    def read_artifact(self, artifact_id):
        return json.loads(self.artifacts.storage_adapter.read(
            "workspace-a", artifact_id
        ).decode("utf-8"))

    def test_input_ready_creates_structured_project_brief_and_three_artifacts(self):
        result = self.orchestrator().run(self.request())
        self.assertEqual(PipelineStatus.READY_FOR_CONTENT, result["status"])
        self.assertEqual(3, len(result["artifacts"]))
        self.assertEqual(set(result["data"]["pending_steps"]), {
            "IMAGE_PACKAGE", "BLOG_PACKAGE", "VIDEO_PACKAGE",
            "YOUTUBE_PACKAGE", "PUBLISHING",
        })
        project = self.orchestrator().get_project(
            "workspace-a", result["data"]["content_project_id"]
        )
        self.assertEqual(self.audio_id, project.source_audio_artifact_id)
        self.assertEqual(PipelineStatus.READY_FOR_CONTENT, project.status)
        self.assertEqual(1, project.revision)

    def test_brief_contains_all_cross_channel_directions_and_safe_sources(self):
        result = self.orchestrator().run(self.request(
            content_goal="private supplemental goal",
            target_audience="adult listeners",
        ))
        brief = self.read_artifact(result["data"]["brief_artifact_id"])
        for key in (
            "visual_concept", "image_requirements", "blog_direction",
            "blog_requirements", "video_direction", "video_requirements",
            "youtube_direction", "youtube_requirements",
            "seo_primary_keywords", "prohibited_elements", "target_audience",
            "core_message",
        ):
            self.assertTrue(brief[key])
        self.assertNotIn("private supplemental goal", repr(result))
        self.assertNotIn("private original request", repr(result))

    def test_execution_plan_reuses_workflow_definition_and_has_pending_dependencies(self):
        result = self.orchestrator().run(self.request())
        plan = self.read_artifact(result["data"]["execution_plan_artifact_id"])
        self.assertEqual(5, len(plan["steps"]))
        self.assertTrue(all(step["status"] == "PENDING" for step in plan["steps"]))
        by_id = {step["step_id"]: step for step in plan["steps"]}
        self.assertEqual(["IMAGE_PACKAGE"], by_id["VIDEO_PACKAGE"]["dependencies"])
        self.assertEqual(
            ["BLOG_PACKAGE", "YOUTUBE_PACKAGE"],
            by_id["PUBLISHING"]["dependencies"],
        )
        self.assertIn(result["data"]["brief_artifact_id"], by_id["IMAGE_PACKAGE"]["required_inputs"])

    def test_usage_full_partial_missing_and_none_cost(self):
        usages = (
            {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5, "estimated_cost_usd": None},
            {"output_tokens": 3}, None,
        )
        for index, usage in enumerate(usages):
            if index:
                self.tearDown(); self.setUp()
            result = self.orchestrator(BriefProvider(usage=usage)).run(self.request())
            actual = result["data"]["provider_usage"]
            if usage is None:
                self.assertIsNone(actual)
            else:
                self.assertEqual(usage.get("output_tokens"), actual.get("output_tokens"))
                if "estimated_cost_usd" in usage:
                    self.assertIsNone(actual["estimated_cost_usd"])

    def test_history_and_usage_are_recorded_without_prompt_or_paths(self):
        provider = BriefProvider(usage={"input_tokens": 2, "output_tokens": 3})
        usage = UsageEngine(self.state)
        result = self.orchestrator(provider, usage=usage).run(self.request(
            additional_notes="private content notes"
        ))
        records = self.history.query(workspace_id="workspace-a")
        self.assertTrue(any(item["task_type"] == "CONTENT_BRIEF" for item in records))
        usage_records = usage.query("workspace-a", provider="brief-test")
        self.assertEqual(1, len(usage_records))
        combined = repr(result) + repr(records) + repr(usage_records)
        self.assertNotIn("private content notes", combined)
        self.assertNotIn(str(self.root), combined)

    def test_workspace_mismatch_not_ready_and_missing_audio_are_blocked(self):
        mismatch = self.orchestrator().run(self.request("workspace-b"))
        self.assertEqual("ContentBriefError: WORKSPACE_MISMATCH", mismatch["error"])

        link = self.state.get("music_audio_link", self.music_id, "workspace-a")
        link["status"] = PipelineStatus.WAITING_FOR_INPUT
        self.state.save("music_audio_link", self.music_id, "workspace-a", link)
        not_ready = self.orchestrator().run(self.request())
        self.assertEqual("ContentBriefError: PROJECT_NOT_INPUT_READY", not_ready["error"])

        link["status"] = PipelineStatus.INPUT_READY
        self.state.save("music_audio_link", self.music_id, "workspace-a", link)
        artifact = self.artifacts.get(self.audio_id, "workspace-a")
        self.artifacts.storage_adapter.storage.delete(artifact["internal_ref"])
        missing = self.orchestrator().run(self.request())
        self.assertEqual("ContentBriefError: AUDIO_ARTIFACT_MISSING", missing["error"])

    def test_idempotent_replay_returns_one_project_without_provider_call(self):
        provider = BriefProvider()
        orchestrator = self.orchestrator(provider)
        first = orchestrator.run(self.request())
        second = orchestrator.run(self.request())
        self.assertEqual(first["data"]["content_project_id"], second["data"]["content_project_id"])
        self.assertTrue(second["data"]["idempotent_replay"])
        self.assertEqual(1, provider.calls)

    def test_concurrent_requests_converge_to_one_project(self):
        provider = BriefProvider(delay=0.05)
        orchestrator = self.orchestrator(provider)
        results = []
        threads = [threading.Thread(target=lambda: results.append(orchestrator.run(self.request()))) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(2, len(results))
        self.assertTrue(all(item["status"] == PipelineStatus.READY_FOR_CONTENT for item in results))
        self.assertEqual(1, provider.calls)
        self.assertEqual(1, len({item["data"]["content_project_id"] for item in results}))

    def test_restart_recovers_content_project_and_artifacts(self):
        result = self.orchestrator().run(self.request())
        restarted_state = JsonStateRepository(self.state_file)
        repository = FileArtifactRepository(self.artifact_file, self.storage)
        restarted_artifacts = ArtifactManager(
            repository,
            storage_adapter=ArtifactStorageAdapter(LocalStorageProvider(self.storage), repository),
        )
        restarted = self.orchestrator(state=restarted_state, artifacts=restarted_artifacts)
        project = restarted.get_project("workspace-a", result["data"]["content_project_id"])
        self.assertEqual(PipelineStatus.READY_FOR_CONTENT, project.status)
        self.assertIsNotNone(restarted_artifacts.get(project.brief_artifact_id, "workspace-a"))

    def test_artifact_failure_marks_project_failed_and_can_retry(self):
        class OnceFailingArtifacts(ArtifactManager):
            def __init__(self, repository, adapter):
                super().__init__(repository, adapter)
                self.failed = False

            def register_file(self, path, artifact_type, *args, **kwargs):
                if artifact_type == "CONTENT_BRIEF" and not self.failed:
                    self.failed = True
                    raise OSError("private artifact path")
                return super().register_file(path, artifact_type, *args, **kwargs)

        repository = FileArtifactRepository(self.artifact_file, self.storage)
        manager = OnceFailingArtifacts(
            repository, ArtifactStorageAdapter(LocalStorageProvider(self.storage), repository)
        )
        orchestrator = self.orchestrator(artifacts=manager)
        failed = orchestrator.run(self.request())
        self.assertEqual("ContentBriefError: ARTIFACT_SAVE_FAILED", failed["error"])
        project = orchestrator.projects.get_by_music_project("workspace-a", self.music_id)
        self.assertEqual(PipelineStatus.FAILED, project.status)
        retried = orchestrator.run(self.request())
        self.assertEqual(PipelineStatus.READY_FOR_CONTENT, retried["status"])
        self.assertNotIn("private artifact path", repr(failed))

    def test_final_project_save_failure_discards_generated_artifacts(self):
        class FailingReadyState(StateRepository):
            def __init__(self, delegate): self.delegate = delegate
            def get(self, *args): return self.delegate.get(*args)
            def list(self, *args): return self.delegate.list(*args)
            def save(self, kind, record_id, workspace_id, payload):
                if kind == CONTENT_PROJECT_KIND and payload.get("status") == PipelineStatus.READY_FOR_CONTENT:
                    raise OSError("private state path")
                return self.delegate.save(kind, record_id, workspace_id, payload)

        state = FailingReadyState(self.state)
        result = self.orchestrator(state=state).run(self.request())
        self.assertEqual("ContentBriefError: PROJECT_SAVE_FAILED", result["error"])
        self.assertFalse(any(
            item.get("artifact_type", "").startswith("CONTENT_")
            for item in self.artifacts.list("workspace-a")
        ))
        self.assertNotIn("private state path", repr(result))

    def test_history_and_usage_failures_do_not_change_ready_result(self):
        class FailingHistory:
            def record_content_stage(self, *_args): raise OSError("private history")
        class FailingUsage:
            def record_safe(self, *_args, **_kwargs): return {"ok": False}
        result = self.orchestrator(
            BriefProvider(usage={"input_tokens": 1}),
            history=FailingHistory(), usage=FailingUsage(),
        ).run(self.request())
        self.assertEqual(PipelineStatus.READY_FOR_CONTENT, result["status"])

    def test_provider_and_schema_failures_are_safe_and_retryable(self):
        cases = (
            BriefProvider(error=TimeoutError("private timeout")),
            BriefProvider(error=TextProviderError("authentication_failed", "openai")),
            BriefProvider(error=TextProviderError("rate_limited", "openai", True)),
            BriefProvider(value="not-json"),
            BriefProvider(value=dict(fake_brief(), visual_concept="")),
        )
        for index, provider in enumerate(cases):
            if index:
                self.tearDown(); self.setUp()
            result = self.orchestrator(provider).run(self.request())
            self.assertEqual(PipelineStatus.FAILED, result["status"])
            self.assertNotIn("private timeout", repr(result))
            project = self.orchestrator().projects.get_by_music_project("workspace-a", self.music_id)
            self.assertEqual(PipelineStatus.FAILED, project.status)

    def test_openai_dependency_injection_uses_common_schema_path(self):
        output = json.dumps(fake_brief(), ensure_ascii=False)
        selection = ProviderFactory.text_from_environment({
            "AICOMPANY_TEXT_PROVIDER": "openai", "AICOMPANY_TEXT_MODEL": "test-model",
            "ALLOW_PAID_PROVIDER": "true", "OPENAI_API_KEY": "test-value",
        }, transport=lambda *_: {
            "id": "resp_safe", "status": "completed", "model": "test-model",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": output}]}],
            "usage": {"input_tokens": 2, "output_tokens": 3},
        })
        orchestrator = ContentProjectOrchestrator(
            self.root / "work", ContentBriefService(selection=selection),
            ContentProjectRepository(self.state), self.state, self.artifacts,
            self.history, UsageEngine(self.state),
        )
        result = orchestrator.run(self.request())
        self.assertEqual(PipelineStatus.READY_FOR_CONTENT, result["status"])
        self.assertEqual("openai", result["data"]["provider"])

    def test_cli_runs_existing_project_without_printing_body(self):
        cli = self.root / "cli"
        planned = run_music_plan("private cli plan", "workspace-cli", cli, environment={})
        music_id = planned["data"]["mission_id"]
        locator = AudioInputLocator(cli / "inputs")
        (locator.workspace_directory("workspace-cli", create=True) / "song.mp3").write_bytes(MP3)
        imported = run_music_import("workspace-cli", music_id, "song", cli, probe=FakeProbe())
        self.assertEqual(PipelineStatus.INPUT_READY, imported["status"])
        result = run_content_brief("workspace-cli", music_id, cli, environment={})
        self.assertEqual(PipelineStatus.READY_FOR_CONTENT, result["status"])
        self.assertNotIn("private cli plan", repr(result))
        self.assertNotIn(str(cli), repr(result))

    def test_brief_validator_rejects_missing_extra_empty_and_wrong_steps(self):
        values = []
        missing = fake_brief(); missing.pop("visual_concept"); values.append(missing)
        extra = fake_brief(); extra["extra"] = "value"; values.append(extra)
        empty = fake_brief(); empty["blog_direction"] = ""; values.append(empty)
        steps = fake_brief(); steps["next_steps"] = ["IMAGE_PACKAGE"]; values.append(steps)
        for value in values:
            with self.assertRaises(ValueError): validate_content_brief(value)


if __name__ == "__main__":
    unittest.main()
