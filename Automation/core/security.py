import os
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class SecuritySettings:
    environment: str
    allowed_origins: tuple[str, ...]
    signing_secret: str | None
    rate_limit_requests: int
    rate_limit_window_seconds: int
    secure_cookies: bool

    @classmethod
    def from_environment(cls, environ=None):
        values = os.environ if environ is None else environ
        environment = values.get("AICOMPANY_ENV", "development").strip().lower()
        if environment not in {"development", "test", "production"}:
            raise ValueError("invalid_environment")
        raw_origins = values.get(
            "AICOMPANY_ALLOWED_ORIGINS",
            "http://127.0.0.1:5173,http://localhost:5173",
        )
        origins = tuple(
            item.strip() for item in raw_origins.split(",") if item.strip()
        )
        if not origins or any(not _valid_origin(item) for item in origins):
            raise ValueError("invalid_allowed_origins")
        secret = values.get("AICOMPANY_SIGNING_SECRET")
        if environment == "production":
            if any(urlparse(item).scheme != "https" for item in origins):
                raise ValueError("production_https_required")
            if not _valid_secret(secret):
                raise ValueError("invalid_signing_secret")
        requests = _positive_int(
            values.get("AICOMPANY_RATE_LIMIT_REQUESTS", "120"),
            "invalid_rate_limit",
        )
        window = _positive_int(
            values.get("AICOMPANY_RATE_LIMIT_WINDOW_SECONDS", "60"),
            "invalid_rate_limit",
        )
        return cls(
            environment=environment,
            allowed_origins=origins,
            signing_secret=secret,
            rate_limit_requests=requests,
            rate_limit_window_seconds=window,
            secure_cookies=environment == "production",
        )


class InMemoryRateLimiter:
    """Single-process safety boundary; distributed limiting is not claimed."""

    def __init__(self, max_requests, window_seconds, clock=None):
        if (
            not isinstance(max_requests, int)
            or isinstance(max_requests, bool)
            or max_requests <= 0
            or not isinstance(window_seconds, int)
            or isinstance(window_seconds, bool)
            or window_seconds <= 0
        ):
            raise ValueError("invalid_rate_limit")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.clock = clock or time.monotonic
        self._entries = {}
        self._lock = threading.Lock()

    def allow(self, key):
        now = self.clock()
        with self._lock:
            start, count = self._entries.get(key, (now, 0))
            if now - start >= self.window_seconds:
                start, count = now, 0
            allowed = count < self.max_requests
            self._entries[key] = (start, count + 1 if allowed else count)
            return allowed


def security_headers(production=False):
    headers = {
        "Content-Security-Policy": (
            "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
            "form-action 'self'; object-src 'none'"
        ),
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Cross-Origin-Resource-Policy": "same-origin",
    }
    if production:
        headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    return headers


def harden_set_cookie(value):
    if not isinstance(value, str) or not value:
        return value
    lowered = value.lower()
    additions = []
    if "secure" not in lowered:
        additions.append("Secure")
    if "httponly" not in lowered:
        additions.append("HttpOnly")
    if "samesite=" not in lowered:
        additions.append("SameSite=Strict")
    return value + ("; " + "; ".join(additions) if additions else "")


def _valid_origin(value):
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and not parsed.username
        and not parsed.password
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def _valid_secret(value):
    if not isinstance(value, str) or len(value) < 32:
        return False
    lowered = value.lower()
    return not any(token in lowered for token in (
        "replace", "example", "changeme", "development", "secret",
    ))


def _positive_int(value, error_code):
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(error_code) from error
    if parsed <= 0:
        raise ValueError(error_code)
    return parsed
