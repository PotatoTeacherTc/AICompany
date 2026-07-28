import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.artifact_manager import ArtifactManager
from core.content_orchestrator import ContentOrchestrator
from core.execution_history import ExecutionHistory
from core.media_pipeline import ImagePipeline, VideoPipeline
from core.music_pipeline import MusicPipeline
from core.status import PipelineStatus
from core.task import Task
from providers.content_media import (
    FakeImageProvider,
    FakeVideoProvider,
    FakeYouTubeProvider,
    MediaArtifact,
    MediaGenerationResult,
)
from providers.factory import ProviderFactory


class MemoryHistoryRepository:
    def __init__(self):
        self.records = []

    def load(self):
        return list(self.records)

    def save(self, records):
        self.records = list(records)


class FailingProvider(FakeImageProvider):
    def generate_image(self, request):
        raise RuntimeError("secret provider detail")


class SlowProvider(FakeImageProvider):
    def generate_image(self, request):
        raise TimeoutError("provider timeout detail")


class EscapingProvider(FakeImageProvider):
    def generate_image(self, request):
        outside = Path(request.output_directory).parent / "outside.txt"
        outside.write_text("unsafe", encoding="utf-8")
        return MediaGenerationResult(
            self.name, "fake", (MediaArtifact(outside.name, "image/fake", str(outside)),)
        )


class PartialUsageProvider(FakeVideoProvider):
    def generate_video(self, request):
        result = super().generate_video(request)
        return MediaGenerationResult(result.provider, result.model, result.artifacts, None)


class PaidProvider(FakeImageProvider):
    is_paid = True


class PartialUsage:
    input_tokens = 3


class PartialImageProvider(FakeImageProvider):
    def generate_image(self, request):
        result = super().generate_image(request)
        return MediaGenerationResult(
            result.provider, result.model, result.artifacts, PartialUsage()
        )


class TimeoutYouTubeProvider(FakeYouTubeProvider):
    def upload(self, request):
        raise TimeoutError("oauth token must never appear")


class ContentGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manager = ArtifactManager()

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def task(workspace="workspace-a", mission="mission-a", prompt="private prompt"):
        task = Task(prompt, {"mission_id": mission}, workspace_id=workspace)
        task.task_type = "CONTENT"
        return task

    def test_fake_image_and_video_dependency_injection(self):
        task = self.task()
        image = ImagePipeline(self.root / "images", provider=FakeImageProvider())
        image_result = image.run(task)
        video = VideoPipeline(self.root / "videos", provider=FakeVideoProvider())
        video_result = video.run(task, image_result["artifacts"])
        self.assertEqual(PipelineStatus.SUCCESS, image_result["status"])
        self.assertEqual(PipelineStatus.SUCCESS, video_result["status"])
        self.assertEqual(
            [image_result["artifacts"][0]["artifact_id"]],
            video_result["data"]["input_artifact_ids"],
        )

    def test_usage_missing_is_normalized_and_output_is_safe(self):
        task = self.task(prompt="do not expose this")
        result = VideoPipeline(
            self.root / "videos", provider=PartialUsageProvider()
        ).run(task)
        self.assertEqual(0, result["data"]["provider_usage"]["total_tokens"])
        serialized = repr(result)
        self.assertNotIn(task.task_text, serialized)
        self.assertNotIn(str(self.root), serialized)
        partial = ImagePipeline(
            self.root / "partial", provider=PartialImageProvider()
        ).run(task)
        self.assertEqual(3, partial["data"]["provider_usage"]["total_tokens"])

    def test_timeout_exception_and_path_escape_are_safe(self):
        task = self.task()
        timed_out = ImagePipeline(
            self.root / "slow", provider=SlowProvider(), timeout_seconds=1
        ).run(task)
        failed = ImagePipeline(
            self.root / "failed", provider=FailingProvider()
        ).run(task)
        escaped = ImagePipeline(
            self.root / "escape", provider=EscapingProvider()
        ).run(task)
        self.assertEqual(PipelineStatus.TIMED_OUT, timed_out["status"])
        self.assertEqual("ProviderError: RuntimeError", failed["error"])
        self.assertNotIn("secret provider detail", repr(failed))
        self.assertEqual(PipelineStatus.FAILED, escaped["status"])

    def test_workspace_isolation_rejects_foreign_artifact(self):
        foreign = ImagePipeline(
            self.root / "images", provider=FakeImageProvider()
        ).run(self.task("workspace-b"))
        result = VideoPipeline(
            self.root / "videos", provider=FakeVideoProvider()
        ).run(self.task("workspace-a"), foreign["artifacts"])
        self.assertEqual(PipelineStatus.FAILED, result["status"])

    def test_paid_provider_policy_and_offline_defaults(self):
        with self.assertRaisesRegex(ValueError, "Paid provider"):
            ProviderFactory.ensure_provider_allowed(
                PaidProvider(), {"ALLOW_PAID_PROVIDER": "false"}
            )
        with patch.dict(os.environ, {"ALLOW_PAID_PROVIDER": "false"}, clear=True):
            self.assertEqual(
                "fake-image", ProviderFactory.image_from_environment().provider.name
            )
            self.assertEqual(
                "fake-video", ProviderFactory.video_from_environment().provider.name
            )
            self.assertEqual(
                "fake-youtube", ProviderFactory.youtube_from_environment().provider.name
            )

    def test_fake_content_end_to_end_and_history(self):
        repository = MemoryHistoryRepository()
        history = ExecutionHistory(repository=repository)
        shared = ArtifactManager()
        task = self.task()
        orchestrator = ContentOrchestrator(
            MusicPipeline(
                self.root / "music", artifact_manager=shared, execution_history=history
            ),
            ImagePipeline(
                self.root / "images", artifact_manager=shared, execution_history=history
            ),
            VideoPipeline(
                self.root / "videos", artifact_manager=shared, execution_history=history
            ),
            youtube_provider=FakeYouTubeProvider(),
            execution_history=history,
        )
        result = orchestrator.run(task)
        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertEqual(
            "SIMULATED", result["data"]["stages"]["youtube"]["upload_status"]
        )
        self.assertEqual(4, len(history.records))
        self.assertNotIn(task.task_text, repr(history.records))
        self.assertNotIn(str(self.root), repr(result))

    def test_content_stops_safely_on_intermediate_failure(self):
        task = self.task()
        result = ContentOrchestrator(
            MusicPipeline(self.root / "music"),
            ImagePipeline(self.root / "images", provider=FailingProvider()),
            VideoPipeline(self.root / "videos"),
            youtube_provider=FakeYouTubeProvider(),
        ).run(task)
        self.assertEqual(PipelineStatus.FAILED, result["status"])
        self.assertIn("image", result["data"]["stages"])
        self.assertNotIn("video", result["data"]["stages"])
        self.assertNotIn("youtube", result["data"]["stages"])

    def test_youtube_timeout_is_safely_aggregated(self):
        task = self.task()
        result = ContentOrchestrator(
            MusicPipeline(self.root / "music-timeout"),
            ImagePipeline(self.root / "images-timeout"),
            VideoPipeline(self.root / "videos-timeout"),
            youtube_provider=TimeoutYouTubeProvider(),
        ).run(task)
        self.assertEqual(PipelineStatus.FAILED, result["status"])
        self.assertEqual(
            "ProviderError: TimeoutError",
            result["data"]["stages"]["youtube"]["error"],
        )
        self.assertNotIn("oauth token", repr(result))


if __name__ == "__main__":
    unittest.main()
