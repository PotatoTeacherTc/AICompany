from datetime import datetime, timezone
import re
from threading import Thread
import uuid

from core.persistence import sanitize_for_read


PRODUCT_WORKFLOW_KIND = "product_workflow"
PRODUCT_STAGES = ("PLANNING", "MUSIC", "IMAGE", "BLOG", "VIDEO", "YOUTUBE", "NAVER")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")


class ProductWorkflowService:
    """Workspace-scoped product facade over an injected @1-@9 stage runner.

    User text is held only until the queued callback consumes it. It is never
    written to StateRepository, logs, history, or an external response.
    """

    def __init__(self, repository, execution_service, stage_runner, connection_probes=None, auto_run=False):
        self.repository = repository
        self.execution = execution_service
        self.stage_runner = stage_runner
        self.connection_probes = dict(connection_probes or {})
        self.auto_run = bool(auto_run)
        self._requests = {}
        self.execution.register_target("product-workflow", self._run_job)

    def submit(self, workspace_id, request_text, idempotency_key):
        workspace_id = _identifier(workspace_id, "workspace_id")
        idempotency_key = _identifier(idempotency_key, "idempotency_key")
        if not isinstance(request_text, str) or not request_text.strip() or len(request_text) > 4000:
            raise ValueError("invalid request")
        product_id = f"product-{uuid.uuid5(uuid.NAMESPACE_URL, workspace_id + ':' + idempotency_key).hex}"
        existing = self.repository.get(PRODUCT_WORKFLOW_KIND, product_id, workspace_id)
        if existing is not None:
            return self._safe(existing)
        now = _now()
        record = {
            "product_id": product_id, "workspace_id": workspace_id,
            "status": "PENDING", "progress": 0,
            "current_stage": "PLANNING", "created_at": now, "updated_at": now,
            "stages": {stage: {"status": "PENDING"} for stage in PRODUCT_STAGES},
            "artifacts": [], "results": {}, "safe_error": None,
            "request_redacted": True,
        }
        self.repository.save(PRODUCT_WORKFLOW_KIND, product_id, workspace_id, record)
        self._requests[(workspace_id, product_id)] = request_text.strip()
        job = self.execution.submit(
            workspace_id, product_id, "product-workflow", idempotency_key,
        )
        record["job_id"] = job.job_id
        record["updated_at"] = _now()
        self.repository.save(PRODUCT_WORKFLOW_KIND, product_id, workspace_id, record)
        if self.auto_run:
            Thread(target=self.run_once, args=(workspace_id,), daemon=True).start()
        return self._safe(record)

    def run_once(self, workspace_id):
        return self.execution.run_once(_identifier(workspace_id, "workspace_id"))

    def list(self, workspace_id):
        workspace_id = _identifier(workspace_id, "workspace_id")
        values = self.repository.list(PRODUCT_WORKFLOW_KIND, workspace_id)
        values.sort(key=lambda value: value.get("created_at", ""), reverse=True)
        return {"items": [self._safe(value) for value in values]}

    def get(self, workspace_id, product_id):
        value = self.repository.get(
            PRODUCT_WORKFLOW_KIND, _identifier(product_id, "product_id"),
            _identifier(workspace_id, "workspace_id"),
        )
        return self._safe(value) if value else None

    def upload_audio(self, workspace_id, product_id, filename, content):
        current = self.get(workspace_id, product_id)
        if current is None: return None
        if current.get("status") != "WAITING_FOR_INPUT" or current.get("current_stage") != "PLANNING":
            raise ValueError("audio checkpoint unavailable")
        project_id = (current.get("results", {}).get("planning") or {}).get("project_id")
        if not project_id or not hasattr(self.stage_runner, "upload_audio"):
            raise ValueError("audio checkpoint unavailable")
        result = self.stage_runner.upload_audio(workspace_id, project_id, filename, content)
        if result.get("status") != "INPUT_READY":
            raise ValueError("audio upload rejected")
        current["current_stage"] = "MUSIC"
        current["status"] = "PENDING"
        current["results"]["audio"] = {
            key: (result.get("data") or {}).get(key) for key in
            ("audio_artifact_id", "source_filename", "detected_format", "duration_seconds")
        }
        current["updated_at"] = _now()
        self.repository.save(PRODUCT_WORKFLOW_KIND, product_id, workspace_id, current)
        self._enqueue_resume(current, "audio")
        return self.get(workspace_id, product_id)

    def resume(self, workspace_id, product_id):
        current = self.get(workspace_id, product_id)
        if current is None: return None
        if current.get("status") not in {"CONNECTION_REQUIRED", "USER_ACTION_REQUIRED", "USER_CONFIRM_REQUIRED"}:
            raise ValueError("checkpoint cannot be resumed")
        current["status"] = "PENDING"; current["updated_at"] = _now()
        self.repository.save(PRODUCT_WORKFLOW_KIND, product_id, workspace_id, current)
        self._enqueue_resume(current, "checkpoint")
        return self.get(workspace_id, product_id)

    def retry(self, workspace_id, product_id, stage):
        current = self.get(workspace_id, product_id)
        if current is None:
            return None
        stage = str(stage or current.get("current_stage", "")).upper()
        if stage not in PRODUCT_STAGES or current["stages"].get(stage, {}).get("status") != "FAILED":
            raise ValueError("stage cannot be retried")
        current["status"] = "PENDING"
        current["current_stage"] = stage
        current["safe_error"] = None
        current["stages"][stage] = {"status": "PENDING"}
        current["updated_at"] = _now()
        self.repository.save(PRODUCT_WORKFLOW_KIND, product_id, workspace_id, current)
        self._enqueue_resume(current, "retry")
        return self._safe(current)

    def _enqueue_resume(self, current, reason):
        job = self.execution.submit(
            current["workspace_id"], current["product_id"], "product-workflow",
            f"{reason}-{current['product_id']}-{uuid.uuid4().hex}",
        )
        current["job_id"] = job.job_id
        self.repository.save(PRODUCT_WORKFLOW_KIND, current["product_id"], current["workspace_id"], current)
        if self.auto_run:
            Thread(target=self.run_once, args=(current["workspace_id"],), daemon=True).start()

    def connections(self, workspace_id):
        workspace_id = _identifier(workspace_id, "workspace_id")
        items = []
        for name in ("comfyui", "youtube", "naver"):
            probe = self.connection_probes.get(name)
            status = "NOT_CONFIGURED"
            if probe is not None:
                try:
                    value = probe(workspace_id)
                    status = "CONNECTED" if value is True else str(value).upper()
                except Exception:
                    status = "UNAVAILABLE"
            items.append({"component": name, "status": status})
        return {"workspace_id": workspace_id, "items": items}

    def _run_job(self, job):
        record = self.repository.get(PRODUCT_WORKFLOW_KIND, job.mission_id, job.workspace_id)
        if record is None:
            raise ValueError("product workflow missing")
        start = PRODUCT_STAGES.index(record.get("current_stage", "PLANNING"))
        request_key = (job.workspace_id, job.mission_id)
        request_text = self._requests.get(request_key)
        record["status"] = "RUNNING"
        for index, stage in enumerate(PRODUCT_STAGES[start:], start=start):
            record["current_stage"] = stage
            record["stages"][stage] = {"status": "RUNNING", "started_at": _now()}
            record["progress"] = int(index * 100 / len(PRODUCT_STAGES))
            record["updated_at"] = _now()
            self.repository.save(PRODUCT_WORKFLOW_KIND, job.mission_id, job.workspace_id, record)
            try:
                output = self.stage_runner(stage, job.workspace_id, job.mission_id, request_text, record)
            except Exception:
                output = {"status": "FAILED", "safe_error": "STAGE_EXECUTION_FAILED"}
            output = output if isinstance(output, dict) else {}
            status = str(output.get("status", "FAILED")).upper()
            if stage == "PLANNING" and status != "FAILED":
                self._requests.pop(request_key, None)
            safe_output = sanitize_for_read(output)
            safe_output.pop("request", None)
            record["stages"][stage] = {
                "status": status, "completed_at": _now(),
                **({"safe_error": safe_output.get("safe_error")} if safe_output.get("safe_error") else {}),
            }
            if safe_output.get("artifacts"):
                record["artifacts"].extend(safe_output["artifacts"])
            if safe_output.get("result") is not None:
                record["results"][stage.lower()] = safe_output["result"]
            record["updated_at"] = _now()
            if status in {"WAITING_FOR_INPUT", "CONNECTION_REQUIRED", "USER_ACTION_REQUIRED", "USER_CONFIRM_REQUIRED"}:
                record["status"] = status
                record["progress"] = int((index + 1) * 100 / len(PRODUCT_STAGES))
                self.repository.save(PRODUCT_WORKFLOW_KIND, job.mission_id, job.workspace_id, record)
                return _pipeline(job, record, "SUCCESS")
            if status not in {"COMPLETED", "SUCCESS", "PUBLISHED"}:
                record["status"] = "FAILED"
                record["safe_error"] = safe_output.get("safe_error") or "STAGE_EXECUTION_FAILED"
                self.repository.save(PRODUCT_WORKFLOW_KIND, job.mission_id, job.workspace_id, record)
                return _pipeline(job, record, "FAILED")
            request_text = None
        record["status"] = "COMPLETED"
        record["progress"] = 100
        record["updated_at"] = _now()
        self.repository.save(PRODUCT_WORKFLOW_KIND, job.mission_id, job.workspace_id, record)
        return _pipeline(job, record, "SUCCESS")

    @staticmethod
    def _safe(value):
        return sanitize_for_read(value)


def _pipeline(job, record, status):
    return {
        "status": status, "pipeline": "Product Workflow", "task_type": "PRODUCT",
        "data": {"product_id": job.mission_id, "product_status": record["status"]},
        "artifacts": [], "error": None if status == "SUCCESS" else "JobError: StageFailure",
    }


def _identifier(value, name):
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ValueError(f"{name} is invalid")
    return value


def _now():
    return datetime.now(timezone.utc).isoformat()
