import json
import os
from pathlib import Path
import sys

from agent.manager import Manager
from application.creative_demo import build_creative_demo
from application.automation_service import AutomationService
from core.artifact_manager import ArtifactManager
from core.base_pipeline import BasePipeline
from core.execution_history import ExecutionHistory
from core.content_pipeline import ContentPipeline
from core.history_pipeline import HistoryPipeline
from core.music_pipeline import MusicPipeline
from core.pipeline import AIPipeline
from core.registry import PipelineRegistry
from core.result import PipelineResult
from core.research_pipeline import ResearchPipeline
from core.status import PipelineStatus
from core.stub_pipelines import StubPipeline
from core.task import Task


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


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "creative-demo":
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
