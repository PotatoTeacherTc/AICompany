from datetime import datetime
import uuid

from core.status import PipelineStatus


class Task:

    def __init__(
        self,
        task_text: str,
        parameters=None,
        parent_task_id=None,
        max_retries=0,
        timeout_seconds=None,
        workspace_id=None,
    ):

        self.id = str(uuid.uuid4())[:8]

        self.task_text = task_text

        if parameters is not None and not isinstance(parameters, dict):
            raise TypeError("parameters must be a dictionary or None")

        self.parameters = dict(parameters or {})
        self.workspace_id = workspace_id or "default"

        if parent_task_id is not None and not isinstance(parent_task_id, str):
            raise TypeError("parent_task_id must be a string or None")

        self.parent_task_id = parent_task_id

        if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")

        self.retry_count = 0

        self.max_retries = max_retries

        if timeout_seconds is not None and (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive number or None")

        self.timeout_seconds = timeout_seconds

        self.last_error_type = None

        self.status = PipelineStatus.PENDING

        self.created_at = datetime.now().isoformat()

        self.queued_at = None

        self.started_at = None

        self.completed_at = None

        self.result = None
        self.task_type = None
        self.pipeline = None


    def queue(self):

        self.status = PipelineStatus.QUEUED

        self.queued_at = datetime.now().isoformat()


    def start(self):

        self.status = PipelineStatus.RUNNING

        self.started_at = datetime.now().isoformat()


    def complete(self, result):

        self.status = PipelineStatus.SUCCESS

        self.completed_at = datetime.now().isoformat()

        self.result = result


    def fail(self, result):

        self.status = PipelineStatus.FAILED

        self.completed_at = datetime.now().isoformat()

        self.result = result


    def mark_not_implemented(self, result):

        self.status = PipelineStatus.NOT_IMPLEMENTED

        self.completed_at = datetime.now().isoformat()

        self.result = result


    def skip(self, result=None):

        self.status = PipelineStatus.SKIPPED

        self.completed_at = datetime.now().isoformat()

        self.result = result


    def cancel(self, result=None):

        self.status = PipelineStatus.CANCELLED

        self.completed_at = datetime.now().isoformat()

        self.result = result


    def timeout(self, result=None):

        self.status = PipelineStatus.TIMED_OUT

        self.completed_at = datetime.now().isoformat()

        self.result = result

        self.last_error_type = "TimeoutError"


    def can_retry(self, error_type):

        return (
            error_type in {"TimeoutError", "ConnectionError", "OSError"}
            and self.retry_count < self.max_retries
        )


    def schedule_retry(self, error_type):

        self.retry_count += 1

        self.last_error_type = error_type

        self.status = PipelineStatus.QUEUED

        self.queued_at = datetime.now().isoformat()

        self.started_at = None

        self.completed_at = None

        self.result = None


    def set_error_type(self, error_type):

        self.last_error_type = error_type


    def is_terminal(self):

        return self.status in {
            PipelineStatus.SUCCESS,
            PipelineStatus.FAILED,
            PipelineStatus.NOT_IMPLEMENTED,
            PipelineStatus.SKIPPED,
            PipelineStatus.CANCELLED,
            PipelineStatus.TIMED_OUT,
        }


    def to_dict(self):

        return {

            "id": self.id,

            "task": self.task_text,

            "parameters": dict(self.parameters),
            "workspace_id": self.workspace_id,

            "parent_task_id": self.parent_task_id,

            "retry_count": self.retry_count,

            "max_retries": self.max_retries,

            "timeout_seconds": self.timeout_seconds,

            "last_error_type": self.last_error_type,

            "status": self.status,

            "created_at": self.created_at,

            "queued_at": self.queued_at,

            "started_at": self.started_at,

            "completed_at": self.completed_at,

            "result": self.result,
            "task_type": self.task_type,
            "pipeline": self.pipeline,

        }
