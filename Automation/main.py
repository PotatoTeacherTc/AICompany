from agent.manager import Manager
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
from core.task_queue import TaskQueue
from core.worker import TaskWorker


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
):
    registry = PipelineRegistry()
    registry.register("FILE", AIPipeline(base_folder=base_folder))
    registry.register("MUSIC", MusicPipeline(music_root=music_root))
    registry.register("CONTENT", ContentPipeline(content_root=content_root))
    registry.register("RESEARCH", ResearchPipeline(research_root=research_root))
    registry.register("HISTORY", HistoryPipeline(history))
    registry.register("FAIL", FailingPipeline())
    return registry


def run(task_texts=None, history=None, registry=None):
    print("AICompany Automation")
    history = history or ExecutionHistory()
    registry = registry or build_registry(history)
    manager = Manager(registry)
    task_queue = TaskQueue()

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
        task_queue.add(Task(task_text))

    completed_tasks = TaskWorker(task_queue, manager, history).run_all()
    print("\n" + "=" * 60 + "\nFINAL TASK SUMMARY\n" + "=" * 60)
    for task in completed_tasks:
        print(f"[{task.id}] {task.status} - {task.task_text}")
    history.print_summary()
    print("\n" + "=" * 60 + "\nAICompany Automation Finished\n" + "=" * 60)
    return completed_tasks


if __name__ == "__main__":
    run()
