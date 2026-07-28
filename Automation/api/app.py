from fastapi import FastAPI, Header, Request
from api.request_context import RequestContext, set_context, reset_context
import time

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
    session_service=None,
    audit_service=None,
    audit_query_service=None,
    authorization_service=None,
    artifact_service=None,
    usage_service=None,
    health_service=None,
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
    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next):
        context=RequestContext.create(request.headers.get("X-Correlation-ID")); token=set_context(context); started=time.perf_counter()
        try:
            response=await call_next(request)
        finally:
            reset_context(token)
        response.headers["X-Correlation-ID"]=context.correlation_id
        return response
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
        from application.session_service import SessionService

        session_service = session_service or SessionService()
        login_service = LoginService(user_service, credential_service, session_service=session_service)
    session_service = session_service or login_service.session_service
    app.state.workspace_service = workspace_service
    app.state.user_service = user_service
    app.state.membership_service = membership_service
    app.state.login_service = login_service
    app.state.session_service = session_service
    if authorization_service is None:
        from application.authorization_service import AuthorizationService

        authorization_service = AuthorizationService(
            login_service, workspace_service, membership_service
        )
    app.state.authorization_service = authorization_service
    if artifact_service is None:
        from application.artifact_service import ArtifactApplicationService

        artifact_service = ArtifactApplicationService(
            getattr(automation_service, "artifact_manager", None)
        )
    app.state.artifact_service = artifact_service
    if usage_service is None:
        usage_engine = getattr(automation_service, "usage_engine", None)
        if usage_engine is not None:
            from application.usage_reporting_service import UsageReportingService

            usage_service = UsageReportingService(usage_engine)
    app.state.usage_service = usage_service
    if audit_service is None:
        from application.audit_service import AuditService
        audit_service = AuditService()
    app.state.audit_service = audit_service
    if audit_query_service is None:
        from application.audit_query_service import AuditQueryService
        audit_query_service = AuditQueryService(audit_service)
    app.state.audit_query_service = audit_query_service
    app.state.auth_required = auth_required
    app.state.health_service = health_service
    app.state.task_api = TaskApi(automation_service, task_query_service, workspace_service)
    for exception_type, handler in HANDLED_EXCEPTIONS.items():
        app.add_exception_handler(exception_type, handler)

    @app.get("/health")
    def health_check():
        if app.state.health_service is None:
            return {"status": "ok"}
        return app.state.health_service.snapshot()

    @app.post("/tasks", status_code=201)
    def create_task(payload: dict, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, payload.get("workspace_id") or "default", authorization, {"OWNER", "ADMIN", "MEMBER"})
        if denied: return denied
        try:
            response = app.state.task_api.create_task(payload)
            app.state.audit_service.record(workspace_id=payload.get("workspace_id") or "default", action="TASK_CREATED", resource_type="task", resource_id=response.get("task_id", ""))
            if response.get("workspace") == "not_found":
                from api.errors import error_response
                return error_response(404, "workspace_not_found", "Workspace not found")
            if response.get("workspace") == "inactive":
                from api.errors import error_response
                return error_response(409, "workspace_inactive", "Workspace is inactive")
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
                result=app.state.membership_service.create_workspace(payload.get("name"), owner_user_id)
            else: result=app.state.workspace_service.create(payload.get("name"))
            app.state.audit_service.record(user_id=owner_user_id,workspace_id=result['workspace_id'],action="WORKSPACE_CREATED",resource_type="workspace",resource_id=result['workspace_id'])
            return result
        except KeyError:
            from api.errors import error_response

            return error_response(404, "user_not_found", "User not found")
        except (TypeError, ValueError):
            from api.errors import error_response
            return error_response(400, "invalid_request", "Invalid workspace request")

    @app.get("/workspaces")
    def list_workspaces(authorization: str | None = Header(default=None)):
        if not app.state.auth_required:
            return {"items": app.state.workspace_service.list()}
        user = _current_user(app, authorization)
        if user is None:
            return _unauthorized()
        workspace_ids = {
            item["workspace_id"]
            for item in app.state.membership_service.repository.list_by_user(
                user["user_id"]
            )
        }
        return {
            "items": [
                workspace
                for workspace in app.state.workspace_service.list()
                if workspace["workspace_id"] in workspace_ids
                and app.state.workspace_service.is_active(workspace["workspace_id"])
            ]
        }

    @app.get("/workspaces/{workspace_id}")
    def get_workspace(workspace_id: str, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"})
        if denied: return denied
        workspace = app.state.workspace_service.get(workspace_id)
        if workspace is None:
            from api.errors import error_response
            return error_response(404, "workspace_not_found", "Workspace not found")
        return workspace

    @app.patch("/workspaces/{workspace_id}")
    def update_workspace(
        workspace_id: str,
        payload: dict,
        authorization: str | None = Header(default=None),
    ):
        denied = _authorize_workspace(
            app, workspace_id, authorization, {"OWNER", "ADMIN"}
        )
        if denied:
            return denied
        try:
            return app.state.workspace_service.update(
                workspace_id,
                name=payload.get("name"),
                status=payload.get("status"),
                expected_revision=payload.get("expected_revision"),
            )
        except KeyError:
            from api.errors import error_response

            return error_response(404, "workspace_not_found", "Workspace not found")
        except ValueError as error:
            from api.errors import error_response

            if str(error) == "revision_conflict":
                return error_response(
                    409, "revision_conflict", "Workspace revision conflict"
                )
            return error_response(400, "invalid_request", "Invalid workspace request")

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
        if app.state.auth_required:
            current = _current_user(app, authorization)
            if current is None:
                return _unauthorized()
            if current["user_id"] != user_id:
                from api.errors import error_response

                return error_response(403, "permission_denied", "Permission denied")
        user = app.state.user_service.get(user_id)
        if user is None:
            from api.errors import error_response

            return error_response(404, "user_not_found", "User not found")
        return user

    @app.get("/auth/me")
    def auth_me(authorization: str | None = Header(default=None)):
        user = _current_user(app, authorization)
        return user if user else _unauthorized()

    @app.patch("/users/{user_id}/deactivate")
    def deactivate_user(
        user_id: str,
        authorization: str | None = Header(default=None),
    ):
        current = _current_user(app, authorization)
        if current is None:
            return _unauthorized()
        if current["user_id"] != user_id:
            from api.errors import error_response

            return error_response(403, "permission_denied", "Permission denied")
        try:
            result = app.state.user_service.deactivate(user_id)
            if app.state.session_service:
                app.state.session_service.revoke_all(user_id)
            app.state.audit_service.record(
                user_id=user_id,
                action="USER_DEACTIVATED",
                resource_type="user",
                resource_id=user_id,
            )
            return result
        except KeyError:
            from api.errors import error_response

            return error_response(404, "user_not_found", "User not found")

    @app.post("/auth/login")
    def login(payload: dict):
        try:
            result=app.state.login_service.login(payload.get("email"), payload.get("password")); user=app.state.login_service.current_user(result['access_token']); app.state.audit_service.record(user_id=user['user_id'],action="LOGIN_SUCCESS",resource_type="session",resource_id=result.get('session_id','')); return result
        except ValueError:
            app.state.audit_service.record(action="LOGIN_FAILED",resource_type="session")
            from api.errors import error_response
            return error_response(401, "invalid_credentials", "Invalid credentials")

    @app.post("/auth/refresh")
    def refresh(payload: dict):
        try:
            result=app.state.login_service.refresh(payload.get("refresh_token")); user=app.state.login_service.current_user(result['access_token']); app.state.audit_service.record(user_id=user['user_id'],action="TOKEN_REFRESHED",resource_type="session",resource_id=result['session_id']); return result
        except ValueError:
            from api.errors import error_response
            return error_response(401, "invalid_refresh_token", "Invalid refresh token")

    @app.post("/auth/logout", status_code=204)
    def logout(payload: dict, authorization: str | None = Header(default=None)):
        user = _current_user(app, authorization)
        if user is None:
            return _unauthorized()
        if not app.state.session_service or not app.state.session_service.revoke(payload.get("session_id"), user["user_id"]):
            from api.errors import error_response
            return error_response(404, "session_not_found", "Session not found")
        app.state.audit_service.record(user_id=user['user_id'],action="LOGOUT",resource_type="session",resource_id=payload.get("session_id",''))

    @app.post("/auth/logout-all")
    def logout_all(authorization: str | None = Header(default=None)):
        user = _current_user(app, authorization)
        if user is None:
            return _unauthorized()
        revoked = (
            app.state.session_service.revoke_all(user["user_id"])
            if app.state.session_service
            else 0
        )
        app.state.audit_service.record(
            user_id=user["user_id"],
            action="LOGOUT_ALL",
            resource_type="session",
        )
        return {"revoked_sessions": revoked}

    @app.get("/auth/sessions")
    def list_sessions(authorization: str | None = Header(default=None)):
        user = _current_user(app, authorization)
        if user is None:
            return _unauthorized()
        return {"items": app.state.session_service.list(user["user_id"]) if app.state.session_service else []}

    @app.delete("/auth/sessions/{session_id}", status_code=204)
    def delete_session(session_id: str, authorization: str | None = Header(default=None)):
        user = _current_user(app, authorization)
        if user is None:
            return _unauthorized()
        if not app.state.session_service or not app.state.session_service.revoke(session_id, user["user_id"]):
            from api.errors import error_response
            return error_response(404, "session_not_found", "Session not found")
        app.state.audit_service.record(user_id=user['user_id'],action="SESSION_REVOKED",resource_type="session",resource_id=session_id)

    @app.get("/workspaces/{workspace_id}/audit-events")
    def audit_events(workspace_id: str, action: str | None = None, resource_type: str | None = None, resource_id: str | None = None, user_id: str | None = None, start_at: str | None = None, end_at: str | None = None, limit: int = 50, offset: int = 0, cursor: str | None = None, authorization: str | None = Header(default=None)):
        denied=_authorize_workspace(app,workspace_id,authorization,{"OWNER","ADMIN"})
        if denied:return denied
        try:return app.state.audit_query_service.query(workspace_id,action=action.split(',') if action and ',' in action else action,resource_type=resource_type,resource_id=resource_id,user_id=user_id,start_at=start_at,end_at=end_at,limit=limit,offset=offset,cursor=cursor)
        except ValueError:
            from api.errors import error_response
            return error_response(400,'invalid_cursor','Invalid audit query')

    @app.get("/workspaces/{workspace_id}/artifacts")
    def list_artifacts(
        workspace_id: str,
        artifact_type: str | None = None,
        mission_id: str | None = None,
        task_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
        authorization: str | None = Header(default=None),
    ):
        denied = _authorize_workspace(
            app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"}
        )
        if denied:
            return denied
        try:
            return app.state.artifact_service.list(
                workspace_id,
                artifact_type=artifact_type,
                mission_id=mission_id,
                task_id=task_id,
                limit=limit,
                offset=offset,
            )
        except (TypeError, ValueError):
            from api.errors import error_response

            return error_response(400, "invalid_request", "Invalid artifact query")

    @app.get("/workspaces/{workspace_id}/artifacts/{artifact_id}")
    def get_artifact(
        workspace_id: str,
        artifact_id: str,
        authorization: str | None = Header(default=None),
    ):
        denied = _authorize_workspace(
            app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"}
        )
        if denied:
            return denied
        try:
            value = app.state.artifact_service.get(workspace_id, artifact_id)
        except (TypeError, ValueError):
            from api.errors import error_response

            return error_response(400, "invalid_request", "Invalid artifact request")
        if value is None:
            from api.errors import error_response

            return error_response(404, "artifact_not_found", "Artifact not found")
        return value

    @app.get("/workspaces/{workspace_id}/artifacts/{artifact_id}/content")
    def get_artifact_content(
        workspace_id: str,
        artifact_id: str,
        authorization: str | None = Header(default=None),
    ):
        denied = _authorize_workspace(
            app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"}
        )
        if denied:
            return denied
        try:
            value = app.state.artifact_service.content(workspace_id, artifact_id)
        except ValueError as error:
            from api.errors import error_response

            code = str(error)
            if code in {
                "content_unavailable",
                "unsupported_content_type",
                "content_too_large",
            }:
                return error_response(409, code, "Artifact content is unavailable")
            return error_response(400, "invalid_request", "Invalid artifact request")
        if value is None:
            from api.errors import error_response

            return error_response(404, "artifact_not_found", "Artifact not found")
        if value.get("status") == "MISSING":
            from api.errors import error_response

            return error_response(409, "artifact_missing", "Artifact content is missing")
        return value

    @app.get("/workspaces/{workspace_id}/usage")
    def list_usage(
        workspace_id: str,
        provider: str | None = None,
        model: str | None = None,
        mission_id: str | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
        limit: int = 50,
        offset: int = 0,
        authorization: str | None = Header(default=None),
    ):
        denied = _authorize_workspace(
            app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"}
        )
        if denied:
            return denied
        if app.state.usage_service is None:
            from api.errors import error_response

            return error_response(503, "usage_unavailable", "Usage is unavailable")
        try:
            return app.state.usage_service.list(
                workspace_id,
                provider=provider,
                model=model,
                mission_id=mission_id,
                start_at=start_at,
                end_at=end_at,
                limit=limit,
                offset=offset,
            )
        except (TypeError, ValueError):
            from api.errors import error_response

            return error_response(400, "invalid_request", "Invalid usage query")

    @app.get("/workspaces/{workspace_id}/usage/summary")
    def usage_summary(
        workspace_id: str,
        provider: str | None = None,
        model: str | None = None,
        mission_id: str | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
        authorization: str | None = Header(default=None),
    ):
        denied = _authorize_workspace(
            app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"}
        )
        if denied:
            return denied
        if app.state.usage_service is None:
            from api.errors import error_response

            return error_response(503, "usage_unavailable", "Usage is unavailable")
        try:
            return app.state.usage_service.summary(
                workspace_id,
                provider=provider,
                model=model,
                mission_id=mission_id,
                start_at=start_at,
                end_at=end_at,
            )
        except (TypeError, ValueError):
            from api.errors import error_response

            return error_response(400, "invalid_request", "Invalid usage query")

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
        workspace_id: str | None = None,
        status: str | None = None,
        pipeline: str | None = None,
        task_type: str | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        authorization: str | None = Header(default=None),
    ):
        if app.state.auth_required:
            if not workspace_id:
                from api.errors import error_response

                return error_response(
                    400, "workspace_required", "Workspace is required"
                )
            denied = _authorize_workspace(
                app,
                workspace_id,
                authorization,
                {"OWNER", "ADMIN", "MEMBER"},
            )
            if denied:
                return denied
        try:
            response = app.state.task_api.list_tasks(
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
            if workspace_id:
                response["items"] = [
                    item
                    for item in response["items"]
                    if (item.get("task") or {}).get("workspace_id") == workspace_id
                ]
            return response
        except ValueError:
            from api.errors import error_response

            return error_response(400, "invalid_request", "Invalid task query")

    @app.post("/tasks/{task_id}/cancel")
    def cancel_task(task_id: str, authorization: str | None = Header(default=None)):
        denied = _authorize_task_control(app, task_id, authorization)
        if denied:
            return denied
        return _control_endpoint(app.state.task_api.cancel_task(task_id))

    @app.post("/tasks/{task_id}/retry")
    def retry_task(task_id: str, authorization: str | None = Header(default=None)):
        denied = _authorize_task_control(app, task_id, authorization)
        if denied:
            return denied
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


def _authorize_task_control(app, task_id, authorization):
    if not app.state.auth_required:
        return None
    response = app.state.task_api.get_task(task_id)
    if not response.get("found"):
        from api.errors import error_response

        return error_response(404, "task_not_found", "Task not found")
    workspace_id = (response.get("task") or {}).get("workspace_id")
    return _authorize_workspace(
        app,
        workspace_id,
        authorization,
        {"OWNER", "ADMIN", "MEMBER"},
    )


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
    token = (
        authorization[7:]
        if isinstance(authorization, str) and authorization.startswith("Bearer ")
        else None
    )
    decision = app.state.authorization_service.authorize_workspace(
        token, workspace_id, set(roles)
    )
    if decision.allowed:
        return None
    if decision.code == "authentication_required":
        return _unauthorized()
    if decision.code == "workspace_not_found":
        from api.errors import error_response
        return error_response(404, "workspace_not_found", "Workspace not found")
    if decision.code == "workspace_inactive":
        from api.errors import error_response
        return error_response(403, "workspace_inactive", "Permission denied")
    from api.errors import error_response
    return error_response(403, "permission_denied", "Permission denied")
