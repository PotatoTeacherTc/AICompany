from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re

from core.structured_logging import LogLevel, safe_log


USAGE_FIELDS = (
    "provider",
    "model",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "estimated_cost_usd",
)
NUMERIC_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "estimated_cost_usd",
)
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]+$")


@dataclass(frozen=True)
class UsageRecord:
    usage_id: str
    workspace_id: str
    execution_id: str
    recorded_at: str
    mission_id: str | None = None
    provider: str | None = None
    model: str | None = None
    input_tokens: int | float | None = None
    output_tokens: int | float | None = None
    total_tokens: int | float | None = None
    estimated_cost_usd: int | float | None = None

    def to_dict(self):
        return {
            key: value for key, value in asdict(self).items()
            if value is not None
        }

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            return None
        try:
            record = cls(**{
                key: value[key] for key in cls.__dataclass_fields__
                if key in value
            })
            _validate_record(record)
            return record
        except (TypeError, ValueError):
            return None

    @property
    def usage(self):
        value = {
            field: getattr(self, field)
            for field in USAGE_FIELDS
            if getattr(self, field) is not None
        }
        return value or None


class UsageEngine:
    """Workspace-scoped usage accounting over the shared StateRepository."""

    def __init__(self, repository, logger=None, clock=None):
        for method in ("save", "get", "list"):
            if not callable(getattr(repository, method, None)):
                raise TypeError("repository must implement StateRepository")
        self.repository = repository
        self.logger = logger
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def record(
        self,
        workspace_id,
        execution_id,
        usage=None,
        *,
        mission_id=None,
        usage_id=None,
    ):
        workspace_id = _identifier(workspace_id, "workspace_id")
        execution_id = _identifier(execution_id, "execution_id")
        usage_id = _identifier(usage_id or execution_id, "usage_id")
        values = _normalize_usage(usage)
        record = UsageRecord(
            usage_id=usage_id,
            workspace_id=workspace_id,
            execution_id=execution_id,
            mission_id=_optional_identifier(mission_id, "mission_id"),
            recorded_at=_aware(self.clock()).isoformat(),
            **values,
        )
        storage_id = _storage_id(workspace_id, usage_id)
        existing = self.repository.get("usage", storage_id, workspace_id)
        if existing is not None:
            restored = UsageRecord.from_dict(existing)
            if restored is None or not _same_record(restored, record):
                raise ValueError("usage_id already exists")
            return restored
        self.repository.save(
            "usage", storage_id, workspace_id, record.to_dict()
        )
        safe_log(
            self.logger,
            "USAGE_RECORDED",
            "UsageEngine",
            workspace_id=workspace_id,
            mission_id=record.mission_id,
            execution_id=execution_id,
            status="RECORDED",
            provider=record.provider,
            model=record.model,
            usage=record.usage,
        )
        return record

    def record_safe(self, *args, **kwargs):
        try:
            return {"ok": True, "record": self.record(*args, **kwargs).to_dict()}
        except Exception as error:
            workspace_id = kwargs.get("workspace_id")
            if workspace_id is None and args:
                workspace_id = args[0]
            safe_log(
                self.logger,
                "USAGE_RECORD_FAILED",
                "UsageEngine",
                level=LogLevel.ERROR,
                workspace_id=workspace_id if isinstance(workspace_id, str) else None,
                status="FAILED",
                error=f"UsageError: {type(error).__name__}",
            )
            return {
                "ok": False,
                "error": f"UsageError: {type(error).__name__}",
            }

    def get(self, usage_id, workspace_id):
        try:
            workspace_id = _identifier(workspace_id, "workspace_id")
            usage_id = _identifier(usage_id, "usage_id")
            value = self.repository.get(
                "usage",
                _storage_id(workspace_id, usage_id),
                workspace_id,
            )
            record = UsageRecord.from_dict(value)
            return record.to_dict() if record is not None else None
        except Exception:
            return None

    def query(
        self,
        workspace_id,
        *,
        provider=None,
        model=None,
        start_at=None,
        end_at=None,
        limit=100,
    ):
        try:
            workspace_id = _identifier(workspace_id, "workspace_id")
            if not isinstance(limit, int) or isinstance(limit, bool) or not 0 <= limit <= 1000:
                raise ValueError("invalid limit")
            start, end = _time_range(start_at, end_at)
            records = []
            for value in self.repository.list("usage", workspace_id):
                record = UsageRecord.from_dict(value)
                if record is None or record.workspace_id != workspace_id:
                    continue
                timestamp = datetime.fromisoformat(record.recorded_at)
                if provider is not None and record.provider != provider:
                    continue
                if model is not None and record.model != model:
                    continue
                if start is not None and timestamp < start:
                    continue
                if end is not None and timestamp > end:
                    continue
                records.append(record)
            records.sort(key=lambda item: item.recorded_at)
            return [item.to_dict() for item in (records[-limit:] if limit else [])]
        except Exception:
            return []

    def summary(self, workspace_id, **filters):
        records = self.query(workspace_id, **filters)
        result = {
            "workspace_id": workspace_id,
            "record_count": len(records),
        }
        for field in NUMERIC_FIELDS:
            values = [
                record[field] for record in records
                if isinstance(record.get(field), (int, float))
                and not isinstance(record.get(field), bool)
            ]
            if values:
                result[field] = round(sum(values), 10)
        for field in ("provider", "model"):
            counts = {}
            for record in records:
                value = record.get(field)
                if value:
                    counts[value] = counts.get(value, 0) + 1
            if counts:
                result[f"{field}_distribution"] = counts
        return result


def _normalize_usage(usage):
    if usage is None:
        return {}
    if isinstance(usage, dict):
        source = usage
    else:
        source = {
            field: getattr(usage, field)
            for field in USAGE_FIELDS
            if hasattr(usage, field)
        }
        if hasattr(usage, "estimated_cost"):
            source["estimated_cost_usd"] = getattr(usage, "estimated_cost")
    unknown = set(source) - set(USAGE_FIELDS) - {"estimated_cost"}
    if unknown:
        raise ValueError("usage contains unsupported fields")
    result = {}
    for field in ("provider", "model"):
        if field in source and source[field] is not None:
            result[field] = _identifier(source[field], field)
    for field in NUMERIC_FIELDS:
        value = source.get(
            field,
            source.get("estimated_cost") if field == "estimated_cost_usd" else None,
        )
        if value is None:
            continue
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or value < 0
        ):
            raise ValueError(f"{field} must be non-negative")
        result[field] = value
    return result


def _validate_record(record):
    for value, name in (
        (record.usage_id, "usage_id"),
        (record.workspace_id, "workspace_id"),
        (record.execution_id, "execution_id"),
    ):
        _identifier(value, name)
    _optional_identifier(record.mission_id, "mission_id")
    _aware(datetime.fromisoformat(record.recorded_at))
    _normalize_usage(record.usage)


def _same_record(left, right):
    left_value = left.to_dict()
    right_value = right.to_dict()
    left_value.pop("recorded_at", None)
    right_value.pop("recorded_at", None)
    return left_value == right_value


def _storage_id(workspace_id, usage_id):
    return f"{workspace_id}:{usage_id}"


def _identifier(value, field_name):
    if (
        not isinstance(value, str)
        or not value.strip()
        or not _SAFE_IDENTIFIER.fullmatch(value.strip())
    ):
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_identifier(value, field_name):
    return None if value is None else _identifier(value, field_name)


def _aware(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


def _time_range(start_at, end_at):
    parsed = []
    for value in (start_at, end_at):
        if value is None:
            parsed.append(None)
            continue
        timestamp = datetime.fromisoformat(value)
        parsed.append(_aware(timestamp))
    if all(parsed) and parsed[0] > parsed[1]:
        raise ValueError("invalid time range")
    return parsed
