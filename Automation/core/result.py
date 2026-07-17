from core.status import PipelineStatus


class PipelineResult:

    def __init__(
        self,
        status,
        pipeline,
        task,
        data=None,
        error=None
    ):

        self.status = status

        self.pipeline = pipeline

        self.task = task

        self.data = data or {}

        self.error = error


    def is_success(self):

        return self.status == PipelineStatus.SUCCESS


    def is_failed(self):

        return self.status == PipelineStatus.FAILED


    def is_pending(self):

        return self.status in [

            PipelineStatus.PENDING,

            PipelineStatus.NOT_IMPLEMENTED

        ]


    def to_dict(self):

        return {

            "status": self.status,

            "pipeline": self.pipeline,

            "task": self.task,

            "data": self.data,

            "error": self.error

        }