from fastapi import FastAPI

from api.errors import HANDLED_EXCEPTIONS
from api.task_api import TaskApi


def create_app(automation_service=None, task_query_service=None):
    """Create the HTTP application without starting a server."""
    if automation_service is None:
        automation_service, task_query_service = _build_default_services()
    elif task_query_service is None:
        from application.task_query_service import TaskQueryService

        task_query_service = TaskQueryService(
            automation_service.history,
            automation_service.artifact_manager,
            automation_service._get_task_for_query,
        )

    app = FastAPI(title="AICompany API", version="0.1.0")
    app.state.automation_service = automation_service
    app.state.task_query_service = task_query_service
    app.state.task_api = TaskApi(automation_service, task_query_service)
    for exception_type, handler in HANDLED_EXCEPTIONS.items():
        app.add_exception_handler(exception_type, handler)

    @app.get("/health")
    def health_check():
        return {"status": "ok"}

    @app.post("/tasks", status_code=201)
    def create_task(payload: dict):
        try:
            return app.state.task_api.create_task(payload)
        except (TypeError, ValueError):
            from api.errors import error_response

            return error_response(400, "invalid_request", "Invalid task request")

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str):
        response = app.state.task_api.get_task(task_id)
        if not response["found"]:
            from api.errors import error_response

            return error_response(404, "task_not_found", "Task not found")
        return response

    @app.get("/tasks")
    def list_tasks(
        status: str | None = None,
        pipeline: str | None = None,
        task_type: str | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ):
        try:
            return app.state.task_api.list_tasks(
                {
                    "status": status,
                    "pipeline": pipeline,
                    "task_type": task_type,
                    "start_at": start_at,
                    "end_at": end_at,
                    "limit": limit,
                    "offset": offset,
                }
            )
        except ValueError:
            from api.errors import error_response

            return error_response(400, "invalid_request", "Invalid task query")

    return app


def _build_default_services():
    from agent.manager import Manager
    from application.automation_service import AutomationService
    from application.task_query_service import TaskQueryService
    from core.artifact_manager import ArtifactManager
    from core.execution_history import ExecutionHistory
    from main import build_registry

    history = ExecutionHistory()
    artifact_manager = ArtifactManager()
    service = AutomationService(
        Manager(build_registry(history, artifact_manager=artifact_manager)),
        history=history,
        artifact_manager=artifact_manager,
    )
    return service, TaskQueryService(
        history,
        artifact_manager,
        service._get_task_for_query,
    )
