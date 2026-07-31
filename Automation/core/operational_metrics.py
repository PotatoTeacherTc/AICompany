from collections import Counter
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class MetricsSnapshot:
    requests_total: int
    requests_active: int
    errors_total: int
    average_duration_ms: float
    status_counts: dict
    error_summary: dict
    health: dict

    def to_dict(self):
        return {
            "requests_total": self.requests_total,
            "requests_active": self.requests_active,
            "errors_total": self.errors_total,
            "average_duration_ms": self.average_duration_ms,
            "status_counts": dict(self.status_counts),
            "error_summary": dict(self.error_summary),
            "health": dict(self.health),
        }


class InMemoryOperationalMetrics:
    """Process-local, aggregate-only operational metrics."""

    def __init__(self):
        self._lock = Lock()
        self._requests_total = 0
        self._requests_active = 0
        self._duration_total_ms = 0.0
        self._status_counts = Counter()
        self._error_summary = Counter()
        self._health = {}

    def request_started(self):
        with self._lock:
            self._requests_active += 1

    def request_finished(self, status_code, duration_ms, error_category=None):
        try:
            status_code = int(status_code)
            duration_ms = max(0.0, float(duration_ms))
            category = (
                error_category
                if isinstance(error_category, str) and error_category.isidentifier()
                else None
            )
        except (TypeError, ValueError):
            return
        with self._lock:
            self._requests_active = max(0, self._requests_active - 1)
            self._requests_total += 1
            self._duration_total_ms += duration_ms
            self._status_counts[str(status_code)] += 1
            if status_code >= 400:
                self._error_summary[category or f"http_{status_code}"] += 1

    def health_observed(self, name, status):
        if not isinstance(name, str) or not name.isidentifier():
            return
        if status not in {"available", "unavailable", "not_configured"}:
            return
        with self._lock:
            self._health[name] = status

    def snapshot(self):
        with self._lock:
            total = self._requests_total
            return MetricsSnapshot(
                requests_total=total,
                requests_active=self._requests_active,
                errors_total=sum(self._error_summary.values()),
                average_duration_ms=round(
                    self._duration_total_ms / total, 3
                ) if total else 0.0,
                status_counts=dict(self._status_counts),
                error_summary=dict(self._error_summary),
                health=dict(self._health),
            ).to_dict()


class NullOperationalMetrics:
    def request_started(self):
        return None

    def request_finished(self, status_code, duration_ms, error_category=None):
        return None

    def health_observed(self, name, status):
        return None

    def snapshot(self):
        return MetricsSnapshot(0, 0, 0, 0.0, {}, {}, {}).to_dict()
