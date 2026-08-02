import os
from dataclasses import dataclass

from providers.mock_provider import MockProvider
from providers.music import FakeMusicProvider
from providers.content_media import (
    ComfyUIImageProvider,
    FakeImageProvider,
    FakeVideoProvider,
    FFmpegVideoProvider,
    FakeYouTubeProvider,
)
from providers.text import FakeTextProvider, OllamaTextProvider, OpenAITextProvider
from providers.naver_blog import FakeNaverBlogBrowser, PlaywrightNaverBlogBrowser
from core.production_config import resolve_secret_value
from core.structured_logging import LogLevel, safe_log


@dataclass(frozen=True)
class ProviderSelection:
    provider: object
    default_model: str | None
    timeout_seconds: float
    paid_allowed: bool = False


class ProviderFactory:
    """Builds offline-safe providers from configuration without exposing secrets."""

    @classmethod
    def from_environment(cls, environment=None):
        environment = os.environ if environment is None else environment
        provider_name = environment.get("AICOMPANY_PROVIDER", "mock").lower()
        timeout_seconds = cls._timeout(environment.get("AICOMPANY_PROVIDER_TIMEOUT", "30"))

        if provider_name == "mock":
            return ProviderSelection(
                provider=MockProvider(),
                default_model=environment.get("AICOMPANY_PROVIDER_MODEL"),
                timeout_seconds=timeout_seconds,
            )

        raise ValueError(f"Unsupported AI provider: {provider_name}")

    @classmethod
    def music_from_environment(cls, environment=None):
        environment = os.environ if environment is None else environment
        provider_name = environment.get("AICOMPANY_MUSIC_PROVIDER", "fake").lower()
        timeout_seconds = cls._timeout(
            environment.get("AICOMPANY_MUSIC_PROVIDER_TIMEOUT", "30")
        )
        if provider_name == "fake":
            return ProviderSelection(
                provider=FakeMusicProvider(),
                default_model=environment.get("AICOMPANY_MUSIC_PROVIDER_MODEL"),
                timeout_seconds=timeout_seconds,
            )
        raise ValueError(f"Unsupported music provider: {provider_name}")

    @classmethod
    def image_from_environment(cls, environment=None, transport=None):
        environment = os.environ if environment is None else environment
        provider_name = environment.get("AICOMPANY_IMAGE_PROVIDER", "fake").lower()
        timeout = cls._timeout(environment.get("AICOMPANY_IMAGE_PROVIDER_TIMEOUT", "30"))
        model = environment.get("AICOMPANY_IMAGE_MODEL")
        if provider_name == "fake":
            provider = FakeImageProvider()
            model = model or "fake-image-default"
        elif provider_name == "comfyui":
            workflow = environment.get("AICOMPANY_COMFYUI_WORKFLOW_PATH")
            if not workflow or not model:
                raise ValueError("ComfyUI workflow and model are required")
            provider = ComfyUIImageProvider(
                environment.get("AICOMPANY_COMFYUI_ENDPOINT", "http://127.0.0.1:8188"),
                workflow, model, transport=transport,
                max_polls=min(1000, int(timeout / 0.25) + 1),
            )
        else:
            raise ValueError("Unsupported or disabled image provider")
        provider = cls.ensure_provider_allowed(provider, environment)
        return ProviderSelection(provider, model, timeout)

    @classmethod
    def video_from_environment(cls, environment=None):
        environment = os.environ if environment is None else environment
        provider_name = environment.get("AICOMPANY_VIDEO_PROVIDER", "fake").lower()
        timeout = cls._timeout(environment.get("AICOMPANY_VIDEO_PROVIDER_TIMEOUT", "120"))
        if provider_name == "fake":
            provider, model = FakeVideoProvider(), "fake-video-default"
        elif provider_name == "ffmpeg":
            provider, model = FFmpegVideoProvider(
                environment.get("AICOMPANY_FFMPEG_PATH", "ffmpeg"),
                environment.get("AICOMPANY_FFPROBE_PATH", "ffprobe"),
            ), "ffmpeg-h264-aac"
        else:
            raise ValueError("Unsupported or disabled video provider")
        return ProviderSelection(cls.ensure_provider_allowed(provider, environment),
                                 environment.get("AICOMPANY_VIDEO_MODEL") or model, timeout)

    @classmethod
    def youtube_from_environment(cls, environment=None):
        return cls._offline_selection(
            environment, "YOUTUBE", "fake", FakeYouTubeProvider
        )

    @classmethod
    def naver_blog_from_environment(cls, environment=None):
        environment = os.environ if environment is None else environment
        name = environment.get("AICOMPANY_NAVER_BLOG_PROVIDER", "fake").lower()
        timeout = cls._timeout(environment.get("AICOMPANY_NAVER_BLOG_TIMEOUT", "900"))
        if name == "fake": provider = FakeNaverBlogBrowser()
        elif name == "playwright":
            profile = environment.get("AICOMPANY_NAVER_PROFILE_DIR")
            if not profile: raise ValueError("AICOMPANY_NAVER_PROFILE_DIR is required")
            provider = PlaywrightNaverBlogBrowser(profile)
        else: raise ValueError("Unsupported Naver blog provider")
        return ProviderSelection(provider, "naver-smart-editor", timeout)

    @classmethod
    def text_from_environment(cls, environment=None, transport=None):
        environment = os.environ if environment is None else environment
        provider_name = environment.get(
            "AICOMPANY_TEXT_PROVIDER", "fake"
        ).lower()
        timeout = cls._timeout(
            environment.get("AICOMPANY_TEXT_PROVIDER_TIMEOUT", "30")
        )
        model = environment.get("AICOMPANY_TEXT_MODEL")
        if provider_name == "fake":
            provider = FakeTextProvider()
            model = model or "fake-creative-v1"
        elif provider_name == "ollama":
            if not model:
                raise ValueError("AICOMPANY_TEXT_MODEL is required for Ollama")
            provider = OllamaTextProvider(
                environment.get(
                    "AICOMPANY_OLLAMA_ENDPOINT", "http://127.0.0.1:11434"
                ),
                transport=transport,
            )
        elif provider_name == "openai":
            cls._require_paid_provider_allowed(environment, "openai")
            if not model:
                raise ValueError("AICOMPANY_TEXT_MODEL is required for OpenAI")
            api_key = resolve_secret_value(
                environment, "OPENAI_API_KEY", prefer_file=True
            )
            if not api_key:
                raise ValueError("OPENAI_API_KEY is required for OpenAI")
            provider = OpenAITextProvider(api_key, transport=transport)
        else:
            raise ValueError("Unsupported or disabled text provider")
        provider = cls.ensure_provider_allowed(provider, environment)
        return ProviderSelection(
            provider, model, timeout,
            paid_allowed=(
                getattr(provider, "is_paid", False)
                and cls._paid_provider_allowed(environment)
            ),
        )

    @classmethod
    def ensure_provider_allowed(
        cls, provider, environment=None, logger=None, workspace_id=None,
        mission_id=None,
    ):
        environment = os.environ if environment is None else environment
        allow_paid = cls._paid_provider_allowed(environment)
        if getattr(provider, "is_paid", False) and not allow_paid:
            safe_log(
                logger, "PROVIDER_BLOCKED", "ProviderFactory",
                level=LogLevel.WARNING,
                workspace_id=workspace_id,
                mission_id=mission_id,
                status="BLOCKED",
                provider=provider.__class__.__name__,
                error="ProviderError: CostPolicy",
                metadata={"policy": "paid_provider_disabled"},
            )
            raise ValueError("Paid provider is disabled by policy")
        safe_log(
            logger, "PROVIDER_SELECTED", "ProviderFactory",
            workspace_id=workspace_id,
            mission_id=mission_id,
            status="SELECTED",
            provider=provider.__class__.__name__,
        )
        return provider

    @classmethod
    def _require_paid_provider_allowed(cls, environment, provider_name):
        if cls._paid_provider_allowed(environment):
            return
        safe_log(
            None, "PROVIDER_BLOCKED", "ProviderFactory",
            level=LogLevel.WARNING, status="BLOCKED", provider=provider_name,
            error="ProviderError: CostPolicy",
            metadata={"policy": "paid_provider_disabled"},
        )
        raise ValueError("Paid provider is disabled by policy")

    @staticmethod
    def _paid_provider_allowed(environment):
        return str(environment.get("ALLOW_PAID_PROVIDER", "false")).lower() == "true"

    @classmethod
    def _offline_selection(cls, environment, kind, default, provider_type):
        environment = os.environ if environment is None else environment
        provider_name = environment.get(f"AICOMPANY_{kind}_PROVIDER", default).lower()
        timeout = cls._timeout(
            environment.get(f"AICOMPANY_{kind}_PROVIDER_TIMEOUT", "30")
        )
        if provider_name != "fake":
            raise ValueError(f"Unsupported or disabled {kind.lower()} provider")
        provider = cls.ensure_provider_allowed(provider_type(), environment)
        return ProviderSelection(
            provider,
            environment.get(f"AICOMPANY_{kind}_PROVIDER_MODEL"),
            timeout,
        )

    @staticmethod
    def _timeout(value):
        try:
            timeout_seconds = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError("AICOMPANY_PROVIDER_TIMEOUT must be a positive number") from error
        if timeout_seconds <= 0:
            raise ValueError("AICOMPANY_PROVIDER_TIMEOUT must be a positive number")
        return timeout_seconds
