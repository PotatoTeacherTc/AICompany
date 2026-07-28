from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path

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
        return self._generate(request, "generated_image.txt", "image/fake", "fake-image-default")

    @staticmethod
    def _generate(request, filename, mime_type, model):
        output = Path(request.output_directory)
        output.mkdir(parents=True, exist_ok=True)
        path = output / filename
        path.write_text(
            f"AICompany offline image artifact\nMission: {request.mission_id}\n",
            encoding="utf-8",
        )
        return MediaGenerationResult(
            "fake-image",
            request.model or model,
            (MediaArtifact(path.name, mime_type, str(path)),),
            UsageMetadata(input_tokens=len(request.prompt.split()), estimated_cost_usd=0.0),
        )


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
