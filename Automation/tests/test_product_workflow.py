import unittest
import shutil
import tempfile
import wave
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from application.product_workflow_service import ProductWorkflowService, PRODUCT_STAGES
from application.persistent_execution_service import PersistentExecutionService
from core.artifact_manager import ArtifactManager
from core.execution_history import ExecutionHistory
from core.persistence import InMemoryStateRepository
from core.task_queue import InProcessJobWorker, PersistentJobQueue
from core.usage_engine import UsageEngine
from application.product_content_runner import ProductContentRunner
from application.local_product import create_local_product_app, reset_local_owner_password
from application.credential_service import CredentialService
from application.user_service import UserService
from core.artifact_repository import StateArtifactRepository
from core.credential_repository import FileCredentialRepository
from core.object_storage import ArtifactStorageAdapter, LocalStorageProvider
from core.secure_token_store import FakeSecureTokenStore
from core.user_repository import FileUserRepository
from core.youtube_publishing import YouTubeConnectionRepository, YouTubeConnectionService, YOUTUBE_SCOPES
from providers.content_media import FakeYouTubeProvider
from providers.naver_blog import FakeNaverBlogBrowser


class ProductWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryStateRepository()
        queue = PersistentJobQueue(self.repository)
        execution = PersistentExecutionService(
            queue, InProcessJobWorker(queue),
            ExecutionHistory(state_repository=self.repository),
            ArtifactManager(), UsageEngine(self.repository),
        )
        self.calls = []

        def runner(stage, workspace_id, product_id, request_text, _record=None):
            self.calls.append((stage, workspace_id, request_text))
            return {
                "status": "COMPLETED",
                "result": {"kind": stage.lower(), "safe_ref": product_id},
            }

        self.service = ProductWorkflowService(
            self.repository, execution, runner,
            {"comfyui": lambda _workspace: True, "youtube": lambda _workspace: "CONNECTED"},
        )

    def test_one_request_runs_all_stages_without_persisting_text(self):
        value = self.service.submit("workspace-a", "private user request", "request-1")
        self.assertEqual(value["status"], "PENDING")
        self.service.run_once("workspace-a")
        result = self.service.get("workspace-a", value["product_id"])
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["progress"], 100)
        self.assertEqual([call[0] for call in self.calls], list(PRODUCT_STAGES))
        self.assertNotIn("private user request", str(self.repository._records))
        self.assertIsNone(self.service.get("workspace-b", value["product_id"]))

    def test_duplicate_submission_and_waiting_boundary(self):
        waiting = ProductWorkflowService(
            InMemoryStateRepository(),
            self.service.execution,
            lambda stage, *_: {"status": "WAITING_FOR_INPUT"} if stage == "MUSIC" else {"status": "COMPLETED"},
        )
        first = waiting.submit("workspace-a", "request", "same-key")
        second = waiting.submit("workspace-a", "different", "same-key")
        self.assertEqual(first["product_id"], second["product_id"])

    def test_failed_stage_only_retry_and_connections_are_safe(self):
        def failing(stage, *_):
            return {"status": "FAILED", "safe_error": "PROVIDER_UNAVAILABLE"} if stage == "IMAGE" else {"status": "COMPLETED"}
        repository = InMemoryStateRepository()
        queue = PersistentJobQueue(repository)
        execution = PersistentExecutionService(queue, InProcessJobWorker(queue), ExecutionHistory(state_repository=repository), ArtifactManager(), UsageEngine(repository))
        service = ProductWorkflowService(repository, execution, failing, {"naver": lambda _: (_ for _ in ()).throw(RuntimeError("cookie=secret"))})
        item = service.submit("workspace-a", "request", "failure-key")
        service.run_once("workspace-a")
        failed = service.get("workspace-a", item["product_id"])
        self.assertEqual(failed["current_stage"], "IMAGE")
        self.assertEqual(failed["status"], "FAILED")
        retried = service.retry("workspace-a", item["product_id"], "IMAGE")
        self.assertEqual(retried["stages"]["IMAGE"]["status"], "PENDING")
        self.assertEqual(service.connections("workspace-a")["items"][2]["status"], "UNAVAILABLE")

    def test_product_http_contract(self):
        client = TestClient(create_app(product_workflow_service=self.service))
        response = client.post("/workspaces/workspace-a/product-jobs", json={"request": "make content", "idempotency_key": "http-1"})
        self.assertEqual(response.status_code, 201)
        product_id = response.json()["product_id"]
        self.service.run_once("workspace-a")
        self.assertEqual(client.get(f"/workspaces/workspace-a/product-jobs/{product_id}").json()["status"], "COMPLETED")
        self.assertEqual(client.get("/workspaces/workspace-a/product-jobs").json()["items"][0]["workspace_id"], "workspace-a")
        self.assertEqual(client.get("/workspaces/workspace-a/connections").status_code, 200)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg local integration required")
    def test_existing_orchestrators_complete_fake_product_e2e(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); repository = InMemoryStateRepository()
            metadata = StateArtifactRepository(repository)
            artifacts = ArtifactManager(metadata, ArtifactStorageAdapter(LocalStorageProvider(root / "artifacts"), metadata))
            history = ExecutionHistory(state_repository=repository); usage = UsageEngine(repository)
            tokens = FakeSecureTokenStore(); connections = YouTubeConnectionService(YouTubeConnectionRepository(repository), tokens)
            connections.connect("workspace-a", "channel-a", "Potato Music Company", {"access_token":"fake-a","refresh_token":"fake-r","expires_at":"2099-01-01T00:00:00+00:00","token_type":"Bearer","granted_scopes":YOUTUBE_SCOPES})
            runner = ProductContentRunner(root, repository, artifacts, history, usage, {
                "AICOMPANY_TEXT_PROVIDER":"fake", "AICOMPANY_IMAGE_PROVIDER":"fake",
                "AICOMPANY_VIDEO_PROVIDER":"ffmpeg", "AICOMPANY_NAVER_BLOG_PROVIDER":"fake",
                "ALLOW_PAID_PROVIDER":"False",
            }, connections, FakeYouTubeProvider(), FakeNaverBlogBrowser())
            queue = PersistentJobQueue(repository)
            execution = PersistentExecutionService(queue, InProcessJobWorker(queue), history, artifacts, usage)
            service = ProductWorkflowService(repository, execution, runner)
            item = service.submit("workspace-a", "make a calm piano video", "actual-contracts")
            service.run_once("workspace-a")
            audio = root / "source.wav"
            with wave.open(str(audio), "wb") as stream:
                stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(8000); stream.writeframes(b"\0\0" * 8000)
            service.upload_audio("workspace-a", item["product_id"], "source.wav", audio.read_bytes())
            restarted_queue = PersistentJobQueue(repository, workspace_ids=("workspace-a",))
            restarted_execution = PersistentExecutionService(restarted_queue, InProcessJobWorker(restarted_queue), history, artifacts, usage)
            restarted = ProductWorkflowService(repository, restarted_execution, runner)
            restarted.run_once("workspace-a")
            waiting = restarted.get("workspace-a", item["product_id"])
            self.assertEqual(waiting["status"], "USER_CONFIRM_REQUIRED")
            restarted.resume("workspace-a", item["product_id"])
            restarted.run_once("workspace-a")
            completed = restarted.get("workspace-a", item["product_id"])
            self.assertEqual(completed["status"], "COMPLETED")
            self.assertEqual(completed["stages"]["YOUTUBE"]["status"], "COMPLETED")
            self.assertEqual(completed["stages"]["NAVER"]["status"], "PUBLISHED")
            self.assertTrue(completed["results"]["youtube"]["published_url"])
            self.assertTrue(completed["results"]["naver"]["published_url"])

    def test_audio_checkpoint_resumes_complete_fake_product(self):
        class Runner:
            def __call__(self, stage, workspace, product, text, record=None):
                if stage == "PLANNING":
                    return {"status": "WAITING_FOR_INPUT", "result": {"project_id": "music-a"}}
                return {"status": "COMPLETED", "result": {"stage": stage}}
            def upload_audio(self, workspace, project, filename, content):
                if workspace != "workspace-a" or project != "music-a" or filename != "song.mp3" or not content:
                    raise ValueError("invalid")
                return {"status": "INPUT_READY", "data": {"audio_artifact_id": "audio-a", "source_filename": filename, "detected_format": "mp3", "duration_seconds": 1.0}}
        repository = InMemoryStateRepository(); queue = PersistentJobQueue(repository)
        execution = PersistentExecutionService(queue, InProcessJobWorker(queue), ExecutionHistory(state_repository=repository), ArtifactManager(), UsageEngine(repository))
        service = ProductWorkflowService(repository, execution, Runner())
        item = service.submit("workspace-a", "request", "audio-flow")
        service.run_once("workspace-a")
        waiting = service.get("workspace-a", item["product_id"])
        self.assertEqual(waiting["status"], "WAITING_FOR_INPUT")
        service.upload_audio("workspace-a", item["product_id"], "song.mp3", b"audio")
        service.run_once("workspace-a")
        self.assertEqual(service.get("workspace-a", item["product_id"])["status"], "COMPLETED")
        self.assertIsNone(service.get("workspace-b", item["product_id"]))

    def test_audio_http_upload_rejects_paths_and_foreign_workspace(self):
        class Runner:
            def __call__(self, stage, workspace, product, text, record=None):
                return {"status":"WAITING_FOR_INPUT","result":{"project_id":"music-http"}} if stage == "PLANNING" else {"status":"COMPLETED"}
            def upload_audio(self, workspace, project, filename, content):
                if "/" in filename or "\\" in filename: raise ValueError("path")
                return {"status":"INPUT_READY","data":{"audio_artifact_id":"a","source_filename":filename,"detected_format":"mp3","duration_seconds":1}}
        repository=InMemoryStateRepository(); queue=PersistentJobQueue(repository)
        execution=PersistentExecutionService(queue,InProcessJobWorker(queue),ExecutionHistory(state_repository=repository),ArtifactManager(),UsageEngine(repository))
        service=ProductWorkflowService(repository,execution,Runner()); client=TestClient(create_app(product_workflow_service=service))
        item=service.submit("workspace-a","request","http-audio"); service.run_once("workspace-a")
        self.assertEqual(client.post(f"/workspaces/workspace-a/product-jobs/{item['product_id']}/audio?filename=../song.mp3",content=b"audio",headers={"Content-Type":"audio/mpeg"}).status_code,400)
        self.assertEqual(client.post(f"/workspaces/workspace-a/product-jobs/{item['product_id']}/audio?filename=song.mp3",content=b"audio",headers={"Content-Type":"text/plain"}).status_code,400)
        self.assertEqual(client.post(f"/workspaces/workspace-b/product-jobs/{item['product_id']}/audio?filename=song.mp3",content=b"audio",headers={"Content-Type":"audio/mpeg"}).status_code,404)

    def test_audio_http_accepts_unicode_filename_without_a_header(self):
        class Runner:
            def __call__(self, stage, *_args, **_kwargs):
                return {"status":"WAITING_FOR_INPUT","result":{"project_id":"music-unicode"}}
            def upload_audio(self, workspace, project, filename, content):
                self.received = (workspace, project, filename, content)
                return {"status":"INPUT_READY","data":{"audio_artifact_id":"a","source_filename":filename,"detected_format":"mp3","duration_seconds":1}}
        runner=Runner(); repository=InMemoryStateRepository(); queue=PersistentJobQueue(repository)
        execution=PersistentExecutionService(queue,InProcessJobWorker(queue),ExecutionHistory(state_repository=repository),ArtifactManager(),UsageEngine(repository))
        service=ProductWorkflowService(repository,execution,runner); client=TestClient(create_app(product_workflow_service=service))
        item=service.submit("workspace-a","request","unicode-audio"); service.run_once("workspace-a")
        response=client.post(f"/workspaces/workspace-a/product-jobs/{item['product_id']}/audio?filename=%ED%95%9C%EA%B8%80-%EC%9D%8C%EC%95%85.mp3",content=b"audio",headers={"Content-Type":"audio/mpeg"})
        self.assertEqual(response.status_code,200)
        self.assertEqual(runner.received[2],"한글-음악.mp3")
        legacy=service.submit("workspace-a","request","legacy-ascii-audio")
        service.run_once("workspace-a"); service.run_once("workspace-a")
        legacy_response=client.post(f"/workspaces/workspace-a/product-jobs/{legacy['product_id']}/audio",content=b"audio",headers={"Content-Type":"audio/mpeg","X-Filename":"legacy.mp3"})
        self.assertEqual(legacy_response.status_code,200)
        self.assertEqual(runner.received[2],"legacy.mp3")

    def test_local_product_bootstrap_has_workspace_and_restart_login(self):
        with tempfile.TemporaryDirectory() as temporary:
            environment = {
                "AICOMPANY_PRODUCT_ROOT": temporary,
                "AICOMPANY_LOCAL_EMAIL": "owner@localhost",
                "AICOMPANY_LOCAL_PASSWORD": "safe-local-password",
                "AICOMPANY_SIGNING_SECRET": "s" * 48,
                "AICOMPANY_TEXT_PROVIDER": "fake",
                "AICOMPANY_IMAGE_PROVIDER": "fake",
                "AICOMPANY_VIDEO_PROVIDER": "fake",
                "AICOMPANY_NAVER_BLOG_PROVIDER": "fake",
                "ALLOW_PAID_PROVIDER": "False",
            }
            client = TestClient(create_local_product_app(environment))
            login = client.post("/auth/login", json={
                "email": "owner@localhost", "password": "safe-local-password",
            })
            self.assertEqual(login.status_code, 200)
            token = login.json()["access_token"]
            values = client.get("/workspaces", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(values.status_code, 200)
            self.assertEqual(values.json()["items"][0]["workspace_id"], "default")

            changed = dict(environment, AICOMPANY_LOCAL_PASSWORD="different-password")
            restarted = TestClient(create_local_product_app(changed))
            self.assertEqual(restarted.post("/auth/login", json={
                "email": "owner@localhost", "password": "safe-local-password",
            }).status_code, 200)
            self.assertEqual(restarted.post("/auth/login", json={
                "email": "owner@localhost", "password": "different-password",
            }).status_code, 401)

    def test_launcher_verifies_its_backend_login_before_success(self):
        source = (Path(__file__).parents[2] / "start-aicompany.ps1").read_text(encoding="utf-8")
        self.assertIn("$backend.HasExited", source)
        self.assertIn('http://127.0.0.1:8000/auth/login', source)
        self.assertLess(source.index('http://127.0.0.1:8000/auth/login'), source.index('AICompany is running and local login was verified'))

    def test_explicit_owner_reset_changes_only_owner_credential(self):
        with tempfile.TemporaryDirectory() as temporary:
            environment = self._local_environment(temporary)
            create_local_product_app(environment)
            local = Path(temporary) / "local-product"
            users = UserService(FileUserRepository(local / "users.json"))
            other = users.create("other@localhost")
            credentials = CredentialService(users, FileCredentialRepository(local / "credentials.json"))
            credentials.set_password(other["user_id"], "other-safe-password")
            before_other = credentials.repository.get(other["user_id"])
            protected = {
                path: path.read_bytes() for path in (
                    local / "workspaces.json", local / "memberships.json",
                    Path(temporary) / "state" / "music-project-state.json",
                ) if path.exists()
            }

            reset_local_owner_password({
                **environment, "AICOMPANY_RESET_OWNER_PASSWORD": "true",
                "AICOMPANY_LOCAL_PASSWORD": "replacement-owner-password",
            })
            restarted = TestClient(create_local_product_app({
                **environment, "AICOMPANY_LOCAL_PASSWORD": "replacement-owner-password",
            }))
            self.assertEqual(restarted.post("/auth/login", json={
                "email": "owner@localhost", "password": "replacement-owner-password",
            }).status_code, 200)
            self.assertEqual(restarted.post("/auth/login", json={
                "email": "owner@localhost", "password": "original-owner-password",
            }).status_code, 401)
            reloaded = FileCredentialRepository(local / "credentials.json")
            self.assertEqual(reloaded.get(other["user_id"]), before_other)
            self.assertTrue(CredentialService(users, reloaded).verify_password(
                other["user_id"], "other-safe-password"
            ))
            for path, content in protected.items():
                self.assertEqual(path.read_bytes(), content)

    def test_owner_reset_failure_is_atomic_and_requires_explicit_option(self):
        with tempfile.TemporaryDirectory() as temporary:
            environment = self._local_environment(temporary)
            create_local_product_app(environment)
            credential_file = Path(temporary) / "local-product" / "credentials.json"
            original = credential_file.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "local_owner_reset_not_requested"):
                reset_local_owner_password(environment)
            with self.assertRaisesRegex(RuntimeError, "local_password_required"):
                reset_local_owner_password({
                    **environment, "AICOMPANY_RESET_OWNER_PASSWORD": "true",
                    "AICOMPANY_LOCAL_PASSWORD": "short",
                })
            self.assertEqual(credential_file.read_bytes(), original)

    def test_launcher_exposes_only_explicit_reset_switch(self):
        source = (Path(__file__).parents[2] / "start-aicompany.ps1").read_text(encoding="utf-8")
        self.assertIn("param([switch]$ResetOwnerPassword)", source)
        self.assertIn("-m application.local_product_reset", source)
        self.assertIn("Push-Location $automationRoot", source)
        self.assertIn("Pop-Location", source)
        self.assertNotIn("PotatoAI", source)

    @staticmethod
    def _local_environment(root):
        return {
            "AICOMPANY_PRODUCT_ROOT": root,
            "AICOMPANY_LOCAL_EMAIL": "owner@localhost",
            "AICOMPANY_LOCAL_PASSWORD": "original-owner-password",
            "AICOMPANY_SIGNING_SECRET": "s" * 48,
            "AICOMPANY_TEXT_PROVIDER": "fake", "AICOMPANY_IMAGE_PROVIDER": "fake",
            "AICOMPANY_VIDEO_PROVIDER": "fake", "AICOMPANY_NAVER_BLOG_PROVIDER": "fake",
            "ALLOW_PAID_PROVIDER": "False",
        }


if __name__ == "__main__":
    unittest.main()
