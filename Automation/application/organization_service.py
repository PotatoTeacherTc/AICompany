class OrganizationService:
    """Safe DTO boundary over the existing Department and Worker contracts."""

    def __init__(self, department_manager, worker_directory, engine=None):
        self.departments = department_manager
        self.workers = worker_directory
        self.engine = engine

    def organization(self, workspace_id):
        return self.engine.snapshot(workspace_id) if self.engine else {
            "workspace_id": workspace_id, "company": None,
            "departments": [], "employees": [], "reporting_lines": [],
        }

    def list_employees(self, workspace_id):
        return {"items": [value.to_dict() for value in self.engine.list_employees(workspace_id)]} if self.engine else {"items": []}

    def list_assignments(self, workspace_id):
        return {"items": [value.to_dict() for value in self.engine.list_assignments(workspace_id)]} if self.engine else {"items": []}

    def get_assignment(self, workspace_id, assignment_id):
        value = self.engine.get_assignment(workspace_id, assignment_id) if self.engine else None
        return value.to_dict() if value else None

    def runtime(self, workspace_id, assignment_id):
        return self.engine.runtime(workspace_id, assignment_id) if self.engine else None

    def list_departments(self, workspace_id):
        return {"items": [
            value.to_dict() for value in self.departments.list(workspace_id)
        ]}

    def get_department(self, workspace_id, department_id):
        value = self.departments.get(department_id, workspace_id)
        return value.to_dict() if value else None

    def create_department(self, workspace_id, payload):
        if not isinstance(payload, dict):
            raise ValueError("invalid payload")
        return self.departments.create(
            workspace_id,
            payload.get("name"),
            payload.get("safe_summary"),
            payload.get("department_type"),
            worker_ids=payload.get("worker_ids", ()),
            lead_worker_id=payload.get("lead_worker_id"),
            supported_task_types=payload.get("supported_task_types", ()),
            department_id=payload.get("department_id"),
            enabled=payload.get("enabled", True),
        ).to_dict()

    def update_department(self, workspace_id, department_id, payload):
        if not isinstance(payload, dict):
            raise ValueError("invalid payload")
        changes = {
            key: value for key, value in payload.items()
            if key != "expected_revision"
        }
        return self.departments.update(
            department_id, workspace_id, changes,
            payload.get("expected_revision"),
        ).to_dict()

    def assign_worker(self, workspace_id, department_id, payload):
        if not isinstance(payload, dict):
            raise ValueError("invalid payload")
        return self.departments.assign_worker(
            department_id, workspace_id, payload.get("worker_id"),
            payload.get("expected_revision"), lead=payload.get("lead", False),
        ).to_dict()

    def remove_worker(
        self, workspace_id, department_id, worker_id, expected_revision,
    ):
        return self.departments.remove_worker(
            department_id, workspace_id, worker_id, expected_revision
        ).to_dict()

    def list_workers(self, workspace_id):
        return {"items": [
            self._worker(value) for value in self.workers.list(workspace_id)
        ]}

    def get_worker(self, workspace_id, worker_id):
        value = self.workers.get(worker_id, workspace_id)
        return self._worker(value) if value else None

    @staticmethod
    def _worker(value):
        return {
            "worker_id": value.worker_id,
            "workspace_id": value.workspace_id,
            "supported_task_types": list(value.supported_task_types),
        }
