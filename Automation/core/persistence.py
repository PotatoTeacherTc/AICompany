import json
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path


SCHEMA_VERSION = 1
_SENSITIVE = ("prompt", "objective", "api_key", "oauth", "token", "password", "secret")
_USAGE_KEYS = {"input_tokens", "output_tokens", "total_tokens"}
_OPAQUE_REFERENCE_KEYS = {"token_reference"}
_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|/)")


class StateRepository(ABC):
    @abstractmethod
    def save(self, kind, record_id, workspace_id, payload):
        pass

    @abstractmethod
    def get(self, kind, record_id, workspace_id):
        pass

    @abstractmethod
    def list(self, kind, workspace_id):
        pass


class InMemoryStateRepository(StateRepository):
    def __init__(self):
        self._records = {}

    def save(self, kind, record_id, workspace_id, payload):
        record = _record(kind, record_id, workspace_id, payload)
        self._records[(kind, record_id)] = record
        return dict(record)

    def get(self, kind, record_id, workspace_id):
        record = self._records.get((kind, record_id))
        return _payload(record, workspace_id)

    def list(self, kind, workspace_id):
        return [
            dict(record["payload"])
            for record in self._records.values()
            if record["kind"] == kind and record["workspace_id"] == workspace_id
        ]


class JsonStateRepository(StateRepository):
    """Atomic, versioned local persistence. Corrupt records are ignored safely."""

    def __init__(self, repository_file):
        self.repository_file = Path(repository_file)
        self.repository_file.parent.mkdir(parents=True, exist_ok=True)
        self._records = self._load()

    def save(self, kind, record_id, workspace_id, payload):
        record = _record(kind, record_id, workspace_id, payload)
        self._records[(kind, record_id)] = record
        self._flush()
        return dict(record)

    def get(self, kind, record_id, workspace_id):
        return _payload(self._records.get((kind, record_id)), workspace_id)

    def list(self, kind, workspace_id):
        return [
            dict(record["payload"])
            for record in self._records.values()
            if record["kind"] == kind and record["workspace_id"] == workspace_id
        ]

    def _load(self):
        if not self.repository_file.exists():
            return {}
        try:
            value = json.loads(self.repository_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        records = value.get("records", []) if isinstance(value, dict) else []
        loaded = {}
        for record in records:
            if not _valid_record(record):
                continue
            loaded[(record["kind"], record["record_id"])] = record
        return loaded

    def _flush(self):
        temporary = self.repository_file.with_suffix(
            self.repository_file.suffix + ".tmp"
        )
        value = {
            "schema_version": SCHEMA_VERSION,
            "records": list(self._records.values()),
        }
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.repository_file)


def _record(kind, record_id, workspace_id, payload):
    for value, name in (
        (kind, "kind"), (record_id, "record_id"), (workspace_id, "workspace_id")
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dictionary")
    safe = _sanitize(payload)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "record_id": record_id,
        "workspace_id": workspace_id,
        "payload": safe,
    }


def _sanitize(value):
    if isinstance(value, dict):
        return {
            key: _sanitize(item)
            for key, item in value.items()
            if isinstance(key, str)
            and (
                key.lower() in _USAGE_KEYS | _OPAQUE_REFERENCE_KEYS
                or not any(token in key.lower() for token in _SENSITIVE)
            )
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, (str, int, float, bool, type(None))):
        if isinstance(value, str) and _ABSOLUTE_PATH.match(value):
            return "[internal reference omitted]"
        return value
    raise ValueError("payload contains a non-serializable value")


def sanitize_for_read(value):
    """Return a recursively redacted copy for read-only external contracts."""
    return _sanitize(value)


def _valid_record(record):
    return (
        isinstance(record, dict)
        and record.get("schema_version") == SCHEMA_VERSION
        and all(
            isinstance(record.get(field), str) and record[field]
            for field in ("kind", "record_id", "workspace_id")
        )
        and isinstance(record.get("payload"), dict)
    )


def _payload(record, workspace_id):
    if not record or record.get("workspace_id") != workspace_id:
        return None
    return dict(record["payload"])


class PersistenceService:
    """Typed convenience boundary over one shared StateRepository."""

    def __init__(self, repository):
        if not isinstance(repository, StateRepository):
            raise TypeError("repository must implement StateRepository")
        self.repository = repository

    def save_mission(self, mission):
        value = mission.to_dict()
        value.pop("objective", None)
        return self.repository.save("mission", mission.id, mission.workspace_id, value)

    def save_schedule(self, schedule):
        return self.repository.save(
            "schedule", schedule.schedule_id, schedule.workspace_id, schedule.to_dict()
        )

    def save_retry_state(self, state_id, workspace_id, state):
        value = state.to_dict() if hasattr(state, "to_dict") else dict(state)
        return self.repository.save("retry", state_id, workspace_id, value)

    def save_history_record(self, record):
        return self.repository.save(
            "history",
            record.get("task_id") or record.get("mission_id"),
            record.get("workspace_id", "default"),
            record,
        )
