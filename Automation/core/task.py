from datetime import datetime
import uuid


class Task:

    def __init__(self, task_text: str, parameters=None):

        self.id = str(uuid.uuid4())[:8]

        self.task_text = task_text

        if parameters is not None and not isinstance(parameters, dict):
            raise TypeError("parameters must be a dictionary or None")

        self.parameters = dict(parameters or {})

        self.status = "PENDING"

        self.created_at = datetime.now().isoformat()

        self.started_at = None

        self.completed_at = None

        self.result = None
        self.task_type = None
        self.pipeline = None


    def start(self):

        self.status = "RUNNING"

        self.started_at = datetime.now().isoformat()


    def complete(self, result):

        self.status = "SUCCESS"

        self.completed_at = datetime.now().isoformat()

        self.result = result


    def fail(self, result):

        self.status = "FAILED"

        self.completed_at = datetime.now().isoformat()

        self.result = result


    def mark_not_implemented(self, result):

        self.status = "NOT_IMPLEMENTED"

        self.completed_at = datetime.now().isoformat()

        self.result = result


    def to_dict(self):

        return {

            "id": self.id,

            "task": self.task_text,

            "parameters": dict(self.parameters),

            "status": self.status,

            "created_at": self.created_at,

            "started_at": self.started_at,

            "completed_at": self.completed_at,

            "result": self.result,
            "task_type": self.task_type,
            "pipeline": self.pipeline,

        }
