from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CreateTaskRequest:
    task_text: str
    parameters: dict | None = None
    parent_task_id: str | None = None
    max_retries: int = 0
    timeout_seconds: float | None = None

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, dict):
            raise TypeError("request payload must be a dictionary")
        task_text = payload.get("task_text")
        if not isinstance(task_text, str) or not task_text.strip():
            raise ValueError("task_text must be a non-empty string")
        parameters = payload.get("parameters")
        if parameters is not None and not isinstance(parameters, dict):
            raise TypeError("parameters must be a dictionary or None")
        return cls(
            task_text=task_text.strip(),
            parameters=dict(parameters) if parameters is not None else None,
            parent_task_id=payload.get("parent_task_id"),
            max_retries=payload.get("max_retries", 0),
            timeout_seconds=payload.get("timeout_seconds"),
        )


@dataclass(frozen=True)
class ListTasksRequest:
    status: str | None = None
    pipeline: str | None = None
    task_type: str | None = None
    start_at: str | None = None
    end_at: str | None = None
    limit: int | None = None
    offset: int = 0

    @classmethod
    def from_dict(cls, payload=None):
        if payload is None:
            return cls()
        if not isinstance(payload, dict):
            raise TypeError("query payload must be a dictionary")
        return cls(**{key: payload[key] for key in cls.__dataclass_fields__ if key in payload})

    def to_filters(self):
        return {
            key: value
            for key, value in asdict(self).items()
            if value is not None
        }


@dataclass(frozen=True)
class TaskResponse:
    data: dict

    def to_dict(self):
        return dict(self.data)


@dataclass(frozen=True)
class TaskListResponse:
    items: list

    def to_dict(self):
        return {"items": [dict(item) for item in self.items]}
