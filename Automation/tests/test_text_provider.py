import unittest

from providers.factory import ProviderFactory
from providers.text import (
    FakeTextProvider,
    OllamaTextProvider,
    TextGenerationRequest,
)


def request(task_type="LYRICS", model=None, timeout_seconds=30.0):
    return TextGenerationRequest(
        "workspace-a", "mission-a", task_type,
        "Create safe structured creative text",
        model=model, timeout_seconds=timeout_seconds,
    )


class TextProviderTests(unittest.TestCase):
    def test_fake_generates_all_supported_creative_types(self):
        provider = FakeTextProvider()
        for task_type in (
            "LYRICS", "CONTENT_PLAN", "VIDEO_SCRIPT", "TITLE_DESCRIPTION"
        ):
            result = provider.generate_text(request(task_type))
            self.assertEqual("fake-text", result.provider)
            self.assertTrue(result.output_text)
            self.assertEqual(0.0, result.usage.estimated_cost_usd)

    def test_factory_defaults_to_fake_and_ollama_requires_explicit_model(self):
        selection = ProviderFactory.text_from_environment({})
        self.assertIsInstance(selection.provider, FakeTextProvider)
        with self.assertRaises(ValueError):
            ProviderFactory.text_from_environment({
                "AICOMPANY_TEXT_PROVIDER": "ollama"
            })

    def test_ollama_uses_injected_transport_and_full_usage(self):
        captured = {}

        def transport(url, payload, timeout):
            captured.update(url=url, payload=payload, timeout=timeout)
            return {
                "response": '{"title":"Local result"}',
                "prompt_eval_count": 3,
                "eval_count": 4,
            }

        selection = ProviderFactory.text_from_environment({
            "AICOMPANY_TEXT_PROVIDER": "ollama",
            "AICOMPANY_TEXT_MODEL": "local-model",
            "AICOMPANY_TEXT_PROVIDER_TIMEOUT": "5",
        }, transport=transport)
        result = selection.provider.generate_text(
            request(
                model=selection.default_model,
                timeout_seconds=selection.timeout_seconds,
            )
        )
        self.assertEqual("ollama-local", result.provider)
        self.assertEqual(7, result.usage.total_tokens)
        self.assertEqual(5, captured["timeout"])

    def test_partial_or_missing_usage_is_allowed(self):
        for response in (
            {"response": '{"title":"partial"}', "eval_count": 2},
            {"response": '{"title":"missing"}'},
        ):
            provider = OllamaTextProvider(
                transport=lambda *_args, value=response: value
            )
            result = provider.generate_text(request(model="local"))
            if "eval_count" in response:
                self.assertEqual(2, result.usage.output_tokens)
            else:
                self.assertIsNone(result.usage)

    def test_timeout_connection_malformed_and_empty_are_safe_types(self):
        failures = (
            (lambda *_: (_ for _ in ()).throw(TimeoutError()), TimeoutError),
            (lambda *_: (_ for _ in ()).throw(OSError("secret")), ConnectionError),
            (lambda *_: [], ValueError),
            (lambda *_: {"response": ""}, ValueError),
        )
        for transport, error_type in failures:
            with self.assertRaises(error_type):
                OllamaTextProvider(transport=transport).generate_text(
                    request(model="local")
                )

    def test_non_loopback_and_paid_or_unknown_provider_are_blocked(self):
        with self.assertRaises(ValueError):
            OllamaTextProvider("https://example.com")
        with self.assertRaises(ValueError):
            ProviderFactory.text_from_environment({
                "AICOMPANY_TEXT_PROVIDER": "paid-service",
                "ALLOW_PAID_PROVIDER": "true",
            })


if __name__ == "__main__":
    unittest.main()
