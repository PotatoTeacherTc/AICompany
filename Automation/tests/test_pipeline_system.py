import importlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.manager import Manager
from config.settings import PROJECT_ROOT
from core.execution_history import ExecutionHistory
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


class FilePipelineTests(PipelineTestCase):
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


class MusicPipelineTests(PipelineTestCase):
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


class ResearchPipelineTests(PipelineTestCase):
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


class FailurePipelineTests(PipelineTestCase):

    def test_failing_pipeline_returns_intentional_failed_result(self):
        main = importlib.import_module("main")
        result = main.FailingPipeline().run(self.task("Run failure test", "FAIL"))
        self.assertEqual(PipelineStatus.FAILED, result["status"])
        self.assertEqual("Intentional test failure", result["error"])
        self.assertTrue(RESULT_KEYS.issubset(result))


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
        self.assertEqual("test exception", completed.result["error"])
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
