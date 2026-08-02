import json
import os
from pathlib import Path
import sys

from agent.manager import Manager
from application.creative_demo import build_creative_demo
from application.automation_service import AutomationService
from core.artifact_manager import ArtifactManager
from core.artifact_repository import FileArtifactRepository
from core.base_pipeline import BasePipeline
from core.execution_history import ExecutionHistory
from core.content_pipeline import ContentPipeline
from core.history_pipeline import HistoryPipeline
from core.execution_history_repository import JsonFileExecutionHistoryRepository
from core.music_planning import MusicPlanningRequest, MusicPlanningService
from core.completed_audio_intake import (
    AudioInputLocator, AudioInputValidator, MusicProjectAudioLinkService,
)
from core.content_brief_orchestration import (
    ContentBriefRequest, ContentBriefService, ContentProjectOrchestrator,
    ContentProjectRepository,
)
from core.image_package import (
    ImagePackageOrchestrator, ImagePackageRequest,
)
from core.blog_package import (
    BlogPackageOrchestrator, BlogPackageRequest, BlogPackageService,
)
from core.video_package import VideoPackageOrchestrator, VideoPackageRequest
from core.object_storage import ArtifactStorageAdapter, LocalStorageProvider
from core.persistence import JsonStateRepository
from core.usage_engine import UsageEngine
from core.secure_token_store import WindowsLocalSecureTokenStore
from core.youtube_publishing import (
    GoogleYouTubeOAuthFlow, GoogleYouTubeProvider,
    YouTubeConnectionRepository, YouTubeConnectionService,
    YouTubeFoundationError, YouTubePublishingService,
)
from core.naver_blog_publishing import NaverBlogPublishingAssistant, NaverPublishingRequest
from core.music_pipeline import MusicPipeline
from core.pipeline import AIPipeline
from core.registry import PipelineRegistry
from core.result import PipelineResult
from core.research_pipeline import ResearchPipeline
from core.status import PipelineStatus
from core.stub_pipelines import StubPipeline
from core.task import Task
from providers.factory import ProviderFactory


class FailingPipeline(BasePipeline):
    """Deliberate failure path retained for end-to-end error handling tests."""

    def __init__(self):
        super().__init__("Failing Test Pipeline")

    def run(self, task):
        return PipelineResult(
            status=PipelineStatus.FAILED,
            pipeline=self.name,
            task=task,
            task_type=task.task_type,
            error="Intentional test failure",
        ).to_dict()


def build_registry(
    history,
    base_folder=None,
    music_root=None,
    content_root=None,
    research_root=None,
    artifact_manager=None,
):
    registry = PipelineRegistry()
    artifact_manager = artifact_manager or ArtifactManager()
    registry.register(
        "FILE",
        AIPipeline(base_folder=base_folder, artifact_manager=artifact_manager),
        capabilities=("file_organization",),
    )
    registry.register(
        "MUSIC",
        MusicPipeline(music_root=music_root, artifact_manager=artifact_manager),
        capabilities=("music_project_creation",),
    )
    registry.register(
        "CONTENT",
        ContentPipeline(content_root=content_root, artifact_manager=artifact_manager),
        capabilities=("content_project_creation",),
    )
    registry.register(
        "RESEARCH",
        ResearchPipeline(research_root=research_root, artifact_manager=artifact_manager),
        capabilities=("research_project_creation",),
    )
    registry.register(
        "HISTORY",
        HistoryPipeline(history),
        capabilities=("execution_history_analysis",),
    )
    registry.register(
        "FAIL", FailingPipeline(), capabilities=("failure_path_testing",)
    )
    return registry


def run(task_texts=None, history=None, registry=None, artifact_manager=None):
    print("AICompany Automation")
    history = history or ExecutionHistory()
    registry = registry or build_registry(history, artifact_manager=artifact_manager)
    manager = Manager(registry)
    service = AutomationService(
        manager,
        history=history,
        artifact_manager=artifact_manager,
    )

    print("\nRegistered Pipelines:")
    print(registry.list_pipelines())

    if task_texts is None:
        task_texts = [
            "Organize TestFiles",
            "Create a new music song",
            "Create a YouTube video",
            "Research AI music trends",
            "Run failure test",
            "Analyze execution history",
        ]
    for task_text in task_texts:
        service.submit(Task(task_text))

    completed_tasks = service.run_all()
    print("\n" + "=" * 60 + "\nFINAL TASK SUMMARY\n" + "=" * 60)
    for task in completed_tasks:
        print(f"[{task.id}] {task.status} - {task.task_text}")
    history.print_summary()
    print("\n" + "=" * 60 + "\nAICompany Automation Finished\n" + "=" * 60)
    return completed_tasks


def run_creative_demo(request=None, use_local_text=False, root=None):
    request = request or (
        "이별 후 다시 일어서는 내용의 한국어 발라드 곡과 "
        "유튜브 영상 구성을 준비해."
    )
    root = Path(root or Path(__file__).parent / "logs" / "creative-demo")
    environment = None
    if use_local_text:
        environment = dict(os.environ)
        environment["AICOMPANY_TEXT_PROVIDER"] = "ollama"
    demo = build_creative_demo(root, text_environment=environment)
    result = demo.execute(request, "default")
    data = result.get("data") or {}
    pipeline = data.get("pipeline") or {}
    selection = data.get("selection") or {}
    summary = {
        "workspace_id": data.get("workspace_id"),
        "mission_id": data.get("mission_id"),
        "department_id": selection.get("department_id"),
        "status": result.get("status"),
        "title": pipeline.get("title"),
        "artifact_ids": [
            item.get("artifact_id") for item in result.get("artifacts", [])
        ],
        "stages": {
            name: value.get("status")
            for name, value in (pipeline.get("stages") or {}).items()
        },
        "usage": pipeline.get("usage"),
        "text_provider_mode": (
            "local-ollama" if use_local_text else "fake-offline"
        ),
        "error": result.get("error"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return result


def run_music_plan(request=None, workspace_id="default", root=None,
                   environment=None, transport=None):
    request = request or "이별 후 다시 일어서는 내용의 한국어 발라드를 기획해 줘."
    root = Path(root or Path(__file__).parent / "logs" / "music-plans").resolve()
    storage = root / "artifacts"
    state = root / "state"
    artifacts = ArtifactManager(FileArtifactRepository(
        state / "artifact-metadata.json", storage
    ))
    history = ExecutionHistory(repository=JsonFileExecutionHistoryRepository(
        state / "execution-history.json"
    ))
    selection = ProviderFactory.text_from_environment(
        dict(os.environ) if environment is None else environment,
        transport=transport,
    )
    result = MusicPlanningService(
        storage, selection=selection, artifact_manager=artifacts,
        execution_history=history,
    ).run(MusicPlanningRequest(workspace_id, request))
    data = result.get("data") or {}
    print(json.dumps({
        "workspace_id": data.get("workspace_id"),
        "request_id": data.get("mission_id"),
        "status": result.get("status"),
        "primary_title": data.get("primary_title"),
        "provider": data.get("provider"),
        "model": data.get("model"),
        "artifact_ids": [item.get("artifact_id") for item in result.get("artifacts", [])],
        "next_action": data.get("next_action"),
        "error": result.get("error"),
    }, ensure_ascii=False, indent=2))
    return result


def run_music_import(workspace_id, project_id, audio_name, root=None,
                     probe=None):
    root = Path(root or Path(__file__).parent / "logs" / "music-plans").resolve()
    storage = root / "artifacts"
    state = root / "state"
    repository = FileArtifactRepository(state / "artifact-metadata.json", storage)
    artifacts = ArtifactManager(
        repository,
        storage_adapter=ArtifactStorageAdapter(LocalStorageProvider(storage), repository),
    )
    history = ExecutionHistory(repository=JsonFileExecutionHistoryRepository(
        state / "execution-history.json"
    ))
    service = MusicProjectAudioLinkService(
        AudioInputLocator(root / "inputs"),
        AudioInputValidator(probe=probe), artifacts,
        JsonStateRepository(state / "music-project-state.json"), history,
    )
    result = service.import_audio(workspace_id, project_id, audio_name)
    data = result.get("data") or {}
    print(json.dumps({
        "workspace_id": data.get("workspace_id"),
        "project_id": data.get("project_id") or data.get("mission_id"),
        "status": data.get("current_status") or result.get("status"),
        "source_filename": data.get("source_filename"),
        "detected_format": data.get("detected_format"),
        "duration_seconds": data.get("duration_seconds"),
        "audio_artifact_id": data.get("audio_artifact_id"),
        "next_action": data.get("next_action"),
        "error": result.get("error"),
    }, ensure_ascii=False, indent=2))
    return result


def run_content_brief(workspace_id, music_project_id, root=None,
                      environment=None, transport=None, **preferences):
    root = Path(root or Path(__file__).parent / "logs" / "music-plans").resolve()
    storage = root / "artifacts"
    state_root = root / "state"
    state = JsonStateRepository(state_root / "music-project-state.json")
    repository = FileArtifactRepository(
        state_root / "artifact-metadata.json", storage
    )
    artifacts = ArtifactManager(
        repository,
        storage_adapter=ArtifactStorageAdapter(LocalStorageProvider(storage), repository),
    )
    history = ExecutionHistory(repository=JsonFileExecutionHistoryRepository(
        state_root / "execution-history.json"
    ))
    selection = ProviderFactory.text_from_environment(
        dict(os.environ) if environment is None else environment,
        transport=transport,
    )
    orchestrator = ContentProjectOrchestrator(
        root / "work", ContentBriefService(selection=selection),
        ContentProjectRepository(state), state, artifacts, history,
        usage_engine=UsageEngine(state),
    )
    result = orchestrator.run(ContentBriefRequest(
        workspace_id, music_project_id, **preferences
    ))
    data = result.get("data") or {}
    print(json.dumps({
        "workspace_id": data.get("workspace_id"),
        "music_project_id": data.get("music_project_id"),
        "content_project_id": data.get("content_project_id"),
        "project_title": data.get("project_title"),
        "status": data.get("current_status") or result.get("status"),
        "brief_artifact_id": data.get("brief_artifact_id"),
        "execution_plan_artifact_id": data.get("execution_plan_artifact_id"),
        "pending_steps": data.get("pending_steps"),
        "next_action": data.get("next_action"),
        "error": result.get("error"),
    }, ensure_ascii=False, indent=2))
    return result


def run_image_package(workspace_id, content_project_id, root=None,
                      environment=None, transport=None, seed=1000,
                      workflow_profile="default"):
    root = Path(root or Path(__file__).parent / "logs" / "music-plans").resolve()
    storage = root / "artifacts"
    state_root = root / "state"
    state = JsonStateRepository(state_root / "music-project-state.json")
    repository = FileArtifactRepository(state_root / "artifact-metadata.json", storage)
    artifacts = ArtifactManager(
        repository,
        storage_adapter=ArtifactStorageAdapter(LocalStorageProvider(storage), repository),
    )
    history = ExecutionHistory(repository=JsonFileExecutionHistoryRepository(
        state_root / "execution-history.json"
    ))
    selection = ProviderFactory.image_from_environment(
        dict(os.environ) if environment is None else environment,
        transport=transport,
    )
    orchestrator = ImagePackageOrchestrator(
        root / "work", selection, ContentProjectRepository(state), state,
        artifacts, history, usage_engine=UsageEngine(state),
    )
    result = orchestrator.run(ImagePackageRequest(
        workspace_id, content_project_id, seed=int(seed),
        workflow_profile=workflow_profile,
    ))
    data = result.get("data") or {}
    print(json.dumps({
        "workspace_id": data.get("workspace_id"),
        "content_project_id": data.get("content_project_id"),
        "image_package_status": data.get("image_package_status"),
        "provider": data.get("provider"), "model": data.get("model"),
        "image_artifact_ids": data.get("image_artifact_ids"),
        "manifest_artifact_id": data.get("manifest_artifact_id"),
        "next_action": data.get("next_action"), "error": result.get("error"),
    }, ensure_ascii=False, indent=2))
    return result


def run_blog_package(workspace_id, content_project_id, root=None,
                     environment=None, transport=None, **options):
    root = Path(root or Path(__file__).parent / "logs" / "music-plans").resolve()
    storage = root / "artifacts"
    state_root = root / "state"
    state = JsonStateRepository(state_root / "music-project-state.json")
    repository = FileArtifactRepository(state_root / "artifact-metadata.json", storage)
    artifacts = ArtifactManager(
        repository,
        storage_adapter=ArtifactStorageAdapter(LocalStorageProvider(storage), repository),
    )
    history = ExecutionHistory(repository=JsonFileExecutionHistoryRepository(
        state_root / "execution-history.json"
    ))
    selection = ProviderFactory.text_from_environment(
        dict(os.environ) if environment is None else environment,
        transport=transport,
    )
    orchestrator = BlogPackageOrchestrator(
        root / "work", BlogPackageService(selection=selection),
        ContentProjectRepository(state), state, artifacts, history,
        usage_engine=UsageEngine(state),
    )
    result = orchestrator.run(BlogPackageRequest(
        workspace_id, content_project_id, **options
    ))
    data = result.get("data") or {}
    print(json.dumps({
        "workspace_id": data.get("workspace_id"),
        "content_project_id": data.get("content_project_id"),
        "blog_package_id": data.get("blog_package_id"),
        "status": data.get("blog_package_status") or result.get("status"),
        "title": data.get("title"), "artifact_ids": data.get("artifact_ids"),
        "image_count": data.get("image_count"), "provider": data.get("provider"),
        "model": data.get("model"), "next_action": data.get("next_action"),
        "error": result.get("error"),
    }, ensure_ascii=False, indent=2))
    return result


def run_video_package(workspace_id, content_project_id, root=None,
                      environment=None, idempotency_key=None):
    root = Path(root or Path(__file__).parent / "logs" / "music-plans").resolve()
    storage, state_root = root / "artifacts", root / "state"
    state = JsonStateRepository(state_root / "music-project-state.json")
    repository = FileArtifactRepository(state_root / "artifact-metadata.json", storage)
    artifacts = ArtifactManager(repository, ArtifactStorageAdapter(LocalStorageProvider(storage), repository))
    history = ExecutionHistory(repository=JsonFileExecutionHistoryRepository(state_root / "execution-history.json"))
    selection = ProviderFactory.video_from_environment(dict(os.environ) if environment is None else environment)
    result = VideoPackageOrchestrator(
        root / "work", selection, ContentProjectRepository(state), state,
        artifacts, history, UsageEngine(state),
    ).run(VideoPackageRequest(workspace_id, content_project_id, idempotency_key))
    data = result.get("data") or {}
    print(json.dumps({key: data.get(key) for key in (
        "workspace_id", "content_project_id", "video_package_id",
        "video_package_status", "duration_seconds", "thumbnail_artifact_id",
        "artifact_ids", "provider", "model", "next_action",
    )} | {"error": result.get("error")}, ensure_ascii=False, indent=2))
    return result


def _youtube_components(root=None):
    root = Path(root or Path(__file__).parent / "logs" / "music-plans").resolve()
    storage, state_root = root / "artifacts", root / "state"
    state = JsonStateRepository(state_root / "music-project-state.json")
    repository = FileArtifactRepository(state_root / "artifact-metadata.json", storage)
    artifacts = ArtifactManager(repository, ArtifactStorageAdapter(LocalStorageProvider(storage), repository))
    connections = YouTubeConnectionService(YouTubeConnectionRepository(state), WindowsLocalSecureTokenStore())
    return root, state, artifacts, connections


def run_youtube_connect(workspace_id, client_secret_file, root=None, flow=None,
                        expected_channel_title=None, timeout_seconds=900):
    _, _, _, connections = _youtube_components(root)
    try:
        config = json.loads(Path(client_secret_file).read_text(encoding="utf-8"))
        token, channel = (flow or GoogleYouTubeOAuthFlow()).authorize(
            config, workspace_id, timeout_seconds=timeout_seconds)
        if (expected_channel_title is not None and
                channel["safe_channel_title"] != expected_channel_title):
            raise YouTubeFoundationError("CHANNEL_TITLE_MISMATCH")
        connection = connections.connect(workspace_id, channel["channel_id"], channel["safe_channel_title"], token)
        result = {"status": "CONNECTED", "workspace_id": workspace_id, "connection_id": connection.connection_id,
                  "channel_id": connection.channel_id, "channel_title": connection.safe_channel_title}
    except Exception as error:
        result = {"status": "FAILED", "workspace_id": workspace_id,
                  "error": "YouTubeConnectionError: " + getattr(error, "code", "CONNECTION_FAILED")}
    print(json.dumps(result, ensure_ascii=False, indent=2)); return result


def run_youtube_connection_status(workspace_id, root=None):
    _, _, _, connections = _youtube_components(root)
    values = connections.repository.list(workspace_id)
    result = {"status": "SUCCESS", "workspace_id": workspace_id, "connections": [
        {"connection_id": item.connection_id, "channel_id": item.channel_id,
         "channel_title": item.safe_channel_title, "status": item.status} for item in values]}
    print(json.dumps(result, ensure_ascii=False, indent=2)); return result


def run_youtube_upload(workspace_id, content_project_id, connection_id, idempotency_key, root=None):
    root, state, artifacts, connections = _youtube_components(root)
    history = ExecutionHistory(repository=JsonFileExecutionHistoryRepository(root / "state" / "execution-history.json"))
    result = YouTubePublishingService(GoogleYouTubeProvider(), connections, ContentProjectRepository(state), state,
        artifacts, history=history, usage=UsageEngine(state)).publish(
            workspace_id, content_project_id, connection_id, idempotency_key, "private")
    data = result.get("data") or {}
    print(json.dumps({"status": result.get("status"), "workspace_id": data.get("workspace_id"),
        "content_project_id": data.get("content_project_id"), "publication_id": data.get("publication_id"),
        "video_id": data.get("video_id"), "privacy_status": data.get("privacy_status"),
        "processing_status": data.get("processing_status"), "thumbnail_status": data.get("thumbnail_status"),
        "error": result.get("error")}, ensure_ascii=False, indent=2)); return result


def run_naver_blog_login(environment=None):
    selection = ProviderFactory.naver_blog_from_environment(environment or dict(os.environ)); browser = selection.provider
    try: result = browser.open_login(selection.timeout_seconds)
    except Exception as error: result = {"status": "FAILED", "error": "NaverPublishingError: " + getattr(error, "code", "LOGIN_FAILED")}
    finally:
        if hasattr(browser, "close"): browser.close()
    print(json.dumps(result, ensure_ascii=False, indent=2)); return result


def run_naver_blog_publish(workspace_id, content_project_id, category=None, root=None, environment=None):
    root = Path(root or Path(__file__).parent / "logs" / "music-plans").resolve(); storage, state_root = root / "artifacts", root / "state"
    state = JsonStateRepository(state_root / "music-project-state.json"); repository = FileArtifactRepository(state_root / "artifact-metadata.json", storage)
    artifacts = ArtifactManager(repository, ArtifactStorageAdapter(LocalStorageProvider(storage), repository))
    history = ExecutionHistory(repository=JsonFileExecutionHistoryRepository(state_root / "execution-history.json"))
    selection = ProviderFactory.naver_blog_from_environment(environment or dict(os.environ)); browser = selection.provider
    try:
        result = NaverBlogPublishingAssistant(browser, state, artifacts, history, root / "work" / "naver", UsageEngine(state)).run(
            NaverPublishingRequest(workspace_id, content_project_id, category, True, selection.timeout_seconds))
    finally:
        if hasattr(browser, "close"): browser.close()
    data = result.get("data") or {}; print(json.dumps({"status": result.get("status"), "workspace_id": data.get("workspace_id"),
        "content_project_id": data.get("content_project_id"), "publication_status": data.get("status") or data.get("publication_status"),
        "published_url": data.get("published_url"), "published_at": data.get("published_at"), "error": result.get("error")}, ensure_ascii=False, indent=2)); return result


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in {"naver-blog-login", "naver-blog-publish"}:
        values = {"--workspace": None, "--content-project-id": None, "--category": None}; index = 2
        while index < len(sys.argv):
            if sys.argv[index] in values and index + 1 < len(sys.argv): values[sys.argv[index]] = sys.argv[index + 1]; index += 2
            else: index += 1
        environment = dict(os.environ); environment["AICOMPANY_NAVER_BLOG_PROVIDER"] = "playwright"
        environment.setdefault("AICOMPANY_NAVER_PROFILE_DIR", str(Path(__file__).parent / ".browser-profiles" / "naver"))
        result = run_naver_blog_login(environment) if sys.argv[1] == "naver-blog-login" else run_naver_blog_publish(
            values["--workspace"], values["--content-project-id"], values["--category"], environment=environment)
        if result.get("status") not in {"LOGIN_READY", PipelineStatus.SUCCESS}: raise SystemExit(1)
    elif len(sys.argv) > 1 and sys.argv[1] in {"youtube-connect", "youtube-connection-status", "youtube-upload"}:
        command = sys.argv[1]; values = {"--workspace": None, "--client-secret-file": None,
            "--content-project-id": None, "--connection-id": None, "--idempotency-key": None,
            "--expected-channel-title": None, "--timeout-seconds": "900"}
        index = 2
        while index < len(sys.argv):
            if sys.argv[index] in values and index + 1 < len(sys.argv): values[sys.argv[index]] = sys.argv[index + 1]; index += 2
            else: index += 1
        if command == "youtube-connect": result = run_youtube_connect(
            values["--workspace"], values["--client-secret-file"],
            expected_channel_title=values["--expected-channel-title"],
            timeout_seconds=int(values["--timeout-seconds"]))
        elif command == "youtube-connection-status": result = run_youtube_connection_status(values["--workspace"])
        else: result = run_youtube_upload(values["--workspace"], values["--content-project-id"], values["--connection-id"], values["--idempotency-key"])
        if result.get("status") not in {"SUCCESS", "CONNECTED"}: raise SystemExit(1)
    elif len(sys.argv) > 1 and sys.argv[1] == "video-package":
        values = {"--workspace-id": None, "--content-project-id": None, "--provider": "ffmpeg", "--idempotency-key": None}
        index = 2
        while index < len(sys.argv):
            if sys.argv[index] in values and index + 1 < len(sys.argv): values[sys.argv[index]] = sys.argv[index + 1]; index += 2
            else: index += 1
        video_environment = dict(os.environ); video_environment["AICOMPANY_VIDEO_PROVIDER"] = values["--provider"]
        video_result = run_video_package(values["--workspace-id"], values["--content-project-id"], environment=video_environment, idempotency_key=values["--idempotency-key"])
        if video_result.get("status") != PipelineStatus.SUCCESS: raise SystemExit(1)
    elif len(sys.argv) > 1 and sys.argv[1] == "blog-package":
        values = {
            "--workspace-id": None, "--content-project-id": None,
            "--provider": None, "--language": None, "--tone": None,
            "--target-platform": "generic_blog", "--article-length": None,
            "--idempotency-key": None,
        }
        index = 2
        while index < len(sys.argv):
            if sys.argv[index] in values and index + 1 < len(sys.argv):
                values[sys.argv[index]] = sys.argv[index + 1]
                index += 2
            else:
                index += 1
        blog_environment = dict(os.environ)
        if values["--provider"] is not None:
            blog_environment["AICOMPANY_TEXT_PROVIDER"] = values.pop("--provider")
        else:
            values.pop("--provider")
        blog_result = run_blog_package(
            values.pop("--workspace-id"), values.pop("--content-project-id"),
            environment=blog_environment,
            **{key[2:].replace("-", "_"): value for key, value in values.items() if value is not None},
        )
        if blog_result.get("status") != PipelineStatus.SUCCESS:
            raise SystemExit(1)
    elif len(sys.argv) > 1 and sys.argv[1] == "image-package":
        values = {
            "--workspace": None, "--content-project-id": None,
            "--provider": None, "--workflow-profile": "default",
            "--seed": "1000", "--fake": None,
        }
        index = 2
        while index < len(sys.argv):
            if sys.argv[index] == "--fake":
                values["--fake"] = "true"
                index += 1
            elif sys.argv[index] in values and index + 1 < len(sys.argv):
                values[sys.argv[index]] = sys.argv[index + 1]
                index += 2
            else:
                index += 1
        image_environment = dict(os.environ)
        if values["--fake"] == "true":
            image_environment["AICOMPANY_IMAGE_PROVIDER"] = "fake"
        elif values["--provider"] is not None:
            image_environment["AICOMPANY_IMAGE_PROVIDER"] = values["--provider"]
        image_result = run_image_package(
            values["--workspace"], values["--content-project-id"],
            environment=image_environment, seed=values["--seed"],
            workflow_profile=values["--workflow-profile"],
        )
        if image_result.get("status") != PipelineStatus.SUCCESS:
            raise SystemExit(1)
    elif len(sys.argv) > 1 and sys.argv[1] == "content-brief":
        values = {
            "--workspace": None, "--music-project-id": None,
            "--content-goal": None, "--target-audience": None,
            "--language": None, "--additional-notes": None,
            "--idempotency-key": None,
        }
        index = 2
        while index < len(sys.argv):
            if sys.argv[index] in values and index + 1 < len(sys.argv):
                values[sys.argv[index]] = sys.argv[index + 1]
                index += 2
            else:
                index += 1
        content_brief_result = run_content_brief(
            values.pop("--workspace"), values.pop("--music-project-id"),
            **{key[2:].replace("-", "_"): value for key, value in values.items() if value is not None},
        )
        if content_brief_result.get("status") != PipelineStatus.READY_FOR_CONTENT:
            raise SystemExit(1)
    elif len(sys.argv) > 1 and sys.argv[1] == "music-import":
        values = {"--workspace": None, "--project-id": None, "--audio-name": None}
        index = 2
        while index < len(sys.argv):
            if sys.argv[index] in values and index + 1 < len(sys.argv):
                values[sys.argv[index]] = sys.argv[index + 1]
                index += 2
            else:
                index += 1
        music_import_result = run_music_import(
            values["--workspace"], values["--project-id"], values["--audio-name"]
        )
        if music_import_result.get("status") != PipelineStatus.INPUT_READY:
            raise SystemExit(1)
    elif len(sys.argv) > 1 and sys.argv[1] == "music-plan":
        workspace = "default"
        request_parts = []
        index = 2
        while index < len(sys.argv):
            if sys.argv[index] == "--workspace" and index + 1 < len(sys.argv):
                workspace = sys.argv[index + 1]
                index += 2
            else:
                request_parts.append(sys.argv[index])
                index += 1
        music_plan_result = run_music_plan(
            " ".join(request_parts) if request_parts else None,
            workspace_id=workspace,
        )
        if music_plan_result.get("status") != PipelineStatus.WAITING_FOR_INPUT:
            raise SystemExit(1)
    elif len(sys.argv) > 1 and sys.argv[1] == "creative-demo":
        local = "--local-text" in sys.argv[2:]
        request_parts = [
            item for item in sys.argv[2:] if item != "--local-text"
        ]
        creative_result = run_creative_demo(
            " ".join(request_parts) if request_parts else None,
            use_local_text=local,
        )
        if creative_result.get("status") != PipelineStatus.SUCCESS:
            raise SystemExit(1)
    else:
        run()
