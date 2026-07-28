from dataclasses import dataclass
from datetime import datetime

from core.mission import Mission
from core.result import PipelineResult
from core.retry_recovery import RetryExecutor
from core.status import PipelineStatus
from core.task import Task


@dataclass(frozen=True)
class PersonalExecution:
    mission: dict
    collaboration: dict
    content: dict
    retry: dict

    def to_dict(self):
        return {
            "mission": dict(self.mission),
            "collaboration": dict(self.collaboration),
            "content": dict(self.content),
            "retry": dict(self.retry),
        }


class PersonalAICompany:
    """Offline composition service for immediate or in-memory scheduled runs."""

    def __init__(
        self,
        collaboration_orchestrator,
        content_orchestrator,
        scheduler,
        retry_executor=None,
    ):
        self.collaboration = collaboration_orchestrator
        self.content = content_orchestrator
        self.scheduler = scheduler
        self.retry = retry_executor or RetryExecutor()
        self._requests = {}

    def execute(self, request, workspace_id, requested_by="personal-user"):
        mission = Mission.create(
            "Personal content request", request, requested_by, workspace_id
        )
        collaboration = self.collaboration.run(mission).to_dict()
        if collaboration["status"] != "COMPLETED":
            failed = PipelineResult(
                PipelineStatus.FAILED,
                "Personal AICompany",
                "Personal request",
                "PERSONAL",
                data={"workspace_id": workspace_id, "retryable": False},
                error="CollaborationError: WorkerFailure",
            ).to_dict()
            failed["task"] = "Personal request"
            return PersonalExecution(
                collaboration["mission"], collaboration, failed, {
                    "retryable": False, "failure_category": "validation"
                }
            )
        task = Task(
            request, {"mission_id": mission.id}, workspace_id=workspace_id
        )
        task.task_type = "CONTENT"

        def operation(previous):
            return self.content.run(task, recovery=previous)

        result, retry = self.retry.execute(operation, recovery=True)
        return PersonalExecution(
            collaboration["mission"], collaboration, result, retry.to_dict()
        )

    def schedule(self, request, workspace_id, run_at, recurrence=None):
        request_id = f"personal-{len(self._requests) + 1}"
        self._requests[request_id] = (request, workspace_id)

        def callback(_schedule):
            stored_request, stored_workspace = self._requests[request_id]
            return self.execute(stored_request, stored_workspace).to_dict()

        self.scheduler.register_target(request_id, callback)
        return self.scheduler.schedule(
            workspace_id,
            request_id,
            run_at,
            recurrence=recurrence,
            metadata={"request_type": "personal-content"},
        )
