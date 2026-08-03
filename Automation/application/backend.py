from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import re

from config.settings import ALLOW_PAID_PROVIDER
from core.structured_logging import LogLevel, safe_log
from core.security import InMemoryRateLimiter, SecuritySettings
from core.operational_metrics import InMemoryOperationalMetrics
from core.structured_logging import NullLogger


BACKEND_SCHEMA_VERSION = "1"
_INSTANCE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class BackendHealthService:
    """Safe, read-only operational health aggregation."""

    def __init__(
        self,
        persistence_probe=None,
        queue_probe=None,
        monitor_probe=None,
        logger=None,
        metrics=None,
        instance_id=None,
        worker_probe=None,
        storage_probe=None,
        required_checks=None,
        probe_timeout_seconds=1.0,
    ):
        self.persistence_probe = persistence_probe
        self.queue_probe = queue_probe
        self.monitor_probe = monitor_probe
        self.logger = logger
        self.metrics = metrics
        self.instance_id = (
            instance_id
            if isinstance(instance_id, str) and _INSTANCE_ID.fullmatch(instance_id)
            else None
        )
        self.worker_probe = worker_probe
        self.storage_probe = storage_probe
        self.required_checks = tuple(required_checks or ("persistence", "queue", "monitor"))
        self.probe_timeout_seconds = float(probe_timeout_seconds)
        if not 0 < self.probe_timeout_seconds <= 5:
            raise ValueError("invalid_probe_timeout")
        self._shutting_down = False

    def snapshot(self):
        probes = {
            "persistence": self.persistence_probe,
            "queue": self.queue_probe,
            "monitor": self.monitor_probe,
            "worker": self.worker_probe,
            "storage": self.storage_probe,
        }
        values = {name: self._probe_value(probe) for name, probe in probes.items()}
        checks = {name: value[0] for name, value in values.items()}
        details = {name: value[1] for name, value in values.items() if value[1]}
        healthy = all(value != "unavailable" for value in checks.values())
        if self.metrics is not None:
            for name, status in checks.items():
                self.metrics.health_observed(name, status)
        if not healthy:
            safe_log(
                self.logger,
                "BACKEND_HEALTH_DEGRADED",
                "BackendHealthService",
                level=LogLevel.WARNING,
                status="DEGRADED",
            )
        result = {
            "service": "AICompany Backend",
            "status": "ok" if healthy else "degraded",
            "schema_version": BACKEND_SCHEMA_VERSION,
            "checks": checks,
            "paid_provider_enabled": bool(ALLOW_PAID_PROVIDER),
        }
        if details:
            result["details"] = details
        if self.instance_id:
            result["instance_id"] = self.instance_id
        return result

    def readiness(self):
        value = self.snapshot()
        ready = not self._shutting_down and all(
            value["checks"].get(name) == "available" for name in self.required_checks
        )
        return {
            "service": value["service"],
            "status": "ready" if ready else "not_ready",
            "schema_version": value["schema_version"],
            "checks": value["checks"],
            "paid_provider_enabled": value["paid_provider_enabled"],
            **({"instance_id": self.instance_id} if self.instance_id else {}),
        }

    def begin_shutdown(self):
        self._shutting_down = True

    def _probe(self, probe):
        return self._probe_value(probe)[0]

    def _probe_value(self, probe):
        if probe is None:
            return "not_configured", None
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            value = executor.submit(probe).result(timeout=self.probe_timeout_seconds)
            if isinstance(value, dict) and value.get("ok") is False:
                status = "unavailable"
            elif value is False:
                status = "unavailable"
            else:
                status = "available"
            details = None
            if isinstance(value, dict):
                allowed = {"configured", "connected", "migration"}
                details = {key: value[key] for key in allowed if key in value}
            return status, details
        except (Exception, TimeoutError):
            return "unavailable", None
        finally:
            executor.shutdown(wait=False, cancel_futures=True)


@dataclass(frozen=True)
class BackendDependencies:
    state_repository: object | None = None
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
    authorization_service: object | None = None
    artifact_service: object | None = None
    usage_service: object | None = None
    persistent_execution_service: object | None = None
    job_execution_api_service: object | None = None
    organization_service: object | None = None
    quota_service: object | None = None
    plan_service: object | None = None
    dashboard_service: object | None = None
    subscription_service: object | None = None
    billing_service: object | None = None
    admin_service: object | None = None
    onboarding_service: object | None = None
    product_workflow_service: object | None = None
    bible_service: object | None = None
    intelligence_service: object | None = None
    production_quality_service: object | None = None
    health_service: BackendHealthService | None = None
    auth_required: bool = False
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    )
    security_settings: SecuritySettings | None = None
    rate_limiter: object | None = None
    logger: object | None = None
    metrics: object | None = None
    infrastructure_resources: object | None = None


def create_backend_app(dependencies=None):
    """Compose the existing HTTP adapter from explicitly replaceable services."""
    from api.app import create_app

    dependencies = dependencies or BackendDependencies()
    logger = dependencies.logger or NullLogger()
    metrics = dependencies.metrics or InMemoryOperationalMetrics()
    health_service = dependencies.health_service or BackendHealthService(
        persistence_probe=(
            dependencies.infrastructure_resources.health
            if dependencies.infrastructure_resources is not None
            else None
        ),
        logger=logger,
        metrics=metrics,
    )
    security = dependencies.security_settings or SecuritySettings.from_environment()
    rate_limiter = dependencies.rate_limiter or InMemoryRateLimiter(
        security.rate_limit_requests,
        security.rate_limit_window_seconds,
    )
    app = create_app(
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
        authorization_service=dependencies.authorization_service,
        artifact_service=dependencies.artifact_service,
        usage_service=dependencies.usage_service,
        persistent_execution_service=dependencies.persistent_execution_service,
        job_execution_api_service=dependencies.job_execution_api_service,
        organization_service=dependencies.organization_service,
        quota_service=dependencies.quota_service,
        plan_service=dependencies.plan_service,
        dashboard_service=dependencies.dashboard_service,
        subscription_service=dependencies.subscription_service,
        billing_service=dependencies.billing_service,
        admin_service=dependencies.admin_service,
        onboarding_service=dependencies.onboarding_service,
        product_workflow_service=dependencies.product_workflow_service,
        bible_service=dependencies.bible_service,
        intelligence_service=dependencies.intelligence_service,
        production_quality_service=dependencies.production_quality_service,
        health_service=health_service,
        auth_required=dependencies.auth_required,
        allowed_origins=(
            dependencies.allowed_origins
            if dependencies.security_settings is None
            else security.allowed_origins
        ),
        security_settings=security,
        rate_limiter=rate_limiter,
        logger=logger,
        metrics=metrics,
        infrastructure_resources=dependencies.infrastructure_resources,
    )
    if dependencies.infrastructure_resources is not None:
        app.state.infrastructure_resources = dependencies.infrastructure_resources
    if dependencies.state_repository is not None:
        app.state.state_repository = dependencies.state_repository
    return app
