from dataclasses import asdict, dataclass
import uuid

from core.task_queue import JobStatus


@dataclass(frozen=True)
class Batch:
    batch_id: str
    workspace_id: str
    job_ids: tuple[str, ...]
    status: str

    def to_dict(self):
        value = asdict(self)
        value["job_ids"] = list(self.job_ids)
        return value


class BatchManager:
    def __init__(self, queue, repository, max_items=100):
        if not isinstance(max_items, int) or max_items < 1:
            raise ValueError("max_items must be positive")
        self.queue = queue
        self.repository = repository
        self.max_items = max_items

    def create(self, workspace_id, items, idempotency_key):
        values = list(items)
        if not values or len(values) > self.max_items:
            raise ValueError("batch item count is invalid")
        existing = self.repository.get("batch-key", idempotency_key, workspace_id)
        if existing:
            return self.get(existing["batch_id"], workspace_id)
        jobs = []
        for index, item in enumerate(values):
            if not isinstance(item, dict):
                raise ValueError("batch items must be dictionaries")
            jobs.append(
                self.queue.enqueue(
                    workspace_id,
                    item["mission_id"],
                    item["target_id"],
                    f"{idempotency_key}:{index}",
                    retry_state=item.get("retry_state"),
                )
            )
        batch = Batch(uuid.uuid4().hex, workspace_id, tuple(
            job.job_id for job in jobs
        ), JobStatus.PENDING)
        self._save(batch)
        self.repository.save(
            "batch-key", idempotency_key, workspace_id,
            {"batch_id": batch.batch_id}
        )
        return batch

    def get(self, batch_id, workspace_id):
        value = self.repository.get("batch", batch_id, workspace_id)
        if not value:
            return None
        jobs = [
            self.queue.get(job_id, workspace_id) for job_id in value["job_ids"]
        ]
        statuses = {job.status for job in jobs if job is not None}
        if statuses == {JobStatus.COMPLETED}:
            status = JobStatus.COMPLETED
        elif JobStatus.RUNNING in statuses:
            status = JobStatus.RUNNING
        elif JobStatus.PENDING in statuses:
            status = JobStatus.PENDING
        else:
            status = JobStatus.FAILED
        batch = Batch(batch_id, workspace_id, tuple(value["job_ids"]), status)
        self._save(batch)
        return batch

    def summary(self, batch_id, workspace_id):
        batch = self.get(batch_id, workspace_id)
        if batch is None:
            return None
        jobs = [self.queue.get(job_id, workspace_id) for job_id in batch.job_ids]
        return {
            "batch_id": batch.batch_id,
            "workspace_id": workspace_id,
            "status": batch.status,
            "items": [
                {
                    "job_id": job.job_id,
                    "status": job.status,
                    "retry_state": job.retry_state,
                    "result": job.result,
                }
                for job in jobs if job is not None
            ],
        }

    def list(self, workspace_id):
        values = []
        for value in self.repository.list("batch", workspace_id):
            if not isinstance(value, dict) or not value.get("batch_id"):
                continue
            batch = self.get(value["batch_id"], workspace_id)
            if batch is not None:
                values.append(batch)
        return values

    def _save(self, batch):
        self.repository.save(
            "batch", batch.batch_id, batch.workspace_id, batch.to_dict()
        )
