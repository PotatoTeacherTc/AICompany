from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re
import uuid

from core.collaboration_worker import BaseWorker
from core.structured_logging import safe_log


DEPARTMENT_TYPES = {
    "PLANNING",
    "RESEARCH",
    "CONTENT",
    "MEDIA",
    "QUALITY_ASSURANCE",
    "OPERATIONS",
    "MUSIC",
    "DESIGN",
    "VIDEO",
    "MARKETING",
    "QA",
    "FILE",
}
DEFAULT_DEPARTMENTS = (
    ("Planning", "PLANNING", ("PLANNING",)),
    ("Research", "RESEARCH", ("RESEARCH",)),
    ("Content", "CONTENT", ("CONTENT",)),
    ("Media", "MEDIA", ("MUSIC", "IMAGE", "VIDEO")),
    ("Quality Assurance", "QUALITY_ASSURANCE", ("VALIDATION",)),
    ("Operations", "OPERATIONS", ("FILE", "HISTORY")),
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")
_SAFE_TEXT = re.compile(r"^[A-Za-z0-9 _.,:()/-]+$")
_ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|^/)")
_SENSITIVE = (
    "prompt", "objective", "api_key", "oauth", "authorization", "cookie",
    "password", "secret", "token",
)


@dataclass(frozen=True)
class WorkerRegistration:
    worker_id: str
    workspace_id: str
    worker: BaseWorker
    supported_task_types: tuple[str, ...]


class WorkerDirectory:
    """Workspace ownership and capabilities for existing Worker instances."""

    def __init__(self):
        self._workers = {}

    def register(self, worker, workspace_id, supported_task_types):
        if not isinstance(worker, BaseWorker):
            raise TypeError("worker must use BaseWorker")
        workspace_id = _identifier(workspace_id, "workspace_id")
        task_types = _task_types(supported_task_types)
        key = (workspace_id, worker.name)
        if key in self._workers:
            raise ValueError("worker is already registered")
        registration = WorkerRegistration(
            worker.name, workspace_id, worker, task_types
        )
        self._workers[key] = registration
        return registration

    def get(self, worker_id, workspace_id):
        return self._workers.get((
            _identifier(workspace_id, "workspace_id"),
            _identifier(worker_id, "worker_id"),
        ))

    def list(self, workspace_id):
        workspace_id = _identifier(workspace_id, "workspace_id")
        return [
            item for (scope, _), item in self._workers.items()
            if scope == workspace_id
        ]


@dataclass(frozen=True)
class Department:
    department_id: str
    workspace_id: str
    name: str
    safe_summary: str
    department_type: str
    enabled: bool
    worker_ids: tuple[str, ...]
    lead_worker_id: str | None
    supported_task_types: tuple[str, ...]
    created_at: str
    updated_at: str
    revision: int

    def to_dict(self):
        value = asdict(self)
        value["worker_ids"] = list(self.worker_ids)
        value["supported_task_types"] = list(self.supported_task_types)
        return value

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            return None
        try:
            department = cls(
                department_id=value["department_id"],
                workspace_id=value["workspace_id"],
                name=value["name"],
                safe_summary=value["safe_summary"],
                department_type=value["department_type"],
                enabled=value["enabled"],
                worker_ids=tuple(value.get("worker_ids", ())),
                lead_worker_id=value.get("lead_worker_id"),
                supported_task_types=tuple(value.get("supported_task_types", ())),
                created_at=value["created_at"],
                updated_at=value["updated_at"],
                revision=value["revision"],
            )
            _validate_department(department)
            return department
        except (KeyError, TypeError, ValueError):
            return None


class DepartmentManager:
    def __init__(
        self, repository, worker_directory, supported_task_types,
        logger=None, clock=None,
    ):
        for method in ("save", "get", "list"):
            if not callable(getattr(repository, method, None)):
                raise TypeError("repository must implement StateRepository")
        if not isinstance(worker_directory, WorkerDirectory):
            raise TypeError("worker_directory must use WorkerDirectory")
        self.repository = repository
        self.workers = worker_directory
        self.supported_task_types = set(_task_types(supported_task_types))
        self.logger = logger
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def create(
        self,
        workspace_id,
        name,
        safe_summary,
        department_type,
        *,
        worker_ids=(),
        lead_worker_id=None,
        supported_task_types=(),
        department_id=None,
        enabled=True,
    ):
        workspace_id = _identifier(workspace_id, "workspace_id")
        department_id = _identifier(
            department_id or uuid.uuid4().hex, "department_id"
        )
        if self.get(department_id, workspace_id) is not None:
            raise ValueError("department already exists")
        now = _aware(self.clock()).isoformat()
        department = Department(
            department_id,
            workspace_id,
            _safe_text(name, "name", 80),
            _safe_text(safe_summary, "safe_summary", 240),
            _department_type(department_type),
            bool(enabled),
            _unique_ids(worker_ids),
            _optional_identifier(lead_worker_id, "lead_worker_id"),
            _task_types(supported_task_types),
            now,
            now,
            0,
        )
        self._validate_relationships(department)
        self._save(department)
        self._log("DEPARTMENT_CREATED", department)
        return department

    def get(self, department_id, workspace_id):
        try:
            workspace_id = _identifier(workspace_id, "workspace_id")
            department_id = _identifier(department_id, "department_id")
            value = self.repository.get(
                "department", _storage_id(workspace_id, department_id),
                workspace_id,
            )
            department = Department.from_dict(value)
            if department is None or department.workspace_id != workspace_id:
                return None
            self._validate_relationships(department, allow_missing_workers=True)
            return department
        except Exception:
            return None

    def list(self, workspace_id):
        try:
            workspace_id = _identifier(workspace_id, "workspace_id")
            values = []
            for value in self.repository.list("department", workspace_id):
                department = Department.from_dict(value)
                if department is None or department.workspace_id != workspace_id:
                    continue
                values.append(department)
            return sorted(values, key=lambda item: (item.name, item.department_id))
        except Exception:
            return []

    def update(self, department_id, workspace_id, changes, expected_revision):
        current = self._required(department_id, workspace_id)
        if expected_revision != current.revision:
            raise ValueError("department revision mismatch")
        allowed = {
            "name", "safe_summary", "department_type", "enabled", "worker_ids",
            "lead_worker_id", "supported_task_types",
        }
        if not isinstance(changes, dict) or not changes or set(changes) - allowed:
            raise ValueError("department changes are invalid")
        values = current.to_dict()
        values.update(changes)
        values["name"] = _safe_text(values["name"], "name", 80)
        values["safe_summary"] = _safe_text(
            values["safe_summary"], "safe_summary", 240
        )
        values["department_type"] = _department_type(values["department_type"])
        values["enabled"] = bool(values["enabled"])
        values["worker_ids"] = _unique_ids(values["worker_ids"])
        values["lead_worker_id"] = _optional_identifier(
            values["lead_worker_id"], "lead_worker_id"
        )
        values["supported_task_types"] = _task_types(
            values["supported_task_types"]
        )
        values["updated_at"] = _aware(self.clock()).isoformat()
        values["revision"] = current.revision + 1
        updated = Department.from_dict(values)
        if updated is None:
            raise ValueError("department changes are invalid")
        self._validate_relationships(updated)
        self._save(updated)
        self._log("DEPARTMENT_UPDATED", updated)
        return updated

    def set_enabled(self, department_id, workspace_id, enabled, expected_revision):
        return self.update(
            department_id, workspace_id, {"enabled": bool(enabled)},
            expected_revision,
        )

    def assign_worker(
        self, department_id, workspace_id, worker_id, expected_revision,
        lead=False,
    ):
        current = self._required(department_id, workspace_id)
        worker_id = _identifier(worker_id, "worker_id")
        if worker_id in current.worker_ids:
            raise ValueError("worker is already assigned")
        changes = {"worker_ids": (*current.worker_ids, worker_id)}
        if lead:
            changes["lead_worker_id"] = worker_id
        return self.update(
            department_id, workspace_id, changes, expected_revision
        )

    def remove_worker(
        self, department_id, workspace_id, worker_id, expected_revision,
    ):
        current = self._required(department_id, workspace_id)
        worker_id = _identifier(worker_id, "worker_id")
        if worker_id not in current.worker_ids:
            raise ValueError("worker is not assigned")
        workers = tuple(item for item in current.worker_ids if item != worker_id)
        changes = {"worker_ids": workers}
        if current.lead_worker_id == worker_id:
            changes["lead_worker_id"] = None
        return self.update(
            department_id, workspace_id, changes, expected_revision
        )

    def create_defaults(self, workspace_id):
        workspace_id = _identifier(workspace_id, "workspace_id")
        existing_types = {item.department_type for item in self.list(workspace_id)}
        created = []
        registrations = self.workers.list(workspace_id)
        for name, department_type, task_types in DEFAULT_DEPARTMENTS:
            available_types = tuple(
                item for item in task_types if item in self.supported_task_types
            )
            matching = [
                item for item in registrations
                if set(item.supported_task_types) & set(available_types)
            ]
            if (
                department_type in existing_types
                or not available_types
                or not matching
            ):
                continue
            worker_ids = tuple(item.worker_id for item in matching)
            created.append(self.create(
                workspace_id,
                name,
                f"{name} offline department",
                department_type,
                worker_ids=worker_ids,
                lead_worker_id=worker_ids[0],
                supported_task_types=available_types,
                department_id=department_type.lower().replace("_", "-"),
            ))
        return created

    def _required(self, department_id, workspace_id):
        department = self.get(department_id, workspace_id)
        if department is None:
            raise ValueError("department not found")
        return department

    def _validate_relationships(self, department, allow_missing_workers=False):
        unsupported = (
            set(department.supported_task_types) - self.supported_task_types
        )
        if unsupported:
            raise ValueError("department task type is unsupported")
        if (
            department.lead_worker_id is not None
            and department.lead_worker_id not in department.worker_ids
        ):
            raise ValueError("lead worker must belong to department")
        for worker_id in department.worker_ids:
            registration = self.workers.get(worker_id, department.workspace_id)
            if registration is None:
                if allow_missing_workers:
                    continue
                raise ValueError("worker is unavailable in workspace")
            if (
                department.supported_task_types
                and not set(registration.supported_task_types)
                & set(department.supported_task_types)
            ):
                raise ValueError("worker does not support department tasks")

    def _save(self, department):
        self.repository.save(
            "department",
            _storage_id(department.workspace_id, department.department_id),
            department.workspace_id,
            department.to_dict(),
        )

    def _log(self, event_type, department):
        safe_log(
            self.logger,
            event_type,
            "DepartmentManager",
            workspace_id=department.workspace_id,
            status="ENABLED" if department.enabled else "DISABLED",
            metadata={
                "department_id": department.department_id,
                "department_type": department.department_type,
                "revision": department.revision,
                "worker_count": len(department.worker_ids),
            },
        )


def _validate_department(department):
    _identifier(department.department_id, "department_id")
    _identifier(department.workspace_id, "workspace_id")
    _safe_text(department.name, "name", 80)
    _safe_text(department.safe_summary, "safe_summary", 240)
    _department_type(department.department_type)
    if not isinstance(department.enabled, bool):
        raise ValueError("enabled must be boolean")
    if len(department.worker_ids) != len(set(department.worker_ids)):
        raise ValueError("worker_ids must be unique")
    for worker_id in department.worker_ids:
        _identifier(worker_id, "worker_id")
    _optional_identifier(department.lead_worker_id, "lead_worker_id")
    _task_types(department.supported_task_types)
    _aware(datetime.fromisoformat(department.created_at))
    _aware(datetime.fromisoformat(department.updated_at))
    if (
        not isinstance(department.revision, int)
        or isinstance(department.revision, bool)
        or department.revision < 0
    ):
        raise ValueError("revision must be non-negative")


def _identifier(value, field_name):
    if (
        not isinstance(value, str)
        or not value.strip()
        or not _SAFE_ID.fullmatch(value.strip())
    ):
        raise ValueError(f"{field_name} contains unsupported characters")
    return value.strip()


def _optional_identifier(value, field_name):
    return None if value is None else _identifier(value, field_name)


def _safe_text(value, field_name, maximum):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > maximum
        or _ABSOLUTE_PATH.search(value)
        or not _SAFE_TEXT.fullmatch(value.strip())
        or any(token in value.lower() for token in _SENSITIVE)
    ):
        raise ValueError(f"{field_name} contains unsafe content")
    return value.strip()


def _department_type(value):
    value = _identifier(value, "department_type").upper()
    if value not in DEPARTMENT_TYPES:
        raise ValueError("unsupported department_type")
    return value


def _task_types(values):
    if isinstance(values, str):
        raise ValueError("task types must be an iterable")
    try:
        result = tuple(_identifier(value, "task_type").upper() for value in values)
    except TypeError as error:
        raise ValueError("task types must be an iterable") from error
    if len(result) != len(set(result)):
        raise ValueError("task types must be unique")
    return result


def _unique_ids(values):
    if isinstance(values, str):
        raise ValueError("worker_ids must be an iterable")
    try:
        result = tuple(_identifier(value, "worker_id") for value in values)
    except TypeError as error:
        raise ValueError("worker_ids must be an iterable") from error
    if len(result) != len(set(result)):
        raise ValueError("worker_ids must be unique")
    return result


def _storage_id(workspace_id, department_id):
    return f"{workspace_id}:{department_id}"


def _aware(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value
