from abc import ABC, abstractmethod
from datetime import datetime, timezone
import json
from pathlib import PurePosixPath
import re
import uuid


BACKUP_SCHEMA_VERSION = 1
_STATE_KINDS = ("subscription", "billing_account", "invoice", "payment")
_STATE_IDS = {
    "subscription": "subscription_id",
    "billing_account": "billing_account_id",
    "invoice": "invoice_id",
    "payment": "payment_id",
}
_ARTIFACT_FIELDS = {
    "artifact_id", "artifact_type", "mime_type", "filename", "size",
    "created_at", "producer_pipeline", "workspace_id", "mission_id",
    "task_id", "stage", "status", "updated_at", "internal_ref",
}
_SENSITIVE = (
    "prompt", "objective", "api_key", "oauth", "authorization", "cookie",
    "password", "secret", "token", "email", "personal",
)
_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|/)")


class BackupStore(ABC):
    @abstractmethod
    def save(self, backup_id, workspace_id, payload):
        pass

    @abstractmethod
    def get(self, backup_id, workspace_id):
        pass


class InMemoryBackupStore(BackupStore):
    """Fake backup storage for deterministic offline verification."""

    def __init__(self):
        self._items = {}

    def save(self, backup_id, workspace_id, payload):
        self._items[(workspace_id, backup_id)] = str(payload)

    def get(self, backup_id, workspace_id):
        return self._items.get((workspace_id, backup_id))


class BackupService:
    """Workspace-scoped metadata export/restore over existing repositories."""

    def __init__(
        self,
        workspace_repository,
        artifact_repository,
        state_repository,
        store=None,
        clock=None,
    ):
        self.workspaces = workspace_repository
        self.artifacts = artifact_repository
        self.states = state_repository
        self.store = store or InMemoryBackupStore()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def export(self, workspace_id):
        self._workspace_id(workspace_id)
        workspace = self.workspaces.get(workspace_id)
        if workspace is None:
            raise KeyError("workspace_not_found")
        backup_id = uuid.uuid4().hex
        value = {
            "schema_version": BACKUP_SCHEMA_VERSION,
            "backup_id": backup_id,
            "workspace_id": workspace_id,
            "created_at": self.clock().isoformat(),
            "workspace": _sanitize(workspace),
            "artifacts": self._artifact_records(workspace_id),
            "records": self._state_records(workspace_id),
        }
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
        self.store.save(backup_id, workspace_id, payload)
        return {
            "backup_id": backup_id,
            "workspace_id": workspace_id,
            "payload": payload,
        }

    def restore(self, workspace_id, payload, *, overwrite=False):
        self._workspace_id(workspace_id)
        if not isinstance(payload, str) or len(payload) > 2_000_000:
            raise ValueError("invalid_backup")
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ValueError("invalid_backup") from error
        self._validate(value, workspace_id)
        existing = self.workspaces.get(workspace_id)
        if existing is not None and not overwrite:
            raise ValueError("workspace_exists")

        workspace = _sanitize(value["workspace"])
        workspace["workspace_id"] = workspace_id
        artifacts = [
            _artifact(artifact, workspace_id)
            for artifact in value["artifacts"]
        ]
        records = [
            _state_record(record, workspace_id)
            for record in value["records"]
        ]
        self.workspaces.save(workspace)
        restored_artifacts = 0
        for clean in artifacts:
            self.artifacts.save(clean)
            restored_artifacts += 1
        restored_records = 0
        for clean in records:
            self.states.save(
                clean["kind"], clean["record_id"], workspace_id,
                clean["payload"],
            )
            restored_records += 1
        return {
            "workspace_id": workspace_id,
            "status": "RESTORED",
            "artifacts_restored": restored_artifacts,
            "records_restored": restored_records,
        }

    def restore_saved(self, workspace_id, backup_id, *, overwrite=False):
        payload = self.store.get(backup_id, workspace_id)
        if payload is None:
            raise KeyError("backup_not_found")
        return self.restore(workspace_id, payload, overwrite=overwrite)

    def _artifact_records(self, workspace_id):
        values = []
        for artifact in self.artifacts.list():
            if artifact.get("workspace_id", "default") != workspace_id:
                continue
            values.append(_artifact(artifact, workspace_id))
        return values

    def _state_records(self, workspace_id):
        values = []
        for kind in _STATE_KINDS:
            for payload in self.states.list(kind, workspace_id):
                identifier = payload.get(_STATE_IDS[kind])
                record_id = workspace_id if kind in {
                    "subscription", "billing_account"
                } else identifier
                if not isinstance(record_id, str) or not record_id:
                    continue
                values.append({
                    "kind": kind,
                    "record_id": record_id,
                    "payload": _sanitize(payload),
                })
        return values

    @staticmethod
    def _workspace_id(workspace_id):
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("invalid_workspace")

    @staticmethod
    def _validate(value, workspace_id):
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != BACKUP_SCHEMA_VERSION
            or value.get("workspace_id") != workspace_id
            or not isinstance(value.get("workspace"), dict)
            or value["workspace"].get("workspace_id") != workspace_id
            or not isinstance(value.get("artifacts"), list)
            or not isinstance(value.get("records"), list)
        ):
            raise ValueError("invalid_backup")


def _artifact(value, workspace_id):
    if not isinstance(value, dict) or value.get("workspace_id") != workspace_id:
        raise ValueError("invalid_artifact_backup")
    if not isinstance(value.get("artifact_id"), str) or not value["artifact_id"]:
        raise ValueError("invalid_artifact_backup")
    clean = {
        key: _sanitize(item)
        for key, item in value.items() if key in _ARTIFACT_FIELDS
    }
    internal_ref = clean.get("internal_ref")
    if internal_ref is not None:
        path = PurePosixPath(internal_ref.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            clean.pop("internal_ref", None)
    return clean


def _state_record(value, workspace_id):
    if not isinstance(value, dict) or value.get("kind") not in _STATE_KINDS:
        raise ValueError("invalid_state_backup")
    record_id = value.get("record_id")
    payload = value.get("payload")
    if not isinstance(record_id, str) or not record_id or not isinstance(payload, dict):
        raise ValueError("invalid_state_backup")
    if payload.get("workspace_id") != workspace_id:
        raise ValueError("workspace_mismatch")
    return {
        "kind": value["kind"],
        "record_id": record_id,
        "payload": _sanitize(payload),
    }


def _sanitize(value):
    if isinstance(value, dict):
        return {
            key: _sanitize(item)
            for key, item in value.items()
            if isinstance(key, str)
            and not any(word in key.lower() for word in _SENSITIVE)
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, (str, int, float, bool, type(None))):
        if isinstance(value, str) and _ABSOLUTE_PATH.match(value):
            return "[internal reference omitted]"
        return value
    raise ValueError("invalid_backup_value")
