from abc import ABC, abstractmethod


class AIProvider(ABC):
    @property
    @abstractmethod
    def name(self):
        """Stable provider identifier that is safe to record in usage metadata."""

    @abstractmethod
    def generate(self, request):
        """Return a ProviderResponse or raise a provider-safe error."""
