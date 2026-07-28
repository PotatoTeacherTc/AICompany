from pathlib import Path

from core.artifact_manager import ArtifactManager
from core.artifact_repository import FileArtifactRepository
from core.collaboration_worker import FunctionWorker
from core.content_orchestrator import ContentOrchestrator
from core.department import DepartmentManager, WorkerDirectory
from core.department_workflow import DepartmentWorkflow
from core.execution_history import ExecutionHistory
from core.execution_history_repository import JsonFileExecutionHistoryRepository
from core.media_pipeline import ImagePipeline, VideoPipeline
from core.mission import Mission
from core.music_pipeline import MusicPipeline
from core.persistence import JsonStateRepository
from core.result import PipelineResult
from core.settings_manager import SettingsManager
from core.status import PipelineStatus
from core.structured_logging import LocalFileLogger
from core.task import Task
from core.text_creation_pipeline import TextCreationPipeline
from core.usage_engine import UsageEngine
from core.worker_result import WorkerResult
from providers.factory import ProviderFactory


class HybridCreativeDemo:
    """Real/local-or-Fake text creation followed by Fake media providers."""

    def __init__(
        self,
        department_manager,
        text_pipeline,
        content_orchestrator,
        *,
        execution_history=None,
        logger=None,
        usage_engine=None,
        settings_manager=None,
    ):
        self.departments = department_manager
        self.text = text_pipeline
        self.content = content_orchestrator
        self.history = execution_history
        self.logger = logger
        self.usage = usage_engine
        self.settings = settings_manager

    def execute(self, request, workspace_id, requested_by="creative-demo-user"):
        if not isinstance(request, str) or not request.strip():
            return self._invalid(workspace_id)
        mission = Mission.create(
            "Creative demo mission", request, requested_by, workspace_id
        )

        def pipeline_executor(_workflow_task, _selection, previous):
            return self._pipeline(request, mission, previous)

        workflow = DepartmentWorkflow(
            self.departments,
            pipeline_executor,
            execution_history=self.history,
            logger=self.logger,
            usage_engine=self.usage,
            settings_manager=self.settings,
        )
        return workflow.execute(mission, "CREATIVE_DEMO")

    def _pipeline(self, request, mission, previous):
        if previous is not None:
            return previous
        stages = {}
        artifacts = []
        text_results = []
        for task_type in ("LYRICS", "CONTENT_PLAN"):
            task = Task(
                request,
                {"mission_id": mission.id},
                workspace_id=mission.workspace_id,
            )
            task.task_type = task_type
            result = self.text.run(task)
            text_results.append(result)
            stages[task_type.lower()] = self._text_stage(result)
            artifacts.extend(result.get("artifacts", []))
            if result.get("status") != PipelineStatus.SUCCESS:
                return self._result(
                    mission, stages, artifacts, PipelineStatus.FAILED,
                    text_results,
                )
        content_task = Task(
            request,
            {
                "mission_id": mission.id,
                "title": text_results[0]["data"]["title"],
                "visibility": "private",
            },
            workspace_id=mission.workspace_id,
        )
        content_task.task_type = "CONTENT"
        content = self.content.run(content_task)
        stages.update({
            f"fake_{name}": dict(value, generation_mode="fake")
            for name, value in (content.get("data", {}).get("stages") or {}).items()
        })
        artifacts.extend(content.get("artifacts", []))
        return self._result(
            mission,
            stages,
            artifacts,
            content.get("status", PipelineStatus.FAILED),
            text_results,
        )

    @staticmethod
    def _text_stage(result):
        data = result.get("data") or {}
        return {
            "status": result.get("status"),
            "provider": data.get("provider"),
            "model": data.get("model"),
            "generation_mode": data.get("generation_mode"),
            "artifact_ids": [
                item.get("artifact_id") for item in result.get("artifacts", [])
            ],
            "error": result.get("error"),
        }

    @staticmethod
    def _result(mission, stages, artifacts, status, text_results):
        usage_values = [
            item.get("data", {}).get("provider_usage")
            for item in text_results
            if isinstance(item.get("data", {}).get("provider_usage"), dict)
        ]
        usage = None
        if usage_values:
            usage = {
                "provider": usage_values[0].get("provider"),
                "model": usage_values[0].get("model"),
            }
            for field in (
                "input_tokens", "output_tokens", "total_tokens",
                "estimated_cost_usd",
            ):
                values = [
                    item[field] for item in usage_values
                    if isinstance(item.get(field), (int, float))
                ]
                if values:
                    usage[field] = sum(values)
        title = next(
            (
                item.get("data", {}).get("title")
                for item in text_results
                if item.get("data", {}).get("title")
            ),
            None,
        )
        return PipelineResult(
            status,
            "Hybrid Creative Demo",
            "Creative demo",
            "CREATIVE_DEMO",
            data={
                "workspace_id": mission.workspace_id,
                "mission_id": mission.id,
                "title": title,
                "stages": stages,
                "provider_usage": usage,
                "task_redacted": True,
            },
            artifacts=artifacts,
            error=None if status == PipelineStatus.SUCCESS else "CreativeDemoError",
        ).to_dict()

    @staticmethod
    def _invalid(workspace_id):
        return PipelineResult(
            PipelineStatus.FAILED,
            "Hybrid Creative Demo",
            "Creative demo",
            "CREATIVE_DEMO",
            data={"workspace_id": workspace_id},
            error="CreativeDemoError: InvalidRequest",
        ).to_dict()


def build_creative_demo(root, text_environment=None, text_transport=None):
    root = Path(root).resolve()
    state_root = root / "state"
    storage_root = root / "artifacts"
    state_root.mkdir(parents=True, exist_ok=True)
    storage_root.mkdir(parents=True, exist_ok=True)
    state = JsonStateRepository(state_root / "state.json")
    artifact_manager = ArtifactManager(FileArtifactRepository(
        state_root / "artifact-metadata.json", storage_root
    ))
    history = ExecutionHistory(repository=JsonFileExecutionHistoryRepository(
        state_root / "execution-history.json"
    ))
    logger = LocalFileLogger(state_root / "creative-demo.jsonl")
    settings = SettingsManager(state, logger=logger)
    usage = UsageEngine(state, logger=logger)
    directory = WorkerDirectory()

    def handler(context):
        return WorkerResult.create(
            PipelineStatus.SUCCESS, "creative-worker", context
        )

    directory.register(
        FunctionWorker("creative-worker", handler),
        "default",
        ("CREATIVE_DEMO",),
    )
    departments = DepartmentManager(
        state, directory, ("CREATIVE_DEMO",), logger=logger
    )
    if departments.get("content", "default") is None:
        departments.create(
            "default",
            "Content",
            "Content creative demo department",
            "CONTENT",
            worker_ids=("creative-worker",),
            lead_worker_id="creative-worker",
            supported_task_types=("CREATIVE_DEMO",),
            department_id="content",
        )
    text_selection = ProviderFactory.text_from_environment(
        text_environment or {}, transport=text_transport
    )
    text = TextCreationPipeline(
        storage_root / "text",
        selection=text_selection,
        artifact_manager=artifact_manager,
        execution_history=history,
        logger=logger,
    )
    music = MusicPipeline(
        storage_root / "music",
        artifact_manager=artifact_manager,
        execution_history=history,
    )
    image = ImagePipeline(
        storage_root / "image",
        artifact_manager=artifact_manager,
        execution_history=history,
    )
    video = VideoPipeline(
        storage_root / "video",
        artifact_manager=artifact_manager,
        execution_history=history,
    )
    content = ContentOrchestrator(
        music, image, video, execution_history=history
    )
    return HybridCreativeDemo(
        departments,
        text,
        content,
        execution_history=history,
        logger=logger,
        usage_engine=usage,
        settings_manager=settings,
    )
