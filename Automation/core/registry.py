class PipelineRegistry:

    def __init__(self):

        self._pipelines = {}


    def register(self, task_type, pipeline):

        self._pipelines[task_type] = pipeline


    def get(self, task_type):

        return self._pipelines.get(task_type)


    def list_pipelines(self):

        return list(self._pipelines.keys())