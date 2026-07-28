from abc import ABC, abstractmethod

from core.status import PipelineStatus
from core.worker_context import WorkerContext
from core.worker_result import WorkerResult


class BaseWorker(ABC):
    def __init__(self, name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("worker name must be a non-empty string")
        self.name = name.strip()

    @abstractmethod
    def execute(self, context):
        """Execute one WorkerContext and return a WorkerResult."""


class FunctionWorker(BaseWorker):
    """Minimal injected implementation for local and fake worker behavior."""

    def __init__(self, name, handler):
        super().__init__(name)
        if not callable(handler):
            raise ValueError("worker handler must be callable")
        self.handler = handler

    def execute(self, context):
        if not isinstance(context, WorkerContext):
            raise ValueError("context must use the WorkerContext contract")
        try:
            result = self.handler(context)
        except Exception as error:
            return WorkerResult.create(
                PipelineStatus.FAILED,
                self.name,
                context,
                error=f"WorkerError: {type(error).__name__}",
            )
        if not isinstance(result, WorkerResult):
            raise ValueError("worker must return a WorkerResult")
        if result.worker != self.name:
            raise ValueError("worker result identity mismatch")
        if result.mission_id != context.mission_id:
            raise ValueError("worker result mission mismatch")
        if result.workspace_id != context.workspace_id:
            raise ValueError("worker result workspace mismatch")
        return result
