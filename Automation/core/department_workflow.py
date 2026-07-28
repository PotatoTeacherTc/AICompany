from dataclasses import asdict, dataclass

from core.collaboration_orchestrator import CollaborationOrchestrator
from core.department import DepartmentManager
from core.mission import Mission
from core.result import PipelineResult
from core.retry_recovery import RetryExecutor
from core.status import PipelineStatus
from core.structured_logging import LogLevel, safe_log
from core.task import Task


@dataclass(frozen=True)
class DepartmentSelection:
    department_id: str
    selected_worker_ids: tuple[str, ...]
    lead_worker_id: str | None
    safe_summary: str

    def to_dict(self):
        value = asdict(self)
        value["selected_worker_ids"] = list(self.selected_worker_ids)
        return value


class DepartmentSelector:
    """Deterministic offline Department selection; no LLM/provider call."""

    def __init__(self, department_manager):
        if not isinstance(department_manager, DepartmentManager):
            raise TypeError("department_manager must use DepartmentManager")
        self.departments = department_manager

    def select(self, mission, task_type, department_id=None):
        if not isinstance(mission, Mission):
            raise ValueError("mission must use Mission")
        task_type = _task_type(task_type)
        if department_id is not None:
            department = self.departments.get(
                department_id, mission.workspace_id
            )
            candidates = [] if department is None else [department]
            reason = "explicit department selection"
        else:
            candidates = self.departments.list(mission.workspace_id)
            reason = "deterministic task type match"
        candidates = [
            item for item in candidates
            if item.enabled
            and item.worker_ids
            and task_type in item.supported_task_types
            and all(
                self.departments.workers.get(worker_id, mission.workspace_id)
                is not None
                for worker_id in item.worker_ids
            )
        ]
        if not candidates:
            raise ValueError("no eligible department")
        candidates.sort(
            key=lambda item: (
                0 if item.lead_worker_id else 1,
                item.department_type,
                item.department_id,
            )
        )
        selected = candidates[0]
        worker_ids = list(selected.worker_ids)
        if selected.lead_worker_id is not None:
            worker_ids.remove(selected.lead_worker_id)
            worker_ids.insert(0, selected.lead_worker_id)
        return DepartmentSelection(
            selected.department_id,
            tuple(worker_ids),
            selected.lead_worker_id,
            reason,
        )


class DepartmentWorkflow:
    """Mission 100 composition over existing Department/Worker/Pipeline layers."""

    def __init__(
        self,
        department_manager,
        pipeline_executor,
        *,
        execution_history=None,
        logger=None,
        usage_engine=None,
        retry_executor=None,
        settings_manager=None,
        orchestrator_factory=None,
    ):
        if not isinstance(department_manager, DepartmentManager):
            raise TypeError("department_manager must use DepartmentManager")
        if not callable(pipeline_executor):
            raise TypeError("pipeline_executor must be callable")
        self.departments = department_manager
        self.selector = DepartmentSelector(department_manager)
        self.pipeline_executor = pipeline_executor
        self.history = execution_history
        self.logger = logger
        self.usage_engine = usage_engine
        self.retry = retry_executor
        self.settings = settings_manager
        self.orchestrator_factory = orchestrator_factory

    def execute(self, mission, task_type, department_id=None):
        if not isinstance(mission, Mission):
            return self._failure(None, task_type, "WorkflowError: InvalidMission")
        try:
            task_type = _task_type(task_type)
            selection = self.selector.select(
                mission, task_type, department_id
            )
        except Exception as error:
            safe_log(
                self.logger,
                "DEPARTMENT_SELECTION_FAILED",
                "DepartmentWorkflow",
                level=LogLevel.ERROR,
                workspace_id=mission.workspace_id,
                mission_id=mission.id,
                status="FAILED",
                error=f"SelectionError: {type(error).__name__}",
            )
            return self._failure(
                mission, task_type, "SelectionError: NoEligibleDepartment"
            )
        safe_log(
            self.logger,
            "DEPARTMENT_SELECTED",
            "DepartmentWorkflow",
            workspace_id=mission.workspace_id,
            mission_id=mission.id,
            status="SELECTED",
            metadata={
                "department_id": selection.department_id,
                "worker_count": len(selection.selected_worker_ids),
            },
        )
        registrations = [
            self.departments.workers.get(worker_id, mission.workspace_id)
            for worker_id in selection.selected_worker_ids
        ]
        if any(item is None for item in registrations):
            return self._failure(
                mission, task_type, "SelectionError: WorkerUnavailable",
                selection,
            )
        workers = [item.worker for item in registrations]
        orchestrator = self._orchestrator(workers)
        try:
            collaboration = orchestrator.run(mission).to_dict()
        except Exception as error:
            return self._failure(
                mission, task_type,
                f"CollaborationError: {type(error).__name__}", selection,
            )
        self._record_collaboration(mission, collaboration)
        if collaboration.get("status") != "COMPLETED":
            return self._failure(
                mission, task_type, "CollaborationError: WorkerFailure",
                selection, collaboration,
            )

        task = Task(
            "Department workflow request",
            {"mission_id": mission.id, "department_id": selection.department_id},
            workspace_id=mission.workspace_id,
        )
        task.task_type = task_type
        task.pipeline = "Department Workflow"
        task.start()
        retry = self._retry(mission.workspace_id)

        def operation(previous):
            try:
                value = self.pipeline_executor(task, selection, previous)
            except Exception as error:
                return self._pipeline_failure(
                    task, f"PipelineError: {type(error).__name__}"
                )
            return self._validate_pipeline_result(
                value, task, mission.workspace_id
            )

        pipeline, retry_state = retry.execute(
            operation,
            recovery=True,
            workspace_id=mission.workspace_id,
            mission_id=mission.id,
            execution_id=task.id,
        )
        final = self._result(
            mission, task_type, selection, collaboration, pipeline,
            retry_state.to_dict(), task.id,
        )
        if final["status"] == PipelineStatus.SUCCESS:
            task.complete(final)
        else:
            task.fail(final)
        self._record(task)
        self._record_usage(task, mission, pipeline)
        safe_log(
            self.logger,
            (
                "DEPARTMENT_WORKFLOW_COMPLETED"
                if final["status"] == PipelineStatus.SUCCESS
                else "DEPARTMENT_WORKFLOW_FAILED"
            ),
            "DepartmentWorkflow",
            level=(
                LogLevel.INFO
                if final["status"] == PipelineStatus.SUCCESS
                else LogLevel.ERROR
            ),
            workspace_id=mission.workspace_id,
            mission_id=mission.id,
            execution_id=task.id,
            status=final["status"],
            error=final.get("error"),
            usage=self._usage(pipeline),
            metadata={"department_id": selection.department_id},
        )
        return final

    def _orchestrator(self, workers):
        if self.orchestrator_factory is not None:
            orchestrator = self.orchestrator_factory(workers)
        else:
            orchestrator = CollaborationOrchestrator(workers)
        if not isinstance(orchestrator, CollaborationOrchestrator):
            raise TypeError("orchestrator_factory returned invalid value")
        return orchestrator

    def _record_collaboration(self, mission, collaboration):
        if self.history is None:
            return
        try:
            class ResultView:
                def to_dict(self):
                    return collaboration

            self.history.record_collaboration(mission, ResultView())
        except Exception:
            safe_log(
                self.logger,
                "DEPARTMENT_HISTORY_FAILED",
                "DepartmentWorkflow",
                level=LogLevel.WARNING,
                workspace_id=mission.workspace_id,
                mission_id=mission.id,
                status="FAILED",
                error="HistoryError: WriteFailure",
            )

    def _retry(self, workspace_id):
        if self.retry is not None:
            return self.retry
        if self.settings is not None:
            return RetryExecutor(
                self.settings.retry_policy(workspace_id), logger=self.logger
            )
        return RetryExecutor(logger=self.logger)

    def _record(self, task):
        if self.history is None:
            return
        try:
            self.history.record(task)
        except Exception:
            safe_log(
                self.logger,
                "DEPARTMENT_HISTORY_FAILED",
                "DepartmentWorkflow",
                level=LogLevel.WARNING,
                workspace_id=task.workspace_id,
                execution_id=task.id,
                status="FAILED",
                error="HistoryError: WriteFailure",
            )

    def _record_usage(self, task, mission, pipeline):
        if self.usage_engine is None:
            return
        self.usage_engine.record_safe(
            mission.workspace_id,
            task.id,
            self._usage(pipeline),
            mission_id=mission.id,
        )

    @staticmethod
    def _validate_pipeline_result(value, task, workspace_id):
        if not isinstance(value, dict):
            return DepartmentWorkflow._pipeline_failure(
                task, "PipelineError: InvalidResult"
            )
        status = value.get("status")
        if status not in {
            PipelineStatus.SUCCESS, PipelineStatus.FAILED,
            PipelineStatus.TIMED_OUT,
        }:
            return DepartmentWorkflow._pipeline_failure(
                task, "PipelineError: InvalidStatus"
            )
        artifacts = value.get("artifacts", [])
        if not isinstance(artifacts, list) or any(
            not isinstance(item, dict)
            or item.get("workspace_id", workspace_id) != workspace_id
            or "path" in item
            for item in artifacts
        ):
            return DepartmentWorkflow._pipeline_failure(
                task, "WorkspaceError: ArtifactMismatch"
            )
        data = value.get("data")
        usage = (
            data.get("provider_usage")
            if isinstance(data, dict) and isinstance(data.get("provider_usage"), dict)
            else None
        )
        return {
            "status": status,
            "pipeline": value.get("pipeline") or "Injected Pipeline",
            "task": "Department workflow request",
            "task_id": task.id,
            "task_type": task.task_type,
            "data": {
                "provider_usage": _usage(usage),
            } if usage is not None else {},
            "artifacts": [
                {
                    key: item[key] for key in (
                        "artifact_id", "artifact_type", "filename",
                        "workspace_id", "mission_id", "stage", "status",
                    ) if key in item
                }
                for item in artifacts
            ],
            "error": (
                None if status == PipelineStatus.SUCCESS
                else _safe_error(value.get("error"))
            ),
        }

    @staticmethod
    def _pipeline_failure(task, error):
        return PipelineResult(
            PipelineStatus.FAILED,
            "Injected Pipeline",
            "Department workflow request",
            task.task_type,
            data={},
            error=error,
        ).to_dict()

    @staticmethod
    def _usage(pipeline):
        data = pipeline.get("data") if isinstance(pipeline, dict) else None
        usage = data.get("provider_usage") if isinstance(data, dict) else None
        return _usage(usage)

    @staticmethod
    def _result(
        mission, task_type, selection, collaboration, pipeline, retry,
        execution_id,
    ):
        status = pipeline.get("status", PipelineStatus.FAILED)
        return PipelineResult(
            status,
            "Department Workflow",
            "Department workflow request",
            task_type,
            data={
                "workspace_id": mission.workspace_id,
                "mission_id": mission.id,
                "execution_id": execution_id,
                "selection": selection.to_dict(),
                "collaboration": {
                    "status": collaboration.get("status"),
                    "worker_results": [
                        {
                            "worker": item.get("worker"),
                            "status": item.get("status"),
                            "usage": _usage(item.get("usage")),
                            "error": _safe_error(item.get("error")),
                        }
                        for item in collaboration.get("worker_results", [])
                    ],
                },
                "pipeline": {
                    "status": pipeline.get("status"),
                    "pipeline": pipeline.get("pipeline"),
                    "usage": DepartmentWorkflow._usage(pipeline),
                },
                "retry": retry,
            },
            artifacts=pipeline.get("artifacts", []),
            error=(
                None if status == PipelineStatus.SUCCESS
                else _safe_error(pipeline.get("error"))
            ),
        ).to_dict()

    @staticmethod
    def _failure(
        mission, task_type, error, selection=None, collaboration=None,
    ):
        workspace_id = mission.workspace_id if isinstance(mission, Mission) else None
        mission_id = mission.id if isinstance(mission, Mission) else None
        result = PipelineResult(
            PipelineStatus.FAILED,
            "Department Workflow",
            "Department workflow request",
            task_type if isinstance(task_type, str) else None,
            data={
                "workspace_id": workspace_id,
                "mission_id": mission_id,
                "selection": selection.to_dict() if selection else None,
                "collaboration": {
                    "status": collaboration.get("status"),
                } if isinstance(collaboration, dict) else None,
                "retry": {"retryable": False},
            },
            error=_safe_error(error),
        ).to_dict()
        return result


def _task_type(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("task_type must be non-empty")
    return value.strip().upper()


def _usage(value):
    if not isinstance(value, dict):
        return None
    allowed = (
        "provider", "model", "input_tokens", "output_tokens", "total_tokens",
        "estimated_cost_usd",
    )
    result = {
        key: value[key] for key in allowed
        if key in value and value[key] is not None
    }
    return result or None


def _safe_error(value):
    if value is None:
        return None
    if not isinstance(value, str) or ":" not in value:
        return "WorkflowError: ReportedFailure"
    prefix, category = (item.strip() for item in value.split(":", 1))
    if (
        not prefix.endswith("Error")
        or not category.replace("_", "").isalnum()
    ):
        return "WorkflowError: ReportedFailure"
    return f"{prefix}: {category}"
