import importlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from agent.manager import Manager
from agent.goal_task_planner import GoalTaskPlanner
from application.automation_service import AutomationService
from application.task_query_service import TaskQueryService
from api.contracts import CreateTaskRequest, ListTasksRequest
from api.app import create_app
from api.task_api import TaskApi
from fastapi.testclient import TestClient
from config.settings import PROJECT_ROOT
from core.execution_history import ExecutionHistory
from core.execution_history_repository import InMemoryExecutionHistoryRepository
from core.artifact_manager import ArtifactManager
from core.artifact_repository import FileArtifactRepository, InMemoryArtifactRepository
from core.base_pipeline import BasePipeline
from core.content_pipeline import ContentPipeline
from core.history_analyzer import HistoryAnalyzer
from core.history_pipeline import HistoryPipeline
from core.music_pipeline import MusicPipeline
from core.mission import Mission, MissionState
from core.mission_workspace import MissionWorkspaceManager
from core.collaboration_worker import BaseWorker, FunctionWorker
from core.collaboration_orchestrator import CollaborationOrchestrator
from core.provider_workers import ClaudeWorker, GeminiWorker
from core.pipeline import AIPipeline
from core.registry import PipelineRegistry
from core.result import PipelineResult
from core.research_pipeline import ResearchPipeline
from core.status import PipelineStatus
from core.stub_pipelines import StubPipeline
from core.task import Task
from core.task_queue import TaskQueue
from core.worker import TaskWorker
from core.worker_context import ContextBuilder, WorkerContext
from core.worker_result import WorkerResult
from core.worker_result_validator import WorkerResultValidator
from core.workspace_repository import FileWorkspaceRepository, InMemoryWorkspaceRepository
from core.user_repository import FileUserRepository
from core.workspace_membership import ADMIN, MEMBER, OWNER
from core.workspace_membership_repository import FileWorkspaceMembershipRepository
from application.workspace_service import WorkspaceService
from application.user_service import UserService
from application.workspace_membership_service import WorkspaceMembershipService
from application.credential_service import CredentialService
from core.credential_repository import FileCredentialRepository
from application.login_service import LoginService
from core.access_token_provider import SignedAccessTokenProvider
from application.session_service import SessionService
from core.session_repository import FileSessionRepository
from application.audit_service import AuditService
from core.audit_repository import FileAuditRepository
from application.audit_query_service import AuditQueryService
from providers.factory import ProviderFactory
from providers.music import (
    FakeMusicProvider,
    GeneratedMusicArtifact,
    MusicGenerationRequest,
    MusicGenerationResult,
    MusicProvider,
)
from providers.mock_provider import MockProvider
from providers.models import ProviderRequest


RESULT_KEYS = {"status", "pipeline", "task", "task_id", "task_type", "data", "error"}


class TestPipeline(BasePipeline):
    def __init__(self):
        super().__init__("Test Pipeline")

    def run(self, task):
        return PipelineResult(PipelineStatus.SUCCESS, self.name, task).to_dict()


class FixedResultPipeline(BasePipeline):
    def __init__(self, result):
        super().__init__("Fixed Result Pipeline")
        self.result = result

    def run(self, task):
        return self.result


class PipelineTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.history = ExecutionHistory(self.root / "history.json")

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def task(task_text, task_type):
        task = Task(task_text)
        task.task_type = task_type
        return task

    def registry(self):
        main = importlib.import_module("main")
        return main.build_registry(
            self.history,
            base_folder=self.root / "test_files",
            music_root=self.root / "music",
            content_root=self.root / "content",
            research_root=self.root / "research",
        )


class TaskTests(unittest.TestCase):
    def test_claude_and_gemini_workers_use_fake_provider_safely(self):
        class FakeProvider:
            name = "fake"

            def generate(self, request):
                return SimpleNamespace(
                    provider=self.name,
                    model=request.model or "fake-model",
                    output_text=f"Completed {request.prompt}",
                    usage=SimpleNamespace(
                        input_tokens=2,
                        output_tokens=3,
                        total_tokens=5,
                        estimated_cost_usd=0.0,
                    ),
                )

        context = ContextBuilder().build(
            Mission.create(
                "Title", "private normalized objective", "user-1", "workspace-1"
            )
        )
        results = [
            ClaudeWorker(provider=FakeProvider()).execute(context),
            GeminiWorker(provider=FakeProvider()).execute(context),
        ]

        self.assertEqual(["claude", "gemini"], [result.worker for result in results])
        self.assertTrue(
            all(result.status == PipelineStatus.SUCCESS for result in results)
        )
        self.assertTrue(all(result.usage["provider"] == "fake" for result in results))
        self.assertNotIn("private normalized objective", str(results))

    def test_provider_workers_handle_missing_usage_timeout_and_errors(self):
        class MissingUsageProvider:
            name = "fake"

            def generate(self, request):
                return SimpleNamespace(
                    provider=self.name,
                    model="fake-model",
                    output_text="done",
                    usage=None,
                )

        class TimeoutProvider:
            name = "fake"

            def generate(self, request):
                raise TimeoutError("private request")

        class ErrorProvider:
            name = "fake"

            def generate(self, request):
                raise RuntimeError("api-key-private")

        context = ContextBuilder().build(
            Mission.create("Title", "Objective", "user-1", "workspace-1")
        )
        missing = ClaudeWorker(provider=MissingUsageProvider()).execute(context)
        timed_out = ClaudeWorker(provider=TimeoutProvider()).execute(context)
        failed = GeminiWorker(provider=ErrorProvider()).execute(context)

        self.assertEqual(0, missing.usage["total_tokens"])
        self.assertEqual(PipelineStatus.TIMED_OUT, timed_out.status)
        self.assertEqual("ProviderError: TimeoutError", timed_out.error)
        self.assertEqual(PipelineStatus.FAILED, failed.status)
        self.assertEqual("ProviderError: RuntimeError", failed.error)
        self.assertNotIn("api-key-private", str(failed.to_dict()))

    def test_orchestrator_rejects_worker_without_mission_lock(self):
        mission = Mission.create(
            "Title", "Objective", "user-1", "workspace-1"
        ).acquire_lock("other-worker")
        worker = FunctionWorker(
            "worker",
            lambda context: WorkerResult.create(
                PipelineStatus.SUCCESS, "worker", context
            ),
        )
        orchestrator = CollaborationOrchestrator([worker])

        result = orchestrator.run_worker(mission, worker)

        self.assertEqual(PipelineStatus.FAILED, result.status)
        self.assertEqual("LockError: MissionLockOwnershipError", result.error)

    def test_orchestrator_runs_multiple_workers_and_records_history(self):
        with tempfile.TemporaryDirectory() as directory:
            history = ExecutionHistory(
                repository=InMemoryExecutionHistoryRepository()
            )
            context_builder = ContextBuilder(MissionWorkspaceManager(directory))

            def handler(worker_name):
                return lambda context: WorkerResult.create(
                    PipelineStatus.SUCCESS,
                    worker_name,
                    context,
                    data={"workspace_path": context.workspace_path},
                )

            workers = [
                FunctionWorker("worker-1", handler("worker-1")),
                FunctionWorker("worker-2", handler("worker-2")),
            ]
            mission = Mission.create(
                "Title", "Objective", "user-1", "workspace-1"
            )
            result = CollaborationOrchestrator(
                workers,
                context_builder=context_builder,
                execution_history=history,
            ).run(mission)

            self.assertEqual(MissionState.COMPLETED, result.status)
            self.assertEqual(2, len(result.worker_results))
            self.assertTrue(
                all(
                    item["status"] == PipelineStatus.SUCCESS
                    for item in result.worker_results
                )
            )
            records = history.query(workspace_id="workspace-1")
            self.assertEqual(1, len(records))
            self.assertEqual("COLLABORATION", records[0]["task_type"])
            self.assertNotIn("Objective", str(records[0]))

    def test_orchestrator_marks_mission_failed_when_one_worker_fails(self):
        context_worker = FunctionWorker(
            "success",
            lambda context: WorkerResult.create(
                PipelineStatus.SUCCESS, "success", context
            ),
        )

        def fail(_context):
            raise RuntimeError("private prompt")

        result = CollaborationOrchestrator(
            [context_worker, FunctionWorker("failure", fail)]
        ).run(Mission.create("Title", "Objective", "user-1", "workspace-1"))

        self.assertEqual(MissionState.FAILED, result.status)
        self.assertEqual(
            [PipelineStatus.SUCCESS, PipelineStatus.FAILED],
            [item["status"] for item in result.worker_results],
        )
        self.assertNotIn("private prompt", str(result.to_dict()))

    def test_orchestrator_sanitizes_reported_worker_data_errors_and_paths(self):
        def unsafe(context):
            return WorkerResult.create(
                PipelineStatus.FAILED,
                "unsafe",
                context,
                data={
                    "api_key": "private-key",
                    "output": f"echo {context.objective}",
                },
                artifacts=[
                    {
                        "artifact_id": "artifact-1",
                        "workspace_id": context.workspace_id,
                        "path": "C:/Users/private/output.txt",
                    }
                ],
                error="raw private prompt and personal information",
            )

        result = CollaborationOrchestrator(
            [FunctionWorker("unsafe", unsafe)]
        ).run(
            Mission.create(
                "Title", "private objective", "user-1", "workspace-1"
            )
        )
        serialized = str(result.to_dict())

        self.assertNotIn("private-key", serialized)
        self.assertNotIn("private objective", serialized)
        self.assertNotIn("C:/Users/private", serialized)
        self.assertIn("WorkerError: ReportedFailure", serialized)

    def test_collaboration_end_to_end_with_fake_provider_and_workspace(self):
        class FakeProvider:
            name = "fake"

            def generate(self, request):
                return SimpleNamespace(
                    provider=self.name,
                    model="fake-model",
                    output_text="validated output",
                    usage=None,
                )

        with tempfile.TemporaryDirectory() as directory:
            mission = Mission.create(
                "Create result",
                "Produce a safe result",
                "user-1",
                "workspace-1",
                metadata={"api_key": "never-expose", "priority": "high"},
            )
            result = CollaborationOrchestrator(
                [
                    ClaudeWorker(provider=FakeProvider()),
                    GeminiWorker(provider=FakeProvider()),
                ],
                context_builder=ContextBuilder(MissionWorkspaceManager(directory)),
                execution_history=ExecutionHistory(
                    repository=InMemoryExecutionHistoryRepository()
                ),
            ).run(mission)

            self.assertEqual(MissionState.COMPLETED, result.status)
            self.assertEqual(MissionState.COMPLETED, result.mission["state"])
            self.assertFalse(result.mission["locked_by"])
            self.assertNotIn("never-expose", str(result.to_dict()))

    def test_mission_workspaces_isolate_workspace_and_mission_files(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = MissionWorkspaceManager(directory)
            first = Mission.create("One", "Objective", "user-1", "workspace-1")
            second = Mission.create("Two", "Objective", "user-1", "workspace-2")

            first_workspace = manager.create(first)
            second_workspace = manager.create(second)
            first_file = manager.resolve_file(first_workspace, "result.txt")
            second_file = manager.resolve_file(second_workspace, "result.txt")
            first_file.write_text("first", encoding="utf-8")
            second_file.write_text("second", encoding="utf-8")

            self.assertNotEqual(first_workspace.path, second_workspace.path)
            self.assertEqual("first", first_file.read_text(encoding="utf-8"))
            self.assertEqual("second", second_file.read_text(encoding="utf-8"))

    def test_mission_workspace_rejects_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = MissionWorkspaceManager(directory)
            workspace = manager.create(
                Mission.create("One", "Objective", "user-1", "workspace-1")
            )
            with self.assertRaisesRegex(ValueError, "escapes"):
                manager.resolve_file(workspace, "../outside.txt")

    def test_context_builder_includes_only_its_mission_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = MissionWorkspaceManager(directory)
            mission = Mission.create(
                "One", "Objective", "user-1", "workspace-1"
            )
            context = ContextBuilder(manager).build(mission)

            self.assertIn(mission.id, context.workspace_path)
            self.assertIn("workspace-1", context.workspace_path)
            self.assertTrue(Path(context.workspace_path).is_dir())

    def test_worker_result_validator_accepts_normal_and_failed_results(self):
        context = ContextBuilder().build(
            Mission.create("Title", "Objective", "user-1", "workspace-1")
        )
        validator = WorkerResultValidator()
        success = WorkerResult.create(
            PipelineStatus.SUCCESS, "worker", context, data={"done": True}
        )
        failure = WorkerResult.create(
            PipelineStatus.FAILED,
            "worker",
            context,
            error="ProviderError: TimeoutError",
        )

        self.assertTrue(validator.validate(context, success).valid)
        self.assertTrue(validator.validate(context, failure).valid)

    def test_worker_result_validator_rejects_invalid_boundaries(self):
        context = ContextBuilder().build(
            Mission.create("Title", "Objective", "user-1", "workspace-1")
        )
        other_context = WorkerContext(
            mission_id=context.mission_id,
            workspace_id="workspace-2",
            title=context.title,
            objective=context.objective,
            requested_by=context.requested_by,
        )
        result = WorkerResult.create(
            PipelineStatus.SUCCESS, "worker", other_context
        )

        validation = WorkerResultValidator().validate(context, result)

        self.assertFalse(validation.valid)
        self.assertEqual("workspace_mismatch", validation.error)

    def test_context_builder_creates_workspace_scoped_safe_context(self):
        mission = Mission.create(
            "Release",
            "Prepare the release candidate",
            "user-1",
            "workspace-1",
            metadata={
                "priority": "high",
                "api_key": "secret-value",
                "prompt": "raw private prompt",
                "nested": {"unsafe": True},
            },
        )

        context = ContextBuilder().build(mission)

        self.assertIsInstance(context, WorkerContext)
        self.assertEqual(mission.id, context.mission_id)
        self.assertEqual("workspace-1", context.workspace_id)
        self.assertEqual({"priority": "high"}, context.metadata)
        self.assertNotIn("secret-value", str(context.to_dict()))
        self.assertNotIn("raw private prompt", str(context.to_dict()))

    def test_context_builder_rejects_non_mission_without_echoing_input(self):
        sensitive = "raw-private-prompt"
        with self.assertRaises(ValueError) as raised:
            ContextBuilder().build(sensitive)
        self.assertNotIn(sensitive, str(raised.exception))

    def test_worker_result_reuses_status_usage_and_workspace_contracts(self):
        context = ContextBuilder().build(
            Mission.create("Title", "Objective", "user-1", "workspace-1")
        )
        usage = {
            "provider": "mock",
            "model": "mock-default",
            "input_tokens": 2,
            "output_tokens": 3,
            "total_tokens": 5,
            "estimated_cost_usd": 0.0,
        }
        result = WorkerResult.create(
            PipelineStatus.SUCCESS,
            "fake-worker",
            context,
            data={"summary": "done"},
            artifacts=[{"artifact_id": "artifact-1", "workspace_id": "workspace-1"}],
            usage=usage,
        )

        serialized = result.to_dict()
        self.assertEqual(PipelineStatus.SUCCESS, serialized["status"])
        self.assertEqual(context.mission_id, serialized["mission_id"])
        self.assertEqual("workspace-1", serialized["workspace_id"])
        self.assertEqual(usage, serialized["usage"])
        self.assertIsNotNone(datetime.fromisoformat(result.created_at).utcoffset())

    def test_worker_result_defaults_are_isolated_and_validation_is_safe(self):
        context = ContextBuilder().build(
            Mission.create("Title", "Objective", "user-1", "workspace-1")
        )
        first = WorkerResult.create(PipelineStatus.SUCCESS, "worker", context)
        second = WorkerResult.create(PipelineStatus.SUCCESS, "worker", context)
        first.data["local"] = True
        self.assertEqual({}, second.data)

        sensitive = "private-token-value"
        with self.assertRaises(ValueError) as raised:
            WorkerResult.create(
                "UNKNOWN",
                sensitive,
                context,
            )
        self.assertNotIn(sensitive, str(raised.exception))

    def test_function_worker_executes_fake_and_checks_result_boundaries(self):
        context = ContextBuilder().build(
            Mission.create("Title", "Objective", "user-1", "workspace-1")
        )
        worker = FunctionWorker(
            "fake-worker",
            lambda value: WorkerResult.create(
                PipelineStatus.SUCCESS,
                "fake-worker",
                value,
                data={"handled": True},
            ),
        )

        result = worker.execute(context)

        self.assertIsInstance(worker, BaseWorker)
        self.assertEqual({"handled": True}, result.data)
        mismatched = FunctionWorker(
            "fake-worker",
            lambda value: WorkerResult.create(
                PipelineStatus.SUCCESS, "other-worker", value
            ),
        )
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            mismatched.execute(context)

    def test_function_worker_sanitizes_handler_failures(self):
        context = ContextBuilder().build(
            Mission.create("Title", "Objective", "user-1", "workspace-1")
        )

        def fail(_context):
            raise RuntimeError("raw prompt and api key")

        result = FunctionWorker("fake-worker", fail).execute(context)

        self.assertEqual(PipelineStatus.FAILED, result.status)
        self.assertEqual("WorkerError: RuntimeError", result.error)
        self.assertNotIn("raw prompt", str(result.to_dict()))

    def test_mission_creation_and_serialization(self):
        mission = Mission.create(
            title="Prepare release",
            objective="Produce a validated release candidate",
            requested_by="user-1",
            workspace_id="workspace-1",
            metadata={"priority": "high"},
        )

        self.assertEqual(
            {
                "id",
                "title",
                "objective",
                "requested_by",
                "workspace_id",
                "created_at",
                "metadata",
                "state",
                "locked_by",
                "locked_at",
            },
            set(mission.to_dict()),
        )
        self.assertEqual("workspace-1", mission.workspace_id)
        self.assertEqual({"priority": "high"}, mission.metadata)
        self.assertIsNotNone(datetime.fromisoformat(mission.created_at).utcoffset())

    def test_mission_ids_are_unique(self):
        arguments = {
            "title": "Title",
            "objective": "Objective",
            "requested_by": "user-1",
            "workspace_id": "workspace-1",
        }
        self.assertNotEqual(Mission.create(**arguments).id, Mission.create(**arguments).id)

    def test_mission_metadata_defaults_are_not_shared(self):
        first = Mission.create("One", "Objective", "user-1", "workspace-1")
        second = Mission.create("Two", "Objective", "user-1", "workspace-1")

        first.metadata["local"] = True
        self.assertEqual({}, second.metadata)

    def test_mission_rejects_missing_required_strings_safely(self):
        arguments = {
            "title": "Title",
            "objective": "Objective",
            "requested_by": "user-1",
            "workspace_id": "workspace-1",
        }
        for field_name in arguments:
            with self.subTest(field_name=field_name):
                invalid = dict(arguments)
                invalid[field_name] = " "
                with self.assertRaises(ValueError) as raised:
                    Mission.create(**invalid)
                self.assertNotIn("sensitive-user-input", str(raised.exception))

    def test_mission_direct_construction_requires_timezone_and_copies_metadata(self):
        metadata = {"source": "request"}
        mission = Mission(
            id="mission-1",
            title="Title",
            objective="Objective",
            requested_by="user-1",
            workspace_id="workspace-1",
            created_at="2026-07-28T12:00:00+00:00",
            metadata=metadata,
        )
        metadata["source"] = "changed"

        self.assertEqual("request", mission.metadata["source"])
        self.assertEqual(mission.to_dict(), dict(mission.to_dict()))
        with self.assertRaisesRegex(ValueError, "timezone"):
            Mission(
                id="mission-2",
                title="Title",
                objective="Objective",
                requested_by="user-1",
                workspace_id="workspace-1",
                created_at="2026-07-28T12:00:00",
            )

    def test_mission_rejects_non_dictionary_metadata_without_echoing_it(self):
        secret = "api-key-sensitive-user-input"
        with self.assertRaises(ValueError) as raised:
            Mission.create("Title", "Objective", "user-1", "workspace-1", secret)
        self.assertNotIn(secret, str(raised.exception))

    def test_mission_state_follows_valid_lifecycle(self):
        mission = Mission.create("Title", "Objective", "user-1", "workspace-1")

        running = mission.transition_to(MissionState.IN_PROGRESS)
        completed = running.transition_to(MissionState.COMPLETED)

        self.assertEqual(MissionState.PENDING, mission.state)
        self.assertEqual(MissionState.IN_PROGRESS, running.state)
        self.assertTrue(completed.is_terminal)
        self.assertEqual(MissionState.COMPLETED, completed.to_dict()["state"])

    def test_mission_state_rejects_invalid_and_terminal_transitions(self):
        mission = Mission.create("Title", "Objective", "user-1", "workspace-1")
        with self.assertRaisesRegex(ValueError, "transition"):
            mission.transition_to(MissionState.COMPLETED)
        with self.assertRaisesRegex(ValueError, "invalid mission state"):
            mission.transition_to("UNKNOWN")

        cancelled = mission.transition_to(MissionState.CANCELLED)
        with self.assertRaisesRegex(ValueError, "transition"):
            cancelled.transition_to(MissionState.IN_PROGRESS)

    def test_mission_lock_is_exclusive_and_timezone_aware(self):
        mission = Mission.create("Title", "Objective", "user-1", "workspace-1")
        locked = mission.acquire_lock("worker-1")

        self.assertFalse(mission.is_locked)
        self.assertTrue(locked.is_locked)
        self.assertEqual("worker-1", locked.locked_by)
        self.assertIsNotNone(datetime.fromisoformat(locked.locked_at).utcoffset())
        self.assertIs(locked, locked.acquire_lock("worker-1"))
        with self.assertRaisesRegex(ValueError, "already locked"):
            locked.acquire_lock("worker-2")

    def test_mission_lock_can_only_be_released_by_owner(self):
        locked = Mission.create(
            "Title", "Objective", "user-1", "workspace-1"
        ).acquire_lock("worker-1")

        with self.assertRaisesRegex(ValueError, "another worker"):
            locked.release_lock("worker-2")
        released = locked.release_lock("worker-1")
        self.assertFalse(released.is_locked)
        self.assertIsNone(released.locked_at)
        self.assertIs(released, released.release_lock("worker-1"))

    def test_mission_rejects_partial_or_unsafe_lock_data(self):
        base = {
            "id": "mission-1",
            "title": "Title",
            "objective": "Objective",
            "requested_by": "user-1",
            "workspace_id": "workspace-1",
            "created_at": "2026-07-28T12:00:00+00:00",
        }
        with self.assertRaisesRegex(ValueError, "set together"):
            Mission(**base, locked_by="worker-1")
        with self.assertRaisesRegex(ValueError, "timezone"):
            Mission(
                **base,
                locked_by="worker-1",
                locked_at="2026-07-28T12:00:00",
            )
        sensitive_owner = "worker-private@example.com"
        with self.assertRaises(ValueError) as raised:
            Mission(**base).acquire_lock(sensitive_owner).release_lock("worker-2")
        self.assertNotIn(sensitive_owner, str(raised.exception))

    def test_audit_repository_filters_and_never_records_sensitive_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            service=AuditService(FileAuditRepository(Path(directory)/'audit.json'))
            service.record('user','one','LOGIN_SUCCESS','session','s1',{'password':'x','token':'y','source':'test'})
            service.record('user','two','TASK_CREATED','task','t1')
            self.assertEqual(1,len(service.query('one',action='LOGIN_SUCCESS',limit=1)))
            self.assertEqual({'source':'test'},service.query('one')[0]['metadata'])

    def test_audit_query_cursor_filters_and_pagination(self):
        service=AuditService()
        for item in [('u1','LOGIN_SUCCESS','session','a'),('u2','TASK_CREATED','task','b'),('u1','TASK_CREATED','task','c')]: service.record(item[0],'one',item[1],item[2],item[3])
        query=AuditQueryService(service); first=query.query('one',limit=1,action=['TASK_CREATED'],user_id='u1')
        self.assertEqual(1,first['total']); self.assertIsNone(first['next_cursor'])
        with self.assertRaises(ValueError):query.query('one',cursor='bad')
    def test_refresh_sessions_rotate_and_store_only_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            sessions=SessionService(FileSessionRepository(Path(directory)/'sessions.json'))
            first, token=sessions.create('user')
            rotated=sessions.rotate(token)
            self.assertIsNone(sessions.rotate(token))
            self.assertTrue(rotated)
            self.assertNotIn(token, Path(directory,'sessions.json').read_text())
            self.assertTrue(sessions.revoke(rotated[0]['session_id'],'user'))
            self.assertEqual(2,len(sessions.list('user')))
    def test_login_service_issues_minimal_token_and_rejects_bad_credentials(self):
        users = UserService()
        user = users.create("login@example.com")
        credentials = CredentialService(users)
        credentials.set_password(user["user_id"], "safe-passphrase")
        tokens = SignedAccessTokenProvider(secret="test-secret", expires_in_seconds=60)
        service = LoginService(users, credentials, tokens)

        result = service.login(" LOGIN@example.com ", "safe-passphrase")
        self.assertEqual("bearer", result["token_type"])
        self.assertEqual(user, service.current_user(result["access_token"]))
        with self.assertRaisesRegex(ValueError, "invalid_credentials"):
            service.login("login@example.com", "wrong-password")

    def test_access_token_rejects_expired_and_tampered_values(self):
        now = [100]
        provider = SignedAccessTokenProvider(secret="test-secret", expires_in_seconds=1, clock=lambda: now[0])
        token = provider.issue("user-1")
        self.assertEqual({"user_id": "user-1"}, provider.verify(token))
        now[0] = 101
        self.assertIsNone(provider.verify(token))
        self.assertIsNone(provider.verify(token + "tampered"))
    def test_credentials_store_only_password_hash_and_verify(self):
        users = UserService()
        user = users.create("credential@example.com")
        service = CredentialService(users)
        service.set_password(user["user_id"], "safe-passphrase")

        credential = service.repository.get(user["user_id"])
        self.assertTrue(service.verify_password(user["user_id"], "safe-passphrase"))
        self.assertFalse(service.verify_password(user["user_id"], "wrong-password"))
        self.assertNotIn("safe-passphrase", str(credential))
        self.assertEqual({"user_id", "password_hash"}, set(credential))

    def test_file_credential_repository_reloads_hash_without_plaintext(self):
        with tempfile.TemporaryDirectory() as directory:
            users = UserService()
            user = users.create("credential@example.com")
            file_path = Path(directory) / "credentials.json"
            CredentialService(users, FileCredentialRepository(file_path)).set_password(
                user["user_id"], "safe-passphrase"
            )
            reloaded = CredentialService(users, FileCredentialRepository(file_path))

            self.assertTrue(reloaded.verify_password(user["user_id"], "safe-passphrase"))
            self.assertNotIn("safe-passphrase", file_path.read_text(encoding="utf-8"))
    def test_user_email_normalization_and_duplicate_protection(self):
        service = UserService()
        user = service.create(" A@Example.COM ")
        self.assertEqual("a@example.com", user["email"])
        with self.assertRaises(ValueError): service.create("a@example.com")

    def test_file_user_repository_reloads_user_without_sensitive_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            repository_file = Path(directory) / "users.json"
            created = UserService(FileUserRepository(repository_file)).create("user@example.com")
            reloaded = UserService(FileUserRepository(repository_file)).get(created["user_id"])

        self.assertEqual(created, reloaded)
        self.assertEqual(
            {"user_id", "email", "created_at", "status", "updated_at"},
            set(reloaded),
        )

    def test_workspace_membership_roles_and_last_owner_protection(self):
        users = UserService()
        owner = users.create("owner@example.com")
        member = users.create("member@example.com")
        third = users.create("third@example.com")
        service = WorkspaceMembershipService(WorkspaceService(), users)
        workspace = service.create_workspace("Team", owner["user_id"])
        workspace_id = workspace["workspace_id"]

        added = service.add(workspace_id, member["user_id"], MEMBER)
        self.assertEqual(MEMBER, added["role"])
        self.assertEqual(OWNER, service.list(workspace_id)[0]["role"])
        with self.assertRaises(ValueError):
            service.add(workspace_id, member["user_id"], MEMBER)
        with self.assertRaises(ValueError):
            service.change_role(workspace_id, owner["user_id"], ADMIN)
        with self.assertRaises(ValueError):
            service.remove(workspace_id, owner["user_id"])
        service.change_role(workspace_id, member["user_id"], OWNER)
        service.remove(workspace_id, owner["user_id"])
        self.assertEqual([member["user_id"]], [item["user_id"] for item in service.list(workspace_id)])

    def test_file_membership_repository_reloads_records(self):
        with tempfile.TemporaryDirectory() as directory:
            users = UserService()
            owner = users.create("owner@example.com")
            workspace_service = WorkspaceService()
            membership_file = Path(directory) / "memberships.json"
            created = WorkspaceMembershipService(
                workspace_service,
                users,
                FileWorkspaceMembershipRepository(membership_file),
            ).create_workspace("Persisted", owner["user_id"])
            reloaded = WorkspaceMembershipService(
                workspace_service,
                users,
                FileWorkspaceMembershipRepository(membership_file),
            ).list(created["workspace_id"])

        self.assertEqual(OWNER, reloaded[0]["role"])
    def test_task_preserves_structured_parameters_in_serialized_form(self):
        parameters = {"target_folder": "input", "priority": "high"}
        task = Task("Organize files", parameters=parameters)
        parameters["priority"] = "changed"

        task_data = task.to_dict()

        self.assertEqual("high", task.parameters["priority"])
        self.assertEqual(task.parameters, task_data["parameters"])

    def test_task_serializes_optional_parent_task_relationship(self):
        parent = Task("Plan campaign")
        child = Task("Research audience", parent_task_id=parent.id)

        self.assertEqual(parent.id, child.parent_task_id)
        self.assertEqual(parent.id, child.to_dict()["parent_task_id"])

    def test_workspace_service_persists_default_and_created_workspaces(self):
        service = WorkspaceService(InMemoryWorkspaceRepository())
        created = service.create("Team A")
        self.assertEqual("default", service.get("default")["workspace_id"])
        self.assertEqual(created, service.get(created["workspace_id"]))

    def test_history_and_artifacts_are_filtered_by_workspace(self):
        first = Task("first", workspace_id="one"); second = Task("second", workspace_id="two")
        history = ExecutionHistory(repository=InMemoryExecutionHistoryRepository())
        history.record(first); history.record(second)
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactManager(); path = Path(directory) / "a.txt"; path.write_text("a")
            artifact = artifacts.register_file(path, "TEXT", "Test", workspace_id="one")
            self.assertEqual([artifact], artifacts.list(workspace_id="one"))
            self.assertIsNone(artifacts.get(artifact["artifact_id"], workspace_id="two"))
        self.assertEqual(["first"], [r["task"] for r in history.query(workspace_id="one")])


class ApplicationServiceTests(PipelineTestCase):
    def test_service_submits_and_executes_task_with_injected_dependencies(self):
        class SuccessfulManager:
            def handle(_, task):
                task.task_type = "FILE"
                task.pipeline = "Test Pipeline"
                return PipelineResult(
                    PipelineStatus.SUCCESS,
                    task.pipeline,
                    task,
                    task.task_type,
                ).to_dict()

        artifacts = ArtifactManager(InMemoryArtifactRepository())
        service = AutomationService(
            SuccessfulManager(),
            history=self.history,
            artifact_manager=artifacts,
        )
        task = service.submit_text(
            "service task",
            parameters={"source": "test"},
            max_retries=1,
        )
        completed = service.run_all()

        self.assertEqual([task], completed)
        self.assertEqual(PipelineStatus.SUCCESS, task.status)
        self.assertIs(self.history, service.task_queue.history)
        self.assertIs(artifacts, service.artifact_manager)
        self.assertEqual(task.id, self.history.get_all()[0]["task_id"])


class TaskQueryServiceTests(PipelineTestCase):
    def test_task_query_returns_serializable_task_history_usage_and_artifacts(self):
        artifact_file = self.root / "output.txt"
        artifact_file.write_text("artifact", encoding="utf-8")
        artifacts = ArtifactManager(InMemoryArtifactRepository())
        artifact = artifacts.register_file(artifact_file, "TEXT", "Test Pipeline")
        task = self.task("query task", "CONTENT")
        task.pipeline = "Test Pipeline"
        task.complete(
            PipelineResult(
                PipelineStatus.SUCCESS,
                task.pipeline,
                task,
                task.task_type,
                data={
                    "provider_usage": {
                        "provider": "mock",
                        "total_tokens": 3,
                        "estimated_cost": 0.0,
                    }
                },
                artifacts=[artifact],
            ).to_dict()
        )
        self.history.record(task)
        service = TaskQueryService(self.history, artifacts, {task.id: task}.get)

        response = service.get(task.id)

        self.assertTrue(response["found"])
        self.assertIsInstance(response["task"], dict)
        self.assertIsNot(task, response["task"])
        self.assertEqual(task.id, response["history"]["task_id"])
        self.assertEqual("mock", response["usage"]["provider"])
        self.assertEqual([artifact], response["artifacts"])
        self.assertEqual([task.id], [item["task_id"] for item in service.list(status="SUCCESS", pipeline="Test Pipeline")])

    def test_task_query_is_safe_for_missing_task_and_repository_implementations(self):
        task = self.task("persisted query", "FILE")
        task.pipeline = "Test Pipeline"
        task.complete(PipelineResult(PipelineStatus.SUCCESS, task.pipeline, task, task.task_type).to_dict())
        histories = [
            ExecutionHistory(repository=InMemoryExecutionHistoryRepository()),
            ExecutionHistory(self.root / "query-history.json"),
        ]
        responses = []
        for history in histories:
            history.record(task)
            service = TaskQueryService(history, ArtifactManager())
            responses.append(service.get(task.id))
            self.assertFalse(service.get("missing-task")["found"])

        self.assertEqual(responses[0], responses[1])


class ApiContractTests(PipelineTestCase):
    def test_task_api_creates_and_queries_serializable_tasks_through_services(self):
        class SuccessfulManager:
            def handle(_, task):
                task.task_type = "CONTENT"
                task.pipeline = "Test Pipeline"
                return PipelineResult(PipelineStatus.SUCCESS, task.pipeline, task, task.task_type).to_dict()

        artifacts = ArtifactManager()
        automation = AutomationService(SuccessfulManager(), history=self.history, artifact_manager=artifacts)
        queries = TaskQueryService(self.history, artifacts, automation._get_task_for_query)
        api = TaskApi(automation, queries)

        created = api.create_task({"task_text": "Create through API", "parameters": {"format": "text"}})
        automation.run_all()
        fetched = api.get_task(created["task_id"])
        listed = api.list_tasks({"status": "SUCCESS", "pipeline": "Test Pipeline"})

        self.assertTrue(created["found"])
        self.assertEqual(PipelineStatus.QUEUED, created["status"])
        self.assertEqual(PipelineStatus.SUCCESS, fetched["status"])
        self.assertEqual([created["task_id"]], [item["task_id"] for item in listed["items"]])
        self.assertFalse(api.get_task("unknown-task")["found"])

    def test_api_contracts_validate_payloads_and_preserve_query_options(self):
        request = CreateTaskRequest.from_dict({"task_text": "  Plan work  ", "max_retries": 1})
        filters = ListTasksRequest.from_dict({"status": "SUCCESS", "limit": 3, "offset": 1}).to_filters()

        self.assertEqual("Plan work", request.task_text)
        self.assertEqual({"status": "SUCCESS", "limit": 3, "offset": 1}, filters)
        with self.assertRaises(ValueError):
            CreateTaskRequest.from_dict({"task_text": ""})
        with self.assertRaises(TypeError):
            CreateTaskRequest.from_dict("invalid")


class FastApiFoundationTests(PipelineTestCase):
    def test_application_factory_exposes_health_with_injected_services(self):
        class UnusedManager:
            def handle(_, task):
                raise AssertionError("health check must not execute tasks")

        service = AutomationService(UnusedManager(), history=self.history)
        app = create_app(automation_service=service)

        response = TestClient(app).get("/health")

        self.assertEqual(200, response.status_code)
        self.assertEqual({"status": "ok"}, response.json())
        self.assertIs(service, app.state.automation_service)

    def test_application_factory_returns_safe_global_error_response(self):
        service = AutomationService(object(), history=self.history)
        app = create_app(automation_service=service)

        @app.get("/test-error")
        def test_error():
            raise RuntimeError("sensitive internal detail")

        response = TestClient(app, raise_server_exceptions=False).get("/test-error")

        self.assertEqual(500, response.status_code)
        self.assertEqual("internal_error", response.json()["error"]["code"])
        self.assertTrue(response.json()["error"]["correlation_id"])
        self.assertNotIn("sensitive internal detail", response.text)


class TaskApiEndpointTests(PipelineTestCase):
    def _client(self):
        artifact_file = self.root / "api-output.txt"
        artifact_file.write_text("result", encoding="utf-8")
        artifacts = ArtifactManager()
        artifact = artifacts.register_file(artifact_file, "TEXT", "API Test Pipeline")

        class SuccessfulManager:
            def handle(_, task):
                task.task_type = "CONTENT"
                task.pipeline = "API Test Pipeline"
                return PipelineResult(
                    PipelineStatus.SUCCESS,
                    task.pipeline,
                    task,
                    task.task_type,
                    data={"provider_usage": {"provider": "mock", "total_tokens": 4}},
                    artifacts=[artifact],
                ).to_dict()

        service = AutomationService(SuccessfulManager(), history=self.history, artifact_manager=artifacts)
        return TestClient(create_app(automation_service=service)), service

    def test_task_endpoints_create_get_and_list_result_metadata(self):
        client, service = self._client()

        created = client.post("/tasks", json={"task_text": "Create API task"})
        task_id = created.json()["task_id"]
        service.run_all()
        fetched = client.get(f"/tasks/{task_id}")
        listed = client.get("/tasks", params={"status": "SUCCESS", "pipeline": "API Test Pipeline"})

        self.assertEqual(201, created.status_code)
        self.assertEqual(PipelineStatus.QUEUED, created.json()["status"])
        self.assertEqual(200, fetched.status_code)
        self.assertEqual(PipelineStatus.SUCCESS, fetched.json()["status"])
        self.assertEqual("mock", fetched.json()["usage"]["provider"])
        self.assertEqual(1, len(fetched.json()["artifacts"]))
        self.assertEqual([task_id], [item["task_id"] for item in listed.json()["items"]])

    def test_task_endpoints_return_safe_4xx_errors(self):
        client, _ = self._client()

        invalid = client.post("/tasks", json={"task_text": ""})
        missing = client.get("/tasks/missing-task")

        self.assertEqual(400, invalid.status_code)
        self.assertEqual("invalid_request", invalid.json()["error"]["code"])
        self.assertTrue(invalid.json()["error"]["correlation_id"])
        self.assertEqual(404, missing.status_code)
        self.assertEqual("task_not_found", missing.json()["error"]["code"])
        self.assertEqual("Task not found", missing.json()["error"]["message"])
        self.assertTrue(missing.json()["error"]["correlation_id"])

    def test_workspace_api_and_workspace_task_creation(self):
        client, _ = self._client()
        created = client.post("/workspaces", json={"name": "API Workspace"})
        workspace_id = created.json()["workspace_id"]
        task = client.post("/tasks", json={"task_text": "workspace task", "workspace_id": workspace_id})
        missing = client.post("/tasks", json={"task_text": "bad", "workspace_id": "missing"})
        self.assertEqual(201, created.status_code)
        self.assertTrue(any(item["workspace_id"] == workspace_id for item in client.get("/workspaces").json()["items"]))
        self.assertEqual(workspace_id, client.get(f"/workspaces/{workspace_id}").json()["workspace_id"])
        self.assertEqual(workspace_id, task.json()["task"]["workspace_id"])
        self.assertEqual(404, missing.status_code)

    def test_user_and_membership_api_preserve_roles_and_workspace_boundary(self):
        client, _ = self._client()
        owner = client.post("/users", json={"email": " Owner@Example.com "})
        member = client.post("/users", json={"email": "member@example.com"})
        duplicate = client.post("/users", json={"email": "owner@example.com"})
        workspace = client.post("/workspaces", json={"name": "Members", "owner_user_id": owner.json()["user_id"]})
        workspace_id = workspace.json()["workspace_id"]
        added = client.post(f"/workspaces/{workspace_id}/members", json={"user_id": member.json()["user_id"], "role": "MEMBER"})
        members = client.get(f"/workspaces/{workspace_id}/members")
        promoted = client.patch(f"/workspaces/{workspace_id}/members/{member.json()['user_id']}", json={"role": "OWNER"})
        removed = client.delete(f"/workspaces/{workspace_id}/members/{owner.json()['user_id']}")
        task = client.post("/tasks", json={"task_text": "isolated task", "workspace_id": workspace_id})
        hidden = client.get(f"/tasks/{task.json()['task_id']}", params={"workspace_id": "default"})

        self.assertEqual("owner@example.com", owner.json()["email"])
        self.assertEqual(409, duplicate.status_code)
        self.assertEqual(201, added.status_code)
        self.assertEqual(2, len(members.json()["items"]))
        self.assertEqual(200, promoted.status_code)
        self.assertEqual(204, removed.status_code)
        self.assertEqual(404, hidden.status_code)

    def test_membership_api_returns_safe_conflict_and_not_found_responses(self):
        client, _ = self._client()
        owner = client.post("/users", json={"email": "owner@example.com"}).json()
        workspace = client.post("/workspaces", json={"name": "Members", "owner_user_id": owner["user_id"]}).json()
        workspace_id = workspace["workspace_id"]
        duplicate = client.post(f"/workspaces/{workspace_id}/members", json={"user_id": owner["user_id"], "role": "OWNER"})
        last_owner = client.delete(f"/workspaces/{workspace_id}/members/{owner['user_id']}")
        unknown_user = client.get("/users/missing")
        unknown_workspace = client.get("/workspaces/missing/members")

        self.assertEqual(409, duplicate.status_code)
        self.assertEqual(409, last_owner.status_code)
        self.assertEqual(404, unknown_user.status_code)
        self.assertEqual(404, unknown_workspace.status_code)

    def test_authenticated_api_enforces_bearer_and_workspace_roles(self):
        users = UserService()
        owner = users.create("owner@example.com")
        member = users.create("member@example.com")
        third = users.create("third@example.com")
        credentials = CredentialService(users)
        credentials.set_password(owner["user_id"], "safe-passphrase")
        credentials.set_password(member["user_id"], "safe-passphrase")
        workspaces = WorkspaceService()
        memberships = WorkspaceMembershipService(workspaces, users)
        workspace = memberships.create_workspace("Secure", owner["user_id"])
        memberships.add(workspace["workspace_id"], member["user_id"], MEMBER)
        app = create_app(
            automation_service=self._client()[1], workspace_service=workspaces,
            user_service=users, membership_service=memberships,
            credential_service=credentials, auth_required=True,
        )
        client = TestClient(app)
        owner_token = client.post("/auth/login", json={"email": "owner@example.com", "password": "safe-passphrase"}).json()["access_token"]
        member_token = client.post("/auth/login", json={"email": "member@example.com", "password": "safe-passphrase"}).json()["access_token"]
        headers = {"Authorization": f"Bearer {member_token}"}
        workspace_id = workspace["workspace_id"]

        self.assertEqual(401, client.get("/users/me").status_code)
        self.assertEqual(member["user_id"], client.get("/users/me", headers=headers).json()["user_id"])
        self.assertEqual(403, client.post(f"/workspaces/{workspace_id}/members", headers=headers, json={"user_id": owner["user_id"], "role": "MEMBER"}).status_code)
        self.assertEqual(200, client.get(f"/workspaces/{workspace_id}", headers=headers).status_code)
        self.assertEqual(201, client.post(f"/workspaces/{workspace_id}/members", headers={"Authorization": f"Bearer {owner_token}"}, json={"user_id": third["user_id"], "role": "MEMBER"}).status_code)

    def test_session_api_refresh_logout_and_user_isolation(self):
        users = UserService(); first = users.create("first@example.com"); second = users.create("second@example.com")
        credentials = CredentialService(users)
        for user in (first, second): credentials.set_password(user["user_id"], "safe-passphrase")
        sessions = SessionService()
        app = create_app(automation_service=self._client()[1], user_service=users, credential_service=credentials, login_service=LoginService(users, credentials, session_service=sessions), session_service=sessions, auth_required=True)
        client = TestClient(app)
        login = client.post("/auth/login", json={"email":"first@example.com","password":"safe-passphrase"}).json()
        refreshed = client.post("/auth/refresh", json={"refresh_token":login["refresh_token"]})
        reused = client.post("/auth/refresh", json={"refresh_token":login["refresh_token"]})
        headers={"Authorization":f"Bearer {refreshed.json()['access_token']}"}
        listed=client.get("/auth/sessions",headers=headers)
        deleted=client.delete(f"/auth/sessions/{refreshed.json()['session_id']}",headers=headers)
        self.assertEqual(200, refreshed.status_code); self.assertEqual(401,reused.status_code)
        self.assertNotIn("refresh_token_hash", listed.text); self.assertEqual(204,deleted.status_code)

    def test_api_correlation_id_header_and_safe_error_body(self):
        client,_=self._client()
        accepted=client.get('/health',headers={'X-Correlation-ID':'trace_12345678'})
        invalid=client.get('/tasks/missing',headers={'X-Correlation-ID':'bad value!'})
        self.assertEqual('trace_12345678',accepted.headers['X-Correlation-ID'])
        self.assertNotEqual('bad value!',invalid.headers['X-Correlation-ID'])
        self.assertEqual(invalid.headers['X-Correlation-ID'],invalid.json()['error']['correlation_id'])


class TaskControlApiEndpointTests(PipelineTestCase):
    def _client_and_service(self):
        class SuccessfulManager:
            def handle(_, task):
                task.task_type = "FILE"
                task.pipeline = "Control Test Pipeline"
                return PipelineResult(PipelineStatus.SUCCESS, task.pipeline, task, task.task_type).to_dict()

        service = AutomationService(SuccessfulManager(), history=self.history)
        return TestClient(create_app(automation_service=service)), service

    def test_cancel_endpoint_updates_single_history_record_and_rejects_terminal_control(self):
        client, service = self._client_and_service()
        created = client.post("/tasks", json={"task_text": "cancel through API"})
        task_id = created.json()["task_id"]

        cancelled = client.post(f"/tasks/{task_id}/cancel")
        repeated = client.post(f"/tasks/{task_id}/cancel")
        completed_task_id = client.post("/tasks", json={"task_text": "complete through API"}).json()["task_id"]
        service.run_all()
        completed_cancel = client.post(f"/tasks/{completed_task_id}/cancel")

        self.assertEqual(200, cancelled.status_code)
        self.assertEqual(PipelineStatus.CANCELLED, cancelled.json()["status"])
        self.assertEqual(
            1,
            len([record for record in self.history.get_all() if record["task_id"] == task_id]),
        )
        self.assertEqual(PipelineStatus.CANCELLED, self.history.get_all()[0]["status"])
        self.assertEqual(409, repeated.status_code)
        self.assertEqual(409, completed_cancel.status_code)
        self.assertNotIn("cancel through API", repeated.text)

    def test_retry_endpoint_requeues_retryable_failure_once_and_rejects_timeout_cancellation(self):
        client, service = self._client_and_service()
        created = client.post("/tasks", json={"task_text": "retry through API", "max_retries": 1})
        task_id = created.json()["task_id"]
        task = service._get_task_for_query(task_id)
        task.task_type = "FILE"
        task.pipeline = "Control Test Pipeline"
        task.fail(PipelineResult(PipelineStatus.FAILED, task.pipeline, task, task.task_type, error="TaskError: TimeoutError").to_dict())
        task.set_error_type("TimeoutError")
        self.history.record(task)

        retried = client.post(f"/tasks/{task_id}/retry")
        duplicate_retry = client.post(f"/tasks/{task_id}/retry")
        service.task_queue.skip(task)
        timed_out_task = client.post("/tasks", json={"task_text": "timeout task"}).json()["task_id"]
        timeout_task = service._get_task_for_query(timed_out_task)
        timeout_task.timeout()
        self.history.record(timeout_task)
        timed_out_cancel = client.post(f"/tasks/{timed_out_task}/cancel")

        self.assertEqual(200, retried.status_code)
        self.assertEqual(PipelineStatus.QUEUED, retried.json()["status"])
        self.assertEqual(
            1,
            len([record for record in self.history.get_all() if record["task_id"] == task_id]),
        )
        self.assertEqual(409, duplicate_retry.status_code)
        self.assertEqual(409, timed_out_cancel.status_code)


class ProviderTests(unittest.TestCase):
    def test_mock_provider_returns_standard_response_with_usage(self):
        response = MockProvider().generate(
            ProviderRequest(prompt="Create a concise outline", model="mock-small")
        )

        self.assertEqual("mock", response.provider)
        self.assertEqual("mock-small", response.model)
        self.assertTrue(response.output_text)
        self.assertEqual(0, response.usage.estimated_cost_usd)
        self.assertGreater(response.usage.total_tokens, 0)

    def test_provider_factory_uses_mock_by_default_and_rejects_unknown_provider(self):
        self.assertIsInstance(ProviderFactory.from_environment({}).provider, MockProvider)
        with self.assertRaisesRegex(ValueError, "Unsupported AI provider"):
            ProviderFactory.from_environment({"AICOMPANY_PROVIDER": "unknown"})


class ArtifactManagerTests(PipelineTestCase):
    def test_artifact_manager_registers_and_queries_file_metadata(self):
        artifact_file = self.root / "output.txt"
        artifact_file.write_text("artifact content", encoding="utf-8")
        manager = ArtifactManager(repository=InMemoryArtifactRepository())

        artifact = manager.register_file(
            artifact_file,
            artifact_type="TEXT",
            producer_pipeline="Test Pipeline",
        )

        self.assertTrue(
            set(ArtifactManager.METADATA_FIELDS).issubset(artifact)
        )
        self.assertTrue(artifact["artifact_id"])
        self.assertEqual("TEXT", artifact["artifact_type"])
        self.assertEqual("output.txt", artifact["filename"])
        self.assertEqual(len("artifact content".encode("utf-8")), artifact["size"])
        self.assertEqual("Test Pipeline", artifact["producer_pipeline"])
        self.assertEqual("default", artifact["workspace_id"])
        self.assertEqual(artifact, manager.get(artifact["artifact_id"]))
        self.assertEqual([artifact], manager.list())

        result = PipelineResult(
            PipelineStatus.SUCCESS,
            "Test Pipeline",
            self.task("artifact task", "FILE"),
            artifacts=[artifact],
        ).to_dict()
        self.assertEqual([artifact], result["artifacts"])

    def test_artifact_metadata_is_preserved_in_execution_history(self):
        artifact_file = self.root / "history_output.txt"
        artifact_file.write_text("history artifact", encoding="utf-8")
        artifact = ArtifactManager().register_file(
            artifact_file, "TEXT", "Test Pipeline"
        )

        class ArtifactManagerBackedManager:
            def handle(_, task):
                task.task_type = "FILE"
                task.pipeline = "Test Pipeline"
                return PipelineResult(
                    PipelineStatus.SUCCESS,
                    task.pipeline,
                    task,
                    artifacts=[artifact],
                ).to_dict()

        queue = TaskQueue(history=self.history)
        queue.add(self.task("artifact history", "FILE"))
        TaskWorker(queue, ArtifactManagerBackedManager(), self.history).run_once()

        self.assertEqual(
            [artifact], self.history.get_all()[0]["result"]["artifacts"]
        )

    def test_file_artifact_repository_reloads_records_and_handles_missing_data(self):
        repository_file = self.root / "artifacts.json"
        manager = ArtifactManager(repository=FileArtifactRepository(repository_file))
        artifact_file = self.root / "report.txt"
        artifact_file.write_text("report", encoding="utf-8")
        artifact = manager.register_file(artifact_file, "TEXT", "Test Pipeline")

        reloaded = ArtifactManager(repository=FileArtifactRepository(repository_file))
        self.assertEqual(artifact, reloaded.get(artifact["artifact_id"]))
        (self.root / "broken_artifacts.json").write_text("{", encoding="utf-8")
        self.assertEqual(
            [],
            ArtifactManager(
                repository=FileArtifactRepository(self.root / "broken_artifacts.json")
            ).list(),
        )


class GoalTaskPlannerTests(unittest.TestCase):
    def test_planner_creates_validated_executable_child_tasks(self):
        registry = PipelineRegistry()
        registry.register("TEST", TestPipeline(), capabilities=("test_execution",))
        parent = Task("Prepare launch")

        children = GoalTaskPlanner(registry).create_subtasks(
            parent,
            [
                {
                    "task_text": "Prepare deliverable",
                    "task_type": "TEST",
                    "parameters": {"priority": "high"},
                }
            ],
        )

        self.assertEqual(1, len(children))
        self.assertEqual(parent.id, children[0].parent_task_id)
        self.assertEqual("TEST", children[0].task_type)
        self.assertEqual({"priority": "high"}, children[0].parameters)

    def test_planner_rejects_unregistered_task_type(self):
        with self.assertRaisesRegex(ValueError, "not registered"):
            GoalTaskPlanner(PipelineRegistry()).create_subtasks(
                Task("Prepare launch"),
                [{"task_text": "Unknown work", "task_type": "UNKNOWN"}],
            )


class FilePipelineTests(PipelineTestCase):
    def test_all_artifact_producing_pipelines_register_queryable_artifacts(self):
        artifact_manager = ArtifactManager()
        test_files = self.root / "artifact_files"
        test_files.mkdir()
        (test_files / "photo.jpg").write_text("image", encoding="utf-8")

        pipelines = [
            (AIPipeline(base_folder=test_files, artifact_manager=artifact_manager), self.task("Organize files", "FILE")),
            (MusicPipeline(music_root=self.root / "music", artifact_manager=artifact_manager), self.task("Create a song", "MUSIC")),
            (ContentPipeline(content_root=self.root / "content", artifact_manager=artifact_manager), self.task("Create a video", "CONTENT")),
            (ResearchPipeline(research_root=self.root / "research", artifact_manager=artifact_manager), self.task("Research a topic", "RESEARCH")),
        ]

        with patch("scripts.file_manager.log"), patch("scripts.report_generator.log"):
            for pipeline, task in pipelines:
                result = pipeline.run(task)
                self.assertEqual(PipelineStatus.SUCCESS, result["status"])
                self.assertTrue(result["artifacts"])
                self.assertEqual(result["artifacts"], result["data"]["artifacts"])
                for artifact in result["artifacts"]:
                    stored = artifact_manager.get(artifact["artifact_id"])
                    self.assertTrue(
                        all(stored.get(key) == value for key, value in artifact.items())
                    )
                    self.assertEqual(pipeline.name, artifact["producer_pipeline"])

    def test_file_pipeline_organizes_files_and_returns_common_result(self):
        test_files = self.root / "test_files"
        test_files.mkdir()
        (test_files / "photo.jpg").write_text("image", encoding="utf-8")
        (test_files / "notes.txt").write_text("notes", encoding="utf-8")

        with patch("scripts.file_manager.log"), patch("scripts.report_generator.log"):
            result = AIPipeline(base_folder=test_files).run(
                self.task("Organize files", "FILE")
            )

        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertTrue(RESULT_KEYS.issubset(result))
        self.assertTrue((test_files / "Images" / "photo.jpg").exists())
        self.assertTrue((test_files / "notes.txt").exists())
        self.assertCountEqual(["SUCCESS", "SKIPPED"], result["data"]["result"])

    def test_file_pipeline_passes_planner_output_to_executor(self):
        test_files = self.root / "planned_files"
        test_files.mkdir()

        class RecordingExecutor:
            def __init__(self):
                self.plan = None

            def execute(self, plan):
                self.plan = plan
                return []

        executor = RecordingExecutor()
        with patch("scripts.report_generator.log"):
            result = AIPipeline(base_folder=test_files, executor=executor).run(
                self.task("Organize files", "FILE")
            )

        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertEqual(str(test_files), executor.plan["target_folder"])
        self.assertEqual(executor.plan, result["data"]["plan"])


class PipelineRegistryTests(unittest.TestCase):
    def test_registry_accepts_base_pipeline_and_rejects_invalid_registrations(self):
        registry = PipelineRegistry()
        pipeline = TestPipeline()

        registry.register("TEST", pipeline)

        self.assertIs(pipeline, registry.get("TEST"))
        with self.assertRaises(ValueError):
            registry.register("", TestPipeline())
        with self.assertRaises(TypeError):
            registry.register("INVALID", object())
        with self.assertRaises(ValueError):
            registry.register("TEST", TestPipeline())

    def test_registry_exposes_registered_pipeline_capabilities(self):
        registry = PipelineRegistry()
        registry.register(
            "CONTENT",
            TestPipeline(),
            capabilities=("project_creation", "artifact_generation"),
        )

        capability = registry.get_capability("CONTENT")

        self.assertEqual("CONTENT", capability["task_type"])
        self.assertEqual("Test Pipeline", capability["pipeline"])
        self.assertEqual(
            ["project_creation", "artifact_generation"], capability["capabilities"]
        )
        self.assertEqual([capability], registry.list_capabilities())


class MusicPipelineTests(PipelineTestCase):
    @staticmethod
    def music_task(text="Create a song", workspace_id="default", mission_id=None):
        task = Task(
            text,
            workspace_id=workspace_id,
            parameters={"mission_id": mission_id} if mission_id else None,
        )
        task.task_type = "MUSIC"
        return task

    def test_fake_music_provider_uses_standard_contract(self):
        output = self.root / "provider"
        result = FakeMusicProvider().generate_music(
            MusicGenerationRequest(
                prompt="Create a safe test song",
                workspace_id="workspace-1",
                mission_id="mission-1",
                output_directory=str(output),
                timeout_seconds=5,
            )
        )

        self.assertEqual("fake-music", result.provider)
        self.assertTrue((output / result.artifacts[0].filename).is_file())
        self.assertNotIn("path", result.artifacts[0].to_dict())
        self.assertGreater(result.usage.total_tokens, 0)

    def test_music_provider_factory_and_selection_support_dependency_injection(self):
        selection = ProviderFactory.music_from_environment(
            {
                "AICOMPANY_MUSIC_PROVIDER": "fake",
                "AICOMPANY_MUSIC_PROVIDER_MODEL": "fake-v2",
                "AICOMPANY_MUSIC_PROVIDER_TIMEOUT": "4",
            }
        )
        result = MusicPipeline(
            music_root=self.root / "music",
            provider_selection=selection,
        ).run(self.music_task())

        self.assertIsInstance(selection.provider, FakeMusicProvider)
        self.assertEqual("fake-v2", result["data"]["model"])
        self.assertEqual(PipelineStatus.SUCCESS, result["status"])

    def test_music_pipeline_provider_usage_and_failures_are_safe(self):
        task = self.music_task("Create a provider-backed song")
        result = MusicPipeline(music_root=self.root / "music", provider=MockProvider()).run(task)
        self.assertEqual("mock", result["data"]["provider_usage"]["provider"])

        class PartialMusicProvider(MusicProvider):
            name = "partial"

            def generate_music(self, request):
                path = Path(request.output_directory) / "partial.txt"
                path.write_text("partial", encoding="utf-8")
                return MusicGenerationResult(
                    self.name,
                    "local",
                    (GeneratedMusicArtifact(path.name, "text/plain", str(path)),),
                    SimpleNamespace(input_tokens=2),
                )

        class MissingUsageProvider(PartialMusicProvider):
            name = "missing"

            def generate_music(self, request):
                generated = super().generate_music(request)
                return MusicGenerationResult(
                    self.name, generated.model, generated.artifacts, None
                )

        partial = MusicPipeline(
            music_root=self.root / "partial", provider=PartialMusicProvider()
        ).run(task)
        missing = MusicPipeline(
            music_root=self.root / "missing", provider=MissingUsageProvider()
        ).run(task)
        self.assertEqual(2, partial["data"]["provider_usage"]["total_tokens"])
        self.assertEqual(0, missing["data"]["provider_usage"]["total_tokens"])

        class TimeoutMusicProvider(MusicProvider):
            name = "timeout"

            def generate_music(self, request):
                raise TimeoutError("private prompt")

        class ErrorMusicProvider(MusicProvider):
            name = "error"

            def generate_music(self, request):
                raise RuntimeError("provider api key")

        timed_out = MusicPipeline(
            music_root=self.root / "timeout", provider=TimeoutMusicProvider()
        ).run(task)
        failed = MusicPipeline(
            music_root=self.root / "failed", provider=ErrorMusicProvider()
        ).run(task)
        self.assertEqual(PipelineStatus.TIMED_OUT, timed_out["status"])
        self.assertEqual("ProviderError: TimeoutError", timed_out["error"])
        self.assertEqual(PipelineStatus.FAILED, failed["status"])
        self.assertEqual("ProviderError: RuntimeError", failed["error"])
        self.assertNotIn("api key", str(failed))

    def test_music_pipeline_creates_complete_project_in_temp_directory(self):
        artifact_manager = ArtifactManager(InMemoryArtifactRepository())
        prompt = "Create a private song request"
        result = MusicPipeline(
            music_root=self.root / "music",
            artifact_manager=artifact_manager,
        ).run(
            self.music_task(prompt, "workspace-1", "mission-1")
        )

        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertTrue(RESULT_KEYS.issubset(result))
        self.assertEqual("workspace-1", result["data"]["workspace_id"])
        self.assertEqual("mission-1", result["data"]["mission_id"])
        self.assertTrue(result["artifacts"])
        self.assertTrue(
            all(item["workspace_id"] == "workspace-1" for item in result["artifacts"])
        )
        self.assertNotIn(str(self.root), str(result))
        self.assertNotIn(prompt, str(result))
        self.assertTrue(artifact_manager.list("workspace-1"))
        self.assertFalse(artifact_manager.list("workspace-2"))

    def test_music_history_is_safe_scoped_and_history_failures_do_not_fail_generation(self):
        history = ExecutionHistory(repository=InMemoryExecutionHistoryRepository())
        prompt = "private original music prompt"
        result = MusicPipeline(
            music_root=self.root / "history",
            execution_history=history,
        ).run(self.music_task(prompt, "workspace-1", "mission-1"))
        record = history.query(workspace_id="workspace-1")[0]

        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertEqual("mission-1", record["mission_id"])
        self.assertEqual("fake-music", record["result"]["provider"])
        self.assertTrue(record["result"]["artifacts"])
        self.assertNotIn(prompt, str(record))
        self.assertNotIn(str(self.root), str(record))
        self.assertEqual([], history.query(workspace_id="workspace-2"))

        completed_task = self.music_task(prompt, "workspace-1", "mission-1")
        completed_result = MusicPipeline(
            music_root=self.root / "history-upsert"
        ).run(completed_task)
        completed_task.pipeline = completed_result["pipeline"]
        completed_task.start()
        completed_task.complete(completed_result)
        history.record(completed_task)
        updated_record = history.query(workspace_id="workspace-1")[0]
        self.assertEqual("mission-1", updated_record["mission_id"])
        self.assertNotIn(prompt, str(updated_record))

        failing_history = SimpleNamespace(
            record_music=lambda task, value: (_ for _ in ()).throw(
                OSError("private history path")
            )
        )
        safe_result = MusicPipeline(
            music_root=self.root / "history-failure",
            execution_history=failing_history,
        ).run(self.music_task(prompt))
        self.assertEqual(PipelineStatus.SUCCESS, safe_result["status"])


class HistoryPipelineTests(PipelineTestCase):
    def test_history_analyzer_aggregates_query_results_and_provider_usage(self):
        records = [
            {
                "task_id": "1", "status": "SUCCESS", "pipeline": "File Pipeline",
                "task_type": "FILE", "completed_at": "2026-01-01T10:00:00",
                "result": {"data": {}},
            },
            {
                "task_id": "2", "status": "FAILED", "pipeline": "Content Pipeline",
                "task_type": "CONTENT", "completed_at": "2026-01-02T10:00:00",
                "result": {"data": {"provider_usage": {
                    "provider": "mock", "model": "mock-small", "input_tokens": 10,
                    "output_tokens": 5, "total_tokens": 15, "estimated_cost_usd": 0.1,
                }}},
            },
            {
                "task_id": "3", "status": "SKIPPED", "pipeline": "Music Pipeline",
                "task_type": "MUSIC", "completed_at": "2026-01-03T10:00:00",
                "result": {"data": {"provider_usage": None}},
            },
            {
                "task_id": "4", "status": "SUCCESS", "pipeline": "Music Pipeline",
                "task_type": "MUSIC", "completed_at": "2026-01-04T10:00:00",
                "result": {"data": {"provider_usage": {
                    "provider": "mock", "model": "mock-small", "input_tokens": 20,
                    "output_tokens": 8, "total_tokens": 28, "estimated_cost_usd": 0.2,
                }}},
            },
            {
                "task_id": "5", "status": "SUCCESS", "pipeline": "Music Pipeline",
                "task_type": "MUSIC", "completed_at": "2026-01-05T10:00:00",
                "result": {"data": {"provider_usage": {
                    "provider": "partial", "model": "local", "input_tokens": 4,
                }}},
            },
        ]
        history = ExecutionHistory(repository=InMemoryExecutionHistoryRepository(records))
        analysis = HistoryAnalyzer(history).analyze()["analysis"]

        self.assertEqual(5, analysis["total_executions"])
        self.assertEqual(3, analysis["successful"])
        self.assertEqual(1, analysis["failed"])
        self.assertEqual(1, analysis["skipped"])
        self.assertEqual(60.0, analysis["success_rate"])
        self.assertEqual({"SUCCESS": 3, "FAILED": 1, "SKIPPED": 1}, analysis["status_distribution"])
        self.assertEqual({"Music Pipeline": 3, "Content Pipeline": 1, "File Pipeline": 1}, analysis["pipeline_distribution"])
        self.assertEqual({"MUSIC": 3, "CONTENT": 1, "FILE": 1}, analysis["task_type_distribution"])
        self.assertEqual({"mock": 2, "partial": 1}, analysis["provider_usage"]["provider_distribution"])
        self.assertEqual(34, analysis["provider_usage"]["input_tokens"])
        self.assertEqual(13, analysis["provider_usage"]["output_tokens"])
        self.assertEqual(43, analysis["provider_usage"]["total_tokens"])
        self.assertEqual(0.3, analysis["provider_usage"]["estimated_cost"])

        filtered = HistoryAnalyzer(history).analyze(task_type="MUSIC", status="SUCCESS")["analysis"]
        self.assertEqual(2, filtered["total_executions"])
        self.assertEqual({"MUSIC": 2}, filtered["task_type_distribution"])
        self.assertEqual(24, filtered["provider_usage"]["input_tokens"])

        json_history = ExecutionHistory(self.root / "analytics_history.json")
        json_history.records = list(records)
        json_history.save()
        self.assertEqual(
            analysis,
            HistoryAnalyzer(ExecutionHistory(self.root / "analytics_history.json")).analyze()["analysis"],
        )

    def test_history_analyzer_returns_empty_analysis_for_empty_history(self):
        result = HistoryAnalyzer(
            ExecutionHistory(repository=InMemoryExecutionHistoryRepository())
        ).analyze(status="SUCCESS")

        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertEqual({}, result["analysis"])

    def test_execution_history_query_filters_sorting_and_pagination(self):
        records = [
            {"task_id": "1", "status": "SUCCESS", "pipeline": "Music Pipeline", "task_type": "MUSIC", "completed_at": "2026-01-01T10:00:00"},
            {"task_id": "2", "status": "FAILED", "pipeline": "Content Pipeline", "task_type": "CONTENT", "completed_at": "2026-01-02T10:00:00"},
            {"task_id": "3", "status": "SUCCESS", "pipeline": "Music Pipeline", "task_type": "MUSIC", "completed_at": "2026-01-03T10:00:00"},
        ]
        history = ExecutionHistory(repository=InMemoryExecutionHistoryRepository(records))
        self.assertEqual(["3", "2"], [record["task_id"] for record in history.query(limit=2)])
        self.assertEqual(["3"], [record["task_id"] for record in history.query(status="SUCCESS", pipeline="Music Pipeline", offset=0, limit=1)])
        self.assertEqual(["2"], [record["task_id"] for record in history.query(task_type="CONTENT", start_at="2026-01-02T00:00:00", end_at="2026-01-02T23:59:59")])
        self.assertEqual([], history.query(limit=0))
        self.assertEqual([], ExecutionHistory(repository=InMemoryExecutionHistoryRepository()).query())
        with self.assertRaises(ValueError):
            history.query(offset=-1)
        with self.assertRaises(ValueError):
            history.query(limit=-1)

        json_history = ExecutionHistory(self.root / "query_history.json")
        json_history.records = list(records)
        json_history.save()
        reloaded_history = ExecutionHistory(self.root / "query_history.json")
        self.assertEqual(
            history.query(status="SUCCESS"),
            reloaded_history.query(status="SUCCESS"),
        )

    def test_execution_history_supports_in_memory_repository_and_json_reload(self):
        memory_history = ExecutionHistory(repository=InMemoryExecutionHistoryRepository())
        task = self.task("memory task", "FILE")
        task.pipeline = "Test Pipeline"
        task.start()
        task.complete(PipelineResult(PipelineStatus.SUCCESS, task.pipeline, task).to_dict())
        memory_history.record(task)
        self.assertEqual(1, memory_history.count())

        file_history = ExecutionHistory(self.root / "history.json")
        file_history.record(task)
        self.assertEqual(1, ExecutionHistory(self.root / "history.json").count())
        (self.root / "broken.json").write_text("{", encoding="utf-8")
        self.assertEqual(0, ExecutionHistory(self.root / "broken.json").count())

    def _record(self, task_type, status):
        task = self.task(f"{task_type} task", task_type)
        task.pipeline = f"{task_type} Pipeline"
        task.start()
        result = PipelineResult(status, task.pipeline, task, task_type=task_type).to_dict()
        if status == PipelineStatus.SUCCESS:
            task.complete(result)
        else:
            task.fail(result)
        self.history.record(task)

    def test_history_pipeline_and_analyzer_use_execution_history_contract(self):
        self._record("FILE", PipelineStatus.SUCCESS)
        self._record("MUSIC", PipelineStatus.SUCCESS)
        self._record("FAIL", PipelineStatus.FAILED)

        result = HistoryPipeline(self.history).run(self.task("Show history", "HISTORY"))
        analysis = HistoryAnalyzer(self.history).analyze()

        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertEqual(3, result["data"]["count"])
        self.assertEqual(PipelineStatus.SUCCESS, analysis["status"])
        self.assertEqual(1, analysis["analysis"]["task_type_distribution"]["FILE"])
        self.assertEqual(1, analysis["analysis"]["task_type_distribution"]["MUSIC"])
        self.assertEqual(1, analysis["analysis"]["task_type_distribution"]["FAIL"])


class ContentPipelineTests(PipelineTestCase):
    def test_content_pipeline_records_mock_provider_usage_in_result_and_history(self):
        task = self.task("Create a provider-backed video", "CONTENT")
        result = ContentPipeline(
            content_root=self.root / "content", provider=MockProvider()
        ).run(task)
        task.pipeline = result["pipeline"]
        task.start()
        task.complete(result)
        self.history.record(task)

        usage = result["data"]["provider_usage"]
        self.assertEqual("mock", usage["provider"])
        self.assertGreater(usage["total_tokens"], 0)
        self.assertEqual(0.0, usage["estimated_cost_usd"])
        self.assertEqual(usage, self.history.get_all()[0]["result"]["data"]["provider_usage"])

    def test_content_pipeline_uses_configurable_task_parameters(self):
        content_root = self.root / "content"
        task = Task(
            "Create a testing video",
            parameters={
                "content_type": "Tutorial",
                "title_prefix": "Practical Guide",
                "tags": ["testing", "quality"],
            },
        )
        task.task_type = "CONTENT"

        result = ContentPipeline(content_root=content_root).run(task)

        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertEqual("Tutorial", result["data"]["content_type"])
        self.assertEqual("Practical Guide: Create a testing video", result["data"]["title"])
        self.assertEqual(["testing", "quality"], result["data"]["tags"])
        metadata = json.loads(
            (Path(result["data"]["project_path"]) / "project.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(result["data"]["content_type"], metadata["content_type"])

    def test_content_pipeline_creates_complete_project_in_temp_directory(self):
        content_root = self.root / "content"
        result = ContentPipeline(content_root=content_root).run(
            self.task("Create a YouTube video", "CONTENT")
        )

        project_path = Path(result["data"]["project_path"])
        expected_files = {
            "project.json",
            "content_plan.txt",
            "script.txt",
            "title.txt",
            "description.txt",
            "tags.txt",
            "review_checklist.txt",
        }
        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertTrue(RESULT_KEYS.issubset(result))
        self.assertEqual(content_root, project_path.parent)
        self.assertFalse((PROJECT_ROOT / "Content" / project_path.name).exists())
        self.assertEqual(expected_files, {path.name for path in project_path.iterdir()})
        for filename in expected_files:
            self.assertGreater((project_path / filename).stat().st_size, 0)
        metadata = json.loads((project_path / "project.json").read_text(encoding="utf-8"))
        self.assertEqual("Create a YouTube video", metadata["task"])
        self.assertEqual("YouTube video", metadata["content_type"])
        self.assertEqual(metadata["title"], result["data"]["title"])
        self.assertIn(
            "Review Checklist",
            (project_path / "review_checklist.txt").read_text(encoding="utf-8"),
        )


class ResearchPipelineTests(PipelineTestCase):
    def test_research_pipeline_records_provider_usage_and_returns_provider_failure(self):
        task = self.task("Research provider safety", "RESEARCH")
        result = ResearchPipeline(
            research_root=self.root / "research", provider=MockProvider()
        ).run(task)
        self.assertEqual("mock", result["data"]["provider_usage"]["provider"])

        class FailingProvider:
            def generate(self, request):
                raise TimeoutError("provider timed out")

        failed = ResearchPipeline(
            research_root=self.root / "failed_research", provider=FailingProvider()
        ).run(self.task("Research timeout", "RESEARCH"))
        self.assertEqual(PipelineStatus.FAILED, failed["status"])
        self.assertEqual("ProviderError: TimeoutError", failed["error"])
        self.assertNotIn("provider timed out", failed["error"])

    def test_research_pipeline_uses_configurable_questions_and_type(self):
        task = Task(
            "Research testing",
            parameters={
                "research_type": "Competitive analysis",
                "research_questions": ["Which testing practices are most repeatable?"],
            },
        )
        task.task_type = "RESEARCH"

        result = ResearchPipeline(research_root=self.root / "research").run(task)

        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertEqual("Competitive analysis", result["data"]["research_type"])
        self.assertEqual(
            ["Which testing practices are most repeatable?"],
            result["data"]["research_questions"],
        )

    def test_research_pipeline_records_structured_local_sources(self):
        research_root = self.root / "research"
        task = Task(
            "Research test strategy",
            parameters={
                "source_records": [
                    {
                        "title": "Internal test brief",
                        "url": "https://example.test/brief",
                        "relevance": "Defines the initial test scope.",
                    }
                ]
            },
        )
        task.task_type = "RESEARCH"

        result = ResearchPipeline(research_root=research_root).run(task)

        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertEqual(task.parameters["source_records"], result["data"]["source_records"])
        sources_text = (Path(result["data"]["project_path"]) / "sources.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("Internal test brief", sources_text)
        self.assertIn("https://example.test/brief", sources_text)

    def test_research_pipeline_creates_complete_project_in_temp_directory(self):
        research_root = self.root / "research"
        result = ResearchPipeline(research_root=research_root).run(
            self.task("Research AI music trends", "RESEARCH")
        )

        project_path = Path(result["data"]["project_path"])
        expected_files = {
            "project.json",
            "research_plan.txt",
            "findings.txt",
            "summary.txt",
            "sources.txt",
            "review_checklist.txt",
        }
        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertTrue(RESULT_KEYS.issubset(result))
        self.assertEqual(research_root, project_path.parent)
        self.assertFalse((PROJECT_ROOT / "Research" / project_path.name).exists())
        self.assertEqual(expected_files, {path.name for path in project_path.iterdir()})
        for filename in expected_files:
            self.assertGreater((project_path / filename).stat().st_size, 0)
        metadata = json.loads((project_path / "project.json").read_text(encoding="utf-8"))
        self.assertEqual("Research AI music trends", metadata["task"])
        self.assertEqual("Structured local research", metadata["research_type"])
        self.assertEqual(metadata["summary"], result["data"]["summary"])
        self.assertIn(
            "Review Checklist",
            (project_path / "review_checklist.txt").read_text(encoding="utf-8"),
        )


class FailurePipelineTests(PipelineTestCase):

    def test_failing_pipeline_returns_intentional_failed_result(self):
        main = importlib.import_module("main")
        result = main.FailingPipeline().run(self.task("Run failure test", "FAIL"))
        self.assertEqual(PipelineStatus.FAILED, result["status"])
        self.assertEqual("Intentional test failure", result["error"])
        self.assertTrue(RESULT_KEYS.issubset(result))


class NotImplementedPipelineTests(PipelineTestCase):
    def test_stub_pipeline_returns_common_not_implemented_result(self):
        result = StubPipeline("Unavailable Pipeline").run(
            self.task("Unavailable task", "UNAVAILABLE")
        )

        self.assertEqual(PipelineStatus.NOT_IMPLEMENTED, result["status"])
        self.assertTrue(RESULT_KEYS.issubset(result))

    def test_worker_preserves_not_implemented_status_and_records_history(self):
        class NotImplementedManager:
            def handle(_, task):
                task.task_type = "UNAVAILABLE"
                task.pipeline = "Unavailable Pipeline"
                return PipelineResult(
                    PipelineStatus.NOT_IMPLEMENTED,
                    task.pipeline,
                    task,
                    task.task_type,
                    error="Unavailable Pipeline is not available yet",
                ).to_dict()

        queue = TaskQueue()
        queue.add(Task("Unavailable task"))
        completed = TaskWorker(queue, NotImplementedManager(), self.history).run_once()

        self.assertEqual(PipelineStatus.NOT_IMPLEMENTED, completed.status)
        self.assertEqual(PipelineStatus.NOT_IMPLEMENTED, self.history.get_all()[0]["status"])


class ManagerTests(PipelineTestCase):
    def _manager_for_result(self, result):
        registry = PipelineRegistry()
        registry.register("CONTENT", FixedResultPipeline(result))
        return Manager(registry)

    def test_manager_accepts_valid_pipeline_result(self):
        task = Task("Create a video")
        result = PipelineResult(
            PipelineStatus.SUCCESS,
            "Fixed Result Pipeline",
            task,
            task_type="CONTENT",
        ).to_dict()

        handled = self._manager_for_result(result).handle(task)

        self.assertEqual(PipelineStatus.SUCCESS, handled["status"])
        self.assertEqual(result, handled)

    def test_manager_converts_missing_required_result_key_to_failed_result(self):
        result = {
            "status": PipelineStatus.SUCCESS,
            "pipeline": "Fixed Result Pipeline",
            "task": "Create a video",
            "task_id": "test-id",
            "task_type": "CONTENT",
            "error": None,
        }

        handled = self._manager_for_result(result).handle(Task("Create a video"))

        self.assertEqual(PipelineStatus.FAILED, handled["status"])
        self.assertIn("missing required keys", handled["error"])

    def test_manager_converts_invalid_result_status_to_failed_result(self):
        result = {
            "status": "INVALID_STATUS",
            "pipeline": "Fixed Result Pipeline",
            "task": "Create a video",
            "task_id": "test-id",
            "task_type": "CONTENT",
            "data": {},
            "error": None,
        }

        handled = self._manager_for_result(result).handle(Task("Create a video"))

        self.assertEqual(PipelineStatus.FAILED, handled["status"])
        self.assertIn("invalid status", handled["error"])

    def test_manager_converts_non_dictionary_result_to_failed_result(self):
        handled = self._manager_for_result(["not", "a", "result"]).handle(
            Task("Create a video")
        )

        self.assertEqual(PipelineStatus.FAILED, handled["status"])
        self.assertIn("PipelineResult dictionary", handled["error"])

    def test_manager_converts_result_with_mismatched_execution_metadata_to_failed_result(self):
        task = Task("Create a video")
        result = PipelineResult(
            PipelineStatus.SUCCESS,
            "Fixed Result Pipeline",
            task,
            task_type="CONTENT",
        ).to_dict()
        result["task_id"] = "different-task-id"

        handled = self._manager_for_result(result).handle(task)

        self.assertEqual(PipelineStatus.FAILED, handled["status"])
        self.assertIn("task_id", handled["error"])

    def test_classifier_identifies_all_registered_task_types(self):
        manager = Manager(self.registry())
        cases = {
            "Organize a folder": "FILE",
            "Create a song": "MUSIC",
            "Create a video": "CONTENT",
            "Research market trends": "RESEARCH",
            "Run failure test": "FAIL",
            "Show execution history": "HISTORY",
        }
        for task_text, expected_type in cases.items():
            self.assertEqual(expected_type, manager.classifier.classify(Task(task_text)))

    def test_manager_uses_validated_declared_task_type(self):
        registry = PipelineRegistry()
        registry.register("TEST", TestPipeline())
        task = Task("Text that the keyword classifier would not classify as TEST")
        task.task_type = "TEST"

        result = Manager(registry).handle(task)

        self.assertEqual(PipelineStatus.SUCCESS, result["status"])
        self.assertEqual("TEST", result["task_type"])
        self.assertEqual("Test Pipeline", result["pipeline"])

    def test_manager_routes_every_registered_pipeline(self):
        files = self.root / "test_files"
        files.mkdir()
        (files / "image.png").write_text("image", encoding="utf-8")
        manager = Manager(self.registry())
        cases = {
            "Organize a folder": ("FILE", "Automation Pipeline"),
            "Create a song": ("MUSIC", "Music Pipeline"),
            "Create a video": ("CONTENT", "Content Pipeline"),
            "Research market trends": ("RESEARCH", "Research Pipeline"),
            "Run failure test": ("FAIL", "Failing Test Pipeline"),
            "Show execution history": ("HISTORY", "Execution History Pipeline"),
        }
        with patch("scripts.file_manager.log"), patch("scripts.report_generator.log"):
            for task_text, (task_type, pipeline_name) in cases.items():
                result = manager.handle(Task(task_text))
                self.assertEqual(task_type, result["task_type"])
                self.assertEqual(pipeline_name, result["pipeline"])
                self.assertTrue(RESULT_KEYS.issubset(result))
                if task_type == "CONTENT":
                    self.assertEqual(PipelineStatus.SUCCESS, result["status"])
                if task_type == "RESEARCH":
                    self.assertEqual(PipelineStatus.SUCCESS, result["status"])


class WorkerTests(PipelineTestCase):
    def test_queue_cancellation_updates_history_and_does_not_recancel_terminal_task(self):
        task = Task("cancel task")
        queue = TaskQueue(history=self.history)
        queue.add(task)

        self.assertTrue(queue.cancel(task))
        self.assertEqual(PipelineStatus.CANCELLED, task.status)
        self.assertEqual(PipelineStatus.CANCELLED, self.history.get_all()[0]["status"])
        self.assertFalse(queue.cancel(task))
        self.assertEqual(1, self.history.count())

    def test_worker_marks_elapsed_task_timed_out_with_safe_result(self):
        class SuccessfulManager:
            def handle(_, task):
                task.task_type = "FILE"
                task.pipeline = "Test Pipeline"
                return PipelineResult(PipelineStatus.SUCCESS, task.pipeline, task).to_dict()

        task = Task("timeout task", timeout_seconds=1)
        queue = TaskQueue(history=self.history)
        queue.add(task)

        with patch("core.worker.time.monotonic", side_effect=[0.0, 2.0]):
            completed = TaskWorker(queue, SuccessfulManager(), self.history).run_once()

        record = self.history.get_all()[0]
        self.assertEqual(PipelineStatus.TIMED_OUT, completed.status)
        self.assertEqual(PipelineStatus.TIMED_OUT, completed.result["status"])
        self.assertEqual("TaskError: TimeoutError", completed.result["error"])
        self.assertEqual(PipelineStatus.TIMED_OUT, record["status"])
        self.assertEqual("TimeoutError", record["last_error_type"])

    def test_worker_retries_retryable_error_and_updates_single_history_record(self):
        attempts = []

        class RetryManager:
            def handle(_, task):
                attempts.append(task.retry_count)
                if len(attempts) == 1:
                    raise TimeoutError("provider key should not be recorded")
                task.task_type = "FILE"
                task.pipeline = "Test Pipeline"
                return PipelineResult(PipelineStatus.SUCCESS, task.pipeline, task).to_dict()

        task = Task("retry task", max_retries=1)
        queue = TaskQueue(history=self.history)
        queue.add(task)
        completed = TaskWorker(queue, RetryManager(), self.history).run_all()
        record = self.history.get_all()[0]

        self.assertEqual([0, 1], attempts)
        self.assertEqual([task], completed)
        self.assertEqual(PipelineStatus.SUCCESS, task.status)
        self.assertEqual(1, task.retry_count)
        self.assertEqual("TimeoutError", task.last_error_type)
        self.assertEqual(1, self.history.count())
        self.assertEqual(1, record["retry_count"])
        self.assertNotIn("provider key", str(record))

    def test_worker_does_not_retry_non_retryable_error_or_store_message(self):
        attempts = []

        class NonRetryManager:
            def handle(_, task):
                attempts.append(task.retry_count)
                raise ValueError("sensitive failure details")

        task = Task("non retry task", max_retries=2)
        queue = TaskQueue(history=self.history)
        queue.add(task)
        completed = TaskWorker(queue, NonRetryManager(), self.history).run_all()
        record = self.history.get_all()[0]

        self.assertEqual([0], attempts)
        self.assertEqual([task], completed)
        self.assertEqual(PipelineStatus.FAILED, task.status)
        self.assertEqual(0, task.retry_count)
        self.assertEqual("ValueError", task.last_error_type)
        self.assertEqual("TaskError: ValueError", task.result["error"])
        self.assertNotIn("sensitive failure details", str(record))

    def test_queue_and_worker_update_one_history_record_through_lifecycle(self):
        class RecordingManager:
            def handle(_, task):
                self.assertEqual(PipelineStatus.RUNNING, self.history.get_all()[0]["status"])
                task.task_type = "FILE"
                task.pipeline = "Test Pipeline"
                return PipelineResult(PipelineStatus.SUCCESS, task.pipeline, task).to_dict()

        task = Task("tracked task")
        queue = TaskQueue(history=self.history)
        queue.add(task)

        self.assertEqual(PipelineStatus.QUEUED, task.status)
        self.assertEqual(PipelineStatus.QUEUED, self.history.get_all()[0]["status"])
        completed = TaskWorker(queue, RecordingManager(), self.history).run_once()
        record = self.history.get_all()[0]

        self.assertEqual(PipelineStatus.SUCCESS, completed.status)
        self.assertEqual(1, self.history.count())
        self.assertEqual(PipelineStatus.SUCCESS, record["status"])
        self.assertEqual(completed.result, record["result"])
        self.assertIsNotNone(record["started_at"])
        self.assertIsNotNone(record["completed_at"])
        self.assertGreaterEqual(record["duration_seconds"], 0)

    def test_queue_skip_records_terminal_skipped_state(self):
        task = Task("skip task")
        queue = TaskQueue(history=self.history)
        queue.add(task)
        queue.skip(task)

        self.assertEqual(PipelineStatus.SKIPPED, task.status)
        self.assertEqual(0, queue.size())
        self.assertEqual(1, self.history.count())
        self.assertEqual(PipelineStatus.SKIPPED, self.history.get_all()[0]["status"])

    def test_worker_runs_research_pipeline_and_records_successful_history(self):
        task = Task("Research AI music trends")
        queue = TaskQueue()
        queue.add(task)
        completed = TaskWorker(queue, Manager(self.registry()), self.history).run_once()

        self.assertEqual(PipelineStatus.SUCCESS, completed.status)
        self.assertEqual("Research Pipeline", completed.pipeline)
        self.assertEqual("RESEARCH", self.history.get_all()[0]["task_type"])
        self.assertEqual("Research Pipeline", self.history.get_all()[0]["pipeline"])
        self.assertTrue(Path(completed.result["data"]["project_path"]).is_dir())

    def test_worker_runs_content_pipeline_and_records_successful_history(self):
        task = Task("Create a YouTube video")
        queue = TaskQueue()
        queue.add(task)
        completed = TaskWorker(queue, Manager(self.registry()), self.history).run_once()

        self.assertEqual(PipelineStatus.SUCCESS, completed.status)
        self.assertEqual("Content Pipeline", completed.pipeline)
        self.assertEqual("CONTENT", self.history.get_all()[0]["task_type"])
        self.assertEqual("Content Pipeline", self.history.get_all()[0]["pipeline"])
        self.assertTrue(Path(completed.result["data"]["project_path"]).is_dir())

    def test_worker_transitions_pending_to_running_to_success_and_records_history(self):
        observed_statuses = []

        class SuccessfulManager:
            def handle(_, task):
                observed_statuses.append(task.status)
                task.task_type = "FILE"
                task.pipeline = "Test Pipeline"
                return PipelineResult(PipelineStatus.SUCCESS, task.pipeline, task, "FILE").to_dict()

        task = Task("successful task")
        queue = TaskQueue()
        queue.add(task)
        completed = TaskWorker(queue, SuccessfulManager(), self.history).run_once()

        self.assertEqual(["RUNNING"], observed_statuses)
        self.assertEqual(PipelineStatus.SUCCESS, completed.status)
        self.assertEqual(1, self.history.count())

    def test_worker_marks_failed_result_and_records_history(self):
        class FailedManager:
            def handle(_, task):
                task.task_type = "FAIL"
                task.pipeline = "Test Pipeline"
                return PipelineResult(PipelineStatus.FAILED, task.pipeline, task, "FAIL").to_dict()

        queue = TaskQueue()
        queue.add(Task("failed task"))
        completed = TaskWorker(queue, FailedManager(), self.history).run_once()

        self.assertEqual(PipelineStatus.FAILED, completed.status)
        self.assertEqual(PipelineStatus.FAILED, self.history.get_all()[0]["status"])

    def test_worker_records_history_when_manager_raises_exception(self):
        class ExplodingManager:
            def handle(_, task):
                raise RuntimeError("test exception")

        queue = TaskQueue()
        queue.add(Task("exception task"))
        completed = TaskWorker(queue, ExplodingManager(), self.history).run_once()

        self.assertEqual(PipelineStatus.FAILED, completed.status)
        self.assertEqual("TaskError: RuntimeError", completed.result["error"])
        self.assertEqual(1, self.history.count())


class MainAndSyntaxTests(PipelineTestCase):
    def test_importing_main_does_not_execute_work(self):
        project_root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [sys.executable, "-B", "-c", "import main; print('IMPORT_OK')"],
            cwd=project_root,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual("IMPORT_OK", completed.stdout.strip())

    def test_main_run_exists_and_runs_with_injected_test_dependencies(self):
        main = importlib.import_module("main")
        registry = self.registry()
        completed = main.run(
            task_texts=["Create a video"],
            history=self.history,
            registry=registry,
        )
        self.assertTrue(callable(main.run))
        self.assertEqual(1, len(completed))
        self.assertEqual(PipelineStatus.SUCCESS, completed[0].status)
        self.assertEqual("Content Pipeline", completed[0].pipeline)
        self.assertEqual(1, self.history.count())

    def test_all_project_python_sources_compile(self):
        project_root = Path(__file__).resolve().parents[1]
        source_files = [
            path for path in project_root.rglob("*.py")
            if "venv" not in path.parts and "__pycache__" not in path.parts
        ]
        self.assertGreater(len(source_files), 1)
        for path in source_files:
            with self.subTest(path=path):
                compile(path.read_text(encoding="utf-8"), str(path), "exec")


if __name__ == "__main__":
    unittest.main(verbosity=2)
