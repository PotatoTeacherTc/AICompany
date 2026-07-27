from agent.classifier import TaskClassifier
from core.result import PipelineResult
from core.status import PipelineStatus


class Manager:
    """Classifies Tasks and delegates them to registered pipelines."""

    def __init__(self, registry, classifier=None):
        self.registry = registry
        self.classifier = classifier or TaskClassifier()

    def handle(self, task):
        if not hasattr(task, "task_text"):
            raise TypeError("Manager.handle expects a Task object")

        print("Manager: Analyzing task...")
        task_type = self.classifier.classify(task)
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
            if not isinstance(result, dict):
                raise TypeError("Pipeline must return a PipelineResult dictionary")
            result.setdefault("task_type", task_type)
            result.setdefault("pipeline", pipeline.name)
            result.setdefault("task", task.task_text)
            result.setdefault("task_id", task.id)
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
