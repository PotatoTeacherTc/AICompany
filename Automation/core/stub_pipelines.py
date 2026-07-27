from core.base_pipeline import BasePipeline
from core.result import PipelineResult
from core.status import PipelineStatus


class StubPipeline(BasePipeline):
    def __init__(self, name):
        super().__init__(name)

    def run(self, task):
        return PipelineResult(
            status=PipelineStatus.NOT_IMPLEMENTED,
            pipeline=self.name,
            task=task,
            task_type=task.task_type,
            error=f"{self.name} is not available yet",
        ).to_dict()
