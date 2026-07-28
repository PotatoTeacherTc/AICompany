from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path

from providers.models import ProviderRequest, UsageMetadata


@dataclass(frozen=True)
class MusicGenerationRequest:
    prompt: str
    workspace_id: str
    mission_id: str
    output_directory: str
    model: str | None = None
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class GeneratedMusicArtifact:
    filename: str
    mime_type: str
    path: str

    def to_dict(self, include_path=False):
        result = asdict(self)
        if not include_path:
            result.pop("path")
        return result


@dataclass(frozen=True)
class MusicGenerationResult:
    provider: str
    model: str
    artifacts: tuple[GeneratedMusicArtifact, ...]
    usage: UsageMetadata | None = None


class MusicProvider(ABC):
    @property
    @abstractmethod
    def name(self):
        """Stable, non-secret provider identifier."""

    @abstractmethod
    def generate_music(self, request):
        """Return MusicGenerationResult or raise a provider-safe exception."""


class FakeMusicProvider(MusicProvider):
    @property
    def name(self):
        return "fake-music"

    def generate_music(self, request):
        if not isinstance(request, MusicGenerationRequest):
            raise ValueError("request must use the MusicGenerationRequest contract")
        if not request.prompt.strip():
            raise ValueError("music request prompt must be non-empty")
        if request.timeout_seconds <= 0:
            raise ValueError("music request timeout_seconds must be positive")
        output_directory = Path(request.output_directory)
        output_directory.mkdir(parents=True, exist_ok=True)
        artifact_path = output_directory / "generated_music.txt"
        artifact_path.write_text(
            "AICompany offline music artifact\n"
            f"Workspace: {request.workspace_id}\n"
            f"Mission: {request.mission_id}\n",
            encoding="utf-8",
        )
        return MusicGenerationResult(
            provider=self.name,
            model=request.model or "fake-music-default",
            artifacts=(
                GeneratedMusicArtifact(
                    filename=artifact_path.name,
                    mime_type="text/plain",
                    path=str(artifact_path),
                ),
            ),
            usage=UsageMetadata(
                input_tokens=len(request.prompt.split()),
                output_tokens=0,
                estimated_cost_usd=0.0,
            ),
        )


class GenericMusicProviderAdapter(MusicProvider):
    """Compatibility adapter for the existing text AIProvider boundary."""

    def __init__(self, provider):
        self.provider = provider

    @property
    def name(self):
        return getattr(self.provider, "name", self.provider.__class__.__name__)

    def generate_music(self, request):
        response = self.provider.generate(
            ProviderRequest(
                prompt=request.prompt,
                model=request.model,
                timeout_seconds=request.timeout_seconds,
                metadata={
                    "workspace_id": request.workspace_id,
                    "mission_id": request.mission_id,
                },
            )
        )
        output_directory = Path(request.output_directory)
        output_directory.mkdir(parents=True, exist_ok=True)
        artifact_path = output_directory / "generated_music.txt"
        output_text = getattr(response, "output_text", "")
        safe_output = (
            output_text.replace(request.prompt, "[request redacted]")
            if isinstance(output_text, str)
            else ""
        )
        artifact_path.write_text(safe_output or "Music generation completed.\n", encoding="utf-8")
        return MusicGenerationResult(
            provider=getattr(response, "provider", self.name),
            model=getattr(response, "model", request.model or "unknown"),
            artifacts=(
                GeneratedMusicArtifact(
                    filename=artifact_path.name,
                    mime_type="text/plain",
                    path=str(artifact_path),
                ),
            ),
            usage=getattr(response, "usage", None),
        )
