"""Production-only configuration validation and Docker-secret compatible input."""

from pathlib import Path
from urllib.parse import urlparse


_SECRET_NAMES = (
    "AICOMPANY_SIGNING_SECRET", "DATABASE_URL", "REDIS_URL",
    "AICOMPANY_STORAGE_SIGNING_KEY",
)
_INSECURE = ("local-development-only", "changeme", "replace-me", "example-secret")


def resolve_secret_files(environment):
    values = dict(environment)
    for name in _SECRET_NAMES:
        file_name = values.get(f"{name}_FILE")
        direct = values.get(name)
        if file_name and direct:
            raise ValueError("duplicate_secret_source")
        if not file_name:
            continue
        try:
            path = Path(file_name)
            if not path.is_file() or path.stat().st_size > 4096:
                raise ValueError
            value = path.read_text(encoding="utf-8").strip()
        except Exception:
            raise ValueError("secret_file_unavailable") from None
        if not value:
            raise ValueError("secret_file_empty")
        values[name] = value
    return values


def validate_production_configuration(environment):
    values = resolve_secret_files(environment)
    if str(values.get("AICOMPANY_ENV", "development")).lower() != "production":
        return values
    if str(values.get("ALLOW_PAID_PROVIDER", "False")).lower() not in {"false", "0"}:
        raise ValueError("paid_provider_policy_violation")
    if values.get("AICOMPANY_REPOSITORY_ADAPTER") != "postgresql":
        raise ValueError("production_postgresql_required")
    if values.get("AICOMPANY_QUEUE_BACKEND") != "redis":
        raise ValueError("production_redis_queue_required")
    if values.get("AICOMPANY_ARTIFACT_STORAGE") != "local":
        raise ValueError("production_artifact_storage_required")
    _validated_url(values.get("DATABASE_URL"), {"postgresql", "postgres"}, True, "invalid_database_url")
    _validated_url(values.get("REDIS_URL"), {"redis", "rediss"}, True, "invalid_redis_url")
    for name in ("DATABASE_URL", "REDIS_URL", "AICOMPANY_SIGNING_SECRET"):
        value = values.get(name)
        if not isinstance(value, str) or any(token in value.lower() for token in _INSECURE):
            raise ValueError("insecure_production_secret")
    return values


def _validated_url(value, schemes, require_password, error):
    try:
        parsed = urlparse(value)
    except (TypeError, ValueError):
        raise ValueError(error) from None
    if parsed.scheme not in schemes or not parsed.hostname or (require_password and not parsed.password):
        raise ValueError(error)
