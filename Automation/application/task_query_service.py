from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class TaskQueryResponse:
    task_id: str
    found: bool
    task: dict | None
    history: dict | None
    status: str | None
    pipeline: str | None
    created_at: str | None
    usage: dict
    artifacts: list

    def to_dict(self):
        return asdict(self)


class TaskQueryService:
    """Builds repository-neutral, serializable task query responses."""

    def __init__(self, history, artifact_manager, task_lookup=None):
        self.history = history
        self.artifact_manager = artifact_manager
        self.task_lookup = task_lookup

    def get(self, task_id):
        if not isinstance(task_id, str) or not task_id:
            return self._missing(task_id)

        record = next(
            (item for item in self.history.query() if item.get("task_id") == task_id),
            None,
        )
        task = self.task_lookup(task_id) if self.task_lookup else None
        if record is None and task is None:
            return self._missing(task_id)

        task_data = dict(task.to_dict()) if task is not None else self._task_from_record(record)
        result = record.get("result") if record else task_data.get("result")
        result = result if isinstance(result, dict) else {}
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        artifacts = self._artifacts(result)
        return TaskQueryResponse(
            task_id=task_id,
            found=True,
            task=task_data,
            history=dict(record) if record else None,
            status=(record or task_data).get("status"),
            pipeline=(record or task_data).get("pipeline"),
            created_at=(record or task_data).get("created_at"),
            usage=dict(data.get("provider_usage") or {}),
            artifacts=artifacts,
        ).to_dict()

    def list(self, **filters):
        return [self._from_record(record) for record in self.history.query(**filters)]

    def _from_record(self, record):
        task_id = record.get("task_id")
        task = self.task_lookup(task_id) if self.task_lookup else None
        task_data = dict(task.to_dict()) if task is not None else self._task_from_record(record)
        result = record.get("result") if isinstance(record.get("result"), dict) else {}
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        return TaskQueryResponse(
            task_id=task_id,
            found=True,
            task=task_data,
            history=dict(record),
            status=record.get("status"),
            pipeline=record.get("pipeline"),
            created_at=record.get("created_at"),
            usage=dict(data.get("provider_usage") or {}),
            artifacts=self._artifacts(result),
        ).to_dict()

    @staticmethod
    def _task_from_record(record):
        return {
            "id": record.get("task_id"),
            "task": record.get("task"),
            "parameters": dict(record.get("parameters") or {}),
            "workspace_id": record.get("workspace_id", "default"),
            "parent_task_id": record.get("parent_task_id"),
            "retry_count": record.get("retry_count", 0),
            "max_retries": record.get("max_retries", 0),
            "timeout_seconds": record.get("timeout_seconds"),
            "last_error_type": record.get("last_error_type"),
            "status": record.get("status"),
            "created_at": record.get("created_at"),
            "queued_at": record.get("queued_at"),
            "started_at": record.get("started_at"),
            "completed_at": record.get("completed_at"),
            "result": record.get("result"),
            "task_type": record.get("task_type"),
            "pipeline": record.get("pipeline"),
        }

    def _artifacts(self, result):
        records = result.get("artifacts") if isinstance(result, dict) else []
        artifacts = []
        for record in records if isinstance(records, list) else []:
            artifact_id = record.get("artifact_id") if isinstance(record, dict) else None
            stored = self.artifact_manager.get(artifact_id) if artifact_id else None
            artifacts.append(dict(stored or record))
        return artifacts

    @staticmethod
    def _missing(task_id):
        return TaskQueryResponse(
            task_id=task_id,
            found=False,
            task=None,
            history=None,
            status=None,
            pipeline=None,
            created_at=None,
            usage={},
            artifacts=[],
        ).to_dict()
