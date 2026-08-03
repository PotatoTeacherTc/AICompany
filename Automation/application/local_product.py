"""Windows host composition for the @10 local product Dashboard."""

import os
from pathlib import Path

from application.backend import BackendDependencies, BackendHealthService, create_backend_app
from application.artifact_service import ArtifactApplicationService
from application.usage_reporting_service import UsageReportingService
from application.credential_service import CredentialService
from application.job_execution_api_service import JobExecutionApiService
from application.login_service import LoginService
from application.intelligence_service import IntelligenceService
from application.organization_service import OrganizationService
from application.persistent_execution_service import PersistentExecutionService
from application.product_content_runner import ProductContentRunner
from application.product_workflow_service import ProductWorkflowService
from application.youtube_connection_coordinator import YouTubeConnectionCoordinator
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
from core.company_bible import BibleManager
from core.department import DepartmentManager, WorkerDirectory
from core.organization_engine import OrganizationEngine, ORGANIZATION_TASK_TYPES
from core.intelligence import IntelligenceEngine
from providers.factory import ProviderFactory


def create_local_product_app(environment=None):
    values = dict(os.environ if environment is None else environment)
    root = Path(values.get("AICOMPANY_PRODUCT_ROOT", Path(__file__).parents[1] / "product-data")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    artifact_root = Path(values.get("AICOMPANY_ARTIFACT_ROOT", root / "artifacts")).resolve()
    artifact_root.mkdir(parents=True, exist_ok=True)
    state_root = root / "state"; state_root.mkdir(parents=True, exist_ok=True)
    states = JsonStateRepository(state_root / "music-project-state.json")
    metadata = FileArtifactRepository(state_root / "artifact-metadata.json", artifact_root)
    artifacts = ArtifactManager(metadata, ArtifactStorageAdapter(LocalStorageProvider(artifact_root), metadata))
    history, usage = ExecutionHistory(state_repository=states), UsageEngine(states)
    queue = PersistentJobQueue(states, workspace_ids=("default", "youtube-smoke"))
    execution = PersistentExecutionService(queue, InProcessJobWorker(queue), history, artifacts, usage)
    connections = YouTubeConnectionService(YouTubeConnectionRepository(states), WindowsLocalSecureTokenStore())
    bible_manager = BibleManager(states)
    youtube_connector = YouTubeConnectionCoordinator(
        connections, values.get("AICOMPANY_GOOGLE_CLIENT_SECRET_FILE")
    )
    try: naver = ProviderFactory.naver_blog_from_environment(values).provider
    except Exception: naver = None
    runner = ProductContentRunner(root, states, artifacts, history, usage, values, connections, None, naver)
    product = ProductWorkflowService(states, execution, runner, {
        "comfyui": lambda _w: "CONFIGURED" if values.get("AICOMPANY_IMAGE_PROVIDER") == "comfyui" else "NOT_CONFIGURED",
        "youtube": youtube_connector.status,
        "naver": lambda _w: "CONNECTED" if naver is not None else "NOT_CONFIGURED",
    }, auto_run=True, youtube_connector=youtube_connector, bible_resolver=bible_manager)
    worker_directory = WorkerDirectory()
    department_manager = DepartmentManager(states, worker_directory, ORGANIZATION_TASK_TYPES)
    organization_engine = OrganizationEngine(states, department_manager, product)
    organization_service = OrganizationService(department_manager, worker_directory, organization_engine)
    research_selection = ProviderFactory.research_from_environment(values)
    meeting_selection = ProviderFactory.meeting_from_environment(values)
    intelligence_service = IntelligenceService(IntelligenceEngine(
        states, organization_engine, bible_manager,
        research_provider=research_selection.provider,
        meeting_provider=meeting_selection.provider,
        timeout_seconds=min(research_selection.timeout_seconds, meeting_selection.timeout_seconds),
        history=history,
    ))

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
        organization_service=organization_service,
        bible_service=bible_manager,
        intelligence_service=intelligence_service,
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


def reset_local_owner_password(environment=None):
    """Explicitly replace only the persisted local owner's credential."""
    values = dict(os.environ if environment is None else environment)
    if values.get("AICOMPANY_RESET_OWNER_PASSWORD", "").lower() != "true":
        raise RuntimeError("local_owner_reset_not_requested")
    password = values.get("AICOMPANY_LOCAL_PASSWORD")
    if not isinstance(password, str) or len(password) < 12:
        raise RuntimeError("local_password_required")
    root = Path(values.get(
        "AICOMPANY_PRODUCT_ROOT", Path(__file__).parents[1] / "product-data"
    )).resolve()
    local_state = root / "local-product"
    users = UserService(FileUserRepository(local_state / "users.json"))
    user = users.get_by_email("owner@localhost")
    if user is None:
        raise RuntimeError("local_owner_not_found")
    repository = FileCredentialRepository(local_state / "credentials.json")
    if repository.get(user["user_id"]) is None:
        raise RuntimeError("local_owner_credential_not_found")
    credentials = CredentialService(users, repository)
    new_hash = credentials.password_hasher.hash(password)
    if not credentials.password_hasher.verify(password, new_hash):
        raise RuntimeError("local_owner_reset_failed")
    repository.save({"user_id": user["user_id"], "password_hash": new_hash})
    return {"status": "OWNER_PASSWORD_RESET", "email": "owner@localhost"}
