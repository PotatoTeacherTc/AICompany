from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re
import uuid


ORGANIZATION_DEPARTMENT_TYPES = (
    "RESEARCH", "MUSIC", "DESIGN", "VIDEO", "MARKETING", "QA", "FILE",
)
ORGANIZATION_ROLE_TYPES = (
    "CEO", "MANAGER", "RESEARCHER", "PLANNER", "CREATOR", "REVIEWER", "QA",
)
RUNTIME_STATUSES = {"IDLE", "ASSIGNED", "RUNNING", "WAITING", "COMPLETED", "FAILED"}
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_SAFE_TEXT = re.compile(r"^[A-Za-z0-9 _.,:()/-]{1,160}$")
_SENSITIVE = ("prompt", "secret", "token", "password", "cookie", "authorization", "api_key", "oauth")

_RULES = {
    "RESEARCH": ("RESEARCH", "RESEARCHER"),
    "MUSIC": ("MUSIC", "CREATOR"),
    "IMAGE": ("DESIGN", "CREATOR"),
    "DESIGN": ("DESIGN", "CREATOR"),
    "VIDEO": ("VIDEO", "CREATOR"),
    "CONTENT": ("MARKETING", "PLANNER"),
    "MARKETING": ("MARKETING", "PLANNER"),
    "BLOG": ("MARKETING", "CREATOR"),
    "YOUTUBE": ("MARKETING", "CREATOR"),
    "NAVER": ("MARKETING", "CREATOR"),
    "PRODUCT": ("MARKETING", "PLANNER"),
    "VALIDATION": ("QA", "QA"),
    "QA": ("QA", "QA"),
    "FILE": ("FILE", "CREATOR"),
}
ORGANIZATION_TASK_TYPES = tuple(_RULES)


@dataclass(frozen=True)
class Company:
    company_id: str
    workspace_id: str
    name: str
    ceo_employee_id: str
    manager_id: str
    department_ids: tuple[str, ...]
    created_at: str
    updated_at: str

    def to_dict(self):
        value = asdict(self)
        value["department_ids"] = list(self.department_ids)
        return value

    @classmethod
    def from_dict(cls, value):
        return _model(cls, value, tuple_fields=("department_ids",))


@dataclass(frozen=True)
class Manager:
    manager_id: str
    workspace_id: str
    company_id: str
    employee_id: str
    department_ids: tuple[str, ...]
    created_at: str
    updated_at: str

    def to_dict(self):
        value = asdict(self)
        value["department_ids"] = list(self.department_ids)
        return value

    @classmethod
    def from_dict(cls, value):
        return _model(cls, value, tuple_fields=("department_ids",))


@dataclass(frozen=True)
class Employee:
    employee_id: str
    workspace_id: str
    company_id: str
    department_id: str | None
    role_type: str
    manager_id: str | None
    enabled: bool
    created_at: str
    updated_at: str

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return _model(cls, value)


@dataclass(frozen=True)
class ReportingLine:
    workspace_id: str
    company_id: str
    employee_id: str
    manager_id: str | None

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Assignment:
    assignment_id: str
    workspace_id: str
    company_id: str
    manager_id: str
    department_id: str
    employee_id: str
    task_type: str
    status: str
    workflow_id: str | None
    created_at: str
    updated_at: str

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return _model(cls, value)


class OrganizationEngine:
    """Rule-based organization layer that delegates execution to Product Workflow."""

    def __init__(self, repository, department_manager, product_workflow=None, clock=None):
        for method in ("save", "get", "list"):
            if not callable(getattr(repository, method, None)):
                raise TypeError("repository must implement StateRepository")
        self.repository = repository
        self.departments = department_manager
        self.product_workflow = product_workflow
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def create_employee(self, workspace_id, company_id, role_type, *, department_id=None, manager_id=None, employee_id=None, enabled=True):
        now = self._now()
        employee = Employee(
            _id(employee_id or uuid.uuid4().hex), _id(workspace_id), _id(company_id),
            _optional_id(department_id), _role(role_type), _optional_id(manager_id),
            bool(enabled), now, now,
        )
        if department_id and self.departments.get(department_id, workspace_id) is None:
            raise ValueError("department not found")
        if employee.role_type not in {"CEO", "MANAGER"} and (employee.department_id is None or employee.manager_id is None):
            raise ValueError("employee reporting line is required")
        if employee.manager_id is not None:
            manager = self.get_manager(workspace_id, employee.manager_id)
            if manager is None or manager.company_id != employee.company_id:
                raise ValueError("employee manager is invalid")
        self._save("organization_employee", employee.employee_id, employee.workspace_id, employee.to_dict())
        return employee

    def create_manager(self, workspace_id, company_id, employee_id, department_ids, manager_id=None):
        employee = self.get_employee(workspace_id, employee_id)
        if employee is None or employee.company_id != company_id or employee.role_type != "MANAGER":
            raise ValueError("manager employee is invalid")
        departments = tuple(_id(value) for value in department_ids)
        if not departments or len(departments) != len(set(departments)):
            raise ValueError("manager departments are invalid")
        if any(self.departments.get(value, workspace_id) is None for value in departments):
            raise ValueError("department not found")
        now = self._now()
        manager = Manager(_id(manager_id or uuid.uuid4().hex), _id(workspace_id), _id(company_id), employee.employee_id, departments, now, now)
        self._save("organization_manager", manager.manager_id, manager.workspace_id, manager.to_dict())
        return manager

    def create_company(self, workspace_id, name, ceo_employee_id, manager_id, department_ids, company_id=None):
        workspace_id = _id(workspace_id)
        if self.get_company(workspace_id) is not None:
            raise ValueError("workspace company already exists")
        ceo = self.get_employee(workspace_id, ceo_employee_id)
        manager = self.get_manager(workspace_id, manager_id)
        departments = tuple(_id(value) for value in department_ids)
        company_id = _id(company_id or uuid.uuid4().hex)
        if ceo is None or ceo.role_type != "CEO" or ceo.company_id != company_id:
            raise ValueError("CEO employee is invalid")
        if manager is None or manager.company_id != company_id or manager.employee_id == ceo.employee_id:
            raise ValueError("manager is invalid")
        if departments != manager.department_ids:
            raise ValueError("company departments do not match manager")
        now = self._now()
        company = Company(company_id, workspace_id, _safe_text(name), ceo.employee_id, manager.manager_id, departments, now, now)
        self._save("organization_company", company.company_id, workspace_id, company.to_dict())
        return company

    def get_company(self, workspace_id, company_id=None):
        values = self._list("organization_company", workspace_id, Company)
        return next((value for value in values if company_id is None or value.company_id == company_id), None)

    def get_manager(self, workspace_id, manager_id):
        return self._get("organization_manager", workspace_id, manager_id, Manager)

    def get_employee(self, workspace_id, employee_id):
        return self._get("organization_employee", workspace_id, employee_id, Employee)

    def list_employees(self, workspace_id):
        return self._list("organization_employee", workspace_id, Employee)

    def reporting_lines(self, workspace_id):
        return [ReportingLine(value.workspace_id, value.company_id, value.employee_id, value.manager_id) for value in self.list_employees(workspace_id)]

    def assign(self, workspace_id, company_id, task_type, idempotency_key):
        workspace_id, company_id = _id(workspace_id), _id(company_id)
        task_type, idempotency_key = _task_type(task_type), _id(idempotency_key)
        assignment_id = "assignment-" + uuid.uuid5(uuid.NAMESPACE_URL, f"{workspace_id}:{company_id}:{idempotency_key}").hex
        existing = self._get("organization_assignment", workspace_id, assignment_id, Assignment)
        if existing:
            return existing
        company = self.get_company(workspace_id, company_id)
        if company is None:
            raise ValueError("company not found")
        if task_type not in _RULES:
            raise ValueError("task type is unsupported")
        department_type, role_type = _RULES[task_type]
        candidates = [value for value in self.departments.list(workspace_id) if value.department_id in company.department_ids and value.department_type == department_type and value.enabled]
        if not candidates:
            raise ValueError("no eligible department")
        department = sorted(candidates, key=lambda value: value.department_id)[0]
        employees = [value for value in self.list_employees(workspace_id) if value.company_id == company_id and value.department_id == department.department_id and value.role_type == role_type and value.manager_id == company.manager_id and value.enabled]
        if not employees:
            raise ValueError("no eligible employee")
        employee = sorted(employees, key=lambda value: value.employee_id)[0]
        now = self._now()
        assignment = Assignment(assignment_id, workspace_id, company_id, company.manager_id, department.department_id, employee.employee_id, task_type, "ASSIGNED", None, now, now)
        self._save_assignment(assignment)
        self._save_runtime(assignment, "ASSIGNED")
        return assignment

    def execute(self, workspace_id, company_id, request_text, task_type, idempotency_key):
        if self.product_workflow is None:
            raise ValueError("product workflow is not configured")
        assignment = self.assign(workspace_id, company_id, task_type, idempotency_key)
        if assignment.workflow_id:
            return assignment
        metadata = {key: getattr(assignment, key) for key in ("assignment_id", "company_id", "manager_id", "department_id", "employee_id")}
        self._save_runtime(assignment, "RUNNING")
        try:
            workflow = self.product_workflow.submit(workspace_id, request_text, "organization-" + assignment.assignment_id, organization_metadata=metadata)
            status = _workflow_status(workflow.get("status"))
            updated = Assignment(**{**assignment.to_dict(), "status": status, "workflow_id": workflow["product_id"], "updated_at": self._now()})
            self._save_assignment(updated)
            self._save_runtime(updated, status)
            return updated
        except Exception:
            failed = Assignment(**{**assignment.to_dict(), "status": "FAILED", "updated_at": self._now()})
            self._save_assignment(failed)
            self._save_runtime(failed, "FAILED")
            raise

    def get_assignment(self, workspace_id, assignment_id):
        return self._get("organization_assignment", workspace_id, assignment_id, Assignment)

    def list_assignments(self, workspace_id):
        return self._list("organization_assignment", workspace_id, Assignment)

    def runtime(self, workspace_id, assignment_id):
        assignment = self.get_assignment(workspace_id, assignment_id)
        if assignment is None:
            return None
        if assignment.workflow_id and self.product_workflow is not None:
            workflow = self.product_workflow.get(workspace_id, assignment.workflow_id)
            if workflow:
                status = _workflow_status(workflow.get("status"))
                if status != assignment.status:
                    assignment = Assignment(**{**assignment.to_dict(), "status": status, "updated_at": self._now()})
                    self._save_assignment(assignment)
                    self._save_runtime(assignment, status)
        return self.repository.get("organization_runtime", assignment.assignment_id, _id(workspace_id))

    def snapshot(self, workspace_id):
        company = self.get_company(workspace_id)
        return {
            "workspace_id": _id(workspace_id),
            "company": company.to_dict() if company else None,
            "departments": [value.to_dict() for value in self.departments.list(workspace_id) if value.department_type in ORGANIZATION_DEPARTMENT_TYPES],
            "employees": [value.to_dict() for value in self.list_employees(workspace_id)],
            "reporting_lines": [value.to_dict() for value in self.reporting_lines(workspace_id)],
        }

    def _save_runtime(self, assignment, status):
        if status not in RUNTIME_STATUSES:
            raise ValueError("runtime status is invalid")
        running = status in {"RUNNING", "WAITING"}
        self._save("organization_runtime", assignment.assignment_id, assignment.workspace_id, {
            "assignment_id": assignment.assignment_id,
            "workspace_id": assignment.workspace_id,
            "company": {"company_id": assignment.company_id, "status": "RUNNING" if running else status},
            "ceo": {"employee_id": self.get_company(assignment.workspace_id, assignment.company_id).ceo_employee_id, "status": "COMPLETED" if status != "FAILED" else "FAILED"},
            "manager": {"manager_id": assignment.manager_id, "status": status},
            "department": {"department_id": assignment.department_id, "status": status},
            "employee": {"employee_id": assignment.employee_id, "status": status},
            "updated_at": self._now(),
        })

    def _save_assignment(self, value):
        self._save("organization_assignment", value.assignment_id, value.workspace_id, value.to_dict())

    def _save(self, kind, record_id, workspace_id, value):
        self.repository.save(kind, record_id, workspace_id, value)

    def _get(self, kind, workspace_id, record_id, cls):
        value = cls.from_dict(self.repository.get(kind, _id(record_id), _id(workspace_id)))
        return value if value and value.workspace_id == workspace_id else None

    def _list(self, kind, workspace_id, cls):
        workspace_id = _id(workspace_id)
        return sorted((value for raw in self.repository.list(kind, workspace_id) if (value := cls.from_dict(raw)) and value.workspace_id == workspace_id), key=lambda value: value.created_at)

    def _now(self):
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must be timezone-aware")
        return value.isoformat()


def _model(cls, value, tuple_fields=()):
    if not isinstance(value, dict):
        return None
    try:
        data = dict(value)
        for field in tuple_fields:
            data[field] = tuple(data.get(field, ()))
        item = cls(**data)
        _validate(item)
        return item
    except (KeyError, TypeError, ValueError):
        return None


def _validate(value):
    for key, item in asdict(value).items():
        if key.endswith("_id") and item is not None:
            _id(item)
        elif key == "workspace_id":
            _id(item)
        elif key == "role_type":
            _role(item)
        elif key == "task_type":
            _task_type(item)
        elif key == "status" and item not in RUNTIME_STATUSES:
            raise ValueError("status is invalid")
        elif key.endswith("_at"):
            parsed = datetime.fromisoformat(item)
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("timestamp is invalid")
        elif key == "name":
            _safe_text(item)
    if isinstance(value, Employee) and not isinstance(value.enabled, bool):
        raise ValueError("enabled is invalid")
    if isinstance(value, (Company, Manager)):
        if not value.department_ids or any(not _ID.fullmatch(item) for item in value.department_ids):
            raise ValueError("department IDs are invalid")


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("identifier is invalid")
    return value


def _optional_id(value):
    return None if value is None else _id(value)


def _safe_text(value):
    if not isinstance(value, str) or not _SAFE_TEXT.fullmatch(value) or any(term in value.lower() for term in _SENSITIVE):
        raise ValueError("text is unsafe")
    return value


def _role(value):
    value = _id(value).upper()
    if value not in ORGANIZATION_ROLE_TYPES:
        raise ValueError("role is unsupported")
    return value


def _task_type(value):
    return _id(value).upper()


def _workflow_status(value):
    if value in {"WAITING_FOR_INPUT", "CONNECTION_REQUIRED", "USER_CONFIRM_REQUIRED", "USER_ACTION_REQUIRED"}:
        return "WAITING"
    if value in {"COMPLETED", "PUBLISHED"}:
        return "COMPLETED"
    if value == "FAILED":
        return "FAILED"
    return "RUNNING"
