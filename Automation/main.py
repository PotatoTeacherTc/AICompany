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
from core.object_storage import ArtifactStorageAdapter, LocalStorageProvider
from core.persistence import JsonStateRepository
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


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "music-import":
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
