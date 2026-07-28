from collections import deque
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import uuid

from core.structured_logging import LogLevel, safe_log


class TaskQueue:

    def __init__(self, history=None, max_retries=0):

        self.queue = deque()

        self.history = history

        if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")

        self.max_retries = max_retries


    def add(self, task):

        if task.max_retries == 0:
            task.max_retries = self.max_retries

        task.queue()

        self.queue.append(task)

        self._record(task)

        print(
            f"Queue: Task added "
            f"[{task.id}] {task.task_text}"
        )


    def get_next(self):

        if not self.queue:

            return None

        return self.queue.popleft()


    def skip(self, task, result=None):

        try:
            self.queue.remove(task)
        except ValueError:
            pass

        task.skip(result)

        self._record(task)


    def cancel(self, task, result=None):

        if task.is_terminal():
            return False

        try:
            self.queue.remove(task)
        except ValueError:
            return False

        task.cancel(result)

        self._record(task)

        return True


    def retry(self, task, error_type):

        if not task.can_retry(error_type):
            return False

        task.schedule_retry(error_type)

        self.queue.append(task)

        self._record(task)

        return True


    def _record(self, task):

        if self.history is not None:
            self.history.record(task)


    def is_empty(self):

        return len(self.queue) == 0


    def size(self):

        return len(self.queue)


class JobStatus:
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class Job:
    job_id: str
    workspace_id: str
    mission_id: str
    target_id: str
    status: str
    created_at: str
    idempotency_key: str
    retry_state: dict | None = None
    result: dict = field(default_factory=dict)
    claimed_by: str | None = None

    def to_dict(self):
        return asdict(self)


class PersistentJobQueue:
    """Repository-backed in-process queue with explicit claim ownership."""

    def __init__(self, repository, workspace_ids=(), logger=None):
        self.repository = repository
        self.logger = logger
        self._jobs = {}
        for workspace_id in workspace_ids:
            for value in repository.list("job", workspace_id):
                job = self._restore(value)
                if job is not None:
                    if job.status == JobStatus.RUNNING:
                        job = replace(job, status=JobStatus.PENDING, claimed_by=None)
                        self._save(job)
                    self._jobs[job.job_id] = job

    def enqueue(
        self, workspace_id, mission_id, target_id, idempotency_key, retry_state=None
    ):
        for job in self._jobs.values():
            if (
                job.workspace_id == workspace_id
                and job.idempotency_key == idempotency_key
            ):
                return job
        job = Job(
            uuid.uuid4().hex,
            workspace_id,
            mission_id,
            target_id,
            JobStatus.PENDING,
            datetime.now(timezone.utc).isoformat(),
            idempotency_key,
            retry_state,
        )
        self._jobs[job.job_id] = job
        self._save(job)
        safe_log(
            self.logger, "QUEUE_ENQUEUED", "PersistentJobQueue",
            workspace_id=workspace_id, mission_id=mission_id, job_id=job.job_id,
            status=job.status,
        )
        return job

    def claim(self, workspace_id, worker_id):
        for job in self._jobs.values():
            if job.workspace_id == workspace_id and job.status == JobStatus.PENDING:
                claimed = replace(
                    job, status=JobStatus.RUNNING, claimed_by=worker_id
                )
                self._jobs[job.job_id] = claimed
                self._save(claimed)
                safe_log(
                    self.logger, "QUEUE_CLAIMED", "PersistentJobQueue",
                    workspace_id=workspace_id, mission_id=job.mission_id,
                    job_id=job.job_id, status=claimed.status,
                )
                return claimed
        return None

    def complete(self, job_id, workspace_id, worker_id, result):
        return self._finish(
            job_id, workspace_id, worker_id, JobStatus.COMPLETED, result
        )

    def fail(self, job_id, workspace_id, worker_id, result, retry_state=None):
        return self._finish(
            job_id, workspace_id, worker_id, JobStatus.FAILED, result, retry_state
        )

    def requeue(self, job_id, workspace_id):
        job = self.get(job_id, workspace_id)
        if job is None or job.status != JobStatus.FAILED:
            raise ValueError("failed job not found")
        retryable = (job.retry_state or {}).get("retryable")
        if not retryable:
            raise ValueError("job is not retryable")
        updated = replace(job, status=JobStatus.PENDING, claimed_by=None)
        self._jobs[job_id] = updated
        self._save(updated)
        safe_log(
            self.logger, "QUEUE_REQUEUED", "PersistentJobQueue",
            workspace_id=workspace_id, mission_id=job.mission_id,
            job_id=job_id, status=updated.status,
        )
        return updated

    def get(self, job_id, workspace_id):
        job = self._jobs.get(job_id)
        return job if job and job.workspace_id == workspace_id else None

    def list(self, workspace_id):
        return [job for job in self._jobs.values() if job.workspace_id == workspace_id]

    def _finish(
        self, job_id, workspace_id, worker_id, status, result, retry_state=None
    ):
        job = self.get(job_id, workspace_id)
        if job is None or job.status != JobStatus.RUNNING or job.claimed_by != worker_id:
            raise ValueError("job claim ownership mismatch")
        safe_result = {
            "status": result.get("status"),
            "error": self._safe_error(result.get("error")),
        } if isinstance(result, dict) else {}
        updated = replace(
            job, status=status, result=safe_result,
            retry_state=retry_state, claimed_by=None
        )
        self._jobs[job_id] = updated
        self._save(updated)
        safe_log(
            self.logger,
            "QUEUE_COMPLETED" if status == JobStatus.COMPLETED else "QUEUE_FAILED",
            "PersistentJobQueue",
            level=LogLevel.INFO if status == JobStatus.COMPLETED else LogLevel.ERROR,
            workspace_id=workspace_id,
            mission_id=job.mission_id,
            job_id=job_id,
            status=status,
            error=safe_result.get("error"),
            metadata={
                "retryable": (retry_state or {}).get("retryable")
                if isinstance(retry_state, dict) else None,
            },
        )
        return updated

    @staticmethod
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

    def _save(self, job):
        self.repository.save("job", job.job_id, job.workspace_id, job.to_dict())

    @staticmethod
    def _restore(value):
        try:
            status = value["status"]
            if status not in {
                JobStatus.PENDING, JobStatus.RUNNING,
                JobStatus.COMPLETED, JobStatus.FAILED,
            }:
                return None
            return Job(**value)
        except (KeyError, TypeError, ValueError):
            return None


class InProcessJobWorker:
    def __init__(self, queue, worker_id="in-process-worker"):
        self.queue = queue
        self.worker_id = worker_id
        self.targets = {}

    def register_target(self, target_id, callback):
        if not callable(callback):
            raise ValueError("job target must be callable")
        self.targets[target_id] = callback

    def run_once(self, workspace_id):
        job = self.queue.claim(workspace_id, self.worker_id)
        if job is None:
            return None
        callback = self.targets.get(job.target_id)
        if callback is None:
            return self.queue.fail(
                job.job_id, workspace_id, self.worker_id,
                {"status": JobStatus.FAILED, "error": "JobError: TargetUnavailable"},
                retry_state={"retryable": False},
            )
        try:
            result = callback(job)
        except Exception as error:
            result = {
                "status": JobStatus.FAILED,
                "error": f"TaskError: {type(error).__name__}",
            }
        if isinstance(result, dict) and result.get("status") in {
            JobStatus.COMPLETED, "SUCCESS"
        }:
            return self.queue.complete(
                job.job_id, workspace_id, self.worker_id, result
            )
        retry_state = (
            (result.get("data") or {}).get("retry")
            if isinstance(result, dict)
            else None
        )
        return self.queue.fail(
            job.job_id, workspace_id, self.worker_id,
            result if isinstance(result, dict) else {},
            retry_state=retry_state or {"retryable": False},
        )
