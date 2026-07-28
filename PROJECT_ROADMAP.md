# AICompany Project Roadmap

## Final goal

AICompany is intended to become a multi-user SaaS automation platform. Users
will submit natural-language goals through a website; AICompany will plan,
execute, validate, and present the work while recording per-user work,
artifacts, usage, and costs. The product will ultimately support accounts,
workspaces, subscriptions, and credit-based billing.

## Current baseline: Mission 98

The verified implementation currently provides a Task/Queue/Worker execution
path, keyword-based classification, a registry of FILE, MUSIC, CONTENT,
RESEARCH, HISTORY, and intentional FAIL pipelines, structured PipelineResult
objects, JSON execution history, validated structured child-task planning, and
automated regression tests. Execution history supports injected in-memory and
JSON repositories, filtered queries, and analytics. Queue lifecycle state is
synchronized into one history record per task, including bounded retry state
and sanitized error types, cancellation, and timeout state. CONTENT and RESEARCH create
local starter projects only; neither calls an external AI provider nor the
web. CONTENT, RESEARCH, and MUSIC provider boundaries are currently verified
with the offline MockProvider. Artifact metadata can now be stored through an
in-memory or file-backed repository boundary, follows a standard artifact
metadata contract, and is registered by the FILE, MUSIC, CONTENT, and RESEARCH
pipelines for result and history queries.
The framework-independent AutomationService now provides the application
boundary for task submission and execution.
Task query responses now combine task state, execution history, provider usage,
and artifact metadata through repository-neutral DTOs.
The transport-neutral API contract layer now defines task creation and task
lookup/list requests and responses without selecting a web framework.
A FastAPI application factory now supplies an HTTP foundation with dependency
injection, health checking, and sanitized global error responses.
Task creation and task single/list retrieval are now available through the
FastAPI boundary while continuing to use only application services.
Task cancellation and retry controls now use the existing queue/history state
contract and return conflict responses for terminal or otherwise invalid state
transitions.
Workspace records and workspace-aware task creation are available through the
API. The new User domain persists only normalized email identity metadata
through injectable in-memory or JSON-file repositories; authentication and
credentials are intentionally outside this boundary.
Workspace membership records now establish OWNER, ADMIN, and MEMBER roles and
protect the final OWNER from removal or demotion. These roles are a domain
contract only; no authentication or request authorization is implemented yet.
User identity and workspace-membership endpoints now expose this domain through
the FastAPI boundary. Their workspace parameter is a data-scoping contract,
not an authorization decision; authentication remains the next separate stage.
Credential storage is now separated from user identity and uses a local PBKDF2
password-hashing abstraction. Login, bearer tokens, and request authorization
are now available as application services using an injected, signed,
expiry-checked token provider. HTTP authentication and authorization remain
incomplete.
The FastAPI factory now supports opt-in Bearer authentication and role-based
workspace boundaries while preserving its prior unauthenticated default for
backward compatibility. Refresh tokens and production secret configuration are
not implemented.
Session-backed refresh-token rotation, logout, and session inspection are now
available through the API. Refresh tokens remain opaque to storage and
responses disclose neither stored hashes nor credentials.
Security audit logging now records safe lifecycle metadata through an
injectable repository boundary; sensitive credentials, token values, hashes,
and prompts are excluded.
Audit-log operations now support cursor pagination and resource/user/action
filters while retaining offset/limit compatibility.
Request correlation IDs now provide safe cross-boundary diagnostics without
recording request bodies, credentials, tokens, prompts, or exception details.
The collaboration domain now has a validated, serializable Mission contract
for representing a requested unit of work with explicit requester and
workspace ownership. Missions now follow validated PENDING, IN_PROGRESS, and
terminal state transitions and support an exclusive, owner-checked,
timezone-stamped collaboration lock. These immutable contract operations do
not yet provide repository-backed distributed locking. A ContextBuilder now
derives a minimum workspace-scoped WorkerContext from Mission while excluding
sensitive and non-scalar metadata. WorkerResult standardizes terminal worker
outcomes with existing PipelineStatus values, provider usage fields, and
artifact metadata. BaseWorker and the injected FunctionWorker establish a
testable worker boundary with sanitized handler failures and strict
mission/workspace/result identity checks.
MissionWorkspaceManager now provides an injected-root, path-escape-safe
`workspace_id/mission_id` directory boundary as the current worktree-equivalent
isolation. WorkerResultValidator checks status/error and Mission, Workspace,
and Artifact identities. ClaudeWorker and GeminiWorker share the existing
provider abstraction, default to the offline ProviderFactory selection, and
normalize missing usage plus sanitized timeout/provider failures.
CollaborationOrchestrator completes the local end-to-end flow from Mission
state transition through per-Worker locking, Context construction, Worker
execution, validation, aggregate Mission outcome, and a safe workspace-scoped
ExecutionHistory summary. It does not call external providers by default,
create Git worktrees, interrupt a provider call in progress, seek human
approval, commit, or push.
The music domain now defines provider-neutral generation request, generated
artifact, result, timeout, and usage contracts. ProviderFactory selects the
offline FakeMusicProvider by default while MusicPipeline also accepts injected
music providers or the existing generic provider boundary through a
compatibility adapter. MusicPipeline validates input and scope identifiers,
writes beneath a workspace-specific root, normalizes partial or absent usage,
returns PipelineResult and artifact metadata without prompt text or absolute
paths, and safely maps provider failures. Its optional ExecutionHistory
integration records one workspace/mission-scoped MUSIC summary without
creating a duplicate repository. No external music service is configured or
called.
ImagePipeline and VideoPipeline now extend the same safe artifact and
PipelineResult conventions with provider-neutral requests, optional usage,
workspace/mission isolation, and deterministic Fake providers. Video inputs
reference safe image/music artifact metadata by ID. The YouTube boundary
supports upload metadata through FakeYouTubeProvider only; it performs no
OAuth flow or upload. ContentOrchestrator completes the offline Music → Image
→ Video → simulated YouTube flow, records stage outcomes, and stops safely
when an intermediate stage fails. Paid providers are disabled by the shared
`ALLOW_PAID_PROVIDER=false` policy and are rejected before invocation.
The personal offline composition baseline is now complete. InMemoryScheduler
represents timezone-aware one-time and interval-recurring work with an
injectable FakeClock, workspace queries, disabled-state enforcement, and
per-occurrence duplicate protection; it does not start an OS or distributed
scheduler. RetryExecutor classifies safe failure categories, bounds attempts,
and exposes backoff timing without sleeping or adding infrastructure.
Content recovery resumes from the failed stage and reuses only path-free
artifacts belonging to the same workspace. PersonalAICompany composes Mission,
CollaborationOrchestrator, immediate or scheduled execution, the Fake content
flow, retry/recovery, artifacts, and history. This is an in-process personal
baseline, not Mission 91 persistence or later SaaS infrastructure.
Mission 91-94 add a local restart-recovery boundary without external
infrastructure. A versioned StateRepository has in-memory and atomic JSON-file
implementations for safe workspace-scoped Mission summaries, schedules,
RetryState, history summaries, Jobs, and Batches; corrupt or incompatible
records are ignored. FileArtifactRepository can persist only storage-root
relative internal references, and ArtifactManager now records Mission, stage,
and availability metadata while metadata deletion never deletes user files.
PersistentJobQueue restores pending work, converts abandoned RUNNING claims
back to PENDING, enforces claim ownership, and supports injected in-process
targets. BatchManager reuses that queue and repository, applies idempotency
keys and item limits, preserves successful items when peers fail, and reflects
retry transitions. The local JSON location is application-injected; no
external database, broker, or cloud storage exists.
WorkspaceMonitor now provides the read-only Mission 95 observation boundary.
It composes the existing StateRepository, PersistentJobQueue, Scheduler,
ArtifactManager, and ExecutionHistory without creating storage or changing
execution state. Workspace summaries expose safe snapshots for Missions,
Schedules, Jobs, Batches, Pipeline history, Retry state, and Artifacts,
including partial failures, retry waiting, and MISSING artifacts. Usage totals
include only fields actually present and preserve provider/model groupings.
The same recursive redaction used at persistence boundaries is applied again
on reads, with standard token-count fields explicitly retained. In-memory and
JSON restart scenarios are supported. No dashboard, APM, Prometheus, Grafana,
Sentry, WebSocket, execution control, or network/provider call is included.
Mission 96 adds a separate structured operational logging boundary. LogEvent
supports timezone-aware timestamps, five severity levels, event/component and
existing correlation identifiers, status, safe message/error, duration,
provider/model, optional UsageMetadata fields, and recursively sanitized
metadata. InMemoryLogger provides deterministic tests and LocalFileLogger
appends JSON Lines beneath an injected local path, supports restart reads, and
ignores corrupt rows. Queries require a Workspace and may filter component,
level, timezone-aware range, and recent count. Logging failures are contained
inside the Logger and never change Pipeline, Queue, Retry, or Provider policy
results. The legacy scripts Logger now delegates to this contract. Manager
Pipeline events, PersistentJobQueue lifecycle, RetryExecutor attempts, and
ProviderFactory paid-policy decisions accept injected logging. ExecutionHistory
remains the user execution record, while Monitor remains a read-only state
view. No remote logging, tracing, retention service, dashboard, network call,
or paid Provider is enabled.
Mission 97 adds a provider-neutral UsageEngine because the existing
HistoryAnalyzer only derives transient statistics from execution records and
does not provide a durable, idempotent usage ledger. UsageEngine accepts the
existing UsageMetadata object or a partial dictionary, keeps absent fields
absent, and records only provider/model/token/cost fields plus existing
Workspace, Mission, and execution identifiers. It reuses StateRepository for
in-memory or versioned JSON persistence, uses the execution ID as the default
idempotency key, supports restart-safe Workspace/provider/model/time/recent
queries, and produces Workspace totals and provider/model distributions.
Invalid or corrupt records are rejected or ignored safely. Optional structured
logging is failure-isolated from usage recording. This is offline accounting
metadata only: it does not price models, infer missing usage, call providers,
manage credits, bill users, or introduce Mission 98 Settings.
Mission 98 adds a Workspace-scoped SettingsManager over the existing
StateRepository rather than expanding the process-level constants into a
second configuration stack. WorkspaceSettings persists only an allowlisted
set of offline provider choices, bounded timeouts, retry/backoff, Batch size,
and structured log level. It uses safe defaults, revision-checked updates,
Workspace-qualified storage IDs, in-memory or JSON restart recovery, and
failure-isolated structured logging. SettingsManager produces the existing
ProviderFactory environment contract and RetryPolicy through dependency
injection. `ALLOW_PAID_PROVIDER` remains false and cannot be enabled through
Workspace settings; unknown, sensitive, path-like, paid, or out-of-range
values are rejected before persistence. It stores no prompts, credentials, or
arbitrary metadata. Mission 99 AI Departments and Mission 100 Personal
Operating System remain separate product-composition work.

## Development stages

| Stage | Goal | Required capabilities | Completion condition |
|---|---|---|---|
| 1. Core architecture stabilization | Keep the execution contract dependable. | Typed/documented Task and PipelineResult schemas, registry validation, deterministic status handling, test coverage for success/failure/not-implemented paths. | Every registered pipeline follows the same contract and regression tests cover its boundary behavior. |
| 2. Task, agent, and pipeline expansion | Support richer work decomposition. | Structured task parameters, subtask relationships, planner output consumed by executors, pipeline capability metadata. | A compound user goal can be represented as validated executable tasks. |
| 3. CONTENT and RESEARCH enrichment | Make local project scaffolds useful production workflows. | Templates, configurable research/content formats, source records, review checkpoints, artifact validation. | Generated projects are configurable and have complete reviewable artifacts. |
| 4. AI Provider integration | Add real providers only behind stable interfaces. | Provider request/response schema, provider selection, credential loading, timeout/error policy, usage metadata, offline fallback. | A configured provider can be used without changing Pipeline/Task contracts or exposing credentials. |
| 5. Persistent work and execution history | Persist jobs, artifacts, and audit history. | Durable storage, migrations, artifact records, execution/usage records. | Work and results survive process restart and can be queried safely. |
| 6. Users and workspaces | Isolate customer data and ownership. | User/workspace models, ownership fields, tenant isolation, workspace-scoped storage. | Every job, artifact, usage record, and cost is attributable to one workspace. |
| 7. Backend API | Provide a safe application boundary. | Versioned API, request validation, job/artifact queries, asynchronous execution interface. | Clients can create and inspect workspace-scoped work without importing Python modules. |
| 8. Web dashboard | Make work inspectable in a browser. | Goal submission, plan/result views, history, artifact browsing, controls. | A user can submit, inspect, and control authorized work from the web. |
| 9. Authentication and authorization | Control SaaS access. | Signup/sign-in, sessions, role/workspace authorization, audit events. | Only authorized users can access their workspace data and actions. |
| 10. Usage, cost, billing, deployment, and operations | Operate the SaaS safely. | Provider usage/cost accounting, credit ledger, subscriptions/payment integration, deployment, monitoring, backups, security review. | A monitored deployment can bill usage accurately and support real customer workloads. |

No stage beyond the current baseline is considered complete merely because a
placeholder module or roadmap item exists.
