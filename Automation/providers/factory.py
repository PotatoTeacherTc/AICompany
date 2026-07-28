import os
from dataclasses import dataclass

from config.settings import ALLOW_PAID_PROVIDER
from providers.mock_provider import MockProvider
from providers.music import FakeMusicProvider
from providers.content_media import (
    FakeImageProvider,
    FakeVideoProvider,
    FakeYouTubeProvider,
)


@dataclass(frozen=True)
class ProviderSelection:
    provider: object
    default_model: str | None
    timeout_seconds: float


class ProviderFactory:
    """Builds offline-safe providers from configuration without exposing secrets."""

    @classmethod
    def from_environment(cls, environment=None):
        environment = os.environ if environment is None else environment
        provider_name = environment.get("AICOMPANY_PROVIDER", "mock").lower()
        timeout_seconds = cls._timeout(environment.get("AICOMPANY_PROVIDER_TIMEOUT", "30"))

        if provider_name == "mock":
            return ProviderSelection(
                provider=MockProvider(),
                default_model=environment.get("AICOMPANY_PROVIDER_MODEL"),
                timeout_seconds=timeout_seconds,
            )

        raise ValueError(f"Unsupported AI provider: {provider_name}")

    @classmethod
    def music_from_environment(cls, environment=None):
        environment = os.environ if environment is None else environment
        provider_name = environment.get("AICOMPANY_MUSIC_PROVIDER", "fake").lower()
        timeout_seconds = cls._timeout(
            environment.get("AICOMPANY_MUSIC_PROVIDER_TIMEOUT", "30")
        )
        if provider_name == "fake":
            return ProviderSelection(
                provider=FakeMusicProvider(),
                default_model=environment.get("AICOMPANY_MUSIC_PROVIDER_MODEL"),
                timeout_seconds=timeout_seconds,
            )
        raise ValueError(f"Unsupported music provider: {provider_name}")

    @classmethod
    def image_from_environment(cls, environment=None):
        return cls._offline_selection(
            environment, "IMAGE", "fake", FakeImageProvider
        )

    @classmethod
    def video_from_environment(cls, environment=None):
        return cls._offline_selection(
            environment, "VIDEO", "fake", FakeVideoProvider
        )

    @classmethod
    def youtube_from_environment(cls, environment=None):
        return cls._offline_selection(
            environment, "YOUTUBE", "fake", FakeYouTubeProvider
        )

    @classmethod
    def ensure_provider_allowed(cls, provider, environment=None):
        environment = os.environ if environment is None else environment
        allow_paid = (
            ALLOW_PAID_PROVIDER
            and str(environment.get("ALLOW_PAID_PROVIDER", "false")).lower() == "true"
        )
        if getattr(provider, "is_paid", False) and not allow_paid:
            raise ValueError("Paid provider is disabled by policy")
        return provider

    @classmethod
    def _offline_selection(cls, environment, kind, default, provider_type):
        environment = os.environ if environment is None else environment
        provider_name = environment.get(f"AICOMPANY_{kind}_PROVIDER", default).lower()
        timeout = cls._timeout(
            environment.get(f"AICOMPANY_{kind}_PROVIDER_TIMEOUT", "30")
        )
        if provider_name != "fake":
            raise ValueError(f"Unsupported or disabled {kind.lower()} provider")
        provider = cls.ensure_provider_allowed(provider_type(), environment)
        return ProviderSelection(
            provider,
            environment.get(f"AICOMPANY_{kind}_PROVIDER_MODEL"),
            timeout,
        )

    @staticmethod
    def _timeout(value):
        try:
            timeout_seconds = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError("AICOMPANY_PROVIDER_TIMEOUT must be a positive number") from error
        if timeout_seconds <= 0:
            raise ValueError("AICOMPANY_PROVIDER_TIMEOUT must be a positive number")
        return timeout_seconds
