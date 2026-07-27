from agent.classifier import TaskClassifier
from core.result import PipelineResult
from core.status import PipelineStatus


class Manager:
    """Classifies Tasks and delegates them to registered pipelines."""

    REQUIRED_RESULT_KEYS = {
        "status",
        "pipeline",
        "task",
        "task_id",
        "task_type",
        "data",
        "error",
    }

    ALLOWED_RESULT_STATUSES = {
        PipelineStatus.SUCCESS,
        PipelineStatus.FAILED,
        PipelineStatus.NOT_IMPLEMENTED,
        PipelineStatus.PENDING,
        PipelineStatus.RUNNING,
        PipelineStatus.CANCELLED,
        PipelineStatus.TIMED_OUT,
    }

    def __init__(self, registry, classifier=None):
        self.registry = registry
        self.classifier = classifier or TaskClassifier()

    def handle(self, task):
        if not hasattr(task, "task_text"):
            raise TypeError("Manager.handle expects a Task object")

        print("Manager: Analyzing task...")
        task_type = task.task_type or self.classifier.classify(task)
        task.task_type = task_type
        print(f"Manager: Task type = {task_type}")

        pipeline = self.registry.get(task_type)
        if pipeline is None:
            return PipelineResult(
                status=PipelineStatus.FAILED,
                pipeline=None,
                task=task,
                task_type=task_type,
                error=f"No pipeline registered for task type: {task_type}",
            ).to_dict()

        task.pipeline = pipeline.name
        print(f"Manager: Routing task to {pipeline.name}...")
        try:
            result = pipeline.run(task)
            self._validate_pipeline_result(result, task, task_type, pipeline)
            print(f"Manager: Task completed with status: {result.get('status')}")
            return result
        except Exception as error:
            print("Manager: Pipeline execution failed")
            return PipelineResult(
                status=PipelineStatus.FAILED,
                pipeline=pipeline.name,
                task=task,
                task_type=task_type,
                error=str(error),
            ).to_dict()

    def _validate_pipeline_result(self, result, task, task_type, pipeline):
        if not isinstance(result, dict):
            raise TypeError("Pipeline must return a PipelineResult dictionary")

        missing_keys = self.REQUIRED_RESULT_KEYS - result.keys()
        if missing_keys:
            raise ValueError(
                "PipelineResult missing required keys: "
                + ", ".join(sorted(missing_keys))
            )

        if result["status"] not in self.ALLOWED_RESULT_STATUSES:
            raise ValueError(f"PipelineResult has invalid status: {result['status']}")

        expected_values = {
            "pipeline": pipeline.name,
            "task": task.task_text,
            "task_id": task.id,
            "task_type": task_type,
        }
        for key, expected_value in expected_values.items():
            if result[key] != expected_value:
                raise ValueError(f"PipelineResult {key} does not match the current execution")

        if not isinstance(result["data"], dict):
            raise ValueError("PipelineResult data must be a dictionary")

        if result["error"] is not None and not isinstance(result["error"], str):
            raise ValueError("PipelineResult error must be a string or None")
