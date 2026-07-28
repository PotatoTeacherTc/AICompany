from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re

from core.collaboration_worker import BaseWorker
from core.mission import Mission, MissionState
from core.status import PipelineStatus
from core.worker_context import ContextBuilder, SENSITIVE_CONTEXT_KEYS
from core.worker_result import WorkerResult
from core.worker_result_validator import WorkerResultValidator


@dataclass(frozen=True)
class CollaborationResult:
    mission: dict
    status: str
    worker_results: tuple[dict, ...]
    started_at: str
    completed_at: str

    def to_dict(self):
        result = asdict(self)
        result["worker_results"] = [
            dict(worker_result) for worker_result in self.worker_results
        ]
        return result


class CollaborationOrchestrator:
    def __init__(
        self,
        workers,
        context_builder=None,
        validator=None,
        execution_history=None,
    ):
        self.workers = list(workers or [])
        if not self.workers or not all(
            isinstance(worker, BaseWorker) for worker in self.workers
        ):
            raise ValueError("workers must contain at least one BaseWorker")
        names = [worker.name for worker in self.workers]
        if len(names) != len(set(names)):
            raise ValueError("worker names must be unique")
        self.context_builder = context_builder or ContextBuilder()
        self.validator = validator or WorkerResultValidator()
        self.execution_history = execution_history

    def run(self, mission):
        if not isinstance(mission, Mission):
            raise ValueError("mission must use the Mission contract")
        if mission.state != MissionState.PENDING:
            raise ValueError("mission must be pending")
        if mission.is_locked:
            raise ValueError("mission is locked by another worker")

        started_at = datetime.now(timezone.utc).isoformat()
        active_mission = mission.transition_to(MissionState.IN_PROGRESS)
        results = []
        for worker in self.workers:
            locked_mission = active_mission.acquire_lock(worker.name)
            result = self.run_worker(locked_mission, worker)
            results.append(result)
            active_mission = locked_mission.release_lock(worker.name)

        failed = any(result.status != PipelineStatus.SUCCESS for result in results)
        final_state = MissionState.FAILED if failed else MissionState.COMPLETED
        final_mission = active_mission.transition_to(final_state)
        completed_at = datetime.now(timezone.utc).isoformat()
        collaboration_result = CollaborationResult(
            mission=self._mission_summary(final_mission),
            status=final_state,
            worker_results=tuple(result.to_dict() for result in results),
            started_at=started_at,
            completed_at=completed_at,
        )
        if self.execution_history is not None:
            self.execution_history.record_collaboration(
                final_mission, collaboration_result
            )
        return collaboration_result

    def run_worker(self, mission, worker):
        if not isinstance(mission, Mission) or not isinstance(worker, BaseWorker):
            raise ValueError("mission and worker contracts are required")
        context = self.context_builder.build(mission)
        if mission.locked_by != worker.name:
            return WorkerResult.create(
                PipelineStatus.FAILED,
                worker.name,
                context,
                error="LockError: MissionLockOwnershipError",
            )
        try:
            result = worker.execute(context)
        except Exception as error:
            result = WorkerResult.create(
                PipelineStatus.FAILED,
                worker.name,
                context,
                error=f"WorkerError: {type(error).__name__}",
            )
        result = self._sanitize_result(context, result)
        validation = self.validator.validate(context, result)
        if validation.valid:
            return result
        return WorkerResult.create(
            PipelineStatus.FAILED,
            worker.name,
            context,
            error=f"ValidationError: {validation.error}",
        )

    @staticmethod
    def _mission_summary(mission):
        return {
            "id": mission.id,
            "workspace_id": mission.workspace_id,
            "state": mission.state,
            "locked_by": mission.locked_by,
            "locked_at": mission.locked_at,
        }

    @staticmethod
    def _sanitize_result(context, result):
        if not isinstance(result, WorkerResult):
            return result
        safe_data = {}
        for key, value in result.data.items():
            if (
                not isinstance(key, str)
                or key.lower() in SENSITIVE_CONTEXT_KEYS
                or not isinstance(value, (str, int, float, bool, type(None)))
            ):
                continue
            safe_data[key] = (
                value.replace(context.objective, "[request redacted]")
                if isinstance(value, str)
                else value
            )
        safe_artifacts = [
            {key: value for key, value in artifact.items() if key != "path"}
            for artifact in result.artifacts
        ]
        safe_error = result.error
        if safe_error is not None and not re.fullmatch(
            r"[A-Za-z]+Error: [A-Za-z][A-Za-z0-9_]*", safe_error
        ):
            safe_error = "WorkerError: ReportedFailure"
        return WorkerResult(
            status=result.status,
            worker=result.worker,
            mission_id=result.mission_id,
            workspace_id=result.workspace_id,
            created_at=result.created_at,
            data=safe_data,
            artifacts=tuple(safe_artifacts),
            usage=result.usage,
            error=safe_error,
        )
