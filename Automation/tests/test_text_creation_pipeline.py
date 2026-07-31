import json
import tempfile
import unittest
from pathlib import Path

from core.artifact_manager import ArtifactManager
from core.artifact_repository import FileArtifactRepository
from core.execution_history import ExecutionHistory
from core.execution_history_repository import InMemoryExecutionHistoryRepository
from core.status import PipelineStatus
from core.structured_logging import InMemoryLogger
from core.task import Task
from core.text_creation_pipeline import TextCreationPipeline
from providers.text import (
    FakeTextProvider, TextGenerationRequest, TextGenerationResult,
)
from providers.factory import ProviderFactory


class PartialProvider(FakeTextProvider):
    def generate_text(self, request):
        result = super().generate_text(request)
        return TextGenerationResult(
            result.provider, result.model, result.output_text,
            {"input_tokens": 2},
        )


class MissingUsageProvider(FakeTextProvider):
    def generate_text(self, request):
        result = super().generate_text(request)
        return TextGenerationResult(
            result.provider, result.model, result.output_text, None
        )


class BadProvider:
    is_paid = False

    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error

    def generate_text(self, _request):
        if self.error:
            raise self.error
        return self.value


class TextCreationPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.storage = self.root / "storage"
        self.repository_file = self.root / "artifacts.json"
        self.artifacts = ArtifactManager(FileArtifactRepository(
            self.repository_file, self.storage
        ))
        self.history = ExecutionHistory(
            repository=InMemoryExecutionHistoryRepository()
        )
        self.logger = InMemoryLogger()

    def tearDown(self):
        self.temp.cleanup()

    def task(self, task_type, workspace="workspace-a"):
        task = Task(
            "private creative request",
            {"mission_id": "mission-a"},
            workspace_id=workspace,
        )
        task.task_type = task_type
        return task

    def pipeline(self, provider=None, logger=None, history=None):
        return TextCreationPipeline(
            self.storage,
            provider=provider or FakeTextProvider(),
            artifact_manager=self.artifacts,
            execution_history=self.history if history is None else history,
            logger=self.logger if logger is None else logger,
        )

    def test_all_creative_types_generate_safe_artifacts(self):
        for task_type in (
            "LYRICS", "CONTENT_PLAN", "VIDEO_SCRIPT", "TITLE_DESCRIPTION"
        ):
            result = self.pipeline().run(self.task(task_type))
            self.assertEqual(PipelineStatus.SUCCESS, result["status"])
            self.assertEqual(task_type.lower() + ".json", result["artifacts"][0]["filename"])
            self.assertNotIn("path", result["artifacts"][0])
            self.assertNotIn("private creative request", repr(result))

    def test_lyrics_artifact_schema_and_utf8_content(self):
        result = self.pipeline().run(self.task("LYRICS"))
        artifact = self.artifacts.get(
            result["artifacts"][0]["artifact_id"], "workspace-a"
        )
        internal = artifact["internal_ref"]
        content = json.loads((self.storage / internal).read_text(encoding="utf-8"))
        for field in (
            "title", "theme_summary", "lyrics", "sections", "language",
            "safe_metadata",
        ):
            self.assertIn(field, content)

    def test_usage_full_partial_and_missing(self):
        providers = (
            FakeTextProvider(), PartialProvider(), MissingUsageProvider()
        )
        values = [
            self.pipeline(provider).run(self.task("CONTENT_PLAN"))["data"][
                "provider_usage"
            ]
            for provider in providers
        ]
        self.assertIn("total_tokens", values[0])
        self.assertEqual(
            {"provider": "fake-text", "model": "fake-creative-v1", "input_tokens": 2},
            values[1],
        )
        self.assertIsNone(values[2])

    def test_openai_selection_converts_to_pipeline_result_without_raw_content(self):
        output = FakeTextProvider().generate_text(
            TextGenerationRequest(
                "workspace-a", "mission-a", "LYRICS", "different"
            )
        ).output_text
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
            "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
        })
        pipeline = TextCreationPipeline(
            self.storage, selection=selection, artifact_manager=self.artifacts,
            execution_history=self.history, logger=self.logger,
        )
        result = pipeline.run(self.task("LYRICS"))
        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertEqual("openai", result["data"]["provider"])
        self.assertEqual("real", result["data"]["generation_mode"])
        self.assertEqual(5, result["data"]["provider_usage"]["total_tokens"])
        self.assertNotIn("private creative request", repr(result))
        self.assertNotIn(
            "private creative request", repr(self.logger.query("workspace-a"))
        )

    def test_invalid_input_timeout_malformed_and_empty_are_safe(self):
        cases = (
            (self.task("UNKNOWN"), FakeTextProvider()),
            (self.task("LYRICS"), BadProvider(error=TimeoutError("secret"))),
            (self.task("LYRICS"), BadProvider(TextGenerationResult(
                "local", "model", "{", None
            ))),
            (self.task("LYRICS"), BadProvider(TextGenerationResult(
                "local", "model", "", None
            ))),
        )
        for task, provider in cases:
            result = self.pipeline(provider).run(task)
            self.assertEqual(PipelineStatus.FAILED, result["status"])
            self.assertNotIn("secret", repr(result))

    def test_ollama_malformed_json_uses_safe_internal_fallback(self):
        result = self.pipeline(BadProvider(TextGenerationResult(
            "ollama", "qwen2.5:1.5b", "한국어 로컬 생성 본문", None
        ))).run(self.task("LYRICS"))
        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        artifact = self.artifacts.get(
            result["artifacts"][0]["artifact_id"], "workspace-a"
        )
        content = json.loads(
            (self.storage / artifact["internal_ref"]).read_text(encoding="utf-8")
        )
        self.assertEqual("한국어 로컬 생성 본문", content["lyrics"])
        self.assertNotIn("한국어 로컬 생성 본문", repr(result))
        unsafe = self.pipeline(BadProvider(TextGenerationResult(
            "ollama", "qwen2.5:1.5b", "secret=private-value", None
        ))).run(self.task("LYRICS"))
        self.assertEqual(PipelineStatus.FAILED, unsafe["status"])
        self.assertNotIn("private-value", repr(unsafe))

    def test_prompt_sensitive_metadata_paths_and_paid_usage_are_rejected(self):
        safe = FakeTextProvider().generate_text(
            TextGenerationRequest(
                "workspace-a", "mission-a", "LYRICS", "different"
            )
        )
        contents = json.loads(safe.output_text)
        unsafe_values = (
            {"secret": "value"},
            {"note": r"C:\Users\private\result.txt"},
            {"note": "private creative request"},
        )
        providers = []
        for unsafe in unsafe_values:
            changed = dict(contents)
            changed["safe_metadata"] = unsafe
            providers.append(BadProvider(TextGenerationResult(
                "local", "model", json.dumps(changed), None
            )))
        providers.append(BadProvider(TextGenerationResult(
            "local", "model", safe.output_text,
            {"estimated_cost_usd": 0.01},
        )))
        for provider in providers:
            result = self.pipeline(provider).run(self.task("LYRICS"))
            self.assertEqual(PipelineStatus.FAILED, result["status"])
            self.assertNotIn("private creative request", repr(result))
            self.assertNotIn(r"C:\Users", repr(result))

    def test_workspace_isolation_and_cross_workspace_artifact_access(self):
        result = self.pipeline().run(self.task("LYRICS", "workspace-a"))
        artifact_id = result["artifacts"][0]["artifact_id"]
        self.assertIsNone(self.artifacts.get(artifact_id, "workspace-b"))

    def test_artifact_metadata_recovers_after_restart(self):
        result = self.pipeline().run(self.task("CONTENT_PLAN"))
        restored = ArtifactManager(FileArtifactRepository(
            self.repository_file, self.storage
        ))
        artifact = restored.get(
            result["artifacts"][0]["artifact_id"], "workspace-a"
        )
        self.assertEqual("AVAILABLE", artifact["status"])
        self.assertNotIn(str(self.storage), repr(artifact))

    def test_history_records_safe_summary(self):
        result = self.pipeline().run(self.task("TITLE_DESCRIPTION"))
        records = self.history.query(workspace_id="workspace-a")
        self.assertEqual(1, len(records))
        self.assertEqual(result["status"], records[0]["status"])
        self.assertNotIn("private creative request", repr(records))

    def test_logger_and_history_failure_are_isolated(self):
        class FailingHistory:
            def record_content_stage(self, *_):
                raise OSError("private path")

        result = self.pipeline(
            logger=InMemoryLogger(fail_writes=True),
            history=FailingHistory(),
        ).run(self.task("LYRICS"))
        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertNotIn("private path", repr(result))


if __name__ == "__main__":
    unittest.main()
