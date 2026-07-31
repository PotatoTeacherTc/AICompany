from core.persistence import sanitize_for_read


class JobExecutionApiService:
    """Workspace-scoped application boundary for persistent execution state."""

    def __init__(
        self, execution_service, history, artifact_manager, usage_engine,
        batch_manager=None,
    ):
        self.execution = execution_service
        self.queue = execution_service.queue
        self.history = history
        self.artifacts = artifact_manager
        self.usage = usage_engine
        self.batches = batch_manager

    def submit(self, workspace_id, payload):
        if not isinstance(payload, dict):
            raise ValueError("invalid payload")
        return self._job(self.execution.submit(
            workspace_id,
            payload.get("mission_id"),
            payload.get("target_id"),
            payload.get("idempotency_key"),
            retry_state=payload.get("retry_state"),
        ))

    def list_jobs(self, workspace_id, status=None, limit=50, offset=0):
        limit, offset = _page(limit, offset)
        jobs = self.queue.list(workspace_id)
        if status is not None:
            jobs = [job for job in jobs if job.status == status]
        jobs.sort(key=lambda job: job.created_at, reverse=True)
        return {
            "items": [self._job(job) for job in jobs[offset:offset + limit]],
            "total": len(jobs), "limit": limit, "offset": offset,
        }

    def get_job(self, workspace_id, job_id):
        job = self.queue.get(job_id, workspace_id)
        return self._job(job, include_result=True) if job else None

    def cancel(self, workspace_id, job_id):
        return self._job(self.queue.cancel(job_id, workspace_id))

    def retry(self, workspace_id, job_id):
        return self._job(self.queue.requeue(job_id, workspace_id))

    def list_executions(
        self, workspace_id, status=None, pipeline=None, task_type=None,
        start_at=None, end_at=None, limit=50, offset=0,
    ):
        limit, offset = _page(limit, offset)
        records = self.history.query(
            workspace_id=workspace_id, status=status, pipeline=pipeline,
            task_type=task_type, start_at=start_at, end_at=end_at,
            limit=limit, offset=offset,
        )
        return {"items": [self._execution(value) for value in records]}

    def get_execution(self, workspace_id, execution_id):
        records = self.history.query(workspace_id=workspace_id)
        value = next(
            (record for record in records
             if record.get("task_id") == execution_id),
            None,
        )
        return self._execution(value) if value else None

    def list_batches(self, workspace_id):
        if self.batches is None:
            return {"items": []}
        return {
            "items": [
                self.batches.summary(batch.batch_id, workspace_id)
                for batch in self.batches.list(workspace_id)
            ]
        }

    def get_batch(self, workspace_id, batch_id):
        return (
            self.batches.summary(batch_id, workspace_id)
            if self.batches is not None else None
        )

    def _job(self, job, include_result=False):
        value = {
            "job_id": job.job_id,
            "workspace_id": job.workspace_id,
            "mission_id": job.mission_id,
            "target_id": job.target_id,
            "status": job.status,
            "created_at": job.created_at,
            "retry_state": sanitize_for_read(job.retry_state),
        }
        if include_result:
            execution = self.get_execution(job.workspace_id, job.job_id)
            value["execution"] = execution
            value["result"] = sanitize_for_read(job.result)
            value["usage"] = [
                record for record in self.usage.query(job.workspace_id)
                if record.get("execution_id") == job.job_id
            ]
        return value

    @staticmethod
    def _execution(value):
        safe = sanitize_for_read(value)
        result = safe.get("result") if isinstance(safe, dict) else None
        if isinstance(result, dict):
            result.pop("task", None)
        if isinstance(safe, dict):
            safe.pop("task", None)
            safe.pop("parameters", None)
        return safe


def _page(limit, offset):
    if (
        not isinstance(limit, int) or isinstance(limit, bool)
        or not 1 <= limit <= 100
        or not isinstance(offset, int) or isinstance(offset, bool) or offset < 0
    ):
        raise ValueError("invalid pagination")
    return limit, offset
