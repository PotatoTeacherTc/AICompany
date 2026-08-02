from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
import json
import re
import struct
import time
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
import zlib

from providers.models import UsageMetadata


@dataclass(frozen=True)
class MediaArtifact:
    filename: str
    mime_type: str
    path: str

    def to_dict(self, include_path=False):
        value = asdict(self)
        if not include_path:
            value.pop("path")
        return value


@dataclass(frozen=True)
class ImageGenerationRequest:
    prompt: str
    workspace_id: str
    mission_id: str
    output_directory: str
    model: str | None = None
    timeout_seconds: float = 30.0
    purpose: str = "IMAGE"
    width: int = 512
    height: int = 512
    seed: int = 0
    steps: int = 4
    guidance: float = 3.5
    negative_prompt: str = ""
    workflow_profile: str = "default"


@dataclass(frozen=True)
class VideoGenerationRequest:
    prompt: str
    workspace_id: str
    mission_id: str
    output_directory: str
    input_artifacts: tuple[dict, ...] = ()
    model: str | None = None
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class MediaGenerationResult:
    provider: str
    model: str
    artifacts: tuple[MediaArtifact, ...]
    usage: UsageMetadata | None = None


class ImageProvider(ABC):
    is_paid = False

    @property
    @abstractmethod
    def name(self):
        pass

    @abstractmethod
    def generate_image(self, request):
        pass


class VideoProvider(ABC):
    is_paid = False

    @property
    @abstractmethod
    def name(self):
        pass

    @abstractmethod
    def generate_video(self, request):
        pass


class FakeImageProvider(ImageProvider):
    @property
    def name(self):
        return "fake-image"

    def generate_image(self, request):
        return self._generate(request, "generated_image.png", "image/png", "fake-image-default")

    @staticmethod
    def _generate(request, filename, mime_type, model):
        output = Path(request.output_directory)
        output.mkdir(parents=True, exist_ok=True)
        path = output / filename
        path.write_bytes(_deterministic_png(request.width, request.height, request.seed))
        return MediaGenerationResult(
            "fake-image",
            request.model or model,
            (MediaArtifact(path.name, mime_type, str(path)),),
            UsageMetadata(input_tokens=len(request.prompt.split()), estimated_cost_usd=0.0),
        )


class ComfyUIImageProvider(ImageProvider):
    """Credential-free, loopback-only ComfyUI workflow adapter."""

    _ALLOWED_NODES = {
        "CheckpointLoaderSimple", "CLIPTextEncode", "EmptyLatentImage",
        "KSampler", "VAEDecode", "SaveImage",
    }

    def __init__(self, endpoint, workflow_path, model, transport=None,
                 poll_interval=0.25, max_polls=120):
        parsed = urlparse(endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment
        ):
            raise ValueError("ComfyUI endpoint must be loopback HTTP")
        path = Path(workflow_path)
        if not path.is_file() or path.is_symlink():
            raise ValueError("ComfyUI workflow is unavailable")
        if not isinstance(model, str) or not model.strip() or Path(model).name != model:
            raise ValueError("ComfyUI model must be a configured filename")
        if not isinstance(max_polls, int) or not 1 <= max_polls <= 1000:
            raise ValueError("ComfyUI polling limit is invalid")
        self.endpoint = endpoint.rstrip("/")
        self.workflow_path = path.resolve()
        self.model = model.strip()
        self.transport = transport or self._transport
        self.poll_interval = max(0, float(poll_interval))
        self.max_polls = max_polls

    @property
    def name(self):
        return "comfyui"

    def generate_image(self, request):
        workflow = self._workflow(request)
        deadline = time.monotonic() + request.timeout_seconds
        submitted = self.transport("POST", "/prompt", {"prompt": workflow}, request.timeout_seconds)
        prompt_id = submitted.get("prompt_id") if isinstance(submitted, dict) else None
        if not isinstance(prompt_id, str) or not prompt_id:
            raise ValueError("ComfyUI returned no job identifier")
        output = None
        for _ in range(self.max_polls):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("ComfyUI generation timed out")
            history = self.transport("GET", f"/history/{prompt_id}", None, remaining)
            output = self._output(history, prompt_id)
            if output is not None:
                break
            if self.poll_interval:
                time.sleep(self.poll_interval)
        if output is None:
            raise TimeoutError("ComfyUI generation timed out")
        query = urlencode({key: output[key] for key in ("filename", "subfolder", "type")})
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("ComfyUI generation timed out")
        content = self.transport("GET_BYTES", f"/view?{query}", None, remaining)
        if not isinstance(content, bytes) or not content:
            raise ValueError("ComfyUI returned an empty image")
        suffix = Path(output["filename"]).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("ComfyUI output format is unsupported")
        destination = Path(request.output_directory) / f"{request.purpose.lower()}{suffix}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        return MediaGenerationResult(
            self.name, request.model or self.model,
            (MediaArtifact(destination.name, {
                ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp",
            }[suffix], str(destination)),),
            UsageMetadata(estimated_cost_usd=0.0),
        )

    def _workflow(self, request):
        try:
            value = json.loads(self.workflow_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("ComfyUI workflow is invalid") from error
        if not isinstance(value, dict) or not value or len(value) > 100:
            raise ValueError("ComfyUI workflow is invalid")
        for node in value.values():
            if not isinstance(node, dict) or node.get("class_type") not in self._ALLOWED_NODES:
                raise ValueError("ComfyUI workflow contains an unsupported node")
            if not isinstance(node.get("inputs"), dict):
                raise ValueError("ComfyUI workflow is invalid")
        serialized = json.dumps(value, ensure_ascii=False)
        if any(marker not in serialized for marker in (
            "{{MODEL}}", "{{PROMPT}}", "{{NEGATIVE_PROMPT}}", "{{WIDTH}}",
            "{{HEIGHT}}", "{{SEED}}", "{{STEPS}}", "{{GUIDANCE}}",
        )):
            raise ValueError("ComfyUI workflow placeholders are incomplete")
        if _unsafe_workflow_value(value):
            raise ValueError("ComfyUI workflow contains an unsafe value")
        replacements = {
            "{{MODEL}}": request.model or self.model,
            "{{PROMPT}}": request.prompt,
            "{{NEGATIVE_PROMPT}}": request.negative_prompt,
            "{{WIDTH}}": request.width, "{{HEIGHT}}": request.height,
            "{{SEED}}": request.seed, "{{STEPS}}": request.steps,
            "{{GUIDANCE}}": request.guidance,
        }
        return _replace_workflow(value, replacements)

    @staticmethod
    def _output(history, prompt_id):
        job = history.get(prompt_id) if isinstance(history, dict) else None
        outputs = job.get("outputs") if isinstance(job, dict) else None
        if outputs is None:
            return None
        if not isinstance(outputs, dict):
            raise ValueError("ComfyUI history is invalid")
        for node in outputs.values():
            for item in node.get("images", ()) if isinstance(node, dict) else ():
                if not isinstance(item, dict):
                    continue
                filename = item.get("filename")
                subfolder = item.get("subfolder", "")
                output_type = item.get("type", "output")
                path = PurePosixPath(str(subfolder).replace("\\", "/"))
                if (
                    not isinstance(filename, str) or Path(filename).name != filename
                    or path.is_absolute() or ".." in path.parts
                    or output_type not in {"output", "temp"}
                ):
                    raise ValueError("ComfyUI output reference is unsafe")
                return {"filename": filename, "subfolder": path.as_posix(), "type": output_type}
        raise ValueError("ComfyUI returned no image")

    def _transport(self, method, path, payload, timeout):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self.endpoint + path, data=body, method="POST" if method == "POST" else "GET",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=timeout) as response:
            content = response.read()
        if method == "GET_BYTES":
            return content
        return json.loads(content.decode("utf-8"))


def _replace_workflow(value, replacements):
    if isinstance(value, dict):
        return {key: _replace_workflow(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_workflow(item, replacements) for item in value]
    return replacements.get(value, value)


def _unsafe_workflow_value(value):
    if isinstance(value, dict):
        return any(_unsafe_workflow_value(item) for item in value.values())
    if isinstance(value, list):
        return any(_unsafe_workflow_value(item) for item in value)
    if not isinstance(value, str) or value.startswith("{{"):
        return False
    normalized = value.replace("\\", "/")
    return bool(re.match(r"^[A-Za-z]:/", normalized)) or normalized.startswith("/") or "../" in normalized


def _deterministic_png(width, height, seed):
    width = max(1, min(int(width), 4096))
    height = max(1, min(int(height), 4096))
    color = ((seed * 31) % 256, (seed * 67 + 53) % 256, (seed * 97 + 101) % 256)
    raw = b"".join(b"\x00" + bytes(color) * width for _ in range(height))
    def chunk(kind, content):
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


class FakeVideoProvider(VideoProvider):
    @property
    def name(self):
        return "fake-video"

    def generate_video(self, request):
        output = Path(request.output_directory)
        output.mkdir(parents=True, exist_ok=True)
        path = output / "generated_video.txt"
        references = ",".join(
            artifact.get("artifact_id", "") for artifact in request.input_artifacts
        )
        path.write_text(
            f"AICompany offline video artifact\nMission: {request.mission_id}\n"
            f"References: {references}\n",
            encoding="utf-8",
        )
        return MediaGenerationResult(
            self.name,
            request.model or "fake-video-default",
            (MediaArtifact(path.name, "video/fake", str(path)),),
            UsageMetadata(input_tokens=len(request.prompt.split()), estimated_cost_usd=0.0),
        )


@dataclass(frozen=True)
class YouTubeUploadRequest:
    workspace_id: str
    mission_id: str
    artifact: dict
    title: str
    description: str = ""
    tags: tuple[str, ...] = ()
    visibility: str = "private"
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class YouTubeUploadResult:
    provider: str
    upload_id: str
    status: str
    visibility: str
    usage: UsageMetadata | None = None


class YouTubeProvider(ABC):
    is_paid = False

    @property
    @abstractmethod
    def name(self):
        pass

    @abstractmethod
    def upload(self, request):
        pass


class FakeYouTubeProvider(YouTubeProvider):
    @property
    def name(self):
        return "fake-youtube"

    def upload(self, request):
        if request.visibility not in {"private", "unlisted", "public"}:
            raise ValueError("unsupported visibility")
        if request.artifact.get("workspace_id") != request.workspace_id:
            raise ValueError("artifact workspace mismatch")
        return YouTubeUploadResult(
            self.name,
            f"fake-{request.mission_id}",
            "SIMULATED",
            request.visibility,
            UsageMetadata(estimated_cost_usd=0.0),
        )
