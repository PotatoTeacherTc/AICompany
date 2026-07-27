# AICompany Architecture

## Current execution flow

```text
Task → TaskQueue → TaskWorker → Manager → TaskClassifier
     → PipelineRegistry → BasePipeline implementation → PipelineResult
     → Task status/result → ExecutionHistory → HistoryAnalyzer
```

`main.py` builds the default registry, creates Tasks from input strings, adds
them to TaskQueue, and invokes TaskWorker. Importing `main` does not execute
the workflow; `run()` is the explicit entry point.

## Implemented layers

| Layer | Current modules | Responsibility and dependency |
|---|---|---|
| Task model | `core/task.py`, `core/status.py`, `core/result.py` | Task owns ID, text, optional structured parameters, an optional parent Task ID, lifecycle timestamps, result, task type, pipeline, bounded retry metadata, and an optional timeout. It exposes PENDING/QUEUED/RUNNING/SUCCESS/FAILED/SKIPPED/CANCELLED/TIMED_OUT transitions. PipelineResult serializes the common result fields. |
| Application service | `application/automation_service.py` | AutomationService is a framework-independent composition boundary for injected Manager, ExecutionHistory, ArtifactManager, TaskQueue, and TaskWorker dependencies. It creates/submits Tasks and runs work without exposing Pipeline or Repository calls to upper layers. Its task-ID cancel/retry controls use a small in-process lock and the existing Queue retry/cancel paths, preserving one upserted history record per Task. `main.py` uses it while preserving `run()` compatibility. |
| Query service | `application/task_query_service.py` | TaskQueryService converts Task, ExecutionHistory query results, provider usage, and ArtifactManager records into serializable DTO dictionaries. It returns a safe `found: false` response for unknown task IDs and reuses `ExecutionHistory.query()` for filtered lists. |
| User domain | `core/user.py`, `core/user_repository.py`, `application/user_service.py` | UserService creates credential-free user records through injected in-memory or JSON-file repositories. A record contains only an ID, normalized email, and creation timestamp; duplicate normalized emails are rejected. Passwords and authentication tokens are not modeled or persisted. |
| Credential domain | `core/password_hasher.py`, `core/credential_repository.py`, `application/credential_service.py` | CredentialService keeps password verification separate from User records. Its injectable PasswordHasher defaults to salted PBKDF2-SHA256 and stores only `user_id` plus a password hash through in-memory or JSON-file repositories. Plain passwords, tokens, and raw verification errors are not persisted. |
| Login and token domain | `core/access_token_provider.py`, `application/login_service.py` | LoginService composes UserService and CredentialService, returning an access token only after safe credential verification. Its injectable provider defaults to an HMAC-signed, expiry-checked standard-library token with only `user_id` and expiry claims; token values are never stored. |
| Session domain | `core/session_repository.py`, `application/session_service.py` | SessionService stores user-scoped refresh-token hashes through injected in-memory or JSON-file repositories. It creates, rotates, expires, lists, and revokes sessions without exposing refresh-token values or hashes. The FastAPI boundary provides refresh, logout, list, and owner-scoped session deletion endpoints. |
| Audit domain | `core/audit_repository.py`, `application/audit_service.py` | AuditService records safe lifecycle events through injectable in-memory or JSON-file repositories. Its metadata guard excludes passwords, tokens, hashes, prompts, and non-scalar values. Workspace audit queries support action/date filters and paging; authenticated OWNER/ADMIN access is required. |
| Membership domain | `core/workspace_membership.py`, `core/workspace_membership_repository.py`, `application/workspace_membership_service.py` | WorkspaceMembershipService links existing Users and Workspaces with OWNER, ADMIN, or MEMBER roles through injectable in-memory or JSON-file repositories. It rejects duplicate or dangling memberships and prevents removal or demotion of the last OWNER. It is not an authentication or authorization mechanism. |
| API contracts and application | `api/contracts.py`, `api/task_api.py`, `api/app.py`, `api/errors.py` | Transport-neutral request/response DTOs define task creation plus single/list retrieval. The FastAPI app factory injects application services, exposes task control/query endpoints, workspace create/list/detail endpoints, `POST /users`, `GET /users/{user_id}`, and workspace-membership create/list/role-change/removal endpoints. It returns uniform sanitized 4xx/5xx errors; duplicate identity/membership and final-OWNER conflicts return 409. TaskApi depends only on AutomationService and TaskQueryService, while user/workspace routes depend only on their application services, never repositories or Pipelines. Authentication is not implemented yet. |
| Queue and execution | `core/task_queue.py`, `core/worker.py` | FIFO queue accepts optional ExecutionHistory and retry-limit dependencies. It cancels only queued non-terminal work. Worker records QUEUED, RUNNING, retry, timeout, and terminal updates through the same task ID; History upserts the record so result, timestamps, duration, retry count, timeout, and sanitized error type remain synchronized without duplicates. Current synchronous timeout handling marks an over-deadline execution after its handler returns; it does not interrupt a running handler. |
| Routing | `agent/manager.py`, `agent/classifier.py`, `agent/goal_task_planner.py`, `core/registry.py` | Classifier selects a task type; GoalTaskPlanner converts structured goal steps into parent-linked Tasks only when their task types are registered. Manager preserves that validated declared type (or classifies an untyped Task), resolves it through the registry, and calls one Pipeline. Registry accepts only non-empty task types and BasePipeline implementations, rejects duplicate type registrations, and exposes registered task-type/pipeline/capability metadata for planning. Manager accepts only PipelineResult dictionaries with every required key, an allowed PipelineStatus value, and metadata matching the current Task and selected Pipeline. |
| Pipeline contract | `core/base_pipeline.py` | Each Pipeline receives a Task and returns a PipelineResult dictionary. |
| Implemented pipelines | `core/pipeline.py` (FILE), `music_pipeline.py`, `content_pipeline.py`, `research_pipeline.py`, `history_pipeline.py`, `main.py` (intentional FAIL) | Produce files or history data and return common results. FILE, MUSIC, CONTENT, and RESEARCH accept test output path injection. |
| Persistence and analysis | `core/execution_history.py`, `core/execution_history_repository.py`, `core/history_analyzer.py` | ExecutionHistory keeps its existing record API while receiving an InMemory or JSON repository through DI. Its query API returns latest-first records and supports status, pipeline, task-type, and ISO-date-range filters with offset/limit pagination. HistoryAnalyzer reuses that query contract to aggregate status, pipeline/task-type, and provider/model usage/token/cost data. JSON persistence safely handles missing/corrupt files and uses atomic replacement for writes. |
| Artifact boundary | `core/artifact_manager.py`, `core/artifact_repository.py` | ArtifactManager creates stable artifact IDs, registers file metadata, and exposes artifact lookup/list queries through an injected InMemory or file-backed repository. FILE, MUSIC, CONTENT, and RESEARCH optionally receive it through DI and register their output files without changing their existing `files_created` results. Standard metadata includes ID, type, MIME type, filename, size, creation time, producer pipeline, and a reserved workspace ID. PipelineResult carries those records into ExecutionHistory without changing its existing fields. |
| Supporting services | `agent/planner.py`, `executor.py`, `validator.py`, `reporter.py`, `scripts/` | FILE pipeline planning, execution, validation, report generation, and logging. TaskPlanner produces the execution plan; FILE adds its target folder and TaskExecutor consumes that plan rather than an independent folder argument. |
| Tests | `Automation/tests/test_pipeline_system.py` | Standard-library unittest regression suite using temporary directories. It covers SUCCESS, FAILED, NOT_IMPLEMENTED, exception, registry, and Manager result-contract boundaries. |
| AI provider boundary | `providers/base.py`, `providers/models.py`, `providers/mock_provider.py`, `providers/factory.py`, `providers/pipeline_utils.py` | Provider-neutral request/response and usage metadata. The shared utility normalizes absent or partial usage and formats provider errors without raw messages. MockProvider is the offline default; CONTENT, RESEARCH, and MUSIC can receive an injected provider and persist only provider/model/token/cost metadata through PipelineResult and ExecutionHistory, never API keys. |

## Current pipeline behavior

- FILE: organizes known file types in a configured folder.
- MUSIC: creates a local music-project scaffold.
- CONTENT: creates a local content-project scaffold with a review checklist;
  content type, title prefix, and tags can be supplied through validated Task
  parameters.
- RESEARCH: creates a structured local research-project scaffold with a review
  checklist and preserves validated local source records; research type and
  questions can be supplied through validated Task parameters. It does not
  perform web search or AI/API calls.
- HISTORY: reads recent execution records.
- FAIL: deliberately returns `FAILED` to validate failure handling.

`StubPipeline` remains as a reusable implementation for future unavailable
capabilities, but it is not registered by the current default registry.

## Planned extension boundaries

```text
UI / CLI ─┐
API layer ├→ Goal intake & planning → existing Task/Queue/Worker flow
          │                              ↓
          └────────────────────────→ PipelineRegistry → provider adapters

Storage layer: durable queue, project artifacts, execution history, settings
AI provider layer: provider adapters used by pipelines, never directly by UI
```

- UI/API should submit validated goals and query job/artifact state; they should
  not call filesystem pipelines directly.
- AI providers should be adapter implementations injected into pipelines so
  offline/local behavior stays testable.
- Storage should replace or extend JSON history with durable job, artifact, and
  queue records without changing Task/PipelineResult semantics.

## SaaS extension boundaries

- AI providers are injected adapters. They receive a standard request and
  return a standard response with usage metadata; Pipelines must not contain a
  provider name, model name, or credential.
- Persistent storage will attach workspace ownership to Tasks, artifacts,
  execution history, provider usage, and costs before any web-facing API is
  introduced.
- User identity is currently a minimal, credential-free domain. Membership,
  authorization, and authentication must build on it without adding secrets to
  its repository records. The existing membership roles are not yet enforced
  by API requests because authentication is not implemented.
- Supplying `workspace_id` when reading a Task provides data-scope mismatch
  protection at the current API boundary. It is not a substitute for future
  authenticated membership authorization.
- `create_app(auth_required=True)` enables Bearer checks for workspace/task
  access. MEMBER may access workspace work; OWNER and ADMIN may change
  membership. The default remains false solely for legacy API compatibility.
- Backend API and web dashboard layers will access workspace-scoped services,
  never filesystem Pipelines or provider credentials directly.
- Authentication, subscription, credit, and payment components belong outside
  the current Pipeline contract and will be added only after storage and API
  boundaries exist.
