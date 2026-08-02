from dataclasses import dataclass, field
import json
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from providers.models import UsageMetadata


TEXT_TASK_TYPES = {
    "LYRICS", "CONTENT_PLAN", "VIDEO_SCRIPT", "TITLE_DESCRIPTION",
    "MUSIC_PLAN",
    "CONTENT_BRIEF",
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
            "MUSIC_PLAN": {
                "title_candidates": ["다시 걷는 밤", "새벽의 약속", "일어서는 마음"],
                "primary_title": "다시 걷는 밤",
                "concept_summary": "이별 뒤 스스로를 회복하는 한국어 발라드",
                "target_listener": "감성 발라드를 듣는 성인 청자",
                "genre": "Korean ballad",
                "subgenres": ["pop ballad"],
                "mood": ["reflective", "hopeful"],
                "tempo_bpm": 74,
                "key_or_tonality": "minor verses, hopeful major lift",
                "time_signature": "4/4",
                "song_structure": ["Intro", "Verse 1", "Pre-Chorus", "Chorus", "Verse 2", "Chorus", "Bridge", "Final Chorus", "Outro"],
                "instrumentation": ["piano", "strings", "soft drums", "bass"],
                "vocal_style": "warm, restrained solo vocal with a stronger final chorus",
                "language": "ko",
                "lyrical_theme": "이별 이후의 회복",
                "lyrical_direction": "구체적인 새벽 풍경으로 시작해 자기 확신으로 마무리",
                "production_direction": "intimate piano opening, gradual orchestral build, clean vocal-forward mix",
                "reference_style_notes": "contemporary Korean pop ballad without imitating a specific artist or song",
                "negative_constraints": ["no artist imitation", "no excessive vocal runs", "no abrupt genre switch"],
                "suno_style_prompt": "Korean pop ballad, 74 BPM, intimate piano, warm strings, restrained drums, emotional solo vocal, gradual hopeful build",
                "suno_lyrics_prompt": "한국어 가사. 이별 뒤 새벽길을 걸으며 다시 일어서는 화자. 절제된 벌스와 기억에 남는 희망적 후렴, 브리지와 마지막 후렴 포함.",
                "suno_exclude_prompt": "artist imitation, EDM drop, aggressive rap, excessive melisma",
                "recommended_settings": {"duration_seconds": 210, "explicit_content": False, "vocal_mode": "solo"},
                "variations": [
                    {"name": "piano intimate", "style_prompt": "minimal Korean piano ballad, close vocal, 72 BPM", "direction": "감정을 절제한 소규모 편곡"},
                    {"name": "cinematic lift", "style_prompt": "cinematic Korean pop ballad, strings and full final chorus, 76 BPM", "direction": "후반부의 큰 상승감을 강조"}
                ],
                "quality_checklist": ["제목과 후렴의 정서가 일치하는지 확인", "보컬이 가사를 가리지 않는지 확인", "특정 아티스트 모사가 없는지 확인"],
                "assumptions": ["선택 입력이 없어 한국어 솔로 보컬을 가정"],
                "warnings": ["생성 서비스의 결과는 시도마다 달라질 수 있음"],
                "next_action": "Suno에서 제목, 스타일, 가사 지시와 제외 요소를 복사해 2~3개 변형을 수동 생성한 뒤 선호 음원을 보관하세요."
            },
            "CONTENT_BRIEF": {
                "project_title": "다시 걷는 밤 콘텐츠 프로젝트",
                "core_message": "상실 이후에도 다시 앞으로 나아갈 수 있다",
                "content_goal": "음악의 회복 서사를 일관된 시각·글·영상 콘텐츠로 전달",
                "target_audience": "감성적인 한국어 음악 콘텐츠를 찾는 성인 청자",
                "listener_profile": "조용한 공감과 희망적인 결말을 선호하는 모바일 중심 청자",
                "emotional_arc": ["고요한 상실", "내면의 성찰", "작은 결심", "따뜻한 회복"],
                "mood_keywords": ["reflective", "warm", "hopeful", "cinematic"],
                "visual_concept": "비가 그친 새벽 도시에서 햇빛이 드는 열린 길로 이동",
                "visual_style": "cinematic editorial realism with restrained symbolism",
                "color_direction": "차가운 청회색에서 부드러운 금색으로 전환",
                "thumbnail_direction": "새벽빛 속 한 인물과 열린 길, 간결한 제목 공간",
                "video_direction": "음악 구조에 맞춰 정적인 도입에서 넓은 풍경의 마지막 후렴으로 확장",
                "blog_direction": "곡의 회복 메시지와 제작 의도를 설명하는 짧은 에디토리얼",
                "youtube_direction": "음악과 회복 서사를 중심으로 과장 없는 제목·설명 구성",
                "seo_primary_keywords": ["한국어 발라드", "회복 노래", "감성 음악"],
                "seo_secondary_keywords": ["이별 후 희망", "새벽 감성", "힐링 음악"],
                "title_keywords": ["다시", "새벽", "회복"],
                "image_requirements": ["16:9 landscape", "no embedded text", "consistent lead character"],
                "blog_requirements": ["공감형 도입", "곡의 핵심 메시지", "과장 없는 제작 노트"],
                "video_requirements": ["audio-synced pacing", "safe transitions", "source attribution checklist"],
                "youtube_requirements": ["private-first workflow", "concise description", "relevant tags only"],
                "prohibited_elements": ["named artist imitation", "misleading claims", "graphic imagery"],
                "safety_notes": ["사용 권리가 확인된 시각 자료만 사용"],
                "assumptions": ["추가 타깃 입력이 없어 음악 기획의 청자를 유지"],
                "source_summary": "한국어 발라드 음악 기획과 검증된 완성 음원 metadata를 기반으로 함",
                "next_steps": ["IMAGE_PACKAGE", "BLOG_PACKAGE", "VIDEO_PACKAGE", "YOUTUBE_PACKAGE", "PUBLISHING"]
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
        "MUSIC_PLAN": '{"music_plan":"use the caller supplied response_schema"}',
        "CONTENT_BRIEF": '{"content_brief":"use the caller supplied response_schema"}',
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
