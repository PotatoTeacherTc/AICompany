from core.status import PipelineStatus


class PipelineResult:

    def __init__(
        self,
        status,
        pipeline,
        task,
        task_type=None,
        data=None,
        error=None
    ):

        self.status = status

        self.pipeline = pipeline

        self.task = task
        self.task_type = task_type if task_type is not None else getattr(task, "task_type", None)

        self.data = data or {}

        self.error = error


    def is_success(self):

        return self.status == PipelineStatus.SUCCESS


    def is_failed(self):

        return self.status in [

            PipelineStatus.FAILED,

            PipelineStatus.TIMED_OUT,

        ]


    def is_pending(self):

        return self.status in [

            PipelineStatus.PENDING,

            PipelineStatus.QUEUED,

            PipelineStatus.RUNNING,

            PipelineStatus.NOT_IMPLEMENTED

        ]


    def to_dict(self):

        task_text = getattr(self.task, "task_text", self.task)
        task_id = getattr(self.task, "id", None)

        return {

            "status": self.status,

            "pipeline": self.pipeline,

            "task": task_text,
            "task_id": task_id,
            "task_type": self.task_type,

            "data": self.data,

            "error": self.error

        }
