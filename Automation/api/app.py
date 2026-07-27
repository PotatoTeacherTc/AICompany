from fastapi import FastAPI, Header

from api.errors import HANDLED_EXCEPTIONS
from api.task_api import TaskApi


def create_app(
    automation_service=None,
    task_query_service=None,
    workspace_service=None,
    user_service=None,
    membership_service=None,
    credential_service=None,
    login_service=None,
    auth_required=False,
):
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
    if workspace_service is None:
        from application.workspace_service import WorkspaceService
        workspace_service = WorkspaceService()
    if user_service is None:
        from application.user_service import UserService

        user_service = UserService()
    if membership_service is None:
        from application.workspace_membership_service import WorkspaceMembershipService

        membership_service = WorkspaceMembershipService(workspace_service, user_service)
    if credential_service is None:
        from application.credential_service import CredentialService

        credential_service = CredentialService(user_service)
    if login_service is None:
        from application.login_service import LoginService

        login_service = LoginService(user_service, credential_service)
    app.state.workspace_service = workspace_service
    app.state.user_service = user_service
    app.state.membership_service = membership_service
    app.state.login_service = login_service
    app.state.auth_required = auth_required
    app.state.task_api = TaskApi(automation_service, task_query_service, workspace_service)
    for exception_type, handler in HANDLED_EXCEPTIONS.items():
        app.add_exception_handler(exception_type, handler)

    @app.get("/health")
    def health_check():
        return {"status": "ok"}

    @app.post("/tasks", status_code=201)
    def create_task(payload: dict, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, payload.get("workspace_id") or "default", authorization, {"OWNER", "ADMIN", "MEMBER"})
        if denied: return denied
        try:
            response = app.state.task_api.create_task(payload)
            if response.get("workspace") == "not_found":
                from api.errors import error_response
                return error_response(404, "workspace_not_found", "Workspace not found")
            return response
        except (TypeError, ValueError):
            from api.errors import error_response

            return error_response(400, "invalid_request", "Invalid task request")

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str, workspace_id: str | None = None, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, workspace_id or "default", authorization, {"OWNER", "ADMIN", "MEMBER"})
        if denied: return denied
        response = app.state.task_api.get_task(task_id, workspace_id=workspace_id)
        if not response["found"]:
            from api.errors import error_response

            return error_response(404, "task_not_found", "Task not found")
        return response

    @app.post("/workspaces", status_code=201)
    def create_workspace(payload: dict, authorization: str | None = Header(default=None)):
        try:
            owner_user_id = payload.get("owner_user_id")
            if app.state.auth_required:
                user = _current_user(app, authorization)
                if user is None: return _unauthorized()
                owner_user_id = user["user_id"]
            if owner_user_id:
                return app.state.membership_service.create_workspace(payload.get("name"), owner_user_id)
            return app.state.workspace_service.create(payload.get("name"))
        except KeyError:
            from api.errors import error_response

            return error_response(404, "user_not_found", "User not found")
        except (TypeError, ValueError):
            from api.errors import error_response
            return error_response(400, "invalid_request", "Invalid workspace request")

    @app.get("/workspaces")
    def list_workspaces(): return {"items": app.state.workspace_service.list()}

    @app.get("/workspaces/{workspace_id}")
    def get_workspace(workspace_id: str, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"})
        if denied: return denied
        workspace = app.state.workspace_service.get(workspace_id)
        if workspace is None:
            from api.errors import error_response
            return error_response(404, "workspace_not_found", "Workspace not found")
        return workspace

    @app.post("/users", status_code=201)
    def create_user(payload: dict):
        try:
            return app.state.user_service.create(payload.get("email"))
        except ValueError as error:
            from api.errors import error_response

            if str(error) == "duplicate_email":
                return error_response(409, "duplicate_email", "Email already exists")
            return error_response(400, "invalid_request", "Invalid user request")

    @app.get("/users/{user_id}")
    def get_user(user_id: str, authorization: str | None = Header(default=None)):
        if user_id == "me":
            user = _current_user(app, authorization)
            return user if user else _unauthorized()
        user = app.state.user_service.get(user_id)
        if user is None:
            from api.errors import error_response

            return error_response(404, "user_not_found", "User not found")
        return user

    @app.post("/auth/login")
    def login(payload: dict):
        try:
            return app.state.login_service.login(payload.get("email"), payload.get("password"))
        except ValueError:
            from api.errors import error_response
            return error_response(401, "invalid_credentials", "Invalid credentials")

    @app.post("/workspaces/{workspace_id}/members", status_code=201)
    def add_member(workspace_id: str, payload: dict, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN"})
        if denied: return denied
        try:
            return app.state.membership_service.add(workspace_id, payload.get("user_id"), payload.get("role", "MEMBER"))
        except KeyError as error:
            return _membership_not_found(error)
        except ValueError as error:
            return _membership_conflict(error)

    @app.get("/workspaces/{workspace_id}/members")
    def list_members(workspace_id: str, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"})
        if denied: return denied
        try:
            return {"items": app.state.membership_service.list(workspace_id)}
        except KeyError as error:
            return _membership_not_found(error)

    @app.patch("/workspaces/{workspace_id}/members/{user_id}")
    def change_member_role(workspace_id: str, user_id: str, payload: dict, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN"})
        if denied: return denied
        try:
            return app.state.membership_service.change_role(workspace_id, user_id, payload.get("role"))
        except KeyError as error:
            return _membership_not_found(error)
        except ValueError as error:
            return _membership_conflict(error)

    @app.delete("/workspaces/{workspace_id}/members/{user_id}", status_code=204)
    def remove_member(workspace_id: str, user_id: str, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN"})
        if denied: return denied
        try:
            app.state.membership_service.remove(workspace_id, user_id)
        except KeyError as error:
            return _membership_not_found(error)
        except ValueError as error:
            return _membership_conflict(error)

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

    @app.post("/tasks/{task_id}/cancel")
    def cancel_task(task_id: str):
        return _control_endpoint(app.state.task_api.cancel_task(task_id))

    @app.post("/tasks/{task_id}/retry")
    def retry_task(task_id: str):
        return _control_endpoint(app.state.task_api.retry_task(task_id))

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


def _control_endpoint(response):
    if response.get("control") == "not_found":
        from api.errors import error_response

        return error_response(404, "task_not_found", "Task not found")
    if response.get("control") == "conflict":
        from api.errors import error_response

        return error_response(409, "task_state_conflict", "Task cannot be controlled in its current state")
    return response


def _membership_not_found(error):
    from api.errors import error_response

    code = str(error).strip("'")
    if code == "user_not_found":
        return error_response(404, code, "User not found")
    return error_response(404, "workspace_not_found" if code == "workspace_not_found" else "membership_not_found", "Resource not found")


def _membership_conflict(error):
    from api.errors import error_response

    code = str(error)
    if code == "last_owner":
        return error_response(409, code, "The last owner cannot be changed")
    if code == "duplicate_membership":
        return error_response(409, code, "Membership already exists")
    return error_response(400, "invalid_request", "Invalid membership request")


def _current_user(app, authorization):
    if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
        return None
    return app.state.login_service.current_user(authorization[7:])


def _unauthorized():
    from api.errors import error_response
    return error_response(401, "authentication_required", "Authentication required")


def _authorize_workspace(app, workspace_id, authorization, roles):
    if not app.state.auth_required:
        return None
    user = _current_user(app, authorization)
    if user is None:
        return _unauthorized()
    if app.state.workspace_service.get(workspace_id) is None:
        from api.errors import error_response
        return error_response(404, "workspace_not_found", "Workspace not found")
    membership = app.state.membership_service.repository.get(workspace_id, user["user_id"])
    if membership is None or membership["role"] not in roles:
        from api.errors import error_response
        return error_response(403, "permission_denied", "Permission denied")
    return None
