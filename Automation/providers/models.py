from dataclasses import dataclass, field


@dataclass(frozen=True)
class UsageMetadata:
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0

    @property
    def total_tokens(self):
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class ProviderRequest:
    prompt: str
    model: str | None = None
    timeout_seconds: float = 30.0
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderResponse:
    provider: str
    model: str
    output_text: str
    usage: UsageMetadata
    response_id: str | None = None
