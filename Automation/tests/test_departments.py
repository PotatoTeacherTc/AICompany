import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.collaboration_worker import FunctionWorker
from core.department import DepartmentManager, WorkerDirectory
from core.persistence import InMemoryStateRepository, JsonStateRepository
from core.status import PipelineStatus
from core.structured_logging import InMemoryLogger
from core.worker_result import WorkerResult


def worker(name):
    return FunctionWorker(
        name,
        lambda context: WorkerResult.create(
            PipelineStatus.SUCCESS, name, context
        ),
    )


class FixedClock:
    def __init__(self):
        self.current = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        value = self.current
        self.current += timedelta(seconds=1)
        return value


class DepartmentTests(unittest.TestCase):
    def setUp(self):
        self.clock = FixedClock()
        self.repository = InMemoryStateRepository()
        self.directory = WorkerDirectory()
        self.directory.register(
            worker("research-worker"), "workspace-a", ("RESEARCH",)
        )
        self.directory.register(
            worker("content-worker"), "workspace-a", ("CONTENT",)
        )
        self.directory.register(
            worker("foreign-worker"), "workspace-b", ("RESEARCH",)
        )
        self.logger = InMemoryLogger(clock=self.clock)
        self.manager = DepartmentManager(
            self.repository,
            self.directory,
            ("RESEARCH", "CONTENT", "FILE"),
            self.logger,
            self.clock,
        )

    def create(self):
        return self.manager.create(
            "workspace-a",
            "Research",
            "Research offline department",
            "RESEARCH",
            worker_ids=("research-worker",),
            lead_worker_id="research-worker",
            supported_task_types=("RESEARCH",),
            department_id="research",
        )

    def test_create_get_list_and_logging(self):
        department = self.create()
        self.assertEqual("research-worker", department.lead_worker_id)
        self.assertEqual(department, self.manager.get(
            "research", "workspace-a"
        ))
        self.assertEqual([department], self.manager.list("workspace-a"))
        self.assertEqual(
            "DEPARTMENT_CREATED",
            self.logger.query("workspace-a")[0]["event_type"],
        )

    def test_update_enable_disable_and_stale_revision(self):
        department = self.create()
        disabled = self.manager.set_enabled(
            department.department_id, "workspace-a", False, 0
        )
        self.assertFalse(disabled.enabled)
        with self.assertRaises(ValueError):
            self.manager.update(
                department.department_id,
                "workspace-a",
                {"safe_summary": "Updated offline department"},
                expected_revision=0,
            )

    def test_assign_remove_and_lead_worker(self):
        department = self.manager.create(
            "workspace-a", "Content", "Content offline department", "CONTENT",
            supported_task_types=("CONTENT",), department_id="content",
        )
        assigned = self.manager.assign_worker(
            "content", "workspace-a", "content-worker", 0, lead=True
        )
        self.assertEqual(("content-worker",), assigned.worker_ids)
        self.assertEqual("content-worker", assigned.lead_worker_id)
        removed = self.manager.remove_worker(
            "content", "workspace-a", "content-worker", 1
        )
        self.assertEqual((), removed.worker_ids)
        self.assertIsNone(removed.lead_worker_id)

    def test_duplicate_and_foreign_workspace_worker_rejected(self):
        with self.assertRaises(ValueError):
            self.manager.create(
                "workspace-a", "Research", "Research offline department",
                "RESEARCH",
                worker_ids=("research-worker", "research-worker"),
                supported_task_types=("RESEARCH",),
            )
        with self.assertRaises(ValueError):
            self.manager.create(
                "workspace-a", "Research", "Research offline department",
                "RESEARCH", worker_ids=("foreign-worker",),
                supported_task_types=("RESEARCH",),
            )

    def test_lead_must_be_member_and_task_type_supported(self):
        with self.assertRaises(ValueError):
            self.manager.create(
                "workspace-a", "Research", "Research offline department",
                "RESEARCH", lead_worker_id="research-worker",
                supported_task_types=("RESEARCH",),
            )
        with self.assertRaises(ValueError):
            self.manager.create(
                "workspace-a", "Media", "Media offline department", "MEDIA",
                supported_task_types=("VIDEO",),
            )

    def test_invalid_ids_names_and_sensitive_summary_rejected(self):
        for values in (
            {"department_id": "../bad"},
            {"name": ""},
            {"safe_summary": "prompt private"},
            {"safe_summary": r"C:\private\file"},
        ):
            arguments = {
                "workspace_id": "workspace-a",
                "name": "Research",
                "safe_summary": "Research offline department",
                "department_type": "RESEARCH",
                "department_id": "safe",
            }
            arguments.update(values)
            with self.assertRaises(ValueError):
                self.manager.create(**arguments)

    def test_workspace_isolation_and_cross_workspace_lookup(self):
        self.create()
        self.assertIsNone(self.manager.get("research", "workspace-b"))
        self.assertEqual([], self.manager.list("workspace-b"))
        self.assertIsNone(self.repository.get(
            "department", "workspace-a:research", "workspace-b"
        ))

    def test_json_restart_and_corrupt_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "departments.json"
            first = DepartmentManager(
                JsonStateRepository(path), self.directory,
                ("RESEARCH",), clock=self.clock,
            )
            first.create(
                "workspace-a", "Research", "Research offline department",
                "RESEARCH", worker_ids=("research-worker",),
                lead_worker_id="research-worker",
                supported_task_types=("RESEARCH",), department_id="research",
            )
            second = DepartmentManager(
                JsonStateRepository(path), self.directory,
                ("RESEARCH",), clock=self.clock,
            )
            self.assertIsNotNone(second.get("research", "workspace-a"))
            second.repository.save(
                "department", "workspace-a:broken", "workspace-a",
                {"department_id": "broken"},
            )
            self.assertEqual(1, len(second.list("workspace-a")))

    def test_default_departments_use_only_real_workers_and_task_types(self):
        created = self.manager.create_defaults("workspace-a")
        types = {item.department_type for item in created}
        self.assertEqual({"RESEARCH", "CONTENT"}, types)
        self.assertNotIn("MEDIA", types)
        self.assertTrue(all(item.worker_ids for item in created))

    def test_logger_failure_does_not_change_department(self):
        manager = DepartmentManager(
            self.repository, self.directory, ("RESEARCH",),
            InMemoryLogger(fail_writes=True), self.clock,
        )
        department = manager.create(
            "workspace-a", "Research", "Research offline department",
            "RESEARCH", worker_ids=("research-worker",),
            supported_task_types=("RESEARCH",), department_id="research",
        )
        self.assertEqual("research", department.department_id)
        self.assertIsNotNone(manager.get("research", "workspace-a"))


if __name__ == "__main__":
    unittest.main()
