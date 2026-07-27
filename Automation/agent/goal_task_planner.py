from core.task import Task


class GoalTaskPlanner:
    """Build validated executable child Tasks from a structured goal plan."""

    def __init__(self, registry):
        self.registry = registry

    def create_subtasks(self, parent_task, task_specs):
        if not isinstance(parent_task, Task):
            raise TypeError("parent_task must be a Task")

        if not isinstance(task_specs, (list, tuple)):
            raise TypeError("task_specs must be a list or tuple")

        subtasks = []
        for task_spec in task_specs:
            subtasks.append(self._create_subtask(parent_task, task_spec))
        return subtasks

    def _create_subtask(self, parent_task, task_spec):
        if not isinstance(task_spec, dict):
            raise TypeError("each task specification must be a dictionary")

        task_text = task_spec.get("task_text")
        task_type = task_spec.get("task_type")
        parameters = task_spec.get("parameters", {})

        if not isinstance(task_text, str) or not task_text.strip():
            raise ValueError("task_text must be a non-empty string")

        if not isinstance(task_type, str) or not task_type.strip():
            raise ValueError("task_type must be a non-empty string")

        if self.registry.get(task_type) is None:
            raise ValueError(f"task_type is not registered: {task_type}")

        subtask = Task(
            task_text,
            parameters=parameters,
            parent_task_id=parent_task.id,
        )
        subtask.task_type = task_type
        return subtask
