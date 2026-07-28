from dataclasses import dataclass

from config.settings import ALLOW_PAID_PROVIDER
from core.structured_logging import LogLevel, safe_log


BACKEND_SCHEMA_VERSION = "1"


class BackendHealthService:
    """Safe, read-only operational health aggregation."""

    def __init__(
        self,
        persistence_probe=None,
        queue_probe=None,
        monitor_probe=None,
        logger=None,
    ):
        self.persistence_probe = persistence_probe
        self.queue_probe = queue_probe
        self.monitor_probe = monitor_probe
        self.logger = logger

    def snapshot(self):
        checks = {
            "persistence": self._probe(self.persistence_probe),
            "queue": self._probe(self.queue_probe),
            "monitor": self._probe(self.monitor_probe),
        }
        healthy = all(value != "unavailable" for value in checks.values())
        if not healthy:
            safe_log(
                self.logger,
                "BACKEND_HEALTH_DEGRADED",
                "BackendHealthService",
                level=LogLevel.WARNING,
                status="DEGRADED",
            )
        return {
            "service": "AICompany Backend",
            "status": "ok" if healthy else "degraded",
            "schema_version": BACKEND_SCHEMA_VERSION,
            "checks": checks,
            "paid_provider_enabled": bool(ALLOW_PAID_PROVIDER),
        }

    @staticmethod
    def _probe(probe):
        if probe is None:
            return "not_configured"
        try:
            value = probe()
            if isinstance(value, dict) and value.get("ok") is False:
                return "unavailable"
            if value is False:
                return "unavailable"
            return "available"
        except Exception:
            return "unavailable"


@dataclass(frozen=True)
class BackendDependencies:
    automation_service: object | None = None
    task_query_service: object | None = None
    workspace_service: object | None = None
    user_service: object | None = None
    membership_service: object | None = None
    credential_service: object | None = None
    login_service: object | None = None
    session_service: object | None = None
    audit_service: object | None = None
    audit_query_service: object | None = None
    health_service: BackendHealthService | None = None
    auth_required: bool = False


def create_backend_app(dependencies=None):
    """Compose the existing HTTP adapter from explicitly replaceable services."""
    from api.app import create_app

    dependencies = dependencies or BackendDependencies()
    health_service = dependencies.health_service or BackendHealthService()
    return create_app(
        automation_service=dependencies.automation_service,
        task_query_service=dependencies.task_query_service,
        workspace_service=dependencies.workspace_service,
        user_service=dependencies.user_service,
        membership_service=dependencies.membership_service,
        credential_service=dependencies.credential_service,
        login_service=dependencies.login_service,
        session_service=dependencies.session_service,
        audit_service=dependencies.audit_service,
        audit_query_service=dependencies.audit_query_service,
        health_service=health_service,
        auth_required=dependencies.auth_required,
    )
