from providers.models import ProviderRequest


def get_provider_usage(provider, task_text):
    if provider is None:
        return None

    response = provider.generate(ProviderRequest(prompt=task_text))
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    return {
        "provider": getattr(response, "provider", provider.__class__.__name__),
        "model": getattr(response, "model", None),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": getattr(usage, "total_tokens", input_tokens + output_tokens) or 0,
        "estimated_cost_usd": getattr(usage, "estimated_cost_usd", 0.0) or 0.0,
    }


def provider_error(error):
    return f"ProviderError: {type(error).__name__}"
