import importlib
import json
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from agent.manager import Manager
from agent.goal_task_planner import GoalTaskPlanner
from application.automation_service import AutomationService
from application.task_query_service import TaskQueryService
from api.contracts import CreateTaskRequest, ListTasksRequest
from api.app import create_app
from api.task_api import TaskApi
from fastapi.testclient import TestClient
from config.settings import PROJECT_ROOT
from core.execution_history import ExecutionHistory
from core.execution_history_repository import InMemoryExecutionHistoryRepository
from core.artifact_manager import ArtifactManager
from core.artifact_repository import FileArtifactRepository, InMemoryArtifactRepository
from core.base_pipeline import BasePipeline
from core.content_pipeline import ContentPipeline
from core.history_analyzer import HistoryAnalyzer
from core.history_pipeline import HistoryPipeline
from core.music_pipeline import MusicPipeline
from core.pipeline import AIPipeline
from core.registry import PipelineRegistry
from core.result import PipelineResult
from core.research_pipeline import ResearchPipeline
from core.status import PipelineStatus
from core.stub_pipelines import StubPipeline
from core.task import Task
from core.task_queue import TaskQueue
from core.worker import TaskWorker
from providers.factory import ProviderFactory
from providers.mock_provider import MockProvider
from providers.models import ProviderRequest


RESULT_KEYS = {"status", "pipeline", "task", "task_id", "task_type", "data", "error"}


class TestPipeline(BasePipeline):
    def __init__(self):
        super().__init__("Test Pipeline")

    def run(self, task):
        return PipelineResult(PipelineStatus.SUCCESS, self.name, task).to_dict()


class FixedResultPipeline(BasePipeline):
    def __init__(self, result):
        super().__init__("Fixed Result Pipeline")
        self.result = result

    def run(self, task):
        return self.result


class PipelineTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.history = ExecutionHistory(self.root / "history.json")

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def task(task_text, task_type):
        task = Task(task_text)
        task.task_type = task_type
        return task

    def registry(self):
        main = importlib.import_module("main")
        return main.build_registry(
            self.history,
            base_folder=self.root / "test_files",
            music_root=self.root / "music",
            content_root=self.root / "content",
            research_root=self.root / "research",
        )


class TaskTests(unittest.TestCase):
    def test_task_preserves_structured_parameters_in_serialized_form(self):
        parameters = {"target_folder": "input", "priority": "high"}
        task = Task("Organize files", parameters=parameters)
        parameters["priority"] = "changed"

        task_data = task.to_dict()

        self.assertEqual("high", task.parameters["priority"])
        self.assertEqual(task.parameters, task_data["parameters"])

    def test_task_serializes_optional_parent_task_relationship(self):
        parent = Task("Plan campaign")
        child = Task("Research audience", parent_task_id=parent.id)

        self.assertEqual(parent.id, child.parent_task_id)
        self.assertEqual(parent.id, child.to_dict()["parent_task_id"])


class ApplicationServiceTests(PipelineTestCase):
    def test_service_submits_and_executes_task_with_injected_dependencies(self):
        class SuccessfulManager:
            def handle(_, task):
                task.task_type = "FILE"
                task.pipeline = "Test Pipeline"
                return PipelineResult(
                    PipelineStatus.SUCCESS,
                    task.pipeline,
                    task,
                    task.task_type,
                ).to_dict()

        artifacts = ArtifactManager(InMemoryArtifactRepository())
        service = AutomationService(
            SuccessfulManager(),
            history=self.history,
            artifact_manager=artifacts,
        )
        task = service.submit_text(
            "service task",
            parameters={"source": "test"},
            max_retries=1,
        )
        completed = service.run_all()

        self.assertEqual([task], completed)
        self.assertEqual(PipelineStatus.SUCCESS, task.status)
        self.assertIs(self.history, service.task_queue.history)
        self.assertIs(artifacts, service.artifact_manager)
        self.assertEqual(task.id, self.history.get_all()[0]["task_id"])


class TaskQueryServiceTests(PipelineTestCase):
    def test_task_query_returns_serializable_task_history_usage_and_artifacts(self):
        artifact_file = self.root / "output.txt"
        artifact_file.write_text("artifact", encoding="utf-8")
        artifacts = ArtifactManager(InMemoryArtifactRepository())
        artifact = artifacts.register_file(artifact_file, "TEXT", "Test Pipeline")
        task = self.task("query task", "CONTENT")
        task.pipeline = "Test Pipeline"
        task.complete(
            PipelineResult(
                PipelineStatus.SUCCESS,
                task.pipeline,
                task,
                task.task_type,
                data={
                    "provider_usage": {
                        "provider": "mock",
                        "total_tokens": 3,
                        "estimated_cost": 0.0,
                    }
                },
                artifacts=[artifact],
            ).to_dict()
        )
        self.history.record(task)
        service = TaskQueryService(self.history, artifacts, {task.id: task}.get)

        response = service.get(task.id)

        self.assertTrue(response["found"])
        self.assertIsInstance(response["task"], dict)
        self.assertIsNot(task, response["task"])
        self.assertEqual(task.id, response["history"]["task_id"])
        self.assertEqual("mock", response["usage"]["provider"])
        self.assertEqual([artifact], response["artifacts"])
        self.assertEqual([task.id], [item["task_id"] for item in service.list(status="SUCCESS", pipeline="Test Pipeline")])

    def test_task_query_is_safe_for_missing_task_and_repository_implementations(self):
        task = self.task("persisted query", "FILE")
        task.pipeline = "Test Pipeline"
        task.complete(PipelineResult(PipelineStatus.SUCCESS, task.pipeline, task, task.task_type).to_dict())
        histories = [
            ExecutionHistory(repository=InMemoryExecutionHistoryRepository()),
            ExecutionHistory(self.root / "query-history.json"),
        ]
        responses = []
        for history in histories:
            history.record(task)
            service = TaskQueryService(history, ArtifactManager())
            responses.append(service.get(task.id))
            self.assertFalse(service.get("missing-task")["found"])

        self.assertEqual(responses[0], responses[1])


class ApiContractTests(PipelineTestCase):
    def test_task_api_creates_and_queries_serializable_tasks_through_services(self):
        class SuccessfulManager:
            def handle(_, task):
                task.task_type = "CONTENT"
                task.pipeline = "Test Pipeline"
                return PipelineResult(PipelineStatus.SUCCESS, task.pipeline, task, task.task_type).to_dict()

        artifacts = ArtifactManager()
        automation = AutomationService(SuccessfulManager(), history=self.history, artifact_manager=artifacts)
        queries = TaskQueryService(self.history, artifacts, automation._get_task_for_query)
        api = TaskApi(automation, queries)

        created = api.create_task({"task_text": "Create through API", "parameters": {"format": "text"}})
        automation.run_all()
        fetched = api.get_task(created["task_id"])
        listed = api.list_tasks({"status": "SUCCESS", "pipeline": "Test Pipeline"})

        self.assertTrue(created["found"])
        self.assertEqual(PipelineStatus.QUEUED, created["status"])
        self.assertEqual(PipelineStatus.SUCCESS, fetched["status"])
        self.assertEqual([created["task_id"]], [item["task_id"] for item in listed["items"]])
        self.assertFalse(api.get_task("unknown-task")["found"])

    def test_api_contracts_validate_payloads_and_preserve_query_options(self):
        request = CreateTaskRequest.from_dict({"task_text": "  Plan work  ", "max_retries": 1})
        filters = ListTasksRequest.from_dict({"status": "SUCCESS", "limit": 3, "offset": 1}).to_filters()

        self.assertEqual("Plan work", request.task_text)
        self.assertEqual({"status": "SUCCESS", "limit": 3, "offset": 1}, filters)
        with self.assertRaises(ValueError):
            CreateTaskRequest.from_dict({"task_text": ""})
        with self.assertRaises(TypeError):
            CreateTaskRequest.from_dict("invalid")


class FastApiFoundationTests(PipelineTestCase):
    def test_application_factory_exposes_health_with_injected_services(self):
        class UnusedManager:
            def handle(_, task):
                raise AssertionError("health check must not execute tasks")

        service = AutomationService(UnusedManager(), history=self.history)
        app = create_app(automation_service=service)

        response = TestClient(app).get("/health")

        self.assertEqual(200, response.status_code)
        self.assertEqual({"status": "ok"}, response.json())
        self.assertIs(service, app.state.automation_service)

    def test_application_factory_returns_safe_global_error_response(self):
        service = AutomationService(object(), history=self.history)
        app = create_app(automation_service=service)

        @app.get("/test-error")
        def test_error():
            raise RuntimeError("sensitive internal detail")

        response = TestClient(app, raise_server_exceptions=False).get("/test-error")

        self.assertEqual(500, response.status_code)
        self.assertEqual(
            {"error": {"code": "internal_error", "message": "Internal server error"}},
            response.json(),
        )
        self.assertNotIn("sensitive internal detail", response.text)


class ProviderTests(unittest.TestCase):
    def test_mock_provider_returns_standard_response_with_usage(self):
        response = MockProvider().generate(
            ProviderRequest(prompt="Create a concise outline", model="mock-small")
        )

        self.assertEqual("mock", response.provider)
        self.assertEqual("mock-small", response.model)
        self.assertTrue(response.output_text)
        self.assertEqual(0, response.usage.estimated_cost_usd)
        self.assertGreater(response.usage.total_tokens, 0)

    def test_provider_factory_uses_mock_by_default_and_rejects_unknown_provider(self):
        self.assertIsInstance(ProviderFactory.from_environment({}).provider, MockProvider)
        with self.assertRaisesRegex(ValueError, "Unsupported AI provider"):
            ProviderFactory.from_environment({"AICOMPANY_PROVIDER": "unknown"})


class ArtifactManagerTests(PipelineTestCase):
    def test_artifact_manager_registers_and_queries_file_metadata(self):
        artifact_file = self.root / "output.txt"
        artifact_file.write_text("artifact content", encoding="utf-8")
        manager = ArtifactManager(repository=InMemoryArtifactRepository())

        artifact = manager.register_file(
            artifact_file,
            artifact_type="TEXT",
            producer_pipeline="Test Pipeline",
        )

        self.assertTrue(
            set(ArtifactManager.METADATA_FIELDS).issubset(artifact)
        )
        self.assertTrue(artifact["artifact_id"])
        self.assertEqual("TEXT", artifact["artifact_type"])
        self.assertEqual("output.txt", artifact["filename"])
        self.assertEqual(len("artifact content".encode("utf-8")), artifact["size"])
        self.assertEqual("Test Pipeline", artifact["producer_pipeline"])
        self.assertIsNone(artifact["workspace_id"])
        self.assertEqual(artifact, manager.get(artifact["artifact_id"]))
        self.assertEqual([artifact], manager.list())

        result = PipelineResult(
            PipelineStatus.SUCCESS,
            "Test Pipeline",
            self.task("artifact task", "FILE"),
            artifacts=[artifact],
        ).to_dict()
        self.assertEqual([artifact], result["artifacts"])

    def test_artifact_metadata_is_preserved_in_execution_history(self):
        artifact_file = self.root / "history_output.txt"
        artifact_file.write_text("history artifact", encoding="utf-8")
        artifact = ArtifactManager().register_file(
            artifact_file, "TEXT", "Test Pipeline"
        )

        class ArtifactManagerBackedManager:
            def handle(_, task):
                task.task_type = "FILE"
                task.pipeline = "Test Pipeline"
                return PipelineResult(
                    PipelineStatus.SUCCESS,
                    task.pipeline,
                    task,
                    artifacts=[artifact],
                ).to_dict()

        queue = TaskQueue(history=self.history)
        queue.add(self.task("artifact history", "FILE"))
        TaskWorker(queue, ArtifactManagerBackedManager(), self.history).run_once()

        self.assertEqual(
            [artifact], self.history.get_all()[0]["result"]["artifacts"]
        )

    def test_file_artifact_repository_reloads_records_and_handles_missing_data(self):
        repository_file = self.root / "artifacts.json"
        manager = ArtifactManager(repository=FileArtifactRepository(repository_file))
        artifact_file = self.root / "report.txt"
        artifact_file.write_text("report", encoding="utf-8")
        artifact = manager.register_file(artifact_file, "TEXT", "Test Pipeline")

        reloaded = ArtifactManager(repository=FileArtifactRepository(repository_file))
        self.assertEqual(artifact, reloaded.get(artifact["artifact_id"]))
        (self.root / "broken_artifacts.json").write_text("{", encoding="utf-8")
        self.assertEqual(
            [],
            ArtifactManager(
                repository=FileArtifactRepository(self.root / "broken_artifacts.json")
            ).list(),
        )


class GoalTaskPlannerTests(unittest.TestCase):
    def test_planner_creates_validated_executable_child_tasks(self):
        registry = PipelineRegistry()
        registry.register("TEST", TestPipeline(), capabilities=("test_execution",))
        parent = Task("Prepare launch")

        children = GoalTaskPlanner(registry).create_subtasks(
            parent,
            [
                {
                    "task_text": "Prepare deliverable",
                    "task_type": "TEST",
                    "parameters": {"priority": "high"},
                }
            ],
        )

        self.assertEqual(1, len(children))
        self.assertEqual(parent.id, children[0].parent_task_id)
        self.assertEqual("TEST", children[0].task_type)
        self.assertEqual({"priority": "high"}, children[0].parameters)

    def test_planner_rejects_unregistered_task_type(self):
        with self.assertRaisesRegex(ValueError, "not registered"):
            GoalTaskPlanner(PipelineRegistry()).create_subtasks(
                Task("Prepare launch"),
                [{"task_text": "Unknown work", "task_type": "UNKNOWN"}],
            )


class FilePipelineTests(PipelineTestCase):
    def test_all_artifact_producing_pipelines_register_queryable_artifacts(self):
        artifact_manager = ArtifactManager()
        test_files = self.root / "artifact_files"
        test_files.mkdir()
        (test_files / "photo.jpg").write_text("image", encoding="utf-8")

        pipelines = [
            (AIPipeline(base_folder=test_files, artifact_manager=artifact_manager), self.task("Organize files", "FILE")),
            (MusicPipeline(music_root=self.root / "music", artifact_manager=artifact_manager), self.task("Create a song", "MUSIC")),
            (ContentPipeline(content_root=self.root / "content", artifact_manager=artifact_manager), self.task("Create a video", "CONTENT")),
            (ResearchPipeline(research_root=self.root / "research", artifact_manager=artifact_manager), self.task("Research a topic", "RESEARCH")),
        ]

        with patch("scripts.file_manager.log"), patch("scripts.report_generator.log"):
            for pipeline, task in pipelines:
                result = pipeline.run(task)
                self.assertEqual(PipelineStatus.SUCCESS, result["status"])
                self.assertTrue(result["artifacts"])
                self.assertEqual(result["artifacts"], result["data"]["artifacts"])
                for artifact in result["artifacts"]:
                    self.assertEqual(artifact, artifact_manager.get(artifact["artifact_id"]))
                    self.assertEqual(pipeline.name, artifact["producer_pipeline"])

    def test_file_pipeline_organizes_files_and_returns_common_result(self):
        test_files = self.root / "test_files"
        test_files.mkdir()
        (test_files / "photo.jpg").write_text("image", encoding="utf-8")
        (test_files / "notes.txt").write_text("notes", encoding="utf-8")

        with patch("scripts.file_manager.log"), patch("scripts.report_generator.log"):
            result = AIPipeline(base_folder=test_files).run(
                self.task("Organize files", "FILE")
            )

        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertTrue(RESULT_KEYS.issubset(result))
        self.assertTrue((test_files / "Images" / "photo.jpg").exists())
        self.assertTrue((test_files / "notes.txt").exists())
        self.assertCountEqual(["SUCCESS", "SKIPPED"], result["data"]["result"])

    def test_file_pipeline_passes_planner_output_to_executor(self):
        test_files = self.root / "planned_files"
        test_files.mkdir()

        class RecordingExecutor:
            def __init__(self):
                self.plan = None

            def execute(self, plan):
                self.plan = plan
                return []

        executor = RecordingExecutor()
        with patch("scripts.report_generator.log"):
            result = AIPipeline(base_folder=test_files, executor=executor).run(
                self.task("Organize files", "FILE")
            )

        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertEqual(str(test_files), executor.plan["target_folder"])
        self.assertEqual(executor.plan, result["data"]["plan"])


class PipelineRegistryTests(unittest.TestCase):
    def test_registry_accepts_base_pipeline_and_rejects_invalid_registrations(self):
        registry = PipelineRegistry()
        pipeline = TestPipeline()

        registry.register("TEST", pipeline)

        self.assertIs(pipeline, registry.get("TEST"))
        with self.assertRaises(ValueError):
            registry.register("", TestPipeline())
        with self.assertRaises(TypeError):
            registry.register("INVALID", object())
        with self.assertRaises(ValueError):
            registry.register("TEST", TestPipeline())

    def test_registry_exposes_registered_pipeline_capabilities(self):
        registry = PipelineRegistry()
        registry.register(
            "CONTENT",
            TestPipeline(),
            capabilities=("project_creation", "artifact_generation"),
        )

        capability = registry.get_capability("CONTENT")

        self.assertEqual("CONTENT", capability["task_type"])
        self.assertEqual("Test Pipeline", capability["pipeline"])
        self.assertEqual(
            ["project_creation", "artifact_generation"], capability["capabilities"]
        )
        self.assertEqual([capability], registry.list_capabilities())


class MusicPipelineTests(PipelineTestCase):
    def test_music_pipeline_provider_usage_and_failures_are_safe(self):
        task = self.task("Create a provider-backed song", "MUSIC")
        result = MusicPipeline(music_root=self.root / "music", provider=MockProvider()).run(task)
        self.assertEqual("mock", result["data"]["provider_usage"]["provider"])
        task.pipeline = result["pipeline"]
        task.start()
        task.complete(result)
        self.history.record(task)
        self.assertEqual(
            result["data"]["provider_usage"],
            self.history.get_all()[0]["result"]["data"]["provider_usage"],
        )

        class PartialProvider:
            def generate(self, request):
                return SimpleNamespace(provider="partial", model="local", usage=SimpleNamespace())

        partial = MusicPipeline(music_root=self.root / "partial", provider=PartialProvider()).run(task)
        self.assertEqual(0, partial["data"]["provider_usage"]["total_tokens"])

        for provider in (
            type("TimeoutProvider", (), {"generate": lambda self, request: (_ for _ in ()).throw(TimeoutError("timed out"))})(),
            type("ErrorProvider", (), {"generate": lambda self, request: (_ for _ in ()).throw(RuntimeError("provider error"))})(),
        ):
            failed = MusicPipeline(music_root=self.root / "failed", provider=provider).run(task)
            self.assertEqual(PipelineStatus.FAILED, failed["status"])
            self.assertIn("Error", failed["error"])

    def test_music_pipeline_creates_complete_project_in_temp_directory(self):
        music_root = self.root / "music"
        result = MusicPipeline(music_root=music_root).run(
            self.task("Create a song", "MUSIC")
        )

        project_path = Path(result["data"]["project_path"])
        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertTrue(RESULT_KEYS.issubset(result))
        self.assertTrue(project_path.is_dir())
        self.assertTrue((project_path / "metadata.txt").is_file())
        self.assertTrue((project_path / "song_structure.txt").is_file())
        self.assertIn("Create a song", (project_path / "metadata.txt").read_text(encoding="utf-8"))


class HistoryPipelineTests(PipelineTestCase):
    def test_history_analyzer_aggregates_query_results_and_provider_usage(self):
        records = [
            {
                "task_id": "1", "status": "SUCCESS", "pipeline": "File Pipeline",
                "task_type": "FILE", "completed_at": "2026-01-01T10:00:00",
                "result": {"data": {}},
            },
            {
                "task_id": "2", "status": "FAILED", "pipeline": "Content Pipeline",
                "task_type": "CONTENT", "completed_at": "2026-01-02T10:00:00",
                "result": {"data": {"provider_usage": {
                    "provider": "mock", "model": "mock-small", "input_tokens": 10,
                    "output_tokens": 5, "total_tokens": 15, "estimated_cost_usd": 0.1,
                }}},
            },
            {
                "task_id": "3", "status": "SKIPPED", "pipeline": "Music Pipeline",
                "task_type": "MUSIC", "completed_at": "2026-01-03T10:00:00",
                "result": {"data": {"provider_usage": None}},
            },
            {
                "task_id": "4", "status": "SUCCESS", "pipeline": "Music Pipeline",
                "task_type": "MUSIC", "completed_at": "2026-01-04T10:00:00",
                "result": {"data": {"provider_usage": {
                    "provider": "mock", "model": "mock-small", "input_tokens": 20,
                    "output_tokens": 8, "total_tokens": 28, "estimated_cost_usd": 0.2,
                }}},
            },
            {
                "task_id": "5", "status": "SUCCESS", "pipeline": "Music Pipeline",
                "task_type": "MUSIC", "completed_at": "2026-01-05T10:00:00",
                "result": {"data": {"provider_usage": {
                    "provider": "partial", "model": "local", "input_tokens": 4,
                }}},
            },
        ]
        history = ExecutionHistory(repository=InMemoryExecutionHistoryRepository(records))
        analysis = HistoryAnalyzer(history).analyze()["analysis"]

        self.assertEqual(5, analysis["total_executions"])
        self.assertEqual(3, analysis["successful"])
        self.assertEqual(1, analysis["failed"])
        self.assertEqual(1, analysis["skipped"])
        self.assertEqual(60.0, analysis["success_rate"])
        self.assertEqual({"SUCCESS": 3, "FAILED": 1, "SKIPPED": 1}, analysis["status_distribution"])
        self.assertEqual({"Music Pipeline": 3, "Content Pipeline": 1, "File Pipeline": 1}, analysis["pipeline_distribution"])
        self.assertEqual({"MUSIC": 3, "CONTENT": 1, "FILE": 1}, analysis["task_type_distribution"])
        self.assertEqual({"mock": 2, "partial": 1}, analysis["provider_usage"]["provider_distribution"])
        self.assertEqual(34, analysis["provider_usage"]["input_tokens"])
        self.assertEqual(13, analysis["provider_usage"]["output_tokens"])
        self.assertEqual(43, analysis["provider_usage"]["total_tokens"])
        self.assertEqual(0.3, analysis["provider_usage"]["estimated_cost"])

        filtered = HistoryAnalyzer(history).analyze(task_type="MUSIC", status="SUCCESS")["analysis"]
        self.assertEqual(2, filtered["total_executions"])
        self.assertEqual({"MUSIC": 2}, filtered["task_type_distribution"])
        self.assertEqual(24, filtered["provider_usage"]["input_tokens"])

        json_history = ExecutionHistory(self.root / "analytics_history.json")
        json_history.records = list(records)
        json_history.save()
        self.assertEqual(
            analysis,
            HistoryAnalyzer(ExecutionHistory(self.root / "analytics_history.json")).analyze()["analysis"],
        )

    def test_history_analyzer_returns_empty_analysis_for_empty_history(self):
        result = HistoryAnalyzer(
            ExecutionHistory(repository=InMemoryExecutionHistoryRepository())
        ).analyze(status="SUCCESS")

        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertEqual({}, result["analysis"])

    def test_execution_history_query_filters_sorting_and_pagination(self):
        records = [
            {"task_id": "1", "status": "SUCCESS", "pipeline": "Music Pipeline", "task_type": "MUSIC", "completed_at": "2026-01-01T10:00:00"},
            {"task_id": "2", "status": "FAILED", "pipeline": "Content Pipeline", "task_type": "CONTENT", "completed_at": "2026-01-02T10:00:00"},
            {"task_id": "3", "status": "SUCCESS", "pipeline": "Music Pipeline", "task_type": "MUSIC", "completed_at": "2026-01-03T10:00:00"},
        ]
        history = ExecutionHistory(repository=InMemoryExecutionHistoryRepository(records))
        self.assertEqual(["3", "2"], [record["task_id"] for record in history.query(limit=2)])
        self.assertEqual(["3"], [record["task_id"] for record in history.query(status="SUCCESS", pipeline="Music Pipeline", offset=0, limit=1)])
        self.assertEqual(["2"], [record["task_id"] for record in history.query(task_type="CONTENT", start_at="2026-01-02T00:00:00", end_at="2026-01-02T23:59:59")])
        self.assertEqual([], history.query(limit=0))
        self.assertEqual([], ExecutionHistory(repository=InMemoryExecutionHistoryRepository()).query())
        with self.assertRaises(ValueError):
            history.query(offset=-1)
        with self.assertRaises(ValueError):
            history.query(limit=-1)

        json_history = ExecutionHistory(self.root / "query_history.json")
        json_history.records = list(records)
        json_history.save()
        reloaded_history = ExecutionHistory(self.root / "query_history.json")
        self.assertEqual(
            history.query(status="SUCCESS"),
            reloaded_history.query(status="SUCCESS"),
        )

    def test_execution_history_supports_in_memory_repository_and_json_reload(self):
        memory_history = ExecutionHistory(repository=InMemoryExecutionHistoryRepository())
        task = self.task("memory task", "FILE")
        task.pipeline = "Test Pipeline"
        task.start()
        task.complete(PipelineResult(PipelineStatus.SUCCESS, task.pipeline, task).to_dict())
        memory_history.record(task)
        self.assertEqual(1, memory_history.count())

        file_history = ExecutionHistory(self.root / "history.json")
        file_history.record(task)
        self.assertEqual(1, ExecutionHistory(self.root / "history.json").count())
        (self.root / "broken.json").write_text("{", encoding="utf-8")
        self.assertEqual(0, ExecutionHistory(self.root / "broken.json").count())

    def _record(self, task_type, status):
        task = self.task(f"{task_type} task", task_type)
        task.pipeline = f"{task_type} Pipeline"
        task.start()
        result = PipelineResult(status, task.pipeline, task, task_type=task_type).to_dict()
        if status == PipelineStatus.SUCCESS:
            task.complete(result)
        else:
            task.fail(result)
        self.history.record(task)

    def test_history_pipeline_and_analyzer_use_execution_history_contract(self):
        self._record("FILE", PipelineStatus.SUCCESS)
        self._record("MUSIC", PipelineStatus.SUCCESS)
        self._record("FAIL", PipelineStatus.FAILED)

        result = HistoryPipeline(self.history).run(self.task("Show history", "HISTORY"))
        analysis = HistoryAnalyzer(self.history).analyze()

        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertEqual(3, result["data"]["count"])
        self.assertEqual(PipelineStatus.SUCCESS, analysis["status"])
        self.assertEqual(1, analysis["analysis"]["task_type_distribution"]["FILE"])
        self.assertEqual(1, analysis["analysis"]["task_type_distribution"]["MUSIC"])
        self.assertEqual(1, analysis["analysis"]["task_type_distribution"]["FAIL"])


class ContentPipelineTests(PipelineTestCase):
    def test_content_pipeline_records_mock_provider_usage_in_result_and_history(self):
        task = self.task("Create a provider-backed video", "CONTENT")
        result = ContentPipeline(
            content_root=self.root / "content", provider=MockProvider()
        ).run(task)
        task.pipeline = result["pipeline"]
        task.start()
        task.complete(result)
        self.history.record(task)

        usage = result["data"]["provider_usage"]
        self.assertEqual("mock", usage["provider"])
        self.assertGreater(usage["total_tokens"], 0)
        self.assertEqual(0.0, usage["estimated_cost_usd"])
        self.assertEqual(usage, self.history.get_all()[0]["result"]["data"]["provider_usage"])

    def test_content_pipeline_uses_configurable_task_parameters(self):
        content_root = self.root / "content"
        task = Task(
            "Create a testing video",
            parameters={
                "content_type": "Tutorial",
                "title_prefix": "Practical Guide",
                "tags": ["testing", "quality"],
            },
        )
        task.task_type = "CONTENT"

        result = ContentPipeline(content_root=content_root).run(task)

        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertEqual("Tutorial", result["data"]["content_type"])
        self.assertEqual("Practical Guide: Create a testing video", result["data"]["title"])
        self.assertEqual(["testing", "quality"], result["data"]["tags"])
        metadata = json.loads(
            (Path(result["data"]["project_path"]) / "project.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(result["data"]["content_type"], metadata["content_type"])

    def test_content_pipeline_creates_complete_project_in_temp_directory(self):
        content_root = self.root / "content"
        result = ContentPipeline(content_root=content_root).run(
            self.task("Create a YouTube video", "CONTENT")
        )

        project_path = Path(result["data"]["project_path"])
        expected_files = {
            "project.json",
            "content_plan.txt",
            "script.txt",
            "title.txt",
            "description.txt",
            "tags.txt",
            "review_checklist.txt",
        }
        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertTrue(RESULT_KEYS.issubset(result))
        self.assertEqual(content_root, project_path.parent)
        self.assertFalse((PROJECT_ROOT / "Content" / project_path.name).exists())
        self.assertEqual(expected_files, {path.name for path in project_path.iterdir()})
        for filename in expected_files:
            self.assertGreater((project_path / filename).stat().st_size, 0)
        metadata = json.loads((project_path / "project.json").read_text(encoding="utf-8"))
        self.assertEqual("Create a YouTube video", metadata["task"])
        self.assertEqual("YouTube video", metadata["content_type"])
        self.assertEqual(metadata["title"], result["data"]["title"])
        self.assertIn(
            "Review Checklist",
            (project_path / "review_checklist.txt").read_text(encoding="utf-8"),
        )


class ResearchPipelineTests(PipelineTestCase):
    def test_research_pipeline_records_provider_usage_and_returns_provider_failure(self):
        task = self.task("Research provider safety", "RESEARCH")
        result = ResearchPipeline(
            research_root=self.root / "research", provider=MockProvider()
        ).run(task)
        self.assertEqual("mock", result["data"]["provider_usage"]["provider"])

        class FailingProvider:
            def generate(self, request):
                raise TimeoutError("provider timed out")

        failed = ResearchPipeline(
            research_root=self.root / "failed_research", provider=FailingProvider()
        ).run(self.task("Research timeout", "RESEARCH"))
        self.assertEqual(PipelineStatus.FAILED, failed["status"])
        self.assertEqual("ProviderError: TimeoutError", failed["error"])
        self.assertNotIn("provider timed out", failed["error"])

    def test_research_pipeline_uses_configurable_questions_and_type(self):
        task = Task(
            "Research testing",
            parameters={
                "research_type": "Competitive analysis",
                "research_questions": ["Which testing practices are most repeatable?"],
            },
        )
        task.task_type = "RESEARCH"

        result = ResearchPipeline(research_root=self.root / "research").run(task)

        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertEqual("Competitive analysis", result["data"]["research_type"])
        self.assertEqual(
            ["Which testing practices are most repeatable?"],
            result["data"]["research_questions"],
        )

    def test_research_pipeline_records_structured_local_sources(self):
        research_root = self.root / "research"
        task = Task(
            "Research test strategy",
            parameters={
                "source_records": [
                    {
                        "title": "Internal test brief",
                        "url": "https://example.test/brief",
                        "relevance": "Defines the initial test scope.",
                    }
                ]
            },
        )
        task.task_type = "RESEARCH"

        result = ResearchPipeline(research_root=research_root).run(task)

        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertEqual(task.parameters["source_records"], result["data"]["source_records"])
        sources_text = (Path(result["data"]["project_path"]) / "sources.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("Internal test brief", sources_text)
        self.assertIn("https://example.test/brief", sources_text)

    def test_research_pipeline_creates_complete_project_in_temp_directory(self):
        research_root = self.root / "research"
        result = ResearchPipeline(research_root=research_root).run(
            self.task("Research AI music trends", "RESEARCH")
        )

        project_path = Path(result["data"]["project_path"])
        expected_files = {
            "project.json",
            "research_plan.txt",
            "findings.txt",
            "summary.txt",
            "sources.txt",
            "review_checklist.txt",
        }
        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertTrue(RESULT_KEYS.issubset(result))
        self.assertEqual(research_root, project_path.parent)
        self.assertFalse((PROJECT_ROOT / "Research" / project_path.name).exists())
        self.assertEqual(expected_files, {path.name for path in project_path.iterdir()})
        for filename in expected_files:
            self.assertGreater((project_path / filename).stat().st_size, 0)
        metadata = json.loads((project_path / "project.json").read_text(encoding="utf-8"))
        self.assertEqual("Research AI music trends", metadata["task"])
        self.assertEqual("Structured local research", metadata["research_type"])
        self.assertEqual(metadata["summary"], result["data"]["summary"])
        self.assertIn(
            "Review Checklist",
            (project_path / "review_checklist.txt").read_text(encoding="utf-8"),
        )


class FailurePipelineTests(PipelineTestCase):

    def test_failing_pipeline_returns_intentional_failed_result(self):
        main = importlib.import_module("main")
        result = main.FailingPipeline().run(self.task("Run failure test", "FAIL"))
        self.assertEqual(PipelineStatus.FAILED, result["status"])
        self.assertEqual("Intentional test failure", result["error"])
        self.assertTrue(RESULT_KEYS.issubset(result))


class NotImplementedPipelineTests(PipelineTestCase):
    def test_stub_pipeline_returns_common_not_implemented_result(self):
        result = StubPipeline("Unavailable Pipeline").run(
            self.task("Unavailable task", "UNAVAILABLE")
        )

        self.assertEqual(PipelineStatus.NOT_IMPLEMENTED, result["status"])
        self.assertTrue(RESULT_KEYS.issubset(result))

    def test_worker_preserves_not_implemented_status_and_records_history(self):
        class NotImplementedManager:
            def handle(_, task):
                task.task_type = "UNAVAILABLE"
                task.pipeline = "Unavailable Pipeline"
                return PipelineResult(
                    PipelineStatus.NOT_IMPLEMENTED,
                    task.pipeline,
                    task,
                    task.task_type,
                    error="Unavailable Pipeline is not available yet",
                ).to_dict()

        queue = TaskQueue()
        queue.add(Task("Unavailable task"))
        completed = TaskWorker(queue, NotImplementedManager(), self.history).run_once()

        self.assertEqual(PipelineStatus.NOT_IMPLEMENTED, completed.status)
        self.assertEqual(PipelineStatus.NOT_IMPLEMENTED, self.history.get_all()[0]["status"])


class ManagerTests(PipelineTestCase):
    def _manager_for_result(self, result):
        registry = PipelineRegistry()
        registry.register("CONTENT", FixedResultPipeline(result))
        return Manager(registry)

    def test_manager_accepts_valid_pipeline_result(self):
        task = Task("Create a video")
        result = PipelineResult(
            PipelineStatus.SUCCESS,
            "Fixed Result Pipeline",
            task,
            task_type="CONTENT",
        ).to_dict()

        handled = self._manager_for_result(result).handle(task)

        self.assertEqual(PipelineStatus.SUCCESS, handled["status"])
        self.assertEqual(result, handled)

    def test_manager_converts_missing_required_result_key_to_failed_result(self):
        result = {
            "status": PipelineStatus.SUCCESS,
            "pipeline": "Fixed Result Pipeline",
            "task": "Create a video",
            "task_id": "test-id",
            "task_type": "CONTENT",
            "error": None,
        }

        handled = self._manager_for_result(result).handle(Task("Create a video"))

        self.assertEqual(PipelineStatus.FAILED, handled["status"])
        self.assertIn("missing required keys", handled["error"])

    def test_manager_converts_invalid_result_status_to_failed_result(self):
        result = {
            "status": "INVALID_STATUS",
            "pipeline": "Fixed Result Pipeline",
            "task": "Create a video",
            "task_id": "test-id",
            "task_type": "CONTENT",
            "data": {},
            "error": None,
        }

        handled = self._manager_for_result(result).handle(Task("Create a video"))

        self.assertEqual(PipelineStatus.FAILED, handled["status"])
        self.assertIn("invalid status", handled["error"])

    def test_manager_converts_non_dictionary_result_to_failed_result(self):
        handled = self._manager_for_result(["not", "a", "result"]).handle(
            Task("Create a video")
        )

        self.assertEqual(PipelineStatus.FAILED, handled["status"])
        self.assertIn("PipelineResult dictionary", handled["error"])

    def test_manager_converts_result_with_mismatched_execution_metadata_to_failed_result(self):
        task = Task("Create a video")
        result = PipelineResult(
            PipelineStatus.SUCCESS,
            "Fixed Result Pipeline",
            task,
            task_type="CONTENT",
        ).to_dict()
        result["task_id"] = "different-task-id"

        handled = self._manager_for_result(result).handle(task)

        self.assertEqual(PipelineStatus.FAILED, handled["status"])
        self.assertIn("task_id", handled["error"])

    def test_classifier_identifies_all_registered_task_types(self):
        manager = Manager(self.registry())
        cases = {
            "Organize a folder": "FILE",
            "Create a song": "MUSIC",
            "Create a video": "CONTENT",
            "Research market trends": "RESEARCH",
            "Run failure test": "FAIL",
            "Show execution history": "HISTORY",
        }
        for task_text, expected_type in cases.items():
            self.assertEqual(expected_type, manager.classifier.classify(Task(task_text)))

    def test_manager_uses_validated_declared_task_type(self):
        registry = PipelineRegistry()
        registry.register("TEST", TestPipeline())
        task = Task("Text that the keyword classifier would not classify as TEST")
        task.task_type = "TEST"

        result = Manager(registry).handle(task)

        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertEqual("TEST", result["task_type"])
        self.assertEqual("Test Pipeline", result["pipeline"])

    def test_manager_routes_every_registered_pipeline(self):
        files = self.root / "test_files"
        files.mkdir()
        (files / "image.png").write_text("image", encoding="utf-8")
        manager = Manager(self.registry())
        cases = {
            "Organize a folder": ("FILE", "Automation Pipeline"),
            "Create a song": ("MUSIC", "Music Pipeline"),
            "Create a video": ("CONTENT", "Content Pipeline"),
            "Research market trends": ("RESEARCH", "Research Pipeline"),
            "Run failure test": ("FAIL", "Failing Test Pipeline"),
            "Show execution history": ("HISTORY", "Execution History Pipeline"),
        }
        with patch("scripts.file_manager.log"), patch("scripts.report_generator.log"):
            for task_text, (task_type, pipeline_name) in cases.items():
                result = manager.handle(Task(task_text))
                self.assertEqual(task_type, result["task_type"])
                self.assertEqual(pipeline_name, result["pipeline"])
                self.assertTrue(RESULT_KEYS.issubset(result))
                if task_type == "CONTENT":
                    self.assertEqual(PipelineStatus.SUCCESS, result["status"])
                if task_type == "RESEARCH":
                    self.assertEqual(PipelineStatus.SUCCESS, result["status"])


class WorkerTests(PipelineTestCase):
    def test_queue_cancellation_updates_history_and_does_not_recancel_terminal_task(self):
        task = Task("cancel task")
        queue = TaskQueue(history=self.history)
        queue.add(task)

        self.assertTrue(queue.cancel(task))
        self.assertEqual(PipelineStatus.CANCELLED, task.status)
        self.assertEqual(PipelineStatus.CANCELLED, self.history.get_all()[0]["status"])
        self.assertFalse(queue.cancel(task))
        self.assertEqual(1, self.history.count())

    def test_worker_marks_elapsed_task_timed_out_with_safe_result(self):
        class SuccessfulManager:
            def handle(_, task):
                task.task_type = "FILE"
                task.pipeline = "Test Pipeline"
                return PipelineResult(PipelineStatus.SUCCESS, task.pipeline, task).to_dict()

        task = Task("timeout task", timeout_seconds=1)
        queue = TaskQueue(history=self.history)
        queue.add(task)

        with patch("core.worker.time.monotonic", side_effect=[0.0, 2.0]):
            completed = TaskWorker(queue, SuccessfulManager(), self.history).run_once()

        record = self.history.get_all()[0]
        self.assertEqual(PipelineStatus.TIMED_OUT, completed.status)
        self.assertEqual(PipelineStatus.TIMED_OUT, completed.result["status"])
        self.assertEqual("TaskError: TimeoutError", completed.result["error"])
        self.assertEqual(PipelineStatus.TIMED_OUT, record["status"])
        self.assertEqual("TimeoutError", record["last_error_type"])

    def test_worker_retries_retryable_error_and_updates_single_history_record(self):
        attempts = []

        class RetryManager:
            def handle(_, task):
                attempts.append(task.retry_count)
                if len(attempts) == 1:
                    raise TimeoutError("provider key should not be recorded")
                task.task_type = "FILE"
                task.pipeline = "Test Pipeline"
                return PipelineResult(PipelineStatus.SUCCESS, task.pipeline, task).to_dict()

        task = Task("retry task", max_retries=1)
        queue = TaskQueue(history=self.history)
        queue.add(task)
        completed = TaskWorker(queue, RetryManager(), self.history).run_all()
        record = self.history.get_all()[0]

        self.assertEqual([0, 1], attempts)
        self.assertEqual([task], completed)
        self.assertEqual(PipelineStatus.SUCCESS, task.status)
        self.assertEqual(1, task.retry_count)
        self.assertEqual("TimeoutError", task.last_error_type)
        self.assertEqual(1, self.history.count())
        self.assertEqual(1, record["retry_count"])
        self.assertNotIn("provider key", str(record))

    def test_worker_does_not_retry_non_retryable_error_or_store_message(self):
        attempts = []

        class NonRetryManager:
            def handle(_, task):
                attempts.append(task.retry_count)
                raise ValueError("sensitive failure details")

        task = Task("non retry task", max_retries=2)
        queue = TaskQueue(history=self.history)
        queue.add(task)
        completed = TaskWorker(queue, NonRetryManager(), self.history).run_all()
        record = self.history.get_all()[0]

        self.assertEqual([0], attempts)
        self.assertEqual([task], completed)
        self.assertEqual(PipelineStatus.FAILED, task.status)
        self.assertEqual(0, task.retry_count)
        self.assertEqual("ValueError", task.last_error_type)
        self.assertEqual("TaskError: ValueError", task.result["error"])
        self.assertNotIn("sensitive failure details", str(record))

    def test_queue_and_worker_update_one_history_record_through_lifecycle(self):
        class RecordingManager:
            def handle(_, task):
                self.assertEqual(PipelineStatus.RUNNING, self.history.get_all()[0]["status"])
                task.task_type = "FILE"
                task.pipeline = "Test Pipeline"
                return PipelineResult(PipelineStatus.SUCCESS, task.pipeline, task).to_dict()

        task = Task("tracked task")
        queue = TaskQueue(history=self.history)
        queue.add(task)

        self.assertEqual(PipelineStatus.QUEUED, task.status)
        self.assertEqual(PipelineStatus.QUEUED, self.history.get_all()[0]["status"])
        completed = TaskWorker(queue, RecordingManager(), self.history).run_once()
        record = self.history.get_all()[0]

        self.assertEqual(PipelineStatus.SUCCESS, completed.status)
        self.assertEqual(1, self.history.count())
        self.assertEqual(PipelineStatus.SUCCESS, record["status"])
        self.assertEqual(completed.result, record["result"])
        self.assertIsNotNone(record["started_at"])
        self.assertIsNotNone(record["completed_at"])
        self.assertGreaterEqual(record["duration_seconds"], 0)

    def test_queue_skip_records_terminal_skipped_state(self):
        task = Task("skip task")
        queue = TaskQueue(history=self.history)
        queue.add(task)
        queue.skip(task)

        self.assertEqual(PipelineStatus.SKIPPED, task.status)
        self.assertEqual(0, queue.size())
        self.assertEqual(1, self.history.count())
        self.assertEqual(PipelineStatus.SKIPPED, self.history.get_all()[0]["status"])

    def test_worker_runs_research_pipeline_and_records_successful_history(self):
        task = Task("Research AI music trends")
        queue = TaskQueue()
        queue.add(task)
        completed = TaskWorker(queue, Manager(self.registry()), self.history).run_once()

        self.assertEqual(PipelineStatus.SUCCESS, completed.status)
        self.assertEqual("Research Pipeline", completed.pipeline)
        self.assertEqual("RESEARCH", self.history.get_all()[0]["task_type"])
        self.assertEqual("Research Pipeline", self.history.get_all()[0]["pipeline"])
        self.assertTrue(Path(completed.result["data"]["project_path"]).is_dir())

    def test_worker_runs_content_pipeline_and_records_successful_history(self):
        task = Task("Create a YouTube video")
        queue = TaskQueue()
        queue.add(task)
        completed = TaskWorker(queue, Manager(self.registry()), self.history).run_once()

        self.assertEqual(PipelineStatus.SUCCESS, completed.status)
        self.assertEqual("Content Pipeline", completed.pipeline)
        self.assertEqual("CONTENT", self.history.get_all()[0]["task_type"])
        self.assertEqual("Content Pipeline", self.history.get_all()[0]["pipeline"])
        self.assertTrue(Path(completed.result["data"]["project_path"]).is_dir())

    def test_worker_transitions_pending_to_running_to_success_and_records_history(self):
        observed_statuses = []

        class SuccessfulManager:
            def handle(_, task):
                observed_statuses.append(task.status)
                task.task_type = "FILE"
                task.pipeline = "Test Pipeline"
                return PipelineResult(PipelineStatus.SUCCESS, task.pipeline, task, "FILE").to_dict()

        task = Task("successful task")
        queue = TaskQueue()
        queue.add(task)
        completed = TaskWorker(queue, SuccessfulManager(), self.history).run_once()

        self.assertEqual(["RUNNING"], observed_statuses)
        self.assertEqual(PipelineStatus.SUCCESS, completed.status)
        self.assertEqual(1, self.history.count())

    def test_worker_marks_failed_result_and_records_history(self):
        class FailedManager:
            def handle(_, task):
                task.task_type = "FAIL"
                task.pipeline = "Test Pipeline"
                return PipelineResult(PipelineStatus.FAILED, task.pipeline, task, "FAIL").to_dict()

        queue = TaskQueue()
        queue.add(Task("failed task"))
        completed = TaskWorker(queue, FailedManager(), self.history).run_once()

        self.assertEqual(PipelineStatus.FAILED, completed.status)
        self.assertEqual(PipelineStatus.FAILED, self.history.get_all()[0]["status"])

    def test_worker_records_history_when_manager_raises_exception(self):
        class ExplodingManager:
            def handle(_, task):
                raise RuntimeError("test exception")

        queue = TaskQueue()
        queue.add(Task("exception task"))
        completed = TaskWorker(queue, ExplodingManager(), self.history).run_once()

        self.assertEqual(PipelineStatus.FAILED, completed.status)
        self.assertEqual("TaskError: RuntimeError", completed.result["error"])
        self.assertEqual(1, self.history.count())


class MainAndSyntaxTests(PipelineTestCase):
    def test_importing_main_does_not_execute_work(self):
        project_root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [sys.executable, "-B", "-c", "import main; print('IMPORT_OK')"],
            cwd=project_root,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual("IMPORT_OK", completed.stdout.strip())

    def test_main_run_exists_and_runs_with_injected_test_dependencies(self):
        main = importlib.import_module("main")
        registry = self.registry()
        completed = main.run(
            task_texts=["Create a video"],
            history=self.history,
            registry=registry,
        )
        self.assertTrue(callable(main.run))
        self.assertEqual(1, len(completed))
        self.assertEqual(PipelineStatus.SUCCESS, completed[0].status)
        self.assertEqual("Content Pipeline", completed[0].pipeline)
        self.assertEqual(1, self.history.count())

    def test_all_project_python_sources_compile(self):
        project_root = Path(__file__).resolve().parents[1]
        source_files = [
            path for path in project_root.rglob("*.py")
            if "venv" not in path.parts and "__pycache__" not in path.parts
        ]
        self.assertGreater(len(source_files), 1)
        for path in source_files:
            with self.subTest(path=path):
                compile(path.read_text(encoding="utf-8"), str(path), "exec")


if __name__ == "__main__":
    unittest.main(verbosity=2)
