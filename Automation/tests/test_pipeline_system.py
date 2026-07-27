import importlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.manager import Manager
from core.execution_history import ExecutionHistory
from core.history_analyzer import HistoryAnalyzer
from core.history_pipeline import HistoryPipeline
from core.music_pipeline import MusicPipeline
from core.pipeline import AIPipeline
from core.registry import PipelineRegistry
from core.result import PipelineResult
from core.status import PipelineStatus
from core.stub_pipelines import StubPipeline
from core.task import Task
from core.task_queue import TaskQueue
from core.worker import TaskWorker


RESULT_KEYS = {"status", "pipeline", "task", "task_id", "task_type", "data", "error"}


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


class StubAndFailurePipelineTests(PipelineTestCase):
    def test_content_pipeline_returns_not_implemented_without_exception(self):
        result = StubPipeline("Content Pipeline").run(self.task("Create video", "CONTENT"))
        self.assertEqual(PipelineStatus.NOT_IMPLEMENTED, result["status"])
        self.assertTrue(RESULT_KEYS.issubset(result))

    def test_research_pipeline_returns_not_implemented_without_exception(self):
        result = StubPipeline("Research Pipeline").run(self.task("Research trends", "RESEARCH"))
        self.assertEqual(PipelineStatus.NOT_IMPLEMENTED, result["status"])
        self.assertTrue(RESULT_KEYS.issubset(result))

    def test_failing_pipeline_returns_intentional_failed_result(self):
        main = importlib.import_module("main")
        result = main.FailingPipeline().run(self.task("Run failure test", "FAIL"))
        self.assertEqual(PipelineStatus.FAILED, result["status"])
        self.assertEqual("Intentional test failure", result["error"])
        self.assertTrue(RESULT_KEYS.issubset(result))


class ManagerTests(PipelineTestCase):
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


class WorkerTests(PipelineTestCase):
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
        self.assertEqual(PipelineStatus.NOT_IMPLEMENTED, completed[0].status)
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
