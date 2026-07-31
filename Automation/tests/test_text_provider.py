import os
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError

from providers.factory import ProviderFactory
from providers.text import (
    FakeTextProvider,
    OllamaTextProvider,
    OpenAITextProvider,
    TextGenerationRequest,
    TextGenerationResult,
    TextProviderError,
)


def request(task_type="LYRICS", model=None, timeout_seconds=30.0, **values):
    return TextGenerationRequest(
        "workspace-a", "mission-a", task_type,
        "Create safe structured creative text",
        model=model, timeout_seconds=timeout_seconds, **values,
    )


def openai_response(text='{"title":"Result"}', usage=None):
    return {
        "id": "resp_safe_123", "status": "completed", "model": "test-model",
        "output": [{"type": "message", "content": [
            {"type": "output_text", "text": text}
        ]}],
        "usage": usage,
    }


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
        self.assertEqual("ollama", result.provider)
        self.assertIn("Return exactly one valid JSON object", captured["payload"]["prompt"])
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

    def test_openai_factory_requires_explicit_paid_opt_in_model_and_key(self):
        base = {"AICOMPANY_TEXT_PROVIDER": "openai", "AICOMPANY_TEXT_MODEL": "test-model"}
        with self.assertRaisesRegex(ValueError, "disabled"):
            ProviderFactory.text_from_environment(base)
        with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
            ProviderFactory.text_from_environment(dict(base, ALLOW_PAID_PROVIDER="true"))
        selection = ProviderFactory.text_from_environment(dict(
            base, ALLOW_PAID_PROVIDER="true", OPENAI_API_KEY="test-value"
        ), transport=lambda *_: openai_response())
        self.assertIsInstance(selection.provider, OpenAITextProvider)

    def test_openai_file_secret_takes_precedence_without_exposure(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "credential"
            path.write_text("file-value", encoding="utf-8")
            captured = {}
            def transport(_url, _payload, headers, _timeout):
                captured.update(headers)
                return openai_response()
            selection = ProviderFactory.text_from_environment({
                "AICOMPANY_TEXT_PROVIDER": "openai",
                "AICOMPANY_TEXT_MODEL": "test-model",
                "ALLOW_PAID_PROVIDER": "true",
                "OPENAI_API_KEY": "direct-value",
                "OPENAI_API_KEY_FILE": str(path),
            }, transport=transport)
            selection.provider.generate_text(request(model="test-model"))
            self.assertEqual("Bearer file-value", captured["Authorization"])

    def test_openai_plain_text_and_structured_results_use_common_contract(self):
        plain = OpenAITextProvider(
            "test-value", transport=lambda *_: openai_response("plain result")
        ).generate_text(request(model="test-model", output_format="text"))
        self.assertIsInstance(plain, TextGenerationResult)
        self.assertEqual("plain result", plain.output_text)
        self.assertEqual("resp_safe_123", plain.response_id)
        self.assertEqual("completed", plain.finish_reason)

        schema = {
            "type": "object", "additionalProperties": False,
            "properties": {"title": {"type": "string"}}, "required": ["title"],
        }
        structured = OpenAITextProvider(
            "test-value", transport=lambda *_: openai_response()
        ).generate_text(request(
            model="test-model", response_schema=schema,
        ))
        self.assertEqual("openai", structured.provider)
        self.assertEqual("test-model", structured.model)

    def test_openai_usage_is_partial_safe_and_cost_is_not_invented(self):
        values = (
            ({"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}, 5),
            ({"output_tokens": 3}, None),
            (None, None),
        )
        for usage, total in values:
            result = OpenAITextProvider(
                "test-value", transport=lambda *_args, value=usage: openai_response(usage=value)
            ).generate_text(request(model="unknown-model"))
            if usage is None:
                self.assertIsNone(result.usage)
            else:
                self.assertEqual(total, result.usage.get("total_tokens"))
                self.assertIsNone(result.usage["estimated_cost_usd"])

    def test_openai_safe_error_mapping_and_retryability(self):
        failures = (
            (lambda *_: (_ for _ in ()).throw(TimeoutError("raw prompt")), "timeout", True),
            (lambda *_: (_ for _ in ()).throw(OSError("raw credential")), "network_error", True),
            (lambda *_: (_ for _ in ()).throw(HTTPError("url", 401, "raw", None, None)), "authentication_failed", False),
            (lambda *_: (_ for _ in ()).throw(HTTPError("url", 429, "raw", None, None)), "rate_limited", True),
            (lambda *_: (_ for _ in ()).throw(HTTPError("url", 503, "raw", None, None)), "provider_unavailable", True),
            (lambda *_: (_ for _ in ()).throw(HTTPError("url", 400, "unknown model", None, None)), "request_rejected", False),
        )
        for transport, code, retryable in failures:
            with self.assertRaises(TextProviderError) as caught:
                OpenAITextProvider("test-value", transport=transport).generate_text(
                    request(model="test-model")
                )
            self.assertEqual(code, caught.exception.code)
            self.assertEqual(retryable, caught.exception.retryable)
            self.assertNotIn("raw", str(caught.exception))
            self.assertNotIn("test-value", str(caught.exception))

    def test_openai_malformed_json_schema_and_provider_objects_are_rejected(self):
        schema = {
            "type": "object",
            "properties": {"title": {"type": "string"}}, "required": ["title"],
        }
        for response, code in (
            ([], "malformed_response"),
            (openai_response(""), "empty_response"),
            (openai_response("not-json"), "invalid_json"),
            (openai_response('{"missing":"title"}'), "schema_validation_failed"),
        ):
            with self.assertRaises(TextProviderError) as caught:
                OpenAITextProvider(
                    "test-value", transport=lambda *_args, value=response: value
                ).generate_text(request(model="test-model", response_schema=schema))
            self.assertEqual(code, caught.exception.code)


@unittest.skipUnless(
    os.environ.get("AICOMPANY_RUN_OPENAI_SMOKE", "false").lower() == "true"
    and os.environ.get("AICOMPANY_TEXT_PROVIDER", "").lower() == "openai"
    and os.environ.get("ALLOW_PAID_PROVIDER", "false").lower() == "true"
    and bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY_FILE"))
    and bool(os.environ.get("AICOMPANY_TEXT_MODEL")),
    "explicit paid OpenAI smoke conditions are not enabled",
)
class OpenAITextProviderSmokeTests(unittest.TestCase):
    def test_explicit_real_plain_text_request(self):
        selection = ProviderFactory.text_from_environment(dict(os.environ))
        result = selection.provider.generate_text(request(
            model=selection.default_model, output_format="text",
            timeout_seconds=selection.timeout_seconds,
        ))
        self.assertTrue(result.output_text.strip())
        self.assertEqual("openai", result.provider)


if __name__ == "__main__":
    unittest.main()
