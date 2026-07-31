import json
from pathlib import Path
import tempfile
import unittest

from core.artifact_manager import ArtifactManager
from core.artifact_repository import FileArtifactRepository
from core.execution_history import ExecutionHistory
from core.execution_history_repository import InMemoryExecutionHistoryRepository
from core.music_planning import (
    MUSIC_PLAN_SCHEMA, MusicPlanningRequest, MusicPlanningResult,
    MusicPlanningService, SunoPackageFormatter, validate_music_plan,
)
from core.status import PipelineStatus
from core.structured_logging import InMemoryLogger
from providers.factory import ProviderFactory
from providers.text import FakeTextProvider, TextGenerationRequest, TextGenerationResult, TextProviderError
from main import run_music_plan


def fake_plan():
    result = FakeTextProvider().generate_text(TextGenerationRequest(
        "workspace-a", "request-a", "MUSIC_PLAN", "safe instruction",
        response_schema=MUSIC_PLAN_SCHEMA,
    ))
    return json.loads(result.output_text)


class ResultProvider:
    is_paid = False

    def __init__(self, value=None, error=None, usage=None):
        self.value = fake_plan() if value is None else value
        self.error = error
        self.usage = usage
        self.requests = []

    def generate_text(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        output = self.value if isinstance(self.value, str) else json.dumps(self.value, ensure_ascii=False)
        return TextGenerationResult("test-text", "test-model", output, self.usage)


class MusicPlanningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.storage = self.root / "storage"
        self.repository_file = self.root / "artifacts.json"
        self.artifacts = ArtifactManager(FileArtifactRepository(
            self.repository_file, self.storage
        ))
        self.history = ExecutionHistory(repository=InMemoryExecutionHistoryRepository())
        self.logger = InMemoryLogger()

    def tearDown(self):
        self.temp.cleanup()

    def request(self, workspace="workspace-a", request_id="request-a", **values):
        return MusicPlanningRequest(
            workspace, "private original music request", request_id=request_id, **values
        )

    def service(self, provider=None, history=None, logger=None, artifacts=None):
        return MusicPlanningService(
            self.storage, provider=provider or FakeTextProvider(),
            artifact_manager=artifacts or self.artifacts,
            execution_history=self.history if history is None else history,
            logger=self.logger if logger is None else logger,
        )

    def test_minimal_request_generates_structured_plan_and_waits_for_input(self):
        result = self.service().run(self.request())
        self.assertEqual(PipelineStatus.WAITING_FOR_INPUT, result["status"])
        self.assertEqual("다시 걷는 밤", result["data"]["primary_title"])
        self.assertEqual("fake-text", result["data"]["provider"])
        self.assertEqual(3, len(result["artifacts"]))
        self.assertNotIn("private original music request", repr(result))
        self.assertTrue(all("path" not in artifact for artifact in result["artifacts"]))

    def test_optional_inputs_are_passed_only_to_structured_provider_request(self):
        provider = ResultProvider()
        request = self.request(
            language="ko", genre_preferences=("ballad",),
            mood_preferences=("hopeful",), vocal_preferences=("solo",),
            reference_notes="Do not imitate named artists", duration_preference=180,
            metadata={"source": "test"},
        )
        result = self.service(provider).run(request)
        self.assertEqual(PipelineStatus.WAITING_FOR_INPUT, result["status"])
        generated_request = provider.requests[0]
        self.assertEqual(MUSIC_PLAN_SCHEMA, generated_request.response_schema)
        self.assertIn("ballad", generated_request.instruction)
        self.assertNotIn(generated_request.instruction, repr(result))

    def test_result_contract_title_bpm_prompts_variations_and_assumptions(self):
        value = fake_plan()
        plan = MusicPlanningResult.from_dict(value)
        self.assertIn(plan.primary_title, plan.title_candidates)
        self.assertTrue(40 <= plan.tempo_bpm <= 240)
        package = SunoPackageFormatter.build(plan)
        self.assertTrue(package["style_prompt"])
        self.assertTrue(package["lyrics_or_prompt"])
        self.assertTrue(package["exclude_styles"])
        self.assertIn(len(package["variations"]), (2, 3))
        self.assertTrue(plan.assumptions)

    def test_schema_rejects_invalid_title_bpm_prompt_and_extra_fields(self):
        mutations = []
        for field, value in (
            ("tempo_bpm", 500), ("suno_style_prompt", ""),
            ("title_candidates", ["other", "another"]),
        ):
            plan = fake_plan(); plan[field] = value; mutations.append(plan)
        extra = fake_plan(); extra["unexpected"] = "value"; mutations.append(extra)
        for value in mutations:
            with self.assertRaises(ValueError):
                validate_music_plan(value)

    def test_openai_provider_dependency_injection_path(self):
        output = json.dumps(fake_plan(), ensure_ascii=False)
        selection = ProviderFactory.text_from_environment({
            "AICOMPANY_TEXT_PROVIDER": "openai",
            "AICOMPANY_TEXT_MODEL": "test-model",
            "ALLOW_PAID_PROVIDER": "true",
            "OPENAI_API_KEY": "test-value",
        }, transport=lambda *_: {
            "id": "resp_safe", "status": "completed", "model": "test-model",
            "output": [{"type": "message", "content": [
                {"type": "output_text", "text": output}
            ]}],
            "usage": {"input_tokens": 2, "output_tokens": 3},
        })
        service = MusicPlanningService(
            self.storage, selection=selection, artifact_manager=self.artifacts,
            execution_history=self.history, logger=self.logger,
        )
        result = service.run(self.request())
        self.assertEqual(PipelineStatus.WAITING_FOR_INPUT, result["status"])
        self.assertEqual("openai", result["data"]["provider"])
        self.assertEqual(5, result["data"]["provider_usage"]["total_tokens"])
        self.assertIsNone(result["data"]["provider_usage"]["estimated_cost_usd"])

    def test_usage_full_partial_missing_and_none_cost(self):
        usages = (
            {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5, "estimated_cost_usd": None},
            {"output_tokens": 3}, None,
        )
        results = [
            self.service(ResultProvider(usage=usage)).run(
                self.request(request_id=f"request-{index}")
            ) for index, usage in enumerate(usages)
        ]
        self.assertEqual(5, results[0]["data"]["provider_usage"]["total_tokens"])
        self.assertEqual(3, results[1]["data"]["provider_usage"]["output_tokens"])
        self.assertIsNone(results[2]["data"]["provider_usage"])

    def test_artifact_metadata_workspace_isolation_and_restart(self):
        result = self.service().run(self.request())
        for safe in result["artifacts"]:
            artifact = self.artifacts.get(safe["artifact_id"], "workspace-a")
            self.assertEqual("fake-text", artifact["metadata"]["provider"])
            self.assertEqual("music-planning-v1", artifact["metadata"]["prompt_version"])
            self.assertIsNone(self.artifacts.get(safe["artifact_id"], "workspace-b"))
        restored = ArtifactManager(FileArtifactRepository(self.repository_file, self.storage))
        artifact = restored.get(result["artifacts"][0]["artifact_id"], "workspace-a")
        self.assertEqual("1.0", artifact["metadata"]["schema_version"])
        self.assertNotIn(str(self.storage), repr(artifact))

    def test_history_records_safe_waiting_summary(self):
        result = self.service().run(self.request())
        records = self.history.query(workspace_id="workspace-a")
        self.assertEqual(1, len(records))
        self.assertEqual(PipelineStatus.WAITING_FOR_INPUT, records[0]["status"])
        self.assertEqual(result["data"]["provider_usage"], records[0]["result"]["usage"])
        self.assertNotIn("private original music request", repr(records))

    def test_provider_failures_and_invalid_outputs_are_safe(self):
        cases = (
            ResultProvider(error=TimeoutError("raw secret prompt")),
            ResultProvider(error=TextProviderError("rate_limited", "openai", True)),
            ResultProvider(error=TextProviderError("authentication_failed", "openai")),
            ResultProvider(value="not-json"),
            ResultProvider(value=dict(fake_plan(), tempo_bpm=999)),
        )
        for index, provider in enumerate(cases):
            result = self.service(provider).run(self.request(request_id=f"failure-{index}"))
            self.assertEqual(PipelineStatus.FAILED, result["status"])
            self.assertNotIn("raw secret prompt", repr(result))
            self.assertNotIn("private original music request", repr(result))

    def test_invalid_request_paid_block_and_workspace_escape_are_safe(self):
        with self.assertRaises(ValueError):
            MusicPlanningRequest("workspace-a", "")
        with self.assertRaises(ValueError):
            MusicPlanningRequest("../other", "request")
        with self.assertRaises(ValueError):
            MusicPlanningRequest("workspace-a", "x" * 6001)
        with self.assertRaisesRegex(ValueError, "disabled"):
            ProviderFactory.text_from_environment({
                "AICOMPANY_TEXT_PROVIDER": "openai",
                "AICOMPANY_TEXT_MODEL": "test-model",
                "OPENAI_API_KEY": "test-value",
            })

    def test_artifact_and_history_logging_failures_are_isolated(self):
        class FailingArtifacts:
            def register_file(self, *_args, **_kwargs):
                raise OSError("private absolute path")

        class FailingHistory:
            def record_content_stage(self, *_args):
                raise OSError("private history")

        failed = self.service(artifacts=FailingArtifacts()).run(self.request())
        self.assertEqual(PipelineStatus.FAILED, failed["status"])
        self.assertNotIn("private absolute path", repr(failed))
        success = self.service(
            history=FailingHistory(), logger=InMemoryLogger(fail_writes=True)
        ).run(self.request(request_id="history-failure"))
        self.assertEqual(PipelineStatus.WAITING_FOR_INPUT, success["status"])

    def test_cli_composition_uses_fake_default_and_safe_summary(self):
        result = run_music_plan(
            "private CLI music request", workspace_id="workspace-cli",
            root=self.root / "cli", environment={},
        )
        self.assertEqual(PipelineStatus.WAITING_FOR_INPUT, result["status"])
        self.assertEqual("fake-text", result["data"]["provider"])
        self.assertNotIn("private CLI music request", repr(result))


if __name__ == "__main__":
    unittest.main()
