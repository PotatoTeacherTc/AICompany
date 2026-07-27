from core.base_pipeline import BasePipeline


class PipelineRegistry:

    def __init__(self):

        self._pipelines = {}


    def register(self, task_type, pipeline, capabilities=()):

        if not isinstance(task_type, str) or not task_type.strip():
            raise ValueError("task_type must be a non-empty string")

        if not isinstance(pipeline, BasePipeline):
            raise TypeError("pipeline must inherit from BasePipeline")

        if task_type in self._pipelines:
            raise ValueError(f"Pipeline already registered for task type: {task_type}")

        if isinstance(capabilities, str):
            raise TypeError("capabilities must be an iterable of strings")

        try:
            capabilities = tuple(capabilities)
        except TypeError as error:
            raise TypeError("capabilities must be an iterable of strings") from error

        if not all(isinstance(capability, str) and capability.strip() for capability in capabilities):
            raise ValueError("capabilities must contain non-empty strings")

        self._pipelines[task_type] = {
            "pipeline": pipeline,
            "capabilities": capabilities,
        }


    def get(self, task_type):

        registration = self._pipelines.get(task_type)
        return registration["pipeline"] if registration else None


    def list_pipelines(self):

        return list(self._pipelines.keys())

    def get_capability(self, task_type):
        registration = self._pipelines.get(task_type)
        if not registration:
            return None

        return {
            "task_type": task_type,
            "pipeline": registration["pipeline"].name,
            "capabilities": list(registration["capabilities"]),
        }

    def list_capabilities(self):
        return [self.get_capability(task_type) for task_type in self.list_pipelines()]
