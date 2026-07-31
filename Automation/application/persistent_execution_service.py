import re

from core.status import PipelineStatus


_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")
_TERMINAL_STATUSES = {
    PipelineStatus.SUCCESS,
    PipelineStatus.FAILED,
    PipelineStatus.TIMED_OUT,
    PipelineStatus.CANCELLED,
}


class PersistentExecutionService:
    """Composes the existing persistent Queue, Worker, and result stores."""

    def __init__(
        self,
        queue,
        worker,
        execution_history,
        artifact_manager,
        usage_engine,
        quota_engine=None,
    ):
        self.queue = queue
        self.worker = worker
        self.execution_history = execution_history
        self.artifact_manager = artifact_manager
        self.usage_engine = usage_engine
        self.quota_engine = quota_engine

    def register_target(self, target_id, callback):
        target_id = _identifier(target_id, "target_id")
        if not callable(callback):
            raise ValueError("target callback must be callable")
        self.worker.register_target(
            target_id,
            lambda job: self._execute(job, callback),
        )

    def submit(
        self,
        workspace_id,
        mission_id,
        target_id,
        idempotency_key,
        retry_state=None,
    ):
        workspace_id = _identifier(workspace_id, "workspace_id")
        idempotency_key = _identifier(idempotency_key, "idempotency_key")
        if self.quota_engine is not None:
            self.quota_engine.reserve(workspace_id, idempotency_key)
        return self.queue.enqueue(
            workspace_id,
            _identifier(mission_id, "mission_id"),
            _identifier(target_id, "target_id"),
            idempotency_key,
            retry_state=retry_state,
        )

    def run_once(self, workspace_id):
        return self.worker.run_once(_identifier(workspace_id, "workspace_id"))

    def _execute(self, job, callback):
        try:
            if self.quota_engine is not None:
                self.quota_engine.assert_allowed(job.workspace_id)
            result = callback(job)
            result = self._validated_result(result)
            artifacts = self._artifacts(job.workspace_id, result)
        except Exception as error:
            result = {
                "status": PipelineStatus.FAILED,
                "pipeline": "Persistent Execution",
                "task_type": "JOB",
                "data": {"retry": {"retryable": False}},
                "artifacts": [],
                "error": f"JobError: {type(error).__name__}",
            }
            artifacts = []

        self._record_history(job, result, artifacts)
        self._record_usage(job, result)
        return result

    @staticmethod
    def _validated_result(result):
        if not isinstance(result, dict):
            raise TypeError("pipeline result must be a dictionary")
        if result.get("status") not in _TERMINAL_STATUSES:
            raise ValueError("pipeline result status is invalid")
        if not isinstance(result.get("pipeline"), str) or not result["pipeline"]:
            raise ValueError("pipeline result pipeline is invalid")
        if any(token in result["pipeline"] for token in ("\\", "/", "\n", "\r")):
            raise ValueError("pipeline result pipeline is invalid")
        task_type = result.get("task_type")
        if task_type is not None:
            _identifier(task_type, "task_type")
        if not isinstance(result.get("data", {}), dict):
            raise ValueError("pipeline result data is invalid")
        if not isinstance(result.get("artifacts", []), list):
            raise ValueError("pipeline result artifacts are invalid")
        value = dict(result)
        value["error"] = _safe_error(result.get("error"))
        return value

    def _artifacts(self, workspace_id, result):
        safe = []
        for value in result.get("artifacts", []):
            if not isinstance(value, dict):
                raise ValueError("artifact result is invalid")
            artifact_id = value.get("artifact_id")
            if not isinstance(artifact_id, str) or not artifact_id:
                raise ValueError("artifact identity is invalid")
            stored = self.artifact_manager.get(artifact_id, workspace_id)
            if stored is None:
                raise ValueError("artifact ownership is invalid")
            safe.append({
                field: stored[field]
                for field in self.artifact_manager.METADATA_FIELDS
                if field in stored
            })
        return safe

    def _record_history(self, job, result, artifacts):
        try:
            self.execution_history.record_persistent_job(job, result, artifacts)
        except Exception:
            pass

    def _record_usage(self, job, result):
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        usage = data.get("provider_usage")
        if usage is None:
            usage = data.get("usage")
        if usage is None:
            return
        retry = data.get("retry") if isinstance(data.get("retry"), dict) else {}
        attempt = retry.get("current_attempt")
        if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 0:
            attempt = (
                (job.retry_state or {}).get("current_attempt", 0)
                if isinstance(job.retry_state, dict)
                else 0
            )
        self.usage_engine.record_safe(
            job.workspace_id,
            job.job_id,
            usage,
            mission_id=job.mission_id,
            usage_id=f"{job.job_id}:{attempt}",
        )


def _identifier(value, field_name):
    if (
        not isinstance(value, str)
        or not value
        or not _SAFE_ID.fullmatch(value)
    ):
        raise ValueError(f"{field_name} is invalid")
    return value


def _safe_error(error):
    if error is None:
        return None
    if not isinstance(error, str) or ":" not in error:
        return "JobError: ReportedFailure"
    prefix, category = (part.strip() for part in error.split(":", 1))
    if (
        prefix not in {"ProviderError", "TaskError", "RetryError", "JobError"}
        or not category.replace("_", "").isalnum()
    ):
        return "JobError: ReportedFailure"
    return f"{prefix}: {category}"
