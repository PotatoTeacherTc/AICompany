from dataclasses import dataclass, field
import json
from urllib.parse import urlparse
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


@dataclass(frozen=True)
class TextGenerationResult:
    provider: str
    model: str
    output_text: str
    usage: UsageMetadata | dict | None = None


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
            "prompt": request.instruction,
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
            "ollama-local", request.model, output, usage
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
    if request.output_format != "json":
        raise ValueError("only json output is supported")
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
