from core.base_pipeline import BasePipeline


class PipelineRegistry:

    def __init__(self):

        self._pipelines = {}


    def register(self, task_type, pipeline):

        if not isinstance(task_type, str) or not task_type.strip():
            raise ValueError("task_type must be a non-empty string")

        if not isinstance(pipeline, BasePipeline):
            raise TypeError("pipeline must inherit from BasePipeline")

        if task_type in self._pipelines:
            raise ValueError(f"Pipeline already registered for task type: {task_type}")

        self._pipelines[task_type] = pipeline


    def get(self, task_type):

        return self._pipelines.get(task_type)


    def list_pipelines(self):

        return list(self._pipelines.keys())
