from core.task_queue import JobStatus


class DashboardService:
    """Read-only Workspace dashboard composition over existing services."""

    def __init__(
        self, workspace_service, job_service, artifact_service,
        usage_service, quota_service, plan_service, organization_service,
    ):
        self.workspaces = workspace_service
        self.jobs = job_service
        self.artifacts = artifact_service
        self.usage = usage_service
        self.quota = quota_service
        self.plans = plan_service
        self.organization = organization_service

    def overview(self, workspace_id, recent_limit=5):
        if not isinstance(recent_limit, int) or not 1 <= recent_limit <= 20:
            raise ValueError("invalid recent limit")
        workspace = self.workspaces.get(workspace_id)
        if workspace is None:
            return None
        jobs = self.jobs.list_jobs(workspace_id, limit=100, offset=0)["items"]
        executions = self.jobs.list_executions(
            workspace_id, limit=100, offset=0
        )["items"]
        artifacts = self.artifacts.list(
            workspace_id, limit=100, offset=0
        )["items"]
        departments = self.organization.list_departments(workspace_id)["items"]
        workers = self.organization.list_workers(workspace_id)["items"]
        plan = self.plans.current(workspace_id)
        return {
            "workspace": workspace,
            "plan": {
                "plan_id": plan.get("plan_id"),
                "name": plan.get("name"),
                "entitlements": plan.get("entitlements", {}),
            },
            "quota": self.quota.get(workspace_id),
            "usage": self.usage.summary(workspace_id),
            "jobs": {
                "counts": _counts(jobs, (
                    JobStatus.PENDING, JobStatus.RUNNING, JobStatus.COMPLETED,
                    JobStatus.FAILED, JobStatus.CANCELLED,
                )),
                "recent": jobs[:recent_limit],
            },
            "executions": {
                "counts": _counts(executions, (
                    "SUCCESS", "FAILED", "TIMED_OUT", "CANCELLED",
                )),
                "recent": executions[:recent_limit],
                "recent_failed": [
                    value for value in executions if value.get("status") == "FAILED"
                ][:recent_limit],
            },
            "artifacts": {
                "counts": _counts(artifacts, (
                    "AVAILABLE", "ARCHIVED", "MISSING",
                )),
                "recent": artifacts[:recent_limit],
            },
            "organization": {
                "department_count": len(departments),
                "enabled_department_count": sum(
                    1 for value in departments if value.get("enabled")
                ),
                "disabled_department_count": sum(
                    1 for value in departments if not value.get("enabled")
                ),
                "worker_capability_count": len(workers),
                "assigned_worker_count": sum(
                    len(value.get("worker_ids", ())) for value in departments
                ),
            },
        }


def _counts(values, statuses):
    result = {"total": len(values)}
    for status in statuses:
        result[status.lower()] = sum(
            1 for value in values if value.get("status") == status
        )
    return result
