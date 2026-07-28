from dataclasses import dataclass

from core.status import PipelineStatus
from core.worker_context import WorkerContext
from core.worker_result import WorkerResult


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    error: str | None = None

    def to_dict(self):
        return {"valid": self.valid, "error": self.error}


class WorkerResultValidator:
    def validate(self, context, result):
        if not isinstance(context, WorkerContext):
            return ValidationResult(False, "invalid_worker_context")
        if not isinstance(result, WorkerResult):
            return ValidationResult(False, "invalid_worker_result")
        if result.mission_id != context.mission_id:
            return ValidationResult(False, "mission_mismatch")
        if result.workspace_id != context.workspace_id:
            return ValidationResult(False, "workspace_mismatch")
        if result.status == PipelineStatus.SUCCESS and result.error is not None:
            return ValidationResult(False, "success_contains_error")
        if result.status != PipelineStatus.SUCCESS and not result.error:
            return ValidationResult(False, "failure_missing_error")
        for artifact in result.artifacts:
            artifact_workspace = artifact.get("workspace_id")
            if artifact_workspace is not None and artifact_workspace != context.workspace_id:
                return ValidationResult(False, "artifact_workspace_mismatch")
        return ValidationResult(True)
