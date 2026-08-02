"""Windows host composition for the @10 local product Dashboard."""

import os
from pathlib import Path

from application.backend import BackendDependencies, BackendHealthService, create_backend_app
from application.artifact_service import ArtifactApplicationService
from application.usage_reporting_service import UsageReportingService
from application.credential_service import CredentialService
from application.job_execution_api_service import JobExecutionApiService
from application.login_service import LoginService
from application.persistent_execution_service import PersistentExecutionService
from application.product_content_runner import ProductContentRunner
from application.product_workflow_service import ProductWorkflowService
from application.session_service import SessionService
from application.user_service import UserService
from application.workspace_membership_service import WorkspaceMembershipService
from application.workspace_service import WorkspaceService
from core.access_token_provider import SignedAccessTokenProvider
from core.artifact_manager import ArtifactManager
from core.artifact_repository import FileArtifactRepository
from core.credential_repository import FileCredentialRepository
from core.execution_history import ExecutionHistory
from core.object_storage import ArtifactStorageAdapter, LocalStorageProvider
from core.persistence import JsonStateRepository
from core.secure_token_store import WindowsLocalSecureTokenStore
from core.security import SecuritySettings
from core.session_repository import FileSessionRepository
from core.task_queue import InProcessJobWorker, PersistentJobQueue
from core.usage_engine import UsageEngine
from core.user_repository import FileUserRepository
from core.workspace_membership import OWNER
from core.workspace_membership_repository import FileWorkspaceMembershipRepository
from core.workspace_repository import FileWorkspaceRepository
from core.workspace import Workspace
from core.youtube_publishing import YouTubeConnectionRepository, YouTubeConnectionService
from providers.factory import ProviderFactory


def create_local_product_app(environment=None):
    values = dict(os.environ if environment is None else environment)
    root = Path(values.get("AICOMPANY_PRODUCT_ROOT", Path(__file__).parents[1] / "product-data")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    state_root = root / "state"; state_root.mkdir(parents=True, exist_ok=True)
    states = JsonStateRepository(state_root / "music-project-state.json")
    metadata = FileArtifactRepository(state_root / "artifact-metadata.json", root / "artifacts")
    artifacts = ArtifactManager(metadata, ArtifactStorageAdapter(LocalStorageProvider(root / "artifacts"), metadata))
    history, usage = ExecutionHistory(state_repository=states), UsageEngine(states)
    queue = PersistentJobQueue(states, workspace_ids=("default", "youtube-smoke"))
    execution = PersistentExecutionService(queue, InProcessJobWorker(queue), history, artifacts, usage)
    connections = YouTubeConnectionService(YouTubeConnectionRepository(states), WindowsLocalSecureTokenStore())
    try: naver = ProviderFactory.naver_blog_from_environment(values).provider
    except Exception: naver = None
    runner = ProductContentRunner(root, states, artifacts, history, usage, values, connections, None, naver)
    product = ProductWorkflowService(states, execution, runner, {
        "comfyui": lambda _w: "CONFIGURED" if values.get("AICOMPANY_IMAGE_PROVIDER") == "comfyui" else "NOT_CONFIGURED",
        "youtube": lambda w: "CONNECTED" if connections.repository.list(w) else "NOT_CONFIGURED",
        "naver": lambda _w: "CONNECTED" if naver is not None else "NOT_CONFIGURED",
    }, auto_run=True)

    local_state = root / "local-product"; local_state.mkdir(parents=True, exist_ok=True)
    users = UserService(FileUserRepository(local_state / "users.json"))
    workspaces = WorkspaceService(FileWorkspaceRepository(local_state / "workspaces.json"))
    if workspaces.get("default") is None:
        workspaces.repository.save(Workspace("default", "My AICompany", "").to_dict())
    if states.list("youtube_connection", "youtube-smoke") and workspaces.get("youtube-smoke") is None:
        workspaces.repository.save(Workspace("youtube-smoke", "Potato Music Company", "").to_dict())
    memberships = WorkspaceMembershipService(workspaces, users, FileWorkspaceMembershipRepository(local_state / "memberships.json"))
    credentials = CredentialService(users, FileCredentialRepository(local_state / "credentials.json"))
    _bootstrap(values, users, workspaces, memberships, credentials)
    sessions = SessionService(FileSessionRepository(local_state / "sessions.json"))
    signing = values.get("AICOMPANY_SIGNING_SECRET")
    if not signing or len(signing) < 32: raise RuntimeError("local_signing_secret_required")
    login = LoginService(users, credentials, SignedAccessTokenProvider(secret=signing), sessions)
    security = SecuritySettings.from_environment({**values, "AICOMPANY_ENV":"development"})
    return create_backend_app(BackendDependencies(
        state_repository=states, workspace_service=workspaces, user_service=users,
        membership_service=memberships, credential_service=credentials,
        login_service=login, session_service=sessions,
        artifact_service=ArtifactApplicationService(artifacts),
        usage_service=UsageReportingService(usage),
        persistent_execution_service=execution,
        job_execution_api_service=JobExecutionApiService(execution, history, artifacts, usage),
        product_workflow_service=product, auth_required=True, security_settings=security,
        health_service=BackendHealthService(lambda: True, lambda: True, lambda: True),
    ))


def _bootstrap(values, users, workspaces, memberships, credentials):
    email = values.get("AICOMPANY_LOCAL_EMAIL", "owner@localhost")
    password = values.get("AICOMPANY_LOCAL_PASSWORD")
    if not password or len(password) < 12: raise RuntimeError("local_password_required")
    user = users.get_by_email(email)
    if user is None:
        user = users.create(email)
    if credentials.repository.get(user["user_id"]) is None:
        credentials.set_password(user["user_id"], password)
    for workspace in workspaces.list():
        if not memberships.repository.get(workspace["workspace_id"], user["user_id"]):
            memberships.add(workspace["workspace_id"], user["user_id"], OWNER)
