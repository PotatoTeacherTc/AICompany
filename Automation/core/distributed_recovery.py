"""Bounded Redis retry scheduling and Workspace-scoped dead letters."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from core.retry_recovery import RetryPolicy
from core.task_queue import JobStatus


class DistributedRecovery:
    def __init__(self, queue, redis_client, namespace="aicompany", policy=None, clock=None):
        self.queue = queue
        self.redis = redis_client
        self.namespace = namespace
        self.policy = policy or RetryPolicy()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def after_failure(self, job):
        state = job.retry_state or {}
        current = int(state.get("current_attempt", 0)) + 1
        retryable = bool(state.get("retryable")) and current < self.policy.max_attempts
        safe = {
            "max_attempts": self.policy.max_attempts,
            "current_attempt": current,
            "retryable": retryable,
            "next_retry_at": None,
            "failure_category": state.get("failure_category") or "unknown",
            "last_safe_error": state.get("last_safe_error") or job.result.get("error"),
        }
        try:
            if retryable:
                delay = self.policy.backoff_seconds * (2 ** (current - 1))
                next_at = self.clock() + timedelta(seconds=delay)
                safe["next_retry_at"] = next_at.isoformat()
                pending = replace(job, status=JobStatus.PENDING, claimed_by=None, retry_state=safe)
                self.queue._jobs[job.job_id] = pending
                self.queue._save(pending)
                self.redis.zadd(self._delayed(job.workspace_id), {job.job_id: next_at.timestamp()})
                return pending
            final = replace(job, retry_state=safe)
            self.queue._jobs[job.job_id] = final
            self.queue._save(final)
            self.redis.rpush(self._dlq(job.workspace_id), job.job_id)
            return final
        except Exception:
            raise RuntimeError("distributed_recovery_unavailable") from None

    def promote_due(self, workspace_id):
        try:
            now = self.clock().timestamp()
            values = self.redis.zrangebyscore(self._delayed(workspace_id), "-inf", now)
            promoted = 0
            for value in values:
                job_id = value.decode("utf-8") if isinstance(value, bytes) else str(value)
                if self.redis.zrem(self._delayed(workspace_id), job_id):
                    self.redis.rpush(self.queue._pending(workspace_id), job_id)
                    promoted += 1
            return promoted
        except Exception:
            raise RuntimeError("distributed_recovery_unavailable") from None

    def list_dlq(self, workspace_id):
        try:
            values = self.redis.lrange(self._dlq(workspace_id), 0, -1)
            jobs = []
            for value in values:
                job_id = value.decode("utf-8") if isinstance(value, bytes) else str(value)
                job = self.queue.get(job_id, workspace_id)
                if job is not None:
                    jobs.append(job)
            return jobs
        except Exception:
            raise RuntimeError("distributed_recovery_unavailable") from None

    def _delayed(self, workspace_id): return f"{self.namespace}:queue:{workspace_id}:delayed"
    def _dlq(self, workspace_id): return f"{self.namespace}:queue:{workspace_id}:dlq"
