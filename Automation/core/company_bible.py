from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import re
import uuid


STATUSES = {"DRAFT", "ACTIVE", "ARCHIVED"}
DEPARTMENT_BIBLE_TYPES = {"RESEARCH", "MUSIC", "DESIGN", "VIDEO", "MARKETING", "QA", "FILE"}
EMPLOYEE_ROLE_TYPES = {"CEO", "MANAGER", "RESEARCHER", "PLANNER", "CREATOR", "REVIEWER", "QA"}
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_VERSION = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
_SENSITIVE = ("prompt", "api_key", "oauth", "authorization", "cookie", "password", "secret", "token")


@dataclass(frozen=True)
class CompanyConstitution:
    constitution_id: str; workspace_id: str; name: str; version: str; status: str
    principles: tuple[str, ...]; prohibited_actions: tuple[str, ...]
    approval_rules: tuple[str, ...]; security_rules: tuple[str, ...]
    quality_rules: tuple[str, ...]; created_at: str; updated_at: str; metadata: dict
    def to_dict(self): return _dict(self)
    @classmethod
    def from_dict(cls, value): return _from(cls, value)


@dataclass(frozen=True)
class CompanyBible:
    bible_id: str; workspace_id: str; name: str; version: str; status: str
    brand_identity: tuple[str, ...]; audience: tuple[str, ...]; tone: tuple[str, ...]
    global_quality_rules: tuple[str, ...]; prohibited_patterns: tuple[str, ...]
    required_outputs: tuple[str, ...]; constitution_version: str
    created_at: str; updated_at: str; metadata: dict
    def to_dict(self): return _dict(self)
    @classmethod
    def from_dict(cls, value): return _from(cls, value)


@dataclass(frozen=True)
class DepartmentBible:
    department_bible_id: str; workspace_id: str; department_type: str
    version: str; purpose: tuple[str, ...]; responsibilities: tuple[str, ...]
    input_contract: tuple[str, ...]; output_contract: tuple[str, ...]
    professional_rules: tuple[str, ...]; review_rules: tuple[str, ...]
    prohibited_patterns: tuple[str, ...]; company_bible_version: str
    status: str; created_at: str; updated_at: str; metadata: dict
    def to_dict(self): return _dict(self)
    @classmethod
    def from_dict(cls, value): return _from(cls, value)


@dataclass(frozen=True)
class EmployeeBible:
    employee_bible_id: str; workspace_id: str; role_type: str
    department_type: str; version: str; role: tuple[str, ...]
    responsibilities: tuple[str, ...]; authority: tuple[str, ...]
    required_inputs: tuple[str, ...]; output_contract: tuple[str, ...]
    decision_rules: tuple[str, ...]; collaboration_rules: tuple[str, ...]
    self_review_rules: tuple[str, ...]; escalation_rules: tuple[str, ...]
    department_bible_version: str; status: str; created_at: str
    updated_at: str; metadata: dict
    def to_dict(self): return _dict(self)
    @classmethod
    def from_dict(cls, value): return _from(cls, value)


@dataclass(frozen=True)
class BibleBundle:
    workspace_id: str
    constitution: CompanyConstitution | None = None
    company_bible: CompanyBible | None = None
    department_bible: DepartmentBible | None = None
    employee_bible: EmployeeBible | None = None
    def version_metadata(self):
        return {key: value for key, value in {
            "constitution_version": self.constitution.version if self.constitution else None,
            "company_bible_version": self.company_bible.version if self.company_bible else None,
            "department_bible_version": self.department_bible.version if self.department_bible else None,
            "employee_bible_version": self.employee_bible.version if self.employee_bible else None,
        }.items() if value is not None}
    def to_dict(self):
        return {"workspace_id": self.workspace_id, "order": ["constitution", "company_bible", "department_bible", "employee_bible"],
                "items": {key: value.to_dict() if value else None for key, value in (("constitution", self.constitution), ("company_bible", self.company_bible), ("department_bible", self.department_bible), ("employee_bible", self.employee_bible))}}


class BibleManager:
    """Versioned Workspace assets stored atomically per asset scope."""
    def __init__(self, repository, clock=None):
        for method in ("save", "get", "list"):
            if not callable(getattr(repository, method, None)): raise TypeError("repository must implement StateRepository")
        self.repository, self.clock = repository, clock or (lambda: datetime.now(timezone.utc))

    def create_constitution(self, workspace_id, payload): return self._create("constitution", workspace_id, None, CompanyConstitution, payload)
    def create_company_bible(self, workspace_id, payload):
        item = self._build(CompanyBible, workspace_id, payload)
        if self.get_constitution(workspace_id, item.constitution_version) is None: raise ValueError("constitution version not found")
        return self._store("company", workspace_id, None, item)
    def create_department_bible(self, workspace_id, payload):
        item = self._build(DepartmentBible, workspace_id, payload)
        if item.department_type not in DEPARTMENT_BIBLE_TYPES: raise ValueError("unsupported department type")
        if self.get_company_bible(workspace_id, item.company_bible_version) is None: raise ValueError("company bible version not found")
        return self._store("department", workspace_id, item.department_type, item)
    def create_employee_bible(self, workspace_id, payload):
        item = self._build(EmployeeBible, workspace_id, payload)
        if item.role_type not in EMPLOYEE_ROLE_TYPES or item.department_type not in DEPARTMENT_BIBLE_TYPES: raise ValueError("unsupported employee role")
        parent = self.get_department_bible(workspace_id, item.department_type, item.department_bible_version)
        if parent is None: raise ValueError("department bible version not found")
        return self._store("employee", workspace_id, f"{item.department_type}:{item.role_type}", item)

    def activate_constitution(self, workspace_id, version): return self._activate("constitution", workspace_id, None, CompanyConstitution, version)
    def activate_company_bible(self, workspace_id, version): return self._activate("company", workspace_id, None, CompanyBible, version)
    def activate_department_bible(self, workspace_id, department_type, version): return self._activate("department", workspace_id, department_type, DepartmentBible, version)
    def activate_employee_bible(self, workspace_id, department_type, role_type, version): return self._activate("employee", workspace_id, f"{department_type}:{role_type}", EmployeeBible, version)

    def get_constitution(self, workspace_id, version=None): return self._get("constitution", workspace_id, None, CompanyConstitution, version)
    def get_company_bible(self, workspace_id, version=None): return self._get("company", workspace_id, None, CompanyBible, version)
    def get_department_bible(self, workspace_id, department_type, version=None): return self._get("department", workspace_id, department_type, DepartmentBible, version)
    def get_employee_bible(self, workspace_id, department_type, role_type, version=None): return self._get("employee", workspace_id, f"{department_type}:{role_type}", EmployeeBible, version)

    def resolve(self, workspace_id, department_type=None, role_type=None, versions=None):
        versions = dict(versions or {}); constitution = self.get_constitution(workspace_id, versions.get("constitution_version"))
        company = self.get_company_bible(workspace_id, versions.get("company_bible_version"))
        department = self.get_department_bible(workspace_id, department_type, versions.get("department_bible_version")) if department_type else None
        employee = self.get_employee_bible(workspace_id, department_type, role_type, versions.get("employee_bible_version")) if department_type and role_type else None
        requested = {
            "constitution_version": constitution,
            "company_bible_version": company,
            "department_bible_version": department,
            "employee_bible_version": employee,
        }
        if any(versions.get(key) is not None and item is None for key, item in requested.items()):
            raise ValueError("requested bible version not found")
        if company and (not constitution or company.constitution_version != constitution.version): raise ValueError("constitution reference mismatch")
        if department and (not company or department.company_bible_version != company.version): raise ValueError("company bible reference mismatch")
        if employee and (not department or employee.department_bible_version != department.version): raise ValueError("department bible reference mismatch")
        return BibleBundle(_id(workspace_id), constitution, company, department, employee)

    def _create(self, kind, workspace_id, scope, cls, payload): return self._store(kind, workspace_id, scope, self._build(cls, workspace_id, payload))
    def _build(self, cls, workspace_id, payload):
        value = dict(payload or {}); workspace_id = _id(workspace_id); now = _now(self.clock)
        id_field = next(field for field in cls.__dataclass_fields__ if field.endswith("_id") and field != "workspace_id")
        value[id_field] = _id(value.get(id_field) or uuid.uuid4().hex); value["workspace_id"] = workspace_id
        value["status"] = str(value.get("status", "DRAFT")).upper(); value["created_at"] = value.get("created_at", now); value["updated_at"] = now
        item = cls.from_dict(value)
        if item is None: raise ValueError("bible payload invalid")
        return item
    def _store(self, kind, workspace_id, scope, item):
        values = self._load(kind, workspace_id, scope)
        if any(current.version == item.version for current in values): raise ValueError("version already exists")
        if item.status == "ACTIVE": values = [replace(current, status="ARCHIVED", updated_at=item.updated_at) if current.status == "ACTIVE" else current for current in values]
        values.append(item); self._save(kind, workspace_id, scope, values); return item
    def _activate(self, kind, workspace_id, scope, cls, version):
        values = self._load(kind, workspace_id, scope, cls); target = next((item for item in values if item.version == version), None)
        if target is None: raise ValueError("version not found")
        now = _now(self.clock); updated = [replace(item, status="ACTIVE" if item.version == version else "ARCHIVED" if item.status == "ACTIVE" else item.status, updated_at=now) for item in values]
        self._save(kind, workspace_id, scope, updated); return next(item for item in updated if item.version == version)
    def _get(self, kind, workspace_id, scope, cls, version):
        values = self._load(kind, workspace_id, scope, cls)
        return next((item for item in values if item.version == version), None) if version else next((item for item in values if item.status == "ACTIVE"), None)
    def _load(self, kind, workspace_id, scope, cls=None):
        workspace_id = _id(workspace_id); raw = self.repository.get("bible_registry", _key(kind, workspace_id, scope), workspace_id) or {"items": []}
        model = cls or {"constitution": CompanyConstitution, "company": CompanyBible, "department": DepartmentBible, "employee": EmployeeBible}[kind]
        return [item for value in raw.get("items", []) if (item := model.from_dict(value)) is not None]
    def _save(self, kind, workspace_id, scope, values):
        self.repository.save("bible_registry", _key(kind, workspace_id, scope), workspace_id, {"items": [item.to_dict() for item in values]})


def _dict(item):
    value = asdict(item)
    for key, field in item.__dataclass_fields__.items():
        if key != "metadata" and str(field.type).startswith("tuple"): value[key] = list(value[key])
    return value
def _from(cls, value):
    if not isinstance(value, dict): return None
    try:
        data = dict(value)
        for key, field in cls.__dataclass_fields__.items():
            if key != "metadata" and ("tuple" in str(field.type)): data[key] = _texts(data.get(key), key)
        item = cls(**data); _validate(item); return item
    except (KeyError, TypeError, ValueError): return None
def _validate(item):
    _id(item.workspace_id); _version(item.version)
    if item.status not in STATUSES: raise ValueError("status invalid")
    for key, value in asdict(item).items():
        if key.endswith("_id"): _id(value)
        elif key.endswith("_version") and value: _version(value)
        elif key in {"department_type", "role_type"}: _id(value)
        elif key == "name": _text(value, key)
        elif key == "metadata": _metadata(value)
        elif key.endswith("_at"): _aware(value)
def _texts(values, field):
    if isinstance(values, str) or not isinstance(values, (list, tuple)) or len(values) > 50: raise ValueError(f"{field} invalid")
    return tuple(_text(value, field) for value in values)
def _text(value, field):
    if not isinstance(value, str) or not value.strip() or len(value) > 500 or any(token in value.lower() for token in _SENSITIVE): raise ValueError(f"{field} unsafe")
    return value.strip()
def _metadata(value):
    if not isinstance(value, dict) or len(value) > 20: raise ValueError("metadata invalid")
    for key, item in value.items():
        if not isinstance(key, str) or any(token in key.lower() for token in _SENSITIVE) or not isinstance(item, (str, int, float, bool, type(None))): raise ValueError("metadata unsafe")
    return dict(value)
def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value): raise ValueError("identifier invalid")
    return value
def _version(value):
    if not isinstance(value, str) or not _VERSION.fullmatch(value): raise ValueError("version invalid")
    return value
def _aware(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None: raise ValueError("timestamp invalid")
def _now(clock):
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None: raise ValueError("clock must be aware")
    return value.isoformat()
def _key(kind, workspace_id, scope): return f"{workspace_id}:{kind}:{scope or 'global'}"
