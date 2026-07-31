from dataclasses import asdict, dataclass, field
import json


@dataclass(frozen=True)
class WorkflowRetryPolicy:
    max_attempts: int = 1
    backoff_seconds: float = 0

    def validate(self):
        if (
            not isinstance(self.max_attempts, int)
            or isinstance(self.max_attempts, bool)
            or not 1 <= self.max_attempts <= 10
            or not isinstance(self.backoff_seconds, (int, float))
            or isinstance(self.backoff_seconds, bool)
            or not 0 <= self.backoff_seconds <= 3600
        ):
            raise ValueError("invalid_retry_policy")


@dataclass(frozen=True)
class ConditionalBranch:
    field: str
    operator: str
    value: str | int | float | bool | None
    target_step_id: str

    def validate(self):
        if (
            not _identifier(self.field)
            or self.operator not in {"EQUALS", "NOT_EQUALS", "EXISTS"}
            or not _identifier(self.target_step_id)
        ):
            raise ValueError("invalid_conditional_branch")


@dataclass(frozen=True)
class StepDefinition:
    step_id: str
    capability: str
    depends_on: tuple[str, ...] = ()
    retry: WorkflowRetryPolicy = field(default_factory=WorkflowRetryPolicy)
    branches: tuple[ConditionalBranch, ...] = ()
    parallel_group: str | None = None

    def validate(self):
        if not _identifier(self.step_id) or not _identifier(self.capability):
            raise ValueError("invalid_step")
        if self.step_id in self.depends_on or any(
            not _identifier(value) for value in self.depends_on
        ):
            raise ValueError("invalid_step_dependency")
        if self.parallel_group is not None and not _identifier(self.parallel_group):
            raise ValueError("invalid_parallel_group")
        self.retry.validate()
        for branch in self.branches:
            branch.validate()


@dataclass(frozen=True)
class WorkflowDefinition:
    workflow_id: str
    name: str
    version: str
    steps: tuple[StepDefinition, ...]
    schema_version: int = 1

    def validate(self):
        if (
            self.schema_version != 1
            or not _identifier(self.workflow_id)
            or not isinstance(self.name, str)
            or not self.name.strip()
            or not _version(self.version)
            or not 1 <= len(self.steps) <= 100
        ):
            raise ValueError("invalid_workflow")
        identifiers = [step.step_id for step in self.steps]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("duplicate_step")
        known = set(identifiers)
        for step in self.steps:
            step.validate()
            if any(value not in known for value in step.depends_on):
                raise ValueError("unknown_step_dependency")
            if any(branch.target_step_id not in known for branch in step.branches):
                raise ValueError("unknown_branch_target")
        self._acyclic()
        return self

    def to_dict(self):
        self.validate()
        return asdict(self)

    def to_json(self):
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, payload):
        if not isinstance(payload, str) or len(payload) > 1_000_000:
            raise ValueError("invalid_workflow_json")
        try:
            value = json.loads(payload)
            steps = tuple(
                StepDefinition(
                    step_id=item["step_id"],
                    capability=item["capability"],
                    depends_on=tuple(item.get("depends_on") or ()),
                    retry=WorkflowRetryPolicy(**(item.get("retry") or {})),
                    branches=tuple(
                        ConditionalBranch(**branch)
                        for branch in item.get("branches") or ()
                    ),
                    parallel_group=item.get("parallel_group"),
                )
                for item in value["steps"]
            )
            return cls(
                workflow_id=value["workflow_id"],
                name=value["name"],
                version=value["version"],
                steps=steps,
                schema_version=value.get("schema_version", 1),
            ).validate()
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("invalid_workflow_json") from error

    def _acyclic(self):
        graph = {step.step_id: step.depends_on for step in self.steps}
        visiting = set()
        visited = set()

        def visit(step_id):
            if step_id in visiting:
                raise ValueError("cyclic_workflow")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in graph[step_id]:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in graph:
            visit(step_id)


def _identifier(value):
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and all(character.isalnum() or character in "_-." for character in value)
    )


def _version(value):
    if not isinstance(value, str):
        return False
    parts = value.split(".")
    return len(parts) == 3 and all(part.isdigit() for part in parts)
