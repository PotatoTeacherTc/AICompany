from dataclasses import dataclass, field
import json
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from providers.models import UsageMetadata


TEXT_TASK_TYPES = {
    "LYRICS", "CONTENT_PLAN", "VIDEO_SCRIPT", "TITLE_DESCRIPTION"
}


@dataclass(frozen=True)
class TextGenerationRequest:
    workspace_id: str
    mission_id: str
    task_type: str
    instruction: str
    context: dict = field(default_factory=dict)
    output_format: str = "json"
    maximum_output_size: int = 12000
    model: str | None = None
    timeout_seconds: float = 30.0
    response_schema: dict | None = None


@dataclass(frozen=True)
class TextGenerationResult:
    provider: str
    model: str
    output_text: str
    usage: UsageMetadata | dict | None = None
    finish_reason: str | None = None
    response_id: str | None = None


class TextProviderError(RuntimeError):
    """Safe Provider failure that never includes upstream payloads or errors."""

    def __init__(self, code, provider, retryable=False, correlation_id=None):
        self.code = code
        self.provider = provider
        self.retryable = bool(retryable)
        self.correlation_id = correlation_id
        super().__init__(f"{provider} text provider failed: {code}")


class TextProvider:
    is_paid = False

    def generate_text(self, request):
        raise NotImplementedError


class FakeTextProvider(TextProvider):
    """Deterministic creative provider for offline tests and fallback demos."""

    def generate_text(self, request):
        _validate_request(request)
        templates = {
            "LYRICS": {
                "title": "다시 피는 바람",
                "theme_summary": "이별 뒤 다시 일어서는 희망",
                "lyrics": "어제의 비를 지나\n오늘의 바람을 따라\n나는 다시 피어난다",
                "sections": {
                    "verse": "어제의 비를 지나",
                    "chorus": "나는 다시 피어난다",
                    "outro": "오늘의 바람을 따라",
                },
                "language": "ko",
                "safe_metadata": {"generation_mode": "fake_offline"},
            },
            "CONTENT_PLAN": {
                "title": "다시 피는 바람 영상 기획",
                "concept": "회복과 희망을 따라가는 짧은 음악 영상",
                "target_audience": "감성 음악 콘텐츠 시청자",
                "content_outline": [
                    "비 내린 도시의 도입",
                    "새벽빛과 함께 전환",
                    "희망적인 후렴과 마무리",
                ],
                "visual_direction": "차가운 청색에서 따뜻한 금색으로 전환",
                "publishing_summary": "Fake 미디어 단계 검증용 비공개 영상 기획",
            },
            "VIDEO_SCRIPT": {
                "title": "다시 피는 바람 영상 구성",
                "scenes": [
                    {"scene": 1, "summary": "비 내리는 창가"},
                    {"scene": 2, "summary": "새벽길을 걷는 인물"},
                    {"scene": 3, "summary": "햇빛 아래 열린 풍경"},
                ],
            },
            "TITLE_DESCRIPTION": {
                "title": "다시 피는 바람",
                "description": "이별 뒤 다시 시작하는 마음을 담은 감성 음악 영상",
                "tags": ["희망", "발라드", "감성음악"],
            },
        }
        output = json.dumps(templates[request.task_type], ensure_ascii=False)
        if len(output.encode("utf-8")) > request.maximum_output_size:
            raise ValueError("generated text exceeds maximum size")
        return TextGenerationResult(
            "fake-text",
            request.model or "fake-creative-v1",
            output,
            UsageMetadata(
                input_tokens=len(request.instruction.split()),
                output_tokens=len(output.split()),
                estimated_cost_usd=0.0,
            ),
        )


class OllamaTextProvider(TextProvider):
    """Explicit local-only Ollama adapter. It is never selected by default."""

    def __init__(self, endpoint="http://127.0.0.1:11434", transport=None):
        parsed = urlparse(endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("Ollama endpoint must be loopback HTTP")
        self.endpoint = endpoint.rstrip("/")
        self.transport = transport or self._transport

    def generate_text(self, request):
        _validate_request(request)
        if not request.model:
            raise ValueError("local model is required")
        payload = {
            "model": request.model,
            "prompt": _structured_prompt(request),
            "stream": False,
            "format": request.output_format,
            "options": {"num_predict": min(request.maximum_output_size // 4, 4096)},
        }
        try:
            response = self.transport(
                f"{self.endpoint}/api/generate", payload, request.timeout_seconds
            )
        except TimeoutError:
            raise
        except Exception as error:
            raise ConnectionError("local text provider unavailable") from error
        if not isinstance(response, dict):
            raise ValueError("local provider returned malformed response")
        output = response.get("response")
        if not isinstance(output, str) or not output.strip():
            raise ValueError("local provider returned empty response")
        if len(output.encode("utf-8")) > request.maximum_output_size:
            raise ValueError("generated text exceeds maximum size")
        usage = None
        if any(key in response for key in ("prompt_eval_count", "eval_count")):
            usage = UsageMetadata(
                input_tokens=_non_negative(response.get("prompt_eval_count", 0)),
                output_tokens=_non_negative(response.get("eval_count", 0)),
                estimated_cost_usd=0.0,
            )
        return TextGenerationResult(
            "ollama", request.model, output, usage
        )

    @staticmethod
    def _transport(url, payload, timeout):
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))


class OpenAITextProvider(TextProvider):
    """Explicit paid OpenAI Responses API adapter with injectable transport."""

    is_paid = True
    provider_name = "openai"
    endpoint = "https://api.openai.com/v1/responses"

    def __init__(self, api_key, transport=None):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("OpenAI API key is required")
        self._api_key = api_key.strip()
        self.transport = transport or self._transport

    def generate_text(self, request):
        _validate_request(request)
        if not request.model:
            raise ValueError("OpenAI model is required")
        payload = {
            "model": request.model,
            "input": (
                request.instruction
                if request.output_format == "text" or request.response_schema is not None
                else _structured_prompt(request)
            ),
            "max_output_tokens": min(request.maximum_output_size // 4, 4096),
            "store": False,
            "text": {"format": self._format(request)},
        }
        try:
            response = self.transport(
                self.endpoint,
                payload,
                {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                request.timeout_seconds,
            )
        except TextProviderError:
            raise
        except TimeoutError:
            raise TextProviderError("timeout", self.provider_name, True) from None
        except HTTPError as error:
            status = error.code
            error.close()
            raise self._http_error(status) from None
        except (URLError, OSError, ConnectionError):
            raise TextProviderError("network_error", self.provider_name, True) from None
        except Exception:
            raise TextProviderError("provider_error", self.provider_name, False) from None
        return self._result(response, request)

    @staticmethod
    def _format(request):
        if request.output_format == "text":
            return {"type": "text"}
        if request.response_schema is None:
            return {"type": "json_object"}
        _validate_schema_definition(request.response_schema)
        return {
            "type": "json_schema",
            "name": "aicompany_response",
            "strict": True,
            "schema": request.response_schema,
        }

    def _result(self, response, request):
        if not isinstance(response, dict):
            raise TextProviderError("malformed_response", self.provider_name)
        output = _openai_output_text(response)
        if not output:
            raise TextProviderError("empty_response", self.provider_name)
        if len(output.encode("utf-8")) > request.maximum_output_size:
            raise TextProviderError("response_too_large", self.provider_name)
        if request.output_format == "json":
            try:
                parsed = json.loads(output)
            except (TypeError, json.JSONDecodeError):
                raise TextProviderError("invalid_json", self.provider_name) from None
            if request.response_schema is not None:
                try:
                    _validate_schema_value(parsed, request.response_schema)
                except ValueError:
                    raise TextProviderError("schema_validation_failed", self.provider_name) from None
        usage = _openai_usage(response.get("usage"))
        return TextGenerationResult(
            self.provider_name,
            response.get("model") if isinstance(response.get("model"), str) else request.model,
            output,
            usage,
            finish_reason=_safe_identifier(response.get("status")),
            response_id=_safe_identifier(response.get("id")),
        )

    def _http_error(self, status):
        if status in {401, 403}:
            return TextProviderError("authentication_failed", self.provider_name)
        if status == 429:
            return TextProviderError("rate_limited", self.provider_name, True)
        if isinstance(status, int) and status >= 500:
            return TextProviderError("provider_unavailable", self.provider_name, True)
        return TextProviderError("request_rejected", self.provider_name)

    @staticmethod
    def _transport(url, payload, headers, timeout):
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))


def _validate_request(request):
    if not isinstance(request, TextGenerationRequest):
        raise TypeError("request must use TextGenerationRequest")
    for value, name in (
        (request.workspace_id, "workspace_id"),
        (request.mission_id, "mission_id"),
        (request.instruction, "instruction"),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be non-empty")
    if request.task_type not in TEXT_TASK_TYPES:
        raise ValueError("unsupported text task type")
    if request.output_format not in {"json", "text"}:
        raise ValueError("output_format must be json or text")
    if request.response_schema is not None:
        if request.output_format != "json":
            raise ValueError("response_schema requires json output")
        _validate_schema_definition(request.response_schema)
    if (
        not isinstance(request.maximum_output_size, int)
        or isinstance(request.maximum_output_size, bool)
        or not 256 <= request.maximum_output_size <= 100000
    ):
        raise ValueError("maximum_output_size is invalid")
    if (
        not isinstance(request.timeout_seconds, (int, float))
        or isinstance(request.timeout_seconds, bool)
        or request.timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be positive")


def _non_negative(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _structured_prompt(request):
    schemas = {
        "LYRICS": (
            '{"title":"string","theme_summary":"string","lyrics":"string",'
            '"sections":{"verse":"string","chorus":"string","outro":"string"},'
            '"language":"ko","safe_metadata":{"generation_mode":"local"}}'
        ),
        "CONTENT_PLAN": (
            '{"title":"string","concept":"string","target_audience":"string",'
            '"content_outline":["string"],"visual_direction":"string",'
            '"publishing_summary":"string"}'
        ),
        "VIDEO_SCRIPT": (
            '{"title":"string","scenes":[{"scene":1,"summary":"string"}]}'
        ),
        "TITLE_DESCRIPTION": (
            '{"title":"string","description":"string","tags":["string"]}'
        ),
    }
    return (
        "Return exactly one valid JSON object with no markdown or commentary. "
        f"Use this exact shape and value types: {schemas[request.task_type]}\n"
        "Write the creative content in Korean.\n"
        f"Creative instruction: {request.instruction}"
    )


def _openai_output_text(response):
    parts = []
    output = response.get("output")
    if not isinstance(output, list):
        return None
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for value in content:
            if isinstance(value, dict) and value.get("type") == "output_text":
                text = value.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
    return "".join(parts).strip() or None


def _openai_usage(value):
    if not isinstance(value, dict):
        return None
    present = any(key in value for key in ("input_tokens", "output_tokens", "total_tokens"))
    if not present:
        return None
    input_tokens = _optional_non_negative(value.get("input_tokens"))
    output_tokens = _optional_non_negative(value.get("output_tokens"))
    total_tokens = _optional_non_negative(value.get("total_tokens"))
    result = {
        "estimated_cost_usd": None,
    }
    if input_tokens is not None:
        result["input_tokens"] = input_tokens
    if output_tokens is not None:
        result["output_tokens"] = output_tokens
    if total_tokens is not None:
        result["total_tokens"] = total_tokens
    elif input_tokens is not None and output_tokens is not None:
        result["total_tokens"] = input_tokens + output_tokens
    return result


def _optional_non_negative(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _safe_identifier(value):
    if (
        isinstance(value, str)
        and 0 < len(value) <= 128
        and all(character.isalnum() or character in "._:-" for character in value)
    ):
        return value
    return None


def _validate_schema_definition(schema, depth=0):
    if depth > 8:
        raise ValueError("response schema is too deep")
    if not isinstance(schema, dict) or schema.get("type") not in {
        "object", "array", "string", "integer", "number", "boolean"
    }:
        raise ValueError("response schema is invalid")
    if schema.get("type") == "object":
        properties = schema.get("properties")
        required = schema.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list):
            raise ValueError("response schema is invalid")
        if any(key not in properties for key in required):
            raise ValueError("response schema is invalid")
        if len(properties) > 100:
            raise ValueError("response schema is too large")
        for nested in properties.values():
            _validate_schema_definition(nested, depth + 1)
    if schema.get("type") == "array":
        _validate_schema_definition(schema.get("items"), depth + 1)


def _validate_schema_value(value, schema):
    kind = schema.get("type")
    matches = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
    }
    if kind not in matches or not matches[kind](value):
        raise ValueError("schema type mismatch")
    if kind == "object":
        required = schema.get("required", [])
        if any(key not in value for key in required):
            raise ValueError("schema required field missing")
        properties = schema.get("properties", {})
        for key, nested in properties.items():
            if key in value:
                _validate_schema_value(value[key], nested)
        if schema.get("additionalProperties") is False and any(
            key not in properties for key in value
        ):
            raise ValueError("schema additional field")
    if kind == "array":
        for item in value:
            _validate_schema_value(item, schema["items"])
