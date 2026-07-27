from providers.base import AIProvider
from providers.models import ProviderResponse, UsageMetadata


class MockProvider(AIProvider):
    """Deterministic offline provider for tests and local development."""

    @property
    def name(self):
        return "mock"

    def generate(self, request):
        if not request.prompt.strip():
            raise ValueError("Provider request prompt must be non-empty")
        if request.timeout_seconds <= 0:
            raise ValueError("Provider request timeout_seconds must be positive")

        output_text = f"Mock response for: {request.prompt}"
        return ProviderResponse(
            provider=self.name,
            model=request.model or "mock-default",
            output_text=output_text,
            usage=UsageMetadata(
                input_tokens=len(request.prompt.split()),
                output_tokens=len(output_text.split()),
                estimated_cost_usd=0.0,
            ),
        )
