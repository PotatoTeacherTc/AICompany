from core.artifact_manager import ArtifactManager
from core.execution_history import ExecutionHistory
from core.task import Task
from core.task_queue import TaskQueue
from core.worker import TaskWorker


class AutomationService:
    """Coordinates task submission and execution without exposing pipelines."""

    def __init__(
        self,
        manager,
        history=None,
        artifact_manager=None,
        task_queue=None,
        worker=None,
        max_retries=0,
    ):
        if manager is None:
            raise ValueError("manager is required")

        self.manager = manager
        self.history = history or getattr(task_queue, "history", None) or ExecutionHistory()
        self.artifact_manager = artifact_manager or ArtifactManager()
        self.task_queue = task_queue or TaskQueue(
            history=self.history,
            max_retries=max_retries,
        )
        if self.task_queue.history is None:
            self.task_queue.history = self.history
        self.worker = worker or TaskWorker(
            self.task_queue,
            self.manager,
            self.history,
        )

    def create_task(
        self,
        task_text,
        parameters=None,
        parent_task_id=None,
        max_retries=0,
        timeout_seconds=None,
    ):
        return Task(
            task_text,
            parameters=parameters,
            parent_task_id=parent_task_id,
            max_retries=max_retries,
            timeout_seconds=timeout_seconds,
        )

    def submit(self, task):
        if not isinstance(task, Task):
            raise TypeError("task must be a Task")
        self.task_queue.add(task)
        return task

    def submit_text(self, task_text, **task_options):
        return self.submit(self.create_task(task_text, **task_options))

    def run_next(self):
        return self.worker.run_once()

    def run_all(self):
        return self.worker.run_all()

    def cancel(self, task):
        return self.task_queue.cancel(task)
