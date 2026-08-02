from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re

from config.settings import ALLOW_PAID_PROVIDER
from core.retry_recovery import RetryPolicy
from core.structured_logging import LogLevel, safe_log


_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")
_PROVIDERS = {
    "provider": {"mock"},
    "music_provider": {"fake"},
    "image_provider": {"fake", "comfyui"},
    "video_provider": {"fake"},
    "youtube_provider": {"fake"},
}
_TIMEOUT_FIELDS = {
    "provider_timeout_seconds",
    "music_timeout_seconds",
    "image_timeout_seconds",
    "video_timeout_seconds",
    "youtube_timeout_seconds",
}
_FIELDS = {
    *_PROVIDERS,
    *_TIMEOUT_FIELDS,
    "retry_max_attempts",
    "retry_backoff_seconds",
    "batch_max_items",
    "log_level",
    "allow_paid_provider",
}


@dataclass(frozen=True)
class WorkspaceSettings:
    workspace_id: str
    revision: int
    updated_at: str
    provider: str = "mock"
    music_provider: str = "fake"
    image_provider: str = "fake"
    video_provider: str = "fake"
    youtube_provider: str = "fake"
    provider_timeout_seconds: float = 30.0
    music_timeout_seconds: float = 30.0
    image_timeout_seconds: float = 30.0
    video_timeout_seconds: float = 30.0
    youtube_timeout_seconds: float = 30.0
    retry_max_attempts: int = 3
    retry_backoff_seconds: float = 0.0
    batch_max_items: int = 100
    log_level: str = LogLevel.INFO
    allow_paid_provider: bool = False

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            return None
        try:
            settings = cls(**{
                key: value[key] for key in cls.__dataclass_fields__
                if key in value
            })
            _validate(settings.to_dict(), include_identity=True)
            return settings
        except (TypeError, ValueError):
            return None


class SettingsManager:
    """Safe Workspace settings over the shared StateRepository."""

    def __init__(self, repository, logger=None, clock=None):
        for method in ("save", "get"):
            if not callable(getattr(repository, method, None)):
                raise TypeError("repository must implement StateRepository")
        self.repository = repository
        self.logger = logger
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def get(self, workspace_id):
        workspace_id = _identifier(workspace_id, "workspace_id")
        try:
            value = self.repository.get(
                "settings", _storage_id(workspace_id), workspace_id
            )
        except Exception:
            value = None
        restored = WorkspaceSettings.from_dict(value)
        return restored or self._defaults(workspace_id)

    def update(self, workspace_id, changes, expected_revision=None):
        workspace_id = _identifier(workspace_id, "workspace_id")
        if not isinstance(changes, dict) or not changes:
            raise ValueError("changes must be a non-empty dictionary")
        unknown = set(changes) - _FIELDS
        if unknown:
            raise ValueError("settings contain unsupported fields")
        current = self.get(workspace_id)
        if expected_revision is not None and expected_revision != current.revision:
            raise ValueError("settings revision mismatch")
        values = current.to_dict()
        values.update(changes)
        values["revision"] = current.revision + 1
        values["updated_at"] = _aware(self.clock()).isoformat()
        _validate(values, include_identity=True)
        updated = WorkspaceSettings(**values)
        self.repository.save(
            "settings", _storage_id(workspace_id), workspace_id, updated.to_dict()
        )
        safe_log(
            self.logger,
            "SETTINGS_UPDATED",
            "SettingsManager",
            workspace_id=workspace_id,
            status="UPDATED",
            metadata={
                "revision": updated.revision,
                "changed_fields": sorted(changes),
            },
        )
        return updated

    def update_safe(self, *args, **kwargs):
        try:
            return {"ok": True, "settings": self.update(*args, **kwargs).to_dict()}
        except Exception as error:
            workspace_id = kwargs.get("workspace_id")
            if workspace_id is None and args:
                workspace_id = args[0]
            safe_log(
                self.logger,
                "SETTINGS_UPDATE_FAILED",
                "SettingsManager",
                level=LogLevel.ERROR,
                workspace_id=workspace_id if isinstance(workspace_id, str) else None,
                status="FAILED",
                error=f"SettingsError: {type(error).__name__}",
            )
            return {
                "ok": False,
                "error": f"SettingsError: {type(error).__name__}",
            }

    def provider_environment(self, workspace_id, kind="provider"):
        settings = self.get(workspace_id)
        mapping = {
            "provider": (
                "AICOMPANY_PROVIDER", "AICOMPANY_PROVIDER_TIMEOUT",
                settings.provider, settings.provider_timeout_seconds,
            ),
            "music": (
                "AICOMPANY_MUSIC_PROVIDER", "AICOMPANY_MUSIC_PROVIDER_TIMEOUT",
                settings.music_provider, settings.music_timeout_seconds,
            ),
            "image": (
                "AICOMPANY_IMAGE_PROVIDER", "AICOMPANY_IMAGE_PROVIDER_TIMEOUT",
                settings.image_provider, settings.image_timeout_seconds,
            ),
            "video": (
                "AICOMPANY_VIDEO_PROVIDER", "AICOMPANY_VIDEO_PROVIDER_TIMEOUT",
                settings.video_provider, settings.video_timeout_seconds,
            ),
            "youtube": (
                "AICOMPANY_YOUTUBE_PROVIDER", "AICOMPANY_YOUTUBE_PROVIDER_TIMEOUT",
                settings.youtube_provider, settings.youtube_timeout_seconds,
            ),
        }
        if kind not in mapping:
            raise ValueError("unsupported provider kind")
        provider_key, timeout_key, provider, timeout = mapping[kind]
        return {
            provider_key: provider,
            timeout_key: str(timeout),
            "ALLOW_PAID_PROVIDER": "false",
        }

    def retry_policy(self, workspace_id):
        settings = self.get(workspace_id)
        return RetryPolicy(
            max_attempts=settings.retry_max_attempts,
            backoff_seconds=settings.retry_backoff_seconds,
        )

    def _defaults(self, workspace_id):
        return WorkspaceSettings(
            workspace_id=workspace_id,
            revision=0,
            updated_at=_aware(self.clock()).isoformat(),
            allow_paid_provider=bool(ALLOW_PAID_PROVIDER),
        )


def _validate(values, include_identity=False):
    if include_identity:
        _identifier(values.get("workspace_id"), "workspace_id")
        revision = values.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise ValueError("revision must be non-negative")
        _aware(datetime.fromisoformat(values.get("updated_at")))
    for field, allowed in _PROVIDERS.items():
        if values.get(field) not in allowed:
            raise ValueError(f"{field} is unsupported or disabled")
    for field in _TIMEOUT_FIELDS:
        value = values.get(field)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not 0 < value <= 3600
        ):
            raise ValueError(f"{field} must be between 0 and 3600")
    attempts = values.get("retry_max_attempts")
    if not isinstance(attempts, int) or isinstance(attempts, bool) or not 1 <= attempts <= 10:
        raise ValueError("retry_max_attempts must be between 1 and 10")
    backoff = values.get("retry_backoff_seconds")
    if (
        not isinstance(backoff, (int, float))
        or isinstance(backoff, bool)
        or not 0 <= backoff <= 3600
    ):
        raise ValueError("retry_backoff_seconds must be between 0 and 3600")
    batch = values.get("batch_max_items")
    if not isinstance(batch, int) or isinstance(batch, bool) or not 1 <= batch <= 1000:
        raise ValueError("batch_max_items must be between 1 and 1000")
    if values.get("log_level") not in LogLevel.ORDER:
        raise ValueError("invalid log_level")
    if values.get("allow_paid_provider") is not False or ALLOW_PAID_PROVIDER:
        raise ValueError("paid providers are disabled by policy")


def _identifier(value, field_name):
    if (
        not isinstance(value, str)
        or not value.strip()
        or not _SAFE_ID.fullmatch(value.strip())
    ):
        raise ValueError(f"{field_name} contains unsupported characters")
    return value.strip()


def _storage_id(workspace_id):
    return f"{workspace_id}:settings"


def _aware(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value
