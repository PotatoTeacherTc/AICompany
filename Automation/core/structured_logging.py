from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import re


class LogLevel:
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

    ORDER = {DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50}


_SENSITIVE = (
    "prompt", "objective", "user_input", "api_key", "oauth", "authorization",
    "cookie", "password", "secret", "raw_response", "raw_error", "stack",
    "traceback", "environment",
)
_TOKEN_USAGE = {"input_tokens", "output_tokens", "total_tokens"}
_ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:[\\/][^\s,;]*|(?<!:)/[^\s,;]+)")
_LABELED_SECRET = re.compile(
    r"(?i)\b(?:authorization|cookie|api[_-]?key|oauth[_-]?token|password|secret)"
    r"\s*[:=]\s*[^\s,;]+"
)
_SAFE_ERROR = re.compile(r"^[A-Za-z]+Error: [A-Za-z][A-Za-z0-9_]*$")
_USAGE_FIELDS = (
    "provider", "model", "input_tokens", "output_tokens", "total_tokens",
    "estimated_cost", "estimated_cost_usd",
)


@dataclass(frozen=True)
class LogEvent:
    timestamp: str
    level: str
    event_type: str
    component: str
    workspace_id: str | None = None
    mission_id: str | None = None
    execution_id: str | None = None
    job_id: str | None = None
    status: str | None = None
    safe_message: str | None = None
    safe_error: str | None = None
    duration_ms: float | None = None
    provider: str | None = None
    model: str | None = None
    usage: dict | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self):
        return _sanitize(asdict(self))

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            return None
        try:
            if value["level"] not in LogLevel.ORDER:
                return None
            timestamp = datetime.fromisoformat(value["timestamp"])
            if timestamp.tzinfo is None:
                return None
            if not value.get("event_type") or not value.get("component"):
                return None
            fields = cls.__dataclass_fields__
            clean = {
                key: item for key, item in _sanitize(value).items()
                if key in fields
            }
            clean["safe_message"] = _safe_message(value.get("safe_message"))
            clean["safe_error"] = _safe_error(value.get("safe_error"))
            clean["usage"] = _usage(value.get("usage"))
            clean["metadata"] = _sanitize(value.get("metadata") or {})
            return cls(**clean)
        except (KeyError, TypeError, ValueError):
            return None


class StructuredLogger:
    """Failure-isolated structured operational logging contract."""

    def __init__(self, minimum_level=LogLevel.INFO, clock=None):
        if minimum_level not in LogLevel.ORDER:
            raise ValueError("invalid minimum log level")
        self.minimum_level = minimum_level
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def emit(
        self,
        event_type,
        component,
        *,
        level=LogLevel.INFO,
        workspace_id=None,
        mission_id=None,
        execution_id=None,
        job_id=None,
        status=None,
        safe_message=None,
        error=None,
        duration_ms=None,
        provider=None,
        model=None,
        usage=None,
        metadata=None,
    ):
        try:
            if level not in LogLevel.ORDER:
                return False
            if LogLevel.ORDER[level] < LogLevel.ORDER[self.minimum_level]:
                return False
            event = LogEvent(
                timestamp=self.clock().isoformat(),
                level=level,
                event_type=str(event_type),
                component=str(component),
                workspace_id=_identifier(workspace_id),
                mission_id=_identifier(mission_id),
                execution_id=_identifier(execution_id),
                job_id=_identifier(job_id),
                status=_scalar(status),
                safe_message=_safe_message(safe_message),
                safe_error=_safe_error(error),
                duration_ms=(
                    float(duration_ms)
                    if isinstance(duration_ms, (int, float)) and duration_ms >= 0
                    else None
                ),
                provider=_scalar(provider),
                model=_scalar(model),
                usage=_usage(usage),
                metadata=_sanitize(metadata or {}),
            )
            self._write(event.to_dict())
            return True
        except Exception:
            return False

    def query(
        self,
        workspace_id,
        *,
        component=None,
        level=None,
        start_at=None,
        end_at=None,
        limit=100,
    ):
        try:
            if not isinstance(workspace_id, str) or not workspace_id:
                return []
            if level is not None and level not in LogLevel.ORDER:
                return []
            if not isinstance(limit, int) or not 0 <= limit <= 1000:
                return []
            start, end = _time_range(start_at, end_at)
            values = []
            for value in self._read():
                event = LogEvent.from_dict(value)
                if event is None or event.workspace_id != workspace_id:
                    continue
                timestamp = datetime.fromisoformat(event.timestamp)
                if component is not None and event.component != component:
                    continue
                if level is not None and event.level != level:
                    continue
                if start is not None and timestamp < start:
                    continue
                if end is not None and timestamp > end:
                    continue
                values.append(event.to_dict())
            values.sort(key=lambda item: item["timestamp"])
            return values[-limit:] if limit else []
        except Exception:
            return []

    def _write(self, event):
        raise NotImplementedError

    def _read(self):
        raise NotImplementedError


class InMemoryLogger(StructuredLogger):
    def __init__(self, minimum_level=LogLevel.INFO, clock=None, fail_writes=False):
        super().__init__(minimum_level, clock)
        self.events = []
        self.fail_writes = fail_writes

    def _write(self, event):
        if self.fail_writes:
            raise OSError("simulated logging failure")
        self.events.append(event)

    def _read(self):
        return list(self.events)


class LocalFileLogger(StructuredLogger):
    def __init__(self, path, minimum_level=LogLevel.INFO, clock=None):
        super().__init__(minimum_level, clock)
        self.path = Path(path)

    def _write(self, event):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _read(self):
        if not self.path.is_file():
            return []
        values = []
        with self.path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    value = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(value, dict):
                    values.append(value)
        return values


class NullLogger(StructuredLogger):
    def _write(self, event):
        return None

    def _read(self):
        return []


def safe_log(logger, event_type, component, **fields):
    if logger is None:
        return False
    try:
        return bool(logger.emit(event_type, component, **fields))
    except Exception:
        return False


def _sanitize(value):
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            lowered = key.lower()
            if lowered not in _TOKEN_USAGE and (
                "token" in lowered or any(name in lowered for name in _SENSITIVE)
            ):
                continue
            clean[key] = _sanitize(item)
        return clean
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, (int, float, bool, type(None))):
        return value
    return str(type(value).__name__)


def _usage(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        value = {
            field: getattr(value, field)
            for field in _USAGE_FIELDS if hasattr(value, field)
        }
    clean = {
        field: value[field] for field in _USAGE_FIELDS
        if field in value and value[field] is not None
    }
    return _sanitize(clean) or None


def _safe_error(error):
    if error is None:
        return None
    if isinstance(error, BaseException):
        return f"LoggingError: {type(error).__name__}"
    if isinstance(error, str) and _SAFE_ERROR.fullmatch(error):
        return error
    return "LoggingError: ReportedFailure"


def _safe_message(value):
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    return _clean_text(value)


def _clean_text(value):
    value = _LABELED_SECRET.sub("[sensitive value omitted]", value)
    return _ABSOLUTE_PATH.sub("[internal reference omitted]", value)


def _identifier(value):
    return value if isinstance(value, str) and value else None


def _scalar(value):
    return value if isinstance(value, (str, int, float, bool)) else None


def _time_range(start_at, end_at):
    parsed = []
    for value in (start_at, end_at):
        if value is None:
            parsed.append(None)
            continue
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None:
            raise ValueError("time filter must be timezone-aware")
        parsed.append(timestamp)
    if all(parsed) and parsed[0] > parsed[1]:
        raise ValueError("invalid time range")
    return parsed
