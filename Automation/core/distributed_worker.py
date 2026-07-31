"""External Worker using existing Queue, Job, and PipelineResult contracts."""

from core.task_queue import InProcessJobWorker, JobStatus


class DistributedJobWorker(InProcessJobWorker):
    def __init__(self, queue, lock_manager, worker_id, lock_ttl=30, recovery=None):
        super().__init__(queue, worker_id)
        self.lock_manager = lock_manager
        self.lock_ttl = lock_ttl
        self.recovery = recovery

    def run_once(self, workspace_id):
        if self.recovery is not None:
            self.recovery.promote_due(workspace_id)
        recover = getattr(self.queue, "recover_abandoned", None)
        if recover:
            recover(workspace_id, self.lock_manager)
        job = self.queue.claim(workspace_id, self.worker_id)
        if job is None:
            return None
        lease = self.lock_manager.acquire(workspace_id, job.job_id, self.lock_ttl)
        if lease is None:
            return None
        try:
            callback = self.targets.get(job.target_id)
            if callback is None:
                failed = self.queue.fail(
                    job.job_id, workspace_id, self.worker_id,
                    {"status": JobStatus.FAILED, "error": "JobError: TargetUnavailable"},
                    retry_state={"retryable": False},
                )
                return self.recovery.after_failure(failed) if self.recovery is not None else failed
            try:
                result = callback(job)
            except Exception as error:
                result = {"status": JobStatus.FAILED, "error": f"TaskError: {type(error).__name__}"}
            if isinstance(result, dict) and result.get("status") in {JobStatus.COMPLETED, "SUCCESS"}:
                return self.queue.complete(job.job_id, workspace_id, self.worker_id, result)
            retry_state = (result.get("data") or {}).get("retry") if isinstance(result, dict) else None
            if retry_state:
                retry_state = {**(job.retry_state or {}), **retry_state}
            failed = self.queue.fail(
                job.job_id, workspace_id, self.worker_id,
                result if isinstance(result, dict) else {},
                retry_state=retry_state or {"retryable": False},
            )
            return self.recovery.after_failure(failed) if self.recovery is not None else failed
        finally:
            self.lock_manager.release(lease)
