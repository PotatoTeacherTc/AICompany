from fastapi import FastAPI, Header, Request
from contextlib import asynccontextmanager
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
    persistent_execution_service=None,
    job_execution_api_service=None,
    organization_service=None,
    quota_service=None,
    plan_service=None,
    dashboard_service=None,
    subscription_service=None,
    billing_service=None,
    admin_service=None,
    onboarding_service=None,
    health_service=None,
    auth_required=False,
    allowed_origins=(
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ),
    security_settings=None,
    rate_limiter=None,
    logger=None,
    metrics=None,
    infrastructure_resources=None,
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

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            if infrastructure_resources is not None:
                infrastructure_resources.close()

    app = FastAPI(
        title="AICompany API",
        version="0.1.0",
        debug=False,
        lifespan=lifespan,
    )
    if allowed_origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(allowed_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=[
                "Authorization", "Content-Type",
                "X-Correlation-ID", "X-Request-ID",
            ],
        )
    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next):
        context=RequestContext.create(
            request.headers.get("X-Correlation-ID"),
            request.headers.get("X-Request-ID"),
        )
        token=set_context(context); started=time.perf_counter()
        if metrics is not None:
            metrics.request_started()
        try:
            if rate_limiter is not None:
                client = request.client.host if request.client else "unknown"
                if not rate_limiter.allow(f"{client}:{request.url.path}"):
                    from api.errors import error_response
                    response = error_response(
                        429, "rate_limit_exceeded", "Too many requests"
                    )
                    response.headers["Retry-After"] = str(
                        getattr(rate_limiter, "window_seconds", 60)
                    )
                    response.headers["X-Correlation-ID"] = context.correlation_id
                    response.headers["X-Request-ID"] = context.request_id
                    from core.security import security_headers
                    production = bool(
                        security_settings
                        and security_settings.environment == "production"
                    )
                    for name, value in security_headers(production).items():
                        response.headers[name] = value
                else:
                    response=await call_next(request)
            else:
                response=await call_next(request)
        except Exception as error:
            duration_ms = (time.perf_counter() - started) * 1000
            if metrics is not None:
                metrics.request_finished(
                    500, duration_ms, type(error).__name__
                )
            from core.structured_logging import LogLevel, safe_log
            safe_log(
                logger, "HTTP_REQUEST_FAILED", "BackendAPI",
                level=LogLevel.ERROR, status="FAILED",
                duration_ms=duration_ms,
                metadata={
                    "request_id": context.request_id,
                    "correlation_id": context.correlation_id,
                },
            )
            raise
        finally:
            reset_context(token)
        duration_ms = (time.perf_counter() - started) * 1000
        if metrics is not None:
            metrics.request_finished(response.status_code, duration_ms)
        from core.structured_logging import LogLevel, safe_log
        safe_log(
            logger, "HTTP_REQUEST_COMPLETED", "BackendAPI",
            level=LogLevel.INFO if response.status_code < 400 else LogLevel.WARNING,
            status=str(response.status_code), duration_ms=duration_ms,
            metadata={
                "request_id": context.request_id,
                "correlation_id": context.correlation_id,
            },
        )
        response.headers["X-Correlation-ID"]=context.correlation_id
        response.headers["X-Request-ID"]=context.request_id
        from core.security import harden_set_cookie, security_headers
        production = bool(
            security_settings
            and security_settings.environment == "production"
        )
        for name, value in security_headers(production).items():
            response.headers[name] = value
        cookie = response.headers.get("set-cookie")
        if cookie and security_settings and security_settings.secure_cookies:
            response.headers["set-cookie"] = harden_set_cookie(cookie)
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
        from core.access_token_provider import SignedAccessTokenProvider

        session_service = session_service or SessionService()
        token_provider = (
            SignedAccessTokenProvider(secret=security_settings.signing_secret)
            if security_settings and security_settings.signing_secret
            else None
        )
        login_service = LoginService(
            user_service,
            credential_service,
            token_provider=token_provider,
            session_service=session_service,
        )
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
    app.state.persistent_execution_service = persistent_execution_service
    app.state.job_execution_api_service = job_execution_api_service
    app.state.organization_service = organization_service
    app.state.quota_service = quota_service
    app.state.plan_service = plan_service
    app.state.dashboard_service = dashboard_service
    app.state.subscription_service = subscription_service
    app.state.billing_service = billing_service
    app.state.admin_service = admin_service
    app.state.onboarding_service = onboarding_service
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
    app.state.metrics = metrics
    app.state.task_api = TaskApi(automation_service, task_query_service, workspace_service)
    for exception_type, handler in HANDLED_EXCEPTIONS.items():
        app.add_exception_handler(exception_type, handler)

    @app.get("/health")
    def health_check():
        if app.state.health_service is None:
            return {"status": "ok"}
        return app.state.health_service.snapshot()

    @app.get("/ready")
    def readiness_check():
        if app.state.health_service is None:
            return {"status": "not_ready"}
        return app.state.health_service.readiness()

    @app.get("/health/metrics")
    def health_metrics():
        if app.state.metrics is None:
            return {"status": "not_configured"}
        return {
            "status": "ok",
            "metrics": app.state.metrics.snapshot(),
        }

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
        status: str | None = None,
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
                status=status,
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

    @app.post("/workspaces/{workspace_id}/artifacts/{artifact_id}/archive")
    def archive_artifact(
        workspace_id: str,
        artifact_id: str,
        authorization: str | None = Header(default=None),
    ):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN"})
        if denied:
            return denied
        return _artifact_lifecycle_response(
            app, workspace_id, artifact_id, archive=True
        )

    @app.post("/workspaces/{workspace_id}/artifacts/{artifact_id}/restore")
    def restore_artifact(
        workspace_id: str,
        artifact_id: str,
        authorization: str | None = Header(default=None),
    ):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN"})
        if denied:
            return denied
        return _artifact_lifecycle_response(
            app, workspace_id, artifact_id, archive=False
        )

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

    @app.get("/workspaces/{workspace_id}/dashboard")
    def workspace_dashboard(
        workspace_id: str,
        recent_limit: int = 5,
        authorization: str | None = Header(default=None),
    ):
        denied = _authorize_workspace(
            app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"}
        )
        if denied:
            return denied
        if app.state.dashboard_service is None:
            from api.errors import error_response
            return error_response(
                503, "dashboard_unavailable", "Dashboard is unavailable"
            )
        try:
            value = app.state.dashboard_service.overview(
                workspace_id, recent_limit=recent_limit
            )
        except (TypeError, ValueError):
            from api.errors import error_response
            return error_response(400, "invalid_request", "Invalid dashboard request")
        if value is None:
            from api.errors import error_response
            return error_response(404, "workspace_not_found", "Workspace not found")
        return value

    @app.post("/workspaces/{workspace_id}/onboarding")
    def onboard_workspace(
        workspace_id: str,
        authorization: str | None = Header(default=None),
    ):
        denied = _authorize_workspace(
            app, workspace_id, authorization, {"OWNER", "ADMIN"}
        )
        if denied:
            return denied
        if app.state.onboarding_service is None:
            from api.errors import error_response
            return error_response(
                503, "onboarding_unavailable", "Onboarding is unavailable"
            )
        try:
            return app.state.onboarding_service.ensure_workspace(workspace_id)
        except KeyError:
            from api.errors import error_response
            return error_response(
                404, "workspace_not_found", "Workspace not found"
            )

    @app.get("/admin/workspaces")
    def admin_list_workspaces(
        authorization: str | None = Header(default=None),
    ):
        denied = _authorize_platform_admin(app, authorization)
        return denied or app.state.admin_service.list_workspaces()

    @app.get("/admin/me")
    def admin_me(authorization: str | None = Header(default=None)):
        denied = _authorize_platform_admin(app, authorization)
        return denied or {"platform_admin": True}

    @app.get("/admin/workspaces/{workspace_id}")
    def admin_get_workspace(
        workspace_id: str,
        authorization: str | None = Header(default=None),
    ):
        denied = _authorize_platform_admin(app, authorization)
        if denied:
            return denied
        value = app.state.admin_service.workspace_operations(workspace_id)
        if value is None:
            from api.errors import error_response
            return error_response(404, "workspace_not_found", "Workspace not found")
        return value

    @app.get("/admin/users")
    def admin_list_users(authorization: str | None = Header(default=None)):
        denied = _authorize_platform_admin(app, authorization)
        return denied or app.state.admin_service.list_users()

    @app.get("/admin/users/{user_id}")
    def admin_get_user(
        user_id: str,
        authorization: str | None = Header(default=None),
    ):
        denied = _authorize_platform_admin(app, authorization)
        if denied:
            return denied
        value = app.state.admin_service.get_user(user_id)
        if value is None:
            from api.errors import error_response
            return error_response(404, "user_not_found", "User not found")
        return value

    @app.get("/admin/plans")
    def admin_list_plans(authorization: str | None = Header(default=None)):
        denied = _authorize_platform_admin(app, authorization)
        return denied or app.state.admin_service.plans_catalog()

    @app.put("/admin/workspaces/{workspace_id}/status")
    def admin_set_workspace_status(
        workspace_id: str,
        payload: dict,
        authorization: str | None = Header(default=None),
    ):
        return _admin_mutation(
            app, authorization, "set_workspace_status",
            workspace_id, payload.get("status")
        )

    @app.put("/admin/workspaces/{workspace_id}/subscription/plan")
    def admin_change_subscription_plan(
        workspace_id: str,
        payload: dict,
        authorization: str | None = Header(default=None),
    ):
        return _admin_mutation(
            app, authorization, "change_subscription_plan",
            workspace_id, payload.get("plan_id")
        )

    @app.post("/admin/workspaces/{workspace_id}/jobs/{job_id}/retry")
    def admin_retry_job(
        workspace_id: str,
        job_id: str,
        authorization: str | None = Header(default=None),
    ):
        return _admin_mutation(
            app, authorization, "retry_failed_job", workspace_id, job_id
        )

    @app.post("/admin/workspaces/{workspace_id}/invoices/{invoice_id}/void")
    def admin_void_invoice(
        workspace_id: str,
        invoice_id: str,
        authorization: str | None = Header(default=None),
    ):
        return _admin_mutation(
            app, authorization, "void_invoice", workspace_id, invoice_id
        )

    @app.post("/admin/workspaces/{workspace_id}/invoices/{invoice_id}/fake-payment")
    def admin_fake_payment(
        workspace_id: str,
        invoice_id: str,
        payload: dict,
        authorization: str | None = Header(default=None),
    ):
        return _admin_mutation(
            app, authorization, "record_fake_payment",
            workspace_id, invoice_id, payload
        )

    @app.get("/workspaces/{workspace_id}/billing/account")
    def get_billing_account(
        workspace_id: str,
        authorization: str | None = Header(default=None),
    ):
        denied = _authorize_workspace(
            app, workspace_id, authorization, {"OWNER", "ADMIN"}
        )
        if denied:
            return denied
        return _billing_read(app, workspace_id, "account")

    @app.put("/workspaces/{workspace_id}/billing/account")
    def update_billing_account(
        workspace_id: str,
        payload: dict,
        authorization: str | None = Header(default=None),
    ):
        return _billing_mutation(
            app, workspace_id, authorization, "update_account", payload
        )

    @app.get("/workspaces/{workspace_id}/billing/prices")
    def list_billing_prices(
        workspace_id: str,
        authorization: str | None = Header(default=None),
    ):
        denied = _authorize_workspace(
            app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"}
        )
        if denied:
            return denied
        return _billing_read(app, workspace_id, "prices")

    @app.get("/workspaces/{workspace_id}/invoices")
    def list_invoices(
        workspace_id: str,
        authorization: str | None = Header(default=None),
    ):
        denied = _authorize_workspace(
            app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"}
        )
        if denied:
            return denied
        return _billing_read(app, workspace_id, "invoices")

    @app.get("/workspaces/{workspace_id}/invoices/{invoice_id}")
    def get_invoice(
        workspace_id: str,
        invoice_id: str,
        authorization: str | None = Header(default=None),
    ):
        denied = _authorize_workspace(
            app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"}
        )
        if denied:
            return denied
        return _billing_read(app, workspace_id, "invoice", invoice_id)

    @app.post("/workspaces/{workspace_id}/invoices", status_code=201)
    def create_invoice(
        workspace_id: str,
        payload: dict,
        authorization: str | None = Header(default=None),
    ):
        return _billing_mutation(
            app, workspace_id, authorization, "create_invoice", payload
        )

    @app.post("/workspaces/{workspace_id}/invoices/{invoice_id}/payments")
    def record_invoice_payment(
        workspace_id: str,
        invoice_id: str,
        payload: dict,
        authorization: str | None = Header(default=None),
    ):
        return _billing_mutation(
            app, workspace_id, authorization, "pay", invoice_id, payload
        )

    @app.get("/workspaces/{workspace_id}/subscription")
    def current_subscription(
        workspace_id: str,
        authorization: str | None = Header(default=None),
    ):
        denied = _authorize_workspace(
            app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"}
        )
        if denied:
            return denied
        if app.state.subscription_service is None:
            from api.errors import error_response
            return error_response(
                503, "subscription_unavailable", "Subscription is unavailable"
            )
        value = app.state.subscription_service.current(workspace_id)
        if value is None:
            from api.errors import error_response
            return error_response(
                404, "subscription_not_found", "Subscription not found"
            )
        return value

    @app.post("/workspaces/{workspace_id}/subscription", status_code=201)
    def create_subscription(
        workspace_id: str,
        payload: dict,
        authorization: str | None = Header(default=None),
    ):
        return _subscription_mutation(
            app, workspace_id, authorization, "create", payload
        )

    @app.put("/workspaces/{workspace_id}/subscription/plan")
    def change_subscription_plan(
        workspace_id: str,
        payload: dict,
        authorization: str | None = Header(default=None),
    ):
        return _subscription_mutation(
            app, workspace_id, authorization, "change_plan", payload
        )

    @app.post("/workspaces/{workspace_id}/subscription/cancel")
    def cancel_subscription(
        workspace_id: str,
        authorization: str | None = Header(default=None),
    ):
        return _subscription_mutation(
            app, workspace_id, authorization, "schedule_cancel"
        )

    @app.post("/workspaces/{workspace_id}/subscription/cancel/undo")
    def undo_subscription_cancel(
        workspace_id: str,
        authorization: str | None = Header(default=None),
    ):
        return _subscription_mutation(
            app, workspace_id, authorization, "undo_cancel"
        )

    @app.put("/workspaces/{workspace_id}/subscription/status")
    def change_subscription_status(
        workspace_id: str,
        payload: dict,
        authorization: str | None = Header(default=None),
    ):
        return _subscription_mutation(
            app, workspace_id, authorization, "transition", payload
        )

    @app.get("/workspaces/{workspace_id}/plans")
    def list_plans(
        workspace_id: str,
        authorization: str | None = Header(default=None),
    ):
        denied = _authorize_workspace(
            app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"}
        )
        if denied:
            return denied
        if app.state.plan_service is None:
            from api.errors import error_response
            return error_response(503, "plan_unavailable", "Plan is unavailable")
        return app.state.plan_service.list()

    @app.get("/workspaces/{workspace_id}/plan")
    def current_plan(
        workspace_id: str,
        authorization: str | None = Header(default=None),
    ):
        denied = _authorize_workspace(
            app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"}
        )
        if denied:
            return denied
        if app.state.plan_service is None:
            from api.errors import error_response
            return error_response(503, "plan_unavailable", "Plan is unavailable")
        return app.state.plan_service.current(workspace_id)

    @app.get("/workspaces/{workspace_id}/entitlements")
    def current_entitlements(
        workspace_id: str,
        authorization: str | None = Header(default=None),
    ):
        denied = _authorize_workspace(
            app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"}
        )
        if denied:
            return denied
        if app.state.plan_service is None:
            from api.errors import error_response
            return error_response(503, "plan_unavailable", "Plan is unavailable")
        return app.state.plan_service.entitlements(workspace_id)

    @app.put("/workspaces/{workspace_id}/plan")
    def assign_plan(
        workspace_id: str,
        payload: dict,
        authorization: str | None = Header(default=None),
    ):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN"})
        if denied:
            return denied
        if app.state.plan_service is None:
            from api.errors import error_response
            return error_response(503, "plan_unavailable", "Plan is unavailable")
        try:
            return app.state.plan_service.assign(workspace_id, payload)
        except (TypeError, ValueError):
            from api.errors import error_response
            return error_response(400, "invalid_request", "Invalid plan request")

    @app.get("/workspaces/{workspace_id}/quota")
    def get_quota(
        workspace_id: str,
        authorization: str | None = Header(default=None),
    ):
        denied = _authorize_workspace(
            app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"}
        )
        if denied:
            return denied
        if app.state.quota_service is None:
            from api.errors import error_response
            return error_response(503, "quota_unavailable", "Quota is unavailable")
        return app.state.quota_service.get(workspace_id)

    @app.put("/workspaces/{workspace_id}/quota")
    def update_quota(
        workspace_id: str,
        payload: dict,
        authorization: str | None = Header(default=None),
    ):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN"})
        if denied:
            return denied
        if app.state.quota_service is None:
            from api.errors import error_response
            return error_response(503, "quota_unavailable", "Quota is unavailable")
        try:
            return app.state.quota_service.update(workspace_id, payload)
        except (TypeError, ValueError):
            from api.errors import error_response
            return error_response(400, "invalid_request", "Invalid quota request")

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

    @app.post("/workspaces/{workspace_id}/jobs", status_code=201)
    def submit_job(workspace_id: str, payload: dict, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"})
        if denied: return denied
        if app.state.job_execution_api_service is None:
            from api.errors import error_response
            return error_response(503, "job_service_unavailable", "Job service is unavailable")
        try:
            return app.state.job_execution_api_service.submit(workspace_id, payload)
        except (TypeError, ValueError):
            from api.errors import error_response
            return error_response(400, "invalid_request", "Invalid job request")

    @app.get("/workspaces/{workspace_id}/jobs")
    def list_jobs(workspace_id: str, status: str | None = None, limit: int = 50, offset: int = 0, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"})
        if denied: return denied
        try:
            return app.state.job_execution_api_service.list_jobs(workspace_id, status, limit, offset)
        except (AttributeError, TypeError, ValueError):
            from api.errors import error_response
            return error_response(400, "invalid_request", "Invalid job query")

    @app.get("/workspaces/{workspace_id}/jobs/{job_id}")
    def get_job(workspace_id: str, job_id: str, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"})
        if denied: return denied
        value = app.state.job_execution_api_service.get_job(workspace_id, job_id)
        if value is None:
            from api.errors import error_response
            return error_response(404, "job_not_found", "Job not found")
        return value

    @app.post("/workspaces/{workspace_id}/jobs/{job_id}/cancel")
    def cancel_job(workspace_id: str, job_id: str, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"})
        if denied: return denied
        if app.state.job_execution_api_service.get_job(workspace_id, job_id) is None:
            from api.errors import error_response
            return error_response(404, "job_not_found", "Job not found")
        try:
            return app.state.job_execution_api_service.cancel(workspace_id, job_id)
        except ValueError:
            from api.errors import error_response
            return error_response(409, "job_state_conflict", "Job cannot be cancelled")

    @app.post("/workspaces/{workspace_id}/jobs/{job_id}/retry")
    def retry_job(workspace_id: str, job_id: str, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"})
        if denied: return denied
        if app.state.job_execution_api_service.get_job(workspace_id, job_id) is None:
            from api.errors import error_response
            return error_response(404, "job_not_found", "Job not found")
        try:
            return app.state.job_execution_api_service.retry(workspace_id, job_id)
        except ValueError:
            from api.errors import error_response
            return error_response(409, "job_state_conflict", "Job cannot be retried")

    @app.get("/workspaces/{workspace_id}/executions")
    def list_executions(workspace_id: str, status: str | None = None, pipeline: str | None = None, task_type: str | None = None, start_at: str | None = None, end_at: str | None = None, limit: int = 50, offset: int = 0, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"})
        if denied: return denied
        try:
            return app.state.job_execution_api_service.list_executions(workspace_id, status, pipeline, task_type, start_at, end_at, limit, offset)
        except (AttributeError, TypeError, ValueError):
            from api.errors import error_response
            return error_response(400, "invalid_request", "Invalid execution query")

    @app.get("/workspaces/{workspace_id}/executions/{execution_id}")
    def get_execution(workspace_id: str, execution_id: str, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"})
        if denied: return denied
        value = app.state.job_execution_api_service.get_execution(workspace_id, execution_id)
        if value is None:
            from api.errors import error_response
            return error_response(404, "execution_not_found", "Execution not found")
        return value

    @app.get("/workspaces/{workspace_id}/batches")
    def list_batches(workspace_id: str, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"})
        if denied: return denied
        return app.state.job_execution_api_service.list_batches(workspace_id)

    @app.get("/workspaces/{workspace_id}/batches/{batch_id}")
    def get_batch(workspace_id: str, batch_id: str, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"})
        if denied: return denied
        value = app.state.job_execution_api_service.get_batch(workspace_id, batch_id)
        if value is None:
            from api.errors import error_response
            return error_response(404, "batch_not_found", "Batch not found")
        return value

    @app.get("/workspaces/{workspace_id}/departments")
    def list_departments(workspace_id: str, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"})
        if denied: return denied
        return app.state.organization_service.list_departments(workspace_id)

    @app.post("/workspaces/{workspace_id}/departments", status_code=201)
    def create_department(workspace_id: str, payload: dict, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN"})
        if denied: return denied
        try:
            return app.state.organization_service.create_department(workspace_id, payload)
        except ValueError as error:
            from api.errors import error_response
            code = "resource_conflict" if "already" in str(error) else "invalid_request"
            return error_response(409 if code == "resource_conflict" else 400, code, "Department request rejected")

    @app.get("/workspaces/{workspace_id}/departments/{department_id}")
    def get_department(workspace_id: str, department_id: str, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"})
        if denied: return denied
        value = app.state.organization_service.get_department(workspace_id, department_id)
        if value is None:
            from api.errors import error_response
            return error_response(404, "department_not_found", "Department not found")
        return value

    @app.patch("/workspaces/{workspace_id}/departments/{department_id}")
    def update_department(workspace_id: str, department_id: str, payload: dict, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN"})
        if denied: return denied
        if app.state.organization_service.get_department(workspace_id, department_id) is None:
            from api.errors import error_response
            return error_response(404, "department_not_found", "Department not found")
        try:
            return app.state.organization_service.update_department(workspace_id, department_id, payload)
        except ValueError as error:
            from api.errors import error_response
            conflict = "revision" in str(error)
            return error_response(409 if conflict else 400, "revision_conflict" if conflict else "invalid_request", "Department update rejected")

    @app.post("/workspaces/{workspace_id}/departments/{department_id}/workers")
    def assign_department_worker(workspace_id: str, department_id: str, payload: dict, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN"})
        if denied: return denied
        try:
            return app.state.organization_service.assign_worker(workspace_id, department_id, payload)
        except ValueError:
            from api.errors import error_response
            return error_response(409, "assignment_rejected", "Worker assignment rejected")

    @app.delete("/workspaces/{workspace_id}/departments/{department_id}/workers/{worker_id}")
    def remove_department_worker(workspace_id: str, department_id: str, worker_id: str, expected_revision: int, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN"})
        if denied: return denied
        try:
            return app.state.organization_service.remove_worker(workspace_id, department_id, worker_id, expected_revision)
        except ValueError:
            from api.errors import error_response
            return error_response(409, "assignment_rejected", "Worker assignment rejected")

    @app.get("/workspaces/{workspace_id}/workers")
    def list_organization_workers(workspace_id: str, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"})
        if denied: return denied
        return app.state.organization_service.list_workers(workspace_id)

    @app.get("/workspaces/{workspace_id}/workers/{worker_id}")
    def get_organization_worker(workspace_id: str, worker_id: str, authorization: str | None = Header(default=None)):
        denied = _authorize_workspace(app, workspace_id, authorization, {"OWNER", "ADMIN", "MEMBER"})
        if denied: return denied
        value = app.state.organization_service.get_worker(workspace_id, worker_id)
        if value is None:
            from api.errors import error_response
            return error_response(404, "worker_not_found", "Worker not found")
        return value

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


def _artifact_lifecycle_response(app, workspace_id, artifact_id, *, archive):
    from api.errors import error_response

    try:
        operation = (
            app.state.artifact_service.archive
            if archive
            else app.state.artifact_service.restore
        )
        value = operation(workspace_id, artifact_id)
    except (TypeError, ValueError) as error:
        if str(error) == "artifact_missing":
            return error_response(409, "artifact_missing", "Artifact content is missing")
        return error_response(400, "invalid_request", "Invalid artifact request")
    if value is None:
        return error_response(404, "artifact_not_found", "Artifact not found")
    return value


def _subscription_mutation(
    app, workspace_id, authorization, operation_name, payload=None
):
    from api.errors import error_response

    denied = _authorize_workspace(
        app, workspace_id, authorization, {"OWNER", "ADMIN"}
    )
    if denied:
        return denied
    service = app.state.subscription_service
    if service is None:
        return error_response(
            503, "subscription_unavailable", "Subscription is unavailable"
        )
    try:
        operation = getattr(service, operation_name)
        return (
            operation(workspace_id)
            if payload is None
            else operation(workspace_id, payload)
        )
    except KeyError:
        return error_response(
            404, "subscription_not_found", "Subscription not found"
        )
    except (TypeError, ValueError):
        return error_response(
            409, "subscription_conflict", "Subscription cannot be changed"
        )


def _billing_read(app, workspace_id, operation_name, *args):
    from api.errors import error_response

    service = app.state.billing_service
    if service is None:
        return error_response(503, "billing_unavailable", "Billing is unavailable")
    try:
        value = getattr(service, operation_name)(workspace_id, *args)
    except (KeyError, TypeError, ValueError):
        return error_response(400, "invalid_billing_request", "Invalid billing request")
    if value is None:
        return error_response(404, "billing_record_not_found", "Billing record not found")
    return value


def _billing_mutation(
    app, workspace_id, authorization, operation_name, *args
):
    from api.errors import error_response

    denied = _authorize_workspace(
        app, workspace_id, authorization, {"OWNER", "ADMIN"}
    )
    if denied:
        return denied
    service = app.state.billing_service
    if service is None:
        return error_response(503, "billing_unavailable", "Billing is unavailable")
    try:
        return getattr(service, operation_name)(workspace_id, *args)
    except KeyError:
        return error_response(404, "billing_record_not_found", "Billing record not found")
    except (TypeError, ValueError):
        return error_response(409, "billing_conflict", "Billing record cannot be changed")


def _authorize_platform_admin(app, authorization):
    from api.errors import error_response

    service = app.state.admin_service
    if service is None:
        return error_response(503, "admin_unavailable", "Admin is unavailable")
    user = _current_user(app, authorization)
    if user is None:
        return _unauthorized()
    if not service.is_admin(user.get("user_id")):
        return error_response(403, "platform_admin_required", "Permission denied")
    return None


def _admin_mutation(app, authorization, operation_name, *args):
    from api.errors import error_response

    denied = _authorize_platform_admin(app, authorization)
    if denied:
        return denied
    try:
        return getattr(app.state.admin_service, operation_name)(*args)
    except KeyError:
        return error_response(404, "admin_resource_not_found", "Resource not found")
    except (TypeError, ValueError):
        return error_response(409, "admin_operation_conflict", "Operation cannot be completed")
