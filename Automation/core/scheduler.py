from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
import re
import uuid


_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_SENSITIVE = {"prompt", "objective", "api_key", "token", "password", "secret"}


@dataclass(frozen=True)
class Recurrence:
    interval_seconds: int

    def __post_init__(self):
        if (
            not isinstance(self.interval_seconds, int)
            or isinstance(self.interval_seconds, bool)
            or self.interval_seconds <= 0
        ):
            raise ValueError("interval_seconds must be a positive integer")


@dataclass(frozen=True)
class Schedule:
    schedule_id: str
    workspace_id: str
    target_id: str
    run_at: str
    created_at: str
    recurrence: Recurrence | None = None
    enabled: bool = True
    metadata: dict = field(default_factory=dict)
    last_run_at: str | None = None

    def to_dict(self):
        return asdict(self)


class SystemClock:
    def now(self):
        return datetime.now(timezone.utc)


class FakeClock:
    def __init__(self, current):
        self.current = _aware(current, "current")

    def now(self):
        return self.current

    def advance(self, seconds):
        self.current += timedelta(seconds=seconds)


class InMemoryScheduler:
    """Workspace-scoped deterministic scheduler; no external timer is started."""

    def __init__(self, clock=None):
        self.clock = clock or SystemClock()
        self._schedules = {}
        self._targets = {}
        self._running = set()

    def register_target(self, target_id, callback):
        _safe_id(target_id, "target_id")
        if not callable(callback):
            raise ValueError("target callback must be callable")
        self._targets[target_id] = callback

    def schedule(
        self,
        workspace_id,
        target_id,
        run_at,
        recurrence=None,
        enabled=True,
        metadata=None,
    ):
        _safe_id(workspace_id, "workspace_id")
        _safe_id(target_id, "target_id")
        timestamp = _aware(run_at, "run_at")
        now = self.clock.now()
        if timestamp <= now:
            raise ValueError("run_at must be in the future")
        safe_metadata = self._metadata(metadata)
        item = Schedule(
            uuid.uuid4().hex,
            workspace_id,
            target_id,
            timestamp.isoformat(),
            now.isoformat(),
            recurrence,
            bool(enabled),
            safe_metadata,
        )
        self._schedules[item.schedule_id] = item
        return item

    def set_enabled(self, schedule_id, enabled, workspace_id):
        item = self.get(schedule_id, workspace_id)
        if item is None:
            raise ValueError("schedule not found")
        updated = replace(item, enabled=bool(enabled))
        self._schedules[schedule_id] = updated
        return updated

    def get(self, schedule_id, workspace_id):
        item = self._schedules.get(schedule_id)
        return item if item and item.workspace_id == workspace_id else None

    def list(self, workspace_id):
        _safe_id(workspace_id, "workspace_id")
        return [
            item for item in self._schedules.values()
            if item.workspace_id == workspace_id
        ]

    def run_due(self, workspace_id):
        _safe_id(workspace_id, "workspace_id")
        now = self.clock.now()
        results = []
        for item in list(self.list(workspace_id)):
            if not item.enabled or _aware(item.run_at, "run_at") > now:
                continue
            if item.schedule_id in self._running:
                continue
            callback = self._targets.get(item.target_id)
            if callback is None:
                results.append(self._failure(item, "ScheduleError: TargetUnavailable"))
                self._advance(item, now)
                continue
            self._running.add(item.schedule_id)
            try:
                value = callback(item)
                results.append(
                    {"schedule_id": item.schedule_id, "status": "SUCCESS", "result": value}
                )
            except Exception as error:
                results.append(
                    self._failure(item, f"ScheduleError: {type(error).__name__}")
                )
            finally:
                self._running.remove(item.schedule_id)
                self._advance(item, now)
        return results

    def _advance(self, item, now):
        if item.recurrence is None:
            updated = replace(item, enabled=False, last_run_at=now.isoformat())
        else:
            next_run = _aware(item.run_at, "run_at")
            interval = timedelta(seconds=item.recurrence.interval_seconds)
            while next_run <= now:
                next_run += interval
            updated = replace(
                item, run_at=next_run.isoformat(), last_run_at=now.isoformat()
            )
        self._schedules[item.schedule_id] = updated

    @staticmethod
    def _failure(item, error):
        return {"schedule_id": item.schedule_id, "status": "FAILED", "error": error}

    @staticmethod
    def _metadata(metadata):
        if metadata is None:
            return {}
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be a dictionary")
        return {
            key: value
            for key, value in metadata.items()
            if isinstance(key, str)
            and not any(token in key.lower() for token in _SENSITIVE)
            and isinstance(value, (str, int, float, bool, type(None)))
        }


def _aware(value, field_name):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f"{field_name} must be a valid timestamp") from error
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


def _safe_id(value, field_name):
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ValueError(f"{field_name} contains unsupported characters")
