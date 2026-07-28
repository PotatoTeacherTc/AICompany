import os
from dataclasses import dataclass

from providers.mock_provider import MockProvider
from providers.music import FakeMusicProvider


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

    @staticmethod
    def _timeout(value):
        try:
            timeout_seconds = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError("AICOMPANY_PROVIDER_TIMEOUT must be a positive number") from error
        if timeout_seconds <= 0:
            raise ValueError("AICOMPANY_PROVIDER_TIMEOUT must be a positive number")
        return timeout_seconds
