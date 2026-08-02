import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid

from application.persistent_execution_service import PersistentExecutionService
from application.product_content_runner import ProductContentRunner
from application.product_workflow_service import ProductWorkflowService
from core.artifact_manager import ArtifactManager
from core.artifact_repository import FileArtifactRepository
from core.execution_history import ExecutionHistory
from core.execution_history_repository import JsonFileExecutionHistoryRepository
from core.object_storage import ArtifactStorageAdapter, LocalStorageProvider
from core.persistence import JsonStateRepository
from core.secure_token_store import WindowsLocalSecureTokenStore
from core.task_queue import InProcessJobWorker, PersistentJobQueue
from core.usage_engine import UsageEngine
from core.youtube_publishing import GoogleYouTubeProvider, YouTubeConnectionRepository, YouTubeConnectionService
from providers.factory import ProviderFactory


@unittest.skipUnless(os.environ.get("AICOMPANY_RUN_PRODUCT_SMOKE", "").lower() == "true", "real Product smoke disabled")
class RealProductSmoke(unittest.TestCase):
    def test_real_local_product_flow(self):
        root = Path(os.environ["AICOMPANY_PRODUCT_ROOT"]).resolve()
        workspace = os.environ.get("AICOMPANY_PRODUCT_WORKSPACE", "youtube-smoke")
        states = JsonStateRepository(root / "state" / "music-project-state.json")
        metadata = FileArtifactRepository(root / "state" / "artifact-metadata.json", root / "artifacts")
        artifacts = ArtifactManager(metadata, ArtifactStorageAdapter(LocalStorageProvider(root / "artifacts"), metadata))
        history = ExecutionHistory(repository=JsonFileExecutionHistoryRepository(root / "state" / "execution-history.json"))
        usage = UsageEngine(states)
        queue = PersistentJobQueue(states, workspace_ids=(workspace,))
        execution = PersistentExecutionService(queue, InProcessJobWorker(queue), history, artifacts, usage)
        connections = YouTubeConnectionService(YouTubeConnectionRepository(states), WindowsLocalSecureTokenStore())
        naver = ProviderFactory.naver_blog_from_environment().provider
        runner = ProductContentRunner(root, states, artifacts, history, usage, os.environ, connections, GoogleYouTubeProvider(), naver)
        service = ProductWorkflowService(states, execution, runner)
        resume_id = os.environ.get("AICOMPANY_PRODUCT_RESUME_ID")
        if resume_id:
            failed = service.get(workspace, resume_id)
            self.assertEqual(failed["status"], "FAILED")
            service.retry(workspace, resume_id, failed["current_stage"])
            service.run_once(workspace)
            confirm = service.get(workspace, resume_id)
            self.assertEqual(confirm["status"], "USER_CONFIRM_REQUIRED")
            service.resume(workspace, resume_id); service.run_once(workspace)
            result = service.get(workspace, resume_id)
            self.assertEqual(result["status"], "COMPLETED")
            self.assertTrue((result["results"].get("naver") or {}).get("published_url"))
            naver.close(); return
        item = service.submit(workspace, "차분한 피아노 음악을 위한 비공개 영상과 블로그 게시물을 준비해줘.", "product-smoke-" + uuid.uuid4().hex)
        service.run_once(workspace)
        waiting = service.get(workspace, item["product_id"])
        for _ in range(2):
            if waiting["status"] != "FAILED": break
            service.retry(workspace, item["product_id"], "PLANNING")
            service.run_once(workspace)
            waiting = service.get(workspace, item["product_id"])
        self.assertEqual(waiting["status"], "WAITING_FOR_INPUT")
        source = Path(os.environ["AICOMPANY_PRODUCT_SMOKE_MEDIA"]).resolve()
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "approved-smoke.wav"
            completed = subprocess.run([os.environ.get("AICOMPANY_FFMPEG_PATH", "ffmpeg"), "-y", "-i", str(source), "-vn", "-acodec", "pcm_s16le", "-metadata", "comment=" + uuid.uuid4().hex, str(audio)], capture_output=True, timeout=30, check=False)
            self.assertEqual(completed.returncode, 0)
            service.upload_audio(workspace, item["product_id"], audio.name, audio.read_bytes())
        service.run_once(workspace)
        confirm = service.get(workspace, item["product_id"])
        self.assertEqual(confirm["status"], "USER_CONFIRM_REQUIRED")
        self.assertTrue((confirm["results"].get("youtube") or {}).get("published_url"))
        service.resume(workspace, item["product_id"])
        service.run_once(workspace)
        result = service.get(workspace, item["product_id"])
        self.assertEqual(result["status"], "COMPLETED")
        self.assertTrue((result["results"].get("naver") or {}).get("published_url"))
        self.assertNotIn(str(root), repr(result))
        naver.close()


if __name__ == "__main__": unittest.main()
