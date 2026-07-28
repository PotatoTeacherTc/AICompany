from dataclasses import asdict, dataclass, field
from datetime import datetime

from core.persistence import sanitize_for_read
from core.task_queue import JobStatus


class MonitorStatus:
    HEALTHY = "HEALTHY"
    EMPTY = "EMPTY"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    RETRY_WAITING = "RETRY_WAITING"
    DEGRADED = "DEGRADED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Snapshot:
    workspace_id: str
    entity_type: str
    entity_id: str
    status: str
    updated_at: str | None = None
    summary: dict = field(default_factory=dict)
    safe_error: str | None = None
    usage: dict | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self):
        return sanitize_for_read(asdict(self))


class WorkspaceMonitor:
    """Read-only observation facade over existing repositories and managers."""

    def __init__(
        self,
        state_repository,
        job_queue,
        scheduler,
        artifact_manager,
        execution_history,
        department_manager=None,
    ):
        self.state_repository = state_repository
        self.job_queue = job_queue
        self.scheduler = scheduler
        self.artifact_manager = artifact_manager
        self.execution_history = execution_history
        self.department_manager = department_manager

    def workspace_summary(self, workspace_id, history_limit=10):
        try:
            self._validate_workspace(workspace_id)
            if not isinstance(history_limit, int) or not 0 <= history_limit <= 100:
                raise ValueError("invalid history limit")
            missions = self.missions(workspace_id)
            schedules = self.schedules(workspace_id)
            jobs = self.jobs(workspace_id)
            batches = self.batches(workspace_id)
            artifacts = self.artifacts(workspace_id)
            history = self.history(workspace_id, limit=history_limit)
            departments = self.departments(workspace_id)
            retry_waiting = sum(
                1 for job in self.job_queue.list(workspace_id)
                if (job.retry_state or {}).get("retryable")
                and job.status in {JobStatus.PENDING, JobStatus.FAILED}
            )
            missing = sum(
                1 for artifact in artifacts
                if artifact["status"] == "MISSING"
            )
            partial = sum(
                1 for batch in batches
                if batch["status"] == MonitorStatus.PARTIAL_FAILURE
            )
            status = (
                MonitorStatus.DEGRADED
                if missing or partial
                else MonitorStatus.RETRY_WAITING
                if retry_waiting
                else MonitorStatus.HEALTHY
            )
            return {
                "ok": True,
                "workspace_id": workspace_id,
                "status": status,
                "counts": {
                    "missions": len(missions),
                    "schedules": len(schedules),
                    "jobs": jobs["counts"],
                    "batches": len(batches),
                    "artifacts": len(artifacts),
                    "missing_artifacts": missing,
                    "retry_waiting": retry_waiting,
                    "history": len(history),
                    "departments": len(departments),
                },
                "usage": self._aggregate_usage(history),
                "entities": {
                    "missions": missions,
                    "schedules": schedules,
                    "batches": batches,
                    "artifacts": artifacts,
                    "history": history,
                    "departments": departments,
                },
            }
        except Exception as error:
            return self._failure(error)

    def missions(self, workspace_id):
        self._validate_workspace(workspace_id)
        return [
            Snapshot(
                workspace_id,
                "MISSION",
                value.get("id", "unknown"),
                value.get("state", MonitorStatus.UNKNOWN),
                value.get("created_at"),
                metadata={
                    "locked": bool(value.get("locked_by")),
                },
            ).to_dict()
            for value in self._safe_list("mission", workspace_id)
            if isinstance(value, dict)
        ]

    def schedules(self, workspace_id):
        self._validate_workspace(workspace_id)
        values = []
        for item in self.scheduler.list(workspace_id):
            status = "ENABLED" if item.enabled else "DISABLED"
            values.append(Snapshot(
                workspace_id, "SCHEDULE", item.schedule_id, status,
                item.last_run_at or item.created_at,
                summary={"run_at": item.run_at, "recurrence": bool(item.recurrence)},
                metadata=item.metadata,
            ).to_dict())
        return values

    def jobs(self, workspace_id):
        self._validate_workspace(workspace_id)
        counts = {
            status: 0 for status in (
                JobStatus.PENDING, JobStatus.RUNNING,
                JobStatus.COMPLETED, JobStatus.FAILED,
            )
        }
        snapshots = []
        for job in self.job_queue.list(workspace_id):
            counts[job.status] = counts.get(job.status, 0) + 1
            retry = job.retry_state or {}
            status = (
                MonitorStatus.RETRY_WAITING
                if retry.get("retryable")
                and job.status in {JobStatus.PENDING, JobStatus.FAILED}
                else job.status
            )
            snapshots.append(Snapshot(
                workspace_id, "JOB", job.job_id, status, job.created_at,
                summary={
                    "mission_id": job.mission_id,
                    "current_attempt": retry.get("current_attempt"),
                    "retryable": retry.get("retryable"),
                    "next_retry_at": retry.get("next_retry_at"),
                },
                safe_error=self._safe_error((job.result or {}).get("error")),
            ).to_dict())
        return {"counts": counts, "items": snapshots}

    def batches(self, workspace_id):
        self._validate_workspace(workspace_id)
        snapshots = []
        for value in self._safe_list("batch", workspace_id):
            job_ids = value.get("job_ids") if isinstance(value, dict) else None
            if not isinstance(job_ids, list):
                continue
            jobs = [self.job_queue.get(job_id, workspace_id) for job_id in job_ids]
            jobs = [job for job in jobs if job is not None]
            counts = {}
            for job in jobs:
                counts[job.status] = counts.get(job.status, 0) + 1
            if counts.get(JobStatus.FAILED) and counts.get(JobStatus.COMPLETED):
                status = MonitorStatus.PARTIAL_FAILURE
            elif counts.get(JobStatus.FAILED):
                status = JobStatus.FAILED
            elif counts.get(JobStatus.RUNNING):
                status = JobStatus.RUNNING
            elif counts.get(JobStatus.PENDING):
                status = JobStatus.PENDING
            elif jobs:
                status = JobStatus.COMPLETED
            else:
                status = MonitorStatus.UNKNOWN
            completed = counts.get(JobStatus.COMPLETED, 0)
            snapshots.append(Snapshot(
                workspace_id, "BATCH", value.get("batch_id", "unknown"), status,
                summary={
                    "total": len(job_ids),
                    "completed": completed,
                    "progress": completed / len(job_ids) if job_ids else 0,
                    "counts": counts,
                },
            ).to_dict())
        return snapshots

    def artifacts(self, workspace_id):
        self._validate_workspace(workspace_id)
        values = []
        for artifact in self.artifact_manager.list(workspace_id):
            if not isinstance(artifact, dict):
                continue
            values.append(Snapshot(
                workspace_id, "ARTIFACT",
                artifact.get("artifact_id", "unknown"),
                artifact.get("status", "AVAILABLE"),
                artifact.get("created_at"),
                summary={
                    "mission_id": artifact.get("mission_id"),
                    "stage": artifact.get("stage"),
                    "artifact_type": artifact.get("artifact_type"),
                    "filename": artifact.get("filename"),
                    "internal_ref": artifact.get("internal_ref"),
                },
            ).to_dict())
        return values

    def departments(self, workspace_id):
        self._validate_workspace(workspace_id)
        if self.department_manager is None:
            return []
        values = []
        for department in self.department_manager.list(workspace_id):
            values.append(Snapshot(
                workspace_id,
                "DEPARTMENT",
                department.department_id,
                "ENABLED" if department.enabled else "DISABLED",
                department.updated_at,
                summary={
                    "department_type": department.department_type,
                    "worker_count": len(department.worker_ids),
                    "supported_task_types": list(
                        department.supported_task_types
                    ),
                    "revision": department.revision,
                },
            ).to_dict())
        return values

    def history(self, workspace_id, limit=10, start_at=None, end_at=None):
        try:
            self._validate_workspace(workspace_id)
            self._validate_range(start_at, end_at)
            records = self.execution_history.query(
                workspace_id=workspace_id, start_at=start_at, end_at=end_at, limit=limit
            )
            values = []
            for record in records:
                result = record.get("result") if isinstance(record.get("result"), dict) else {}
                usage = result.get("usage")
                if usage is None:
                    usage = (result.get("data") or {}).get("provider_usage") if isinstance(result.get("data"), dict) else None
                values.append(Snapshot(
                    workspace_id, "PIPELINE",
                    record.get("task_id", "unknown"),
                    record.get("status", MonitorStatus.UNKNOWN),
                    record.get("completed_at") or record.get("started_at"),
                    summary={
                        "mission_id": record.get("mission_id"),
                        "pipeline": record.get("pipeline"),
                        "task_type": record.get("task_type"),
                    },
                    safe_error=self._safe_error(result.get("error")),
                    usage=self._usage(usage),
                ).to_dict())
            return values
        except Exception as error:
            return self._failure(error)

    def entity(self, entity_type, entity_id, workspace_id):
        try:
            self._validate_workspace(workspace_id)
            if entity_type == "mission":
                value = self.state_repository.get("mission", entity_id, workspace_id)
            elif entity_type == "job":
                job = self.job_queue.get(entity_id, workspace_id)
                value = job.to_dict() if job else None
            elif entity_type == "artifact":
                value = self.artifact_manager.get(entity_id, workspace_id)
            else:
                raise ValueError("unsupported entity type")
            if value is None:
                return {"ok": False, "error": "MonitorError: EntityNotFound"}
            return {"ok": True, "entity": sanitize_for_read(value)}
        except Exception as error:
            return self._failure(error)

    def _safe_list(self, kind, workspace_id):
        values = self.state_repository.list(kind, workspace_id)
        return values if isinstance(values, list) else []

    @staticmethod
    def _aggregate_usage(history):
        usage_values = [item.get("usage") for item in history if item.get("usage")]
        if not usage_values:
            return None
        result = {}
        for field in ("input_tokens", "output_tokens", "total_tokens", "estimated_cost_usd"):
            present = [usage[field] for usage in usage_values if isinstance(usage.get(field), (int, float))]
            if present:
                result[field] = sum(present)
        for field in ("provider", "model"):
            present = sorted({usage[field] for usage in usage_values if usage.get(field)})
            if present:
                result[field] = present
        return result or None

    @staticmethod
    def _usage(usage):
        if not isinstance(usage, dict):
            return None
        allowed = {
            key: usage[key] for key in (
                "provider", "model", "input_tokens", "output_tokens",
                "total_tokens", "estimated_cost_usd",
            ) if key in usage and usage[key] is not None
        }
        return allowed or None

    @staticmethod
    def _safe_error(error):
        if not isinstance(error, str) or ":" not in error:
            return None if error is None else "MonitorError: ReportedFailure"
        prefix, category = (part.strip() for part in error.split(":", 1))
        if not prefix.endswith("Error") or not category.replace("_", "").isalnum():
            return "MonitorError: ReportedFailure"
        return f"{prefix}: {category}"

    @staticmethod
    def _validate_workspace(workspace_id):
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("invalid workspace")

    @staticmethod
    def _validate_range(start_at, end_at):
        parsed = []
        for value in (start_at, end_at):
            if value is None:
                parsed.append(None)
                continue
            timestamp = datetime.fromisoformat(value)
            if timestamp.tzinfo is None:
                raise ValueError("time range must be timezone-aware")
            parsed.append(timestamp)
        if all(parsed) and parsed[0] > parsed[1]:
            raise ValueError("invalid time range")

    @staticmethod
    def _failure(error):
        return {
            "ok": False,
            "error": f"MonitorError: {type(error).__name__}",
        }
