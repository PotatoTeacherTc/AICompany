import hashlib
import json
import os
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path

from core.artifact_manager import ArtifactManager
from core.artifact_repository import FileArtifactRepository
from core.content_brief_orchestration import ContentProject, ContentProjectRepository
from core.execution_history import ExecutionHistory
from core.image_package import (
    IMAGE_PACKAGE_KIND, ImagePackageOrchestrator, ImagePackageRequest,
    ImagePackageError, inspect_image,
)
from core.object_storage import ArtifactStorageAdapter, LocalStorageProvider
from core.persistence import JsonStateRepository
from core.status import PipelineStatus
from core.usage_engine import UsageEngine
from providers.content_media import (
    ComfyUIImageProvider, FakeImageProvider, MediaArtifact,
    MediaGenerationResult, _deterministic_png,
)
from providers.factory import ProviderFactory, ProviderSelection


class MemoryHistoryRepository:
    def __init__(self): self.records = []
    def load(self): return list(self.records)
    def save(self, records): self.records = list(records)


class FailOnceProvider(FakeImageProvider):
    def __init__(self): self.calls = 0
    def generate_image(self, request):
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("private provider response")
        return super().generate_image(request)


class BrokenProvider(FakeImageProvider):
    def __init__(self, content=b"broken", suffix=".png"): self.content, self.suffix = content, suffix
    def generate_image(self, request):
        path = Path(request.output_directory) / ("result" + self.suffix)
        path.write_bytes(self.content)
        return MediaGenerationResult(self.name, "fake-image-default", (MediaArtifact(path.name, "image/png", str(path)),))


class ImagePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state_file = self.root / "state.json"
        self.artifact_file = self.root / "artifacts.json"
        self.storage_root = self.root / "storage"
        self.history_repository = MemoryHistoryRepository()
        self._compose()
        self.project = self._project("workspace-a", "content-a")

    def tearDown(self): self.temp.cleanup()

    def _compose(self, provider=None):
        self.states = JsonStateRepository(self.state_file)
        repository = FileArtifactRepository(self.artifact_file, self.storage_root)
        self.artifacts = ArtifactManager(
            repository, ArtifactStorageAdapter(LocalStorageProvider(self.storage_root), repository)
        )
        self.projects = ContentProjectRepository(self.states)
        self.provider = provider or FakeImageProvider()
        self.orchestrator = ImagePackageOrchestrator(
            self.root / "work", ProviderSelection(self.provider, "fake-image-default", 5),
            self.projects, self.states, self.artifacts,
            ExecutionHistory(repository=self.history_repository), UsageEngine(self.states),
        )

    def _project(self, workspace, content_id):
        brief = {
            "visual_concept": "quiet dawn city opening into a bright road",
            "visual_style": "cinematic editorial realism",
            "color_direction": "cool blue to warm gold",
            "thumbnail_direction": "one person and open road",
            "target_audience": "adult Korean ballad listeners",
            "core_message": "recovery after loss",
            "image_requirements": ["consistent lead character", "no embedded text"],
            "mood_keywords": ["reflective", "hopeful"],
            "prohibited_elements": ["named artist imitation", "graphic imagery"],
        }
        source = self.root / f"brief-{content_id}.json"
        source.write_text(json.dumps(brief), encoding="utf-8")
        artifact = self.artifacts.register_file(
            source, "CONTENT_BRIEF", "test", workspace_id=workspace,
            mission_id="music-a", task_id=content_id, stage="CONTENT_BRIEF",
        )
        now = "2026-08-02T00:00:00+00:00"
        project = ContentProject(
            content_id, workspace, "music-a", "plan-a", "audio-a",
            PipelineStatus.READY_FOR_CONTENT, 1, artifact["artifact_id"], "plan-exec-a",
            now, now, completed_steps=("MUSIC_PLAN", "AUDIO_INPUT", "CONTENT_BRIEF"),
        )
        self.projects.save(project)
        return project

    def test_fake_package_registers_four_images_manifest_state_usage_and_history(self):
        result = self.orchestrator.run(ImagePackageRequest("workspace-a", "content-a", seed=7))
        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertEqual(5, len(result["artifacts"]))
        self.assertEqual(4, len(result["data"]["image_artifact_ids"]))
        record = self.states.get(IMAGE_PACKAGE_KIND, "content-a", "workspace-a")
        self.assertEqual("COMPLETED", record["status"])
        self.assertEqual("COMPLETED", record["execution_steps"]["IMAGE_PACKAGE"])
        self.assertEqual("PENDING", record["execution_steps"]["BLOG_PACKAGE"])
        self.assertEqual(4, len(record["images"]))
        updated = self.projects.get("workspace-a", "content-a")
        self.assertIn("IMAGE_PACKAGE", updated.completed_steps)
        self.assertNotIn("IMAGE_PACKAGE", updated.pending_steps)
        self.assertEqual(1, len(self.history_repository.records))
        usage = UsageEngine(self.states).get("image-package-content-a", "workspace-a")
        self.assertEqual(0.0, usage["estimated_cost_usd"])
        self.assertNotIn("quiet dawn", repr(result))
        self.assertNotIn(str(self.root), repr(result))

    def test_manifest_and_artifact_metadata_are_safe_and_complete(self):
        result = self.orchestrator.run(ImagePackageRequest("workspace-a", "content-a"))
        manifest_id = result["data"]["manifest_artifact_id"]
        manifest = json.loads(self.artifacts.storage_adapter.read("workspace-a", manifest_id))
        self.assertEqual({"COVER", "YOUTUBE_THUMBNAIL_SOURCE", "VIDEO_BACKGROUND", "BLOG_INLINE"}, {item["purpose"] for item in manifest["images"]})
        for image in manifest["images"]:
            artifact = self.artifacts.get(image["artifact_id"], "workspace-a")
            self.assertEqual(image["checksum_sha256"], artifact["metadata"]["checksum_sha256"])
            self.assertNotIn("path", artifact)
            self.assertNotIn("prompt", repr(artifact).lower().replace("prompt_hash", "hash").replace("prompt_version", "version"))

    def test_idempotent_replay_and_restart_do_not_regenerate(self):
        provider = FailOnceProvider()
        self._compose(provider)
        self.project = self._project("workspace-a", "content-restart")
        failed = self.orchestrator.run(ImagePackageRequest("workspace-a", "content-restart"))
        self.assertEqual(PipelineStatus.FAILED, failed["status"])
        self.assertNotIn("private provider response", repr(failed))
        self.assertEqual(1, len(self.states.get(IMAGE_PACKAGE_KIND, "content-restart", "workspace-a")["images"]))
        recovered = self.orchestrator.run(ImagePackageRequest("workspace-a", "content-restart"))
        self.assertEqual(PipelineStatus.SUCCESS, recovered["status"])
        calls = provider.calls
        self._compose(provider)
        replay = self.orchestrator.run(ImagePackageRequest("workspace-a", "content-restart"))
        self.assertTrue(replay["data"]["idempotent_replay"])
        self.assertEqual(calls, provider.calls)

    def test_workspace_isolation_and_invalid_input(self):
        foreign = self.orchestrator.run(ImagePackageRequest("workspace-b", "content-a"))
        self.assertEqual(PipelineStatus.FAILED, foreign["status"])
        self.assertIn("WORKSPACE_MISMATCH", foreign["error"])
        invalid = self.orchestrator.run(ImagePackageRequest("../escape", "content-a"))
        self.assertEqual(PipelineStatus.FAILED, invalid["status"])

    def test_concurrent_requests_have_one_generation(self):
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.orchestrator.run(ImagePackageRequest("workspace-a", "content-a")))) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual([PipelineStatus.SUCCESS, PipelineStatus.SUCCESS], [item["status"] for item in results])
        self.assertTrue(any(item["data"].get("idempotent_replay") for item in results))
        self.assertEqual(5, len(self.artifacts.find("workspace-a", mission_id="music-a")) - 1)

    def test_corrupt_extension_and_aspect_ratio_are_rejected(self):
        for provider, code in (
            (BrokenProvider(), "IMAGE_FORMAT_INVALID"),
            (BrokenProvider(_deterministic_png(10, 10, 1), ".jpg"), "IMAGE_EXTENSION_MISMATCH"),
            (BrokenProvider(_deterministic_png(10, 20, 1)), "IMAGE_ASPECT_RATIO_INVALID"),
        ):
            self._compose(provider)
            content_id = f"content-{code.lower()}"
            self._project("workspace-a", content_id)
            result = self.orchestrator.run(ImagePackageRequest("workspace-a", content_id))
            self.assertIn(code, result["error"])

    def test_png_decoder_rejects_corruption(self):
        value = _deterministic_png(12, 8, 3)
        self.assertEqual(("PNG", 12, 8), inspect_image(value))
        with self.assertRaises(ImagePackageError): inspect_image(value[:-5])

    def test_cli_composition_returns_safe_fake_package(self):
        from main import run_image_package
        cli_root = self.root / "cli"
        state_root = cli_root / "state"
        storage = cli_root / "artifacts"
        states = JsonStateRepository(state_root / "music-project-state.json")
        repository = FileArtifactRepository(state_root / "artifact-metadata.json", storage)
        artifacts = ArtifactManager(repository, ArtifactStorageAdapter(LocalStorageProvider(storage), repository))
        projects = ContentProjectRepository(states)
        brief = self.root / "cli-brief.json"
        brief.write_text(json.dumps({
            "visual_concept": "safe dawn road", "visual_style": "editorial realism",
            "color_direction": "blue to gold", "thumbnail_direction": "one person",
            "target_audience": "adult listeners", "core_message": "recovery",
            "image_requirements": ["no text"], "mood_keywords": ["hopeful"],
            "prohibited_elements": ["artist imitation"],
        }), encoding="utf-8")
        artifact = artifacts.register_file(brief, "CONTENT_BRIEF", "test", workspace_id="workspace-cli", mission_id="music-cli", task_id="content-cli")
        now = "2026-08-02T00:00:00+00:00"
        projects.save(ContentProject(
            "content-cli", "workspace-cli", "music-cli", "plan-cli", "audio-cli",
            PipelineStatus.READY_FOR_CONTENT, 1, artifact["artifact_id"], "execution-cli",
            now, now, completed_steps=("MUSIC_PLAN", "AUDIO_INPUT", "CONTENT_BRIEF"),
        ))
        result = run_image_package(
            "workspace-cli", "content-cli", root=cli_root,
            environment={"ALLOW_PAID_PROVIDER": "false", "AICOMPANY_IMAGE_PROVIDER": "fake"},
        )
        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertNotIn(str(cli_root), repr(result))


class ComfyUIProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workflow = Path(__file__).parents[1] / "workflows" / "comfyui" / "checkpoint-basic-v1.json"

    def tearDown(self): self.temp.cleanup()

    def request(self):
        from providers.content_media import ImageGenerationRequest
        return ImageGenerationRequest("safe visual", "workspace-a", "mission-a", str(self.root), model="model.safetensors", timeout_seconds=2, purpose="COVER", width=32, height=32, seed=9, steps=2)

    def test_factory_selects_comfyui_and_submits_validated_workflow(self):
        calls = []
        png = _deterministic_png(32, 32, 9)
        def transport(method, path, payload, timeout):
            calls.append((method, path, payload))
            if path == "/prompt": return {"prompt_id": "job-1"}
            if path.startswith("/history/"): return {"job-1": {"outputs": {"7": {"images": [{"filename": "safe.png", "subfolder": "", "type": "output"}]}}}}
            return png
        selection = ProviderFactory.image_from_environment({
            "ALLOW_PAID_PROVIDER": "false", "AICOMPANY_IMAGE_PROVIDER": "comfyui",
            "AICOMPANY_COMFYUI_ENDPOINT": "http://127.0.0.1:8188",
            "AICOMPANY_COMFYUI_WORKFLOW_PATH": str(self.workflow),
            "AICOMPANY_IMAGE_MODEL": "model.safetensors", "AICOMPANY_IMAGE_PROVIDER_TIMEOUT": "2",
        }, transport=transport)
        result = selection.provider.generate_image(self.request())
        self.assertEqual("comfyui", result.provider)
        submitted = calls[0][2]["prompt"]
        self.assertEqual("safe visual", submitted["2"]["inputs"]["text"])
        self.assertEqual(9, submitted["5"]["inputs"]["seed"])
        self.assertEqual(("PNG", 32, 32), inspect_image(Path(result.artifacts[0].path).read_bytes()))

    def test_endpoint_credentials_lan_and_external_are_blocked(self):
        for endpoint in ("http://192.168.1.3:8188", "https://example.com", "http://user:pass@127.0.0.1:8188"):
            with self.assertRaisesRegex(ValueError, "loopback"):
                ComfyUIImageProvider(endpoint, self.workflow, "model.safetensors")

    def test_invalid_workflow_node_and_unsafe_output_are_blocked(self):
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"1": {"class_type": "ExecuteShell", "inputs": {}}}), encoding="utf-8")
        provider = ComfyUIImageProvider("http://localhost:8188", bad, "model.safetensors", transport=lambda *_: {"prompt_id": "job"})
        with self.assertRaisesRegex(ValueError, "unsupported node"):
            provider.generate_image(self.request())
        def unsafe(method, path, payload, timeout):
            if path == "/prompt": return {"prompt_id": "job"}
            return {"job": {"outputs": {"7": {"images": [{"filename": "x.png", "subfolder": "../escape", "type": "output"}]}}}}
        provider = ComfyUIImageProvider("http://localhost:8188", self.workflow, "model.safetensors", transport=unsafe, poll_interval=0, max_polls=1)
        with self.assertRaisesRegex(ValueError, "unsafe"):
            provider.generate_image(self.request())

    def test_workflow_absolute_value_is_blocked(self):
        value = json.loads(self.workflow.read_text(encoding="utf-8"))
        value["7"]["inputs"]["filename_prefix"] = "C:\\private\\output"
        bad = self.root / "absolute.json"
        bad.write_text(json.dumps(value), encoding="utf-8")
        provider = ComfyUIImageProvider("http://127.0.0.1:8188", bad, "model.safetensors", transport=lambda *_: {})
        with self.assertRaisesRegex(ValueError, "unsafe value"):
            provider.generate_image(self.request())

    def test_missing_job_empty_result_timeout_and_connection_failure(self):
        sequences = (
            (lambda *_: {}, ValueError),
            (lambda method, path, *_: {"prompt_id": "job"} if path == "/prompt" else {}, TimeoutError),
            (lambda *_: (_ for _ in ()).throw(ConnectionError("private endpoint error")), ConnectionError),
        )
        for transport, error in sequences:
            provider = ComfyUIImageProvider("http://127.0.0.1:8188", self.workflow, "model.safetensors", transport=transport, poll_interval=0, max_polls=1)
            with self.assertRaises(error): provider.generate_image(self.request())

    @unittest.skipUnless(
        os.environ.get("AICOMPANY_RUN_COMFYUI_SMOKE", "false").lower() == "true",
        "real ComfyUI smoke is explicit opt-in",
    )
    def test_real_comfyui_one_image_smoke(self):
        selection = ProviderFactory.image_from_environment(os.environ)
        states = JsonStateRepository(self.root / "smoke-state.json")
        repository = FileArtifactRepository(
            self.root / "smoke-artifacts.json", self.root / "smoke-storage"
        )
        artifacts = ArtifactManager(
            repository,
            ArtifactStorageAdapter(
                LocalStorageProvider(self.root / "smoke-storage"), repository
            ),
        )
        projects = ContentProjectRepository(states)
        brief_path = self.root / "brief.json"
        brief_path.write_text(json.dumps({
            "visual_concept": "calm abstract sunrise landscape",
            "visual_style": "safe editorial composition",
            "color_direction": "cool blue to warm gold",
            "thumbnail_direction": "one clear focal point",
            "target_audience": "general adult audience",
            "core_message": "renewal and hope",
            "image_requirements": ["no embedded text"],
            "mood_keywords": ["calm", "hopeful"],
            "prohibited_elements": ["artist imitation", "watermark"],
        }), encoding="utf-8")
        brief = artifacts.register_file(
            brief_path, "CONTENT_BRIEF", "smoke-setup",
            workspace_id="smoke-workspace", mission_id="smoke-music",
            task_id="smoke-content", stage="CONTENT_BRIEF",
        )
        now = "2026-08-02T00:00:00+00:00"
        projects.save(ContentProject(
            "smoke-content", "smoke-workspace", "smoke-music",
            "smoke-plan", "smoke-audio", PipelineStatus.READY_FOR_CONTENT,
            1, brief["artifact_id"], "smoke-execution", now, now,
            completed_steps=("MUSIC_PLAN", "AUDIO_INPUT", "CONTENT_BRIEF"),
        ))
        history_repository = MemoryHistoryRepository()
        usage = UsageEngine(states)
        orchestrator = ImagePackageOrchestrator(
            self.root / "smoke-work", selection, projects, states, artifacts,
            ExecutionHistory(repository=history_repository), usage,
            steps=2, guidance=1.0,
        )
        result = orchestrator.smoke(ImagePackageRequest(
            "smoke-workspace", "smoke-content", seed=20260802
        ))
        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertEqual("comfyui", result["data"]["provider"])
        self.assertEqual(selection.default_model, result["data"]["model"])
        artifact_id = result["data"]["artifact_id"]
        content = artifacts.storage_adapter.read("smoke-workspace", artifact_id)
        self.assertEqual(("PNG", 512, 512), inspect_image(content))
        self.assertEqual(hashlib.sha256(content).hexdigest(), result["data"]["checksum_sha256"])
        self.assertIsNone(artifacts.get(artifact_id, "foreign-workspace"))
        history_record = history_repository.records[0]
        self.assertEqual("IMAGE_PACKAGE_SMOKE", history_record["task_type"])
        self.assertEqual("comfyui", history_record["result"]["provider"])
        self.assertEqual(artifact_id, history_record["result"]["artifacts"][0]["artifact_id"])
        usage_record = usage.get("image-package-smoke-smoke-content", "smoke-workspace")
        self.assertEqual(0.0, usage_record["estimated_cost_usd"])
        self.assertEqual("comfyui", usage_record["provider"])
        self.assertEqual(selection.default_model, usage_record["model"])
        self.assertNotIn(str(self.root), repr(result))
        self.assertNotIn("calm abstract sunrise", repr(result))


if __name__ == "__main__": unittest.main()
