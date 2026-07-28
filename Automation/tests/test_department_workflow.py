import unittest

from core.collaboration_worker import FunctionWorker
from core.department import DepartmentManager, WorkerDirectory
from core.department_workflow import DepartmentSelector, DepartmentWorkflow
from core.execution_history import ExecutionHistory
from core.execution_history_repository import InMemoryExecutionHistoryRepository
from core.mission import Mission
from core.persistence import InMemoryStateRepository
from core.result import PipelineResult
from core.settings_manager import SettingsManager
from core.status import PipelineStatus
from core.structured_logging import InMemoryLogger
from core.usage_engine import UsageEngine
from core.worker_result import WorkerResult


def success_worker(name, usage=None):
    return FunctionWorker(
        name,
        lambda context: WorkerResult.create(
            PipelineStatus.SUCCESS, name, context, usage=usage
        ),
    )


def failed_worker(name):
    return FunctionWorker(
        name,
        lambda context: WorkerResult.create(
            PipelineStatus.FAILED, name, context,
            error="WorkerError: OfflineFailure",
        ),
    )


class DepartmentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryStateRepository()
        self.directory = WorkerDirectory()
        self.directory.register(
            success_worker("content-lead"), "workspace-a", ("CONTENT",)
        )
        self.directory.register(
            success_worker("content-worker"), "workspace-a", ("CONTENT",)
        )
        self.directory.register(
            success_worker("foreign-worker"), "workspace-b", ("CONTENT",)
        )
        self.logger = InMemoryLogger()
        self.departments = DepartmentManager(
            self.repository, self.directory, ("CONTENT", "RESEARCH"),
            self.logger,
        )
        self.content = self.departments.create(
            "workspace-a", "Content", "Content offline department", "CONTENT",
            worker_ids=("content-worker", "content-lead"),
            lead_worker_id="content-lead",
            supported_task_types=("CONTENT",),
            department_id="content",
        )
        self.history = ExecutionHistory(
            repository=InMemoryExecutionHistoryRepository()
        )
        self.usage = UsageEngine(self.repository, self.logger)

    def mission(self, workspace="workspace-a"):
        return Mission.create(
            "Safe department mission",
            "private objective",
            "user-a",
            workspace,
        )

    @staticmethod
    def pipeline(task, _selection, _previous):
        return PipelineResult(
            PipelineStatus.SUCCESS,
            "Fake Content Pipeline",
            task,
            task.task_type,
            data={"provider_usage": {
                "provider": "fake",
                "input_tokens": 2,
                "total_tokens": 2,
                "estimated_cost_usd": 0.0,
            }},
            artifacts=[{
                "artifact_id": "artifact-a",
                "artifact_type": "TEXT",
                "filename": "result.txt",
                "workspace_id": task.workspace_id,
                "status": "AVAILABLE",
            }],
        ).to_dict()

    def workflow(self, pipeline=None, **kwargs):
        return DepartmentWorkflow(
            self.departments,
            pipeline or self.pipeline,
            execution_history=self.history,
            logger=self.logger,
            usage_engine=self.usage,
            settings_manager=SettingsManager(self.repository),
            **kwargs,
        )

    def test_task_type_selection_is_deterministic_and_lead_first(self):
        selection = DepartmentSelector(self.departments).select(
            self.mission(), "CONTENT"
        )
        self.assertEqual("content", selection.department_id)
        self.assertEqual("content-lead", selection.selected_worker_ids[0])
        self.assertEqual(
            "deterministic task type match", selection.safe_summary
        )

    def test_explicit_selection_and_foreign_workspace_rejected(self):
        selected = DepartmentSelector(self.departments).select(
            self.mission(), "CONTENT", "content"
        )
        self.assertEqual("explicit department selection", selected.safe_summary)
        with self.assertRaises(ValueError):
            DepartmentSelector(self.departments).select(
                self.mission("workspace-b"), "CONTENT", "content"
            )

    def test_disabled_or_empty_department_is_excluded(self):
        self.departments.set_enabled("content", "workspace-a", False, 0)
        result = self.workflow().execute(self.mission(), "CONTENT")
        self.assertEqual(PipelineStatus.FAILED, result["status"])
        self.assertEqual(
            "SelectionError: NoEligibleDepartment", result["error"]
        )

    def test_success_flow_history_logging_usage_and_safe_result(self):
        result = self.workflow().execute(self.mission(), "CONTENT")
        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertEqual("content", result["data"]["selection"]["department_id"])
        self.assertEqual(2, len(
            result["data"]["collaboration"]["worker_results"]
        ))
        self.assertEqual(1, len(result["artifacts"]))
        self.assertGreaterEqual(len(self.history.query(workspace_id="workspace-a")), 2)
        self.assertEqual(1, self.usage.summary("workspace-a")["record_count"])
        events = [
            item["event_type"] for item in self.logger.query("workspace-a")
        ]
        self.assertIn("DEPARTMENT_SELECTED", events)
        self.assertIn("DEPARTMENT_WORKFLOW_COMPLETED", events)
        self.assertNotIn("private objective", repr(result))

    def test_worker_partial_failure_stops_pipeline_safely(self):
        self.directory.register(
            failed_worker("failed-worker"), "workspace-a", ("CONTENT",)
        )
        updated = self.departments.assign_worker(
            "content", "workspace-a", "failed-worker", 0
        )
        called = {"value": False}

        def pipeline(*_):
            called["value"] = True
            return {}

        result = self.workflow(pipeline).execute(self.mission(), "CONTENT")
        self.assertEqual(PipelineStatus.FAILED, result["status"])
        self.assertEqual("CollaborationError: WorkerFailure", result["error"])
        self.assertFalse(called["value"])
        self.assertEqual(1, updated.revision)

    def test_pipeline_failure_and_raw_error_are_safe(self):
        def pipeline(task, _selection, _previous):
            return PipelineResult(
                PipelineStatus.FAILED, "Fake", task, task.task_type,
                error="raw provider secret and path C:\\private",
            ).to_dict()

        result = self.workflow(pipeline).execute(self.mission(), "CONTENT")
        self.assertEqual(PipelineStatus.FAILED, result["status"])
        self.assertEqual("WorkflowError: ReportedFailure", result["error"])
        self.assertNotIn("secret", repr(result))
        self.assertNotIn("C:\\private", repr(result))

    def test_pipeline_artifact_workspace_mismatch_is_rejected(self):
        def pipeline(task, _selection, _previous):
            value = self.pipeline(task, _selection, _previous)
            value["artifacts"][0]["workspace_id"] = "workspace-b"
            return value

        result = self.workflow(pipeline).execute(self.mission(), "CONTENT")
        self.assertEqual(PipelineStatus.FAILED, result["status"])
        self.assertEqual("WorkspaceError: ArtifactMismatch", result["error"])

    def test_usage_partial_and_missing(self):
        usages = (
            {"provider": "fake", "input_tokens": 1},
            None,
        )
        for index, usage in enumerate(usages):
            def pipeline(task, _selection, _previous, value=usage):
                return PipelineResult(
                    PipelineStatus.SUCCESS, "Fake", task, task.task_type,
                    data={} if value is None else {"provider_usage": value},
                ).to_dict()

            result = self.workflow(pipeline).execute(
                self.mission(), "CONTENT"
            )
            self.assertEqual(PipelineStatus.SUCCESS, result["status"])
            recorded = result["data"]["pipeline"]["usage"]
            self.assertEqual(usage, recorded)

    def test_retry_recovery_after_transient_pipeline_failure(self):
        attempts = {"count": 0}

        def pipeline(task, _selection, previous):
            attempts["count"] += 1
            if previous is None:
                return PipelineResult(
                    PipelineStatus.FAILED, "Fake", task, task.task_type,
                    error="ProviderError: ConnectionError",
                ).to_dict()
            return self.pipeline(task, _selection, previous)

        result = self.workflow(pipeline).execute(self.mission(), "CONTENT")
        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertEqual(2, attempts["count"])
        self.assertEqual(2, result["data"]["retry"]["current_attempt"])

    def test_logger_and_history_failure_do_not_change_success(self):
        class FailingHistory:
            def record_collaboration(self, *_):
                raise OSError("private history path")

            def record(self, *_):
                raise OSError("private history path")

        workflow = DepartmentWorkflow(
            self.departments,
            self.pipeline,
            execution_history=FailingHistory(),
            logger=InMemoryLogger(fail_writes=True),
            usage_engine=self.usage,
        )
        result = workflow.execute(self.mission(), "CONTENT")
        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertNotIn("private history path", repr(result))

    def test_invalid_task_type_and_no_network_or_paid_provider(self):
        result = self.workflow().execute(self.mission(), "")
        self.assertEqual(PipelineStatus.FAILED, result["status"])


if __name__ == "__main__":
    unittest.main()
