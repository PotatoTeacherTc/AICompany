# AICompany Project Roadmap

## Final goal

AICompany is intended to become a multi-user SaaS automation platform. Users
will submit natural-language goals through a website; AICompany will plan,
execute, validate, and present the work while recording per-user work,
artifacts, usage, and costs. The product will ultimately support accounts,
workspaces, subscriptions, and credit-based billing.

## Current baseline: Mission 141

Mission 1-141 completion means completion of each Mission's explicitly bounded
Contract, Foundation, Fake/Offline, or Local Integration scope. It does not
mean that Project APEX as a whole is Production Ready.

Current product status: **Local/Fake SaaS Beta + bounded single-host Production
Integration**. Mission 121-130 are Foundation work; Missions 131-141 integrate
and validate the approved local Production Execution and Operations layers.

### Completion level vocabulary

- **Contract**: interfaces and data contracts are implemented and tested.
- **Foundation**: minimum replaceable structure exists; production composition
  may still be absent.
- **Fake/Offline**: deterministic local substitutes work without external
  accounts, networks, or paid services.
- **Local Integration**: components execute together in the supported local
  process or Docker development environment.
- **Production Integration**: real production dependencies are composed and
  operationally verified.
- **Production Ready**: production integration, security, recovery,
  observability, deployment, and operational acceptance are complete.

### Current user-value flow

The verified local product supports authentication and Workspace RBAC; Task
and persistent Job submission, query, and retry; ExecutionHistory; Artifact;
Usage and Quota; Department and Worker contracts; explicit loopback Ollama
Text; Fake media pipelines; Dashboard reads; Local/Fake Subscription, Billing,
and Admin; and Docker-based local execution.

These capabilities do not imply external media generation, real payment,
cloud deployment, distributed execution, or Production Ready status.

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
Mission 99 adds a persistent AI Department organization contract. Department
records contain Workspace ownership, safe name/summary, a fixed department
type, enabled state, existing Worker IDs, an optional member lead, supported
registered task types, timestamps, and an optimistic revision. DepartmentManager
reuses StateRepository with Workspace-qualified IDs for in-memory/JSON restart
recovery and supports create/get/list/update, enable/disable, Worker
assignment/removal, lead selection, and task-type changes. WorkerDirectory
registers actual injected BaseWorker instances with Workspace and capability
ownership; it does not create another Worker implementation or repository.
Default Planning, Research, Content, Media, Quality Assurance, and Operations
definitions are considered only when matching registered task types and real
Workers exist, so no fictional completed capability is created. Sensitive
text, paths, duplicate/foreign Workers, unsupported task types, invalid leads,
and stale revisions are rejected. Mission 100 workflow selection and execution
remain unimplemented at this checkpoint.
Mission 100 is the roadmap's Personal Operating System checkpoint, but no
broader Personal OS completion contract is defined in the repository. Its
verified scope is therefore the requested Department Workflow Integration:
DepartmentSelector deterministically chooses an explicit or task-type-matched
enabled Department with available same-Workspace Workers, placing the lead
first and returning only a safe selection reason. DepartmentWorkflow composes
those existing Worker instances through CollaborationOrchestrator, then calls
an injected Pipeline executor and reuses RetryExecutor, WorkspaceSettings
RetryPolicy, ExecutionHistory, structured Logging, UsageEngine, PipelineResult,
and safe Artifact metadata. Worker/collaboration failure stops the Pipeline;
transient Pipeline failure can recover through the existing Retry contract;
history, logging, and usage recording failures are isolated from a successful
core result. Workspace mismatch, missing/disabled Departments, missing Workers,
invalid results, foreign/path-bearing artifacts, raw errors, and absent usage
are handled safely. WorkspaceMonitor optionally exposes Department
enabled/disabled snapshots without adding storage. Selection is entirely
offline and deterministic; it performs no LLM, provider, network, OAuth, or
paid operation. A general desktop agent, dynamic organization design, HR
system, Web UI, and Mission 101+ SaaS backend remain outside this baseline.

The first post-Mission-100 creative validation is implemented without assigning
an undocumented Mission number. A provider-neutral text contract supports
lyrics, content plans, video scripts, and title/description output. Its safe
default is deterministic Fake generation; an Ollama adapter is available only
by explicit local configuration, requires a model name, accepts loopback
endpoints only, and was contract-tested through an injected transport because
Ollama is not installed in the verified environment. TextCreationPipeline
validates JSON schemas, rejects echoed request text, sensitive keys, absolute
paths, and non-zero cost, persists UTF-8 text artifacts through the existing
ArtifactManager, and returns PipelineResult with optional UsageMetadata. The
HybridCreativeDemo connects one Mission and Content Department workflow to
lyrics/content planning plus the existing Fake music/image/video/YouTube flow.
It is runnable from the CLI, restart-persists safe artifact metadata, and makes
no external or paid call by default.

Mission 101 establishes the Backend application foundation without replacing
the existing FastAPI routes or Automation Engine. `BackendDependencies`
provides one explicit composition contract for the existing application
services, and tests can replace each service without global mutable state.
`BackendHealthService` aggregates injected persistence, queue, and monitor
probes into safe states and reports that paid providers are disabled. Probe and
Logger failures are isolated and never expose environment values, paths,
headers, request bodies, or exception messages. `/health` now uses this service
while all prior routes remain backward compatible. FastAPI TestClient is the
verified HTTP smoke boundary; no ASGI server package, external service, network
call, new authentication claim, or Mission 102+ capability is introduced.

Mission 102 is the Phase D User boundary. User creation, normalized-email
deduplication, lookup, file persistence, credentials, login, and HTTP routes
already existed from Missions 50-69 and were not reimplemented. The actual gap
was lifecycle state: User records now carry ACTIVE/INACTIVE status and an
updated timestamp, legacy persisted records safely default to ACTIVE, and
deactivation survives file-repository restart. Inactive Users cannot log in,
refresh/current-user authorization, or receive new Workspace memberships.
`PATCH /users/{user_id}/deactivate` permits authenticated self-deactivation
only; another User receives a safe 403. Passwords, hashes, sessions, tokens,
headers, raw errors, and paths remain outside User responses. Administrative
reactivation/deactivation, email verification, profile/PII expansion, and
Mission 103 Workspace changes are not included.

Mission 103 is the Phase D Workspace boundary. Workspace creation, lookup,
listing, file persistence, membership ownership, and role enforcement already
existed and were not reimplemented. Workspace records now add ACTIVE/INACTIVE
state, update timestamp, and optimistic revision. Legacy records safely
normalize to ACTIVE revision 0; name/status updates require the exact expected
revision and stale requests return 409. Authenticated OWNER/ADMIN principals
may use `PATCH /workspaces/{workspace_id}`. Inactive Workspaces reject
authorized access, new Membership operations, and new Task submission without
deleting existing data or exposing tenant existence beyond the established
policy. Reactivation, deletion, transfer, quotas, and Mission 104 Auth changes
remain outside this scope.

Mission 104 is the Phase D Auth boundary. It hardens the existing Mission
63-66 credential, login, signed-access-token, refresh-session, and audit
implementation instead of duplicating it. Access tokens now validate an
internal version, access-token type, issuer, audience, issued time, expiry,
signature, subject, and optional session identifier. The payload contains no
email, role, Workspace, credential, or other profile data. Login and every
authenticated request re-check the current User; session-backed tokens also
re-check the current persisted session.

Refresh tokens remain opaque random values and only SHA-256 digests are stored.
Rotation replaces the digest within the same session using a revision
compare-and-save, so concurrent reuse has at most one winner and the previous
token is rejected. Logout is owner-scoped and idempotent; logout-all revokes
all current User sessions. User self-deactivation revokes all sessions.
In-memory and JSON-file repositories restore revisions, expiry, and revocation
state and safely ignore malformed records. Rate-limit infrastructure, email
verification, OAuth, MFA, administrative User management, and Mission 105 RBAC
changes remain unimplemented.

Mission 105 is the Phase D RBAC boundary. The existing OWNER, ADMIN, MEMBER,
WorkspaceMembershipService, and repositories remain the source of truth.
AuthorizationService now provides one dependency-injected decision boundary
for authenticated Workspace operations. It re-checks the current User,
session, Workspace lifecycle, Membership, and allowed role on every decision;
no role or Workspace permission is trusted from an access-token claim. Role
changes, Membership removal, User/session invalidation, and Workspace
deactivation therefore affect existing tokens immediately. The FastAPI
Workspace authorization helper delegates to this service while preserving
legacy opt-in authentication behavior and existing safe HTTP responses.
Custom policy storage, resource-level permissions, organization-wide roles,
and Mission 106 API expansion remain unimplemented.

Mission 106 is the Phase D API authentication-context integration. It closes
the remaining authenticated collection and control-route gaps without adding
a second API layer. With `auth_required=True`, `GET /auth/me` resolves the
current persisted principal, direct User lookup is self-only, Workspace
listing returns only current active Memberships, Task listing requires and
filters by an authorized Workspace, and Task cancellation/retry derives the
stored Task Workspace before authorizing. Authorization/Cookie values are
never reflected. The WorkspaceMembershipRepository adds only the missing
user-scoped query needed for collection filtering, including JSON-file
implementations. The historical `auth_required=False` default remains for
backward compatibility. API versioning, public server deployment, signup,
admin User APIs, and Mission 107 Artifact expansion remain unimplemented.

Mission 107 is the Phase D Artifact application boundary. It reuses
ArtifactManager and the in-memory/JSON FileArtifactRepository rather than
creating another store. ArtifactApplicationService returns path-free safe DTOs
with Workspace, Artifact type, Mission, and optional Task filtering,
newest-first ordering, and bounded pagination. The authenticated API exposes
Workspace-scoped list, detail, and bounded content endpoints through the
existing AuthorizationService.

Content access is limited to UTF-8 TEXT/JSON artifacts persisted beneath a
configured storage root. It resolves only validated `internal_ref` values,
limits reads to 1 MiB, returns MIME type, size, and SHA-256 checksum, and
recursively redacts sensitive JSON keys. Missing files become MISSING without
revealing paths or raw filesystem errors. Metadata restart recovery and
traversal/corrupt-record rejection remain in FileArtifactRepository. Binary
download/streaming and artifact deletion/archive policy are not implemented.
Mission 108 completes the named Phase D Usage boundary by exposing the
existing UsageEngine through a read-only, Workspace-scoped application
service and authenticated API routes. Usage records support provider, model,
Mission, timezone-aware date-range, and bounded pagination filters. Summary
responses aggregate only fields that are actually present, preserve zero
estimated cost, and clearly state that estimated cost is not a billed amount.
The in-memory and JSON repositories remain the source of truth, so restart
recovery, idempotency, and Workspace isolation are retained without a second
ledger. The current aggregation is bounded to the latest 100 matching records
and reports when it is limited. Pricing, credits, billing, and subscription
work are not implemented. The code-based gap analysis below now defines the
next sequence without treating any of those missing contracts as complete.

## Post-backend gap analysis

The Mission 109+ order below is based on the source and tests at Mission 108,
not on placeholder numbering:

| Area | Current implementation | Level | Missing contract |
|---|---|---|---|
| Backend job execution | `AutomationService` submits to the in-memory `TaskQueue`; `PersistentJobQueue` and `InProcessJobWorker` exist separately. | PARTIAL | A DI composition that persists an accepted execution and processes it outside the request path without losing Workspace/Mission ownership. |
| Job and execution access | Task list/detail/cancel/retry routes exist; Persistent Job, Batch, and ExecutionHistory do not have a unified authenticated API. | PARTIAL | Workspace-scoped Job status/control and execution/history/result queries over persistent state. |
| AI organization access | `DepartmentManager`, `WorkerDirectory`, and `DepartmentWorkflow` are tested domain services used by the creative composition. | FOUNDATION_ONLY | Authenticated Department and safe Worker-capability application/API contracts. |
| Artifact lifecycle | Safe list/detail and bounded TEXT/JSON reads exist; metadata-only deletion exists only at the core boundary. | PARTIAL | Explicit archive/soft-delete policy, lifecycle authorization, retention semantics, and stable result-to-artifact access. |
| Quota and budgets | UsageEngine reports durable usage and Settings has operational limits. | FOUNDATION_ONLY | Enforced Workspace quotas and budget decisions before work is accepted or a provider is invoked. |
| Plans and entitlements | No plan, entitlement, credit, or billing domain exists. | NOT_STARTED | Provider-neutral feature/limit entitlements that do not yet charge money. |
| Dashboard support | Monitor, audit, tasks, artifacts, and usage each expose parts of the required data. | FOUNDATION_ONLY | One authenticated dashboard-oriented read model with stable pagination and partial-failure behavior. |
| Web dashboard | No UI application or frontend build exists. | NOT_STARTED | Authenticated user interface over the completed Backend contracts. |
| Subscription, billing, admin | Not implemented. | NOT_STARTED | Subscription lifecycle, billable ledger/payment boundary, and operator authorization/audit. |
| Real media providers | Music, image, video, and YouTube remain Fake; Ollama text is explicit loopback-only. | BLOCKED_EXTERNAL | Explicit provider credentials/accounts, cost approval, network policy, and integration verification. |
| Deployment and operations | Tests use FastAPI TestClient; no selected ASGI deployment, external database/broker, CI/CD, or cloud runtime exists. | FOUNDATION_ONLY | Production runtime, migrations, observability, backup, and security/deployment policy. |

The existing synchronous Task controls, Usage reporting, Artifact reads,
Monitor, Logging, and Department domain are not renamed or counted again as
new Missions.

## Defined next Missions

### Mission 109 — Persistent Job Execution

- Status: completed.
- Goal: connect accepted Backend work to the existing persistent Job contract
  without running a Pipeline inside the HTTP request.
- Scope: a dependency-injected application coordinator over
  `PersistentJobQueue`, `InProcessJobWorker`, Mission/Task identity, existing
  settings, Logging, and ExecutionHistory; idempotent submission; safe
  restart recovery; explicit in-process worker execution.
- Excludes: HTTP Job query/control routes, distributed workers, Redis/Celery,
  OS services, and concurrent multi-process claims.
- Completion: an accepted Workspace execution survives repository restart,
  is claimed once, produces the existing PipelineResult/history contract, and
  safely records retryable or terminal failure.
- Prerequisites: Missions 91, 93, 96, 101, and 106.
- Tests: submission/idempotency, Workspace isolation, restart recovery,
  single claim, success/failure/retry, missing target, Logging failure
  isolation, and no network/paid-provider calls.

The implemented `PersistentExecutionService` composes the existing
PersistentJobQueue, InProcessJobWorker, ExecutionHistory, ArtifactManager, and
UsageEngine. Registered Pipeline targets remain dependency-injected. Accepted
Jobs are idempotent per Workspace, abandoned RUNNING Jobs recover as PENDING
when configured Workspaces are restored, and Queue mutations use an
in-process lock so concurrent workers have one claim winner. Terminal
PipelineResult values upsert a prompt-free, path-free history record, validate
same-Workspace Artifact references already registered by the Pipeline, and
record only present Usage fields. Integration failures remain safe and retry
metadata continues through the existing Queue contract. BackendDependencies
can receive this coordinator, but no Job, Execution, or Batch route is added.

### Mission 110 — Job & Execution API

- Status: completed.
- Goal: expose persistent execution state and controls through the
  authenticated Backend boundary.
- Scope: Workspace-scoped Job list/detail, safe retry/cancel semantics,
  ExecutionHistory list/detail, Batch status where already supported, and
  path-free result/Artifact references. Reuse Mission 106 authorization and
  Mission 109 execution services.
- Excludes: streaming events, WebSocket, queue administration, Dashboard view
  models, and distributed cancellation.
- Completion: an authorized member can submit, inspect, and safely control
  their Workspace Job and retrieve its execution result/history; another
  Workspace receives no existence disclosure.
- Prerequisites: Mission 109.
- Tests: RBAC, filtering/pagination, pending/running/terminal states,
  idempotency, valid/invalid retry and cancellation, partial Usage, safe
  errors, result/Artifact linkage, restart recovery, and cross-Workspace
  denial.

Mission 110 adds `JobExecutionApiService` and authenticated Workspace routes
for persistent Job submission/list/detail/cancel/retry, ExecutionHistory
list/detail, and the existing BatchManager list/detail contract. Responses
reuse Queue state, strip task/prompt fields, expose only path-free Artifact
references and recorded Usage, and return non-disclosing not-found responses
across Workspaces. Cancellation is limited to PENDING Jobs and retry to
retryable FAILED Jobs. The legacy in-memory Task API remains unchanged.

### Mission 111 — AI Organization API

- Status: completed.
- Goal: expose the existing AI Department organization safely without
  duplicating Worker or Department storage.
- Scope: application DTOs and authenticated Department list/detail/create/
  update/enable operations, Worker capability listing, assignment, lead, and
  optimistic revision handling.
- Excludes: arbitrary code upload, dynamic LLM hiring, payroll/HR, provider
  credentials, and execution scheduling.
- Completion: OWNER/ADMIN can manage safe Department composition and MEMBER
  can inspect it, with actual registered Worker capabilities and Workspace
  isolation preserved.
- Prerequisites: Missions 105, 106, and 110.
- Tests: RBAC, revision conflict, Worker capability validation, disabled
  Department behavior, restart recovery, sensitive-data rejection, and
  cross-Workspace denial.

Mission 111 adds `OrganizationService` and authenticated Workspace Department
list/detail/create/update/enable/disable plus Worker assignment/removal routes.
OWNER/ADMIN manage organization state and MEMBER may read it. WorkerDirectory
is exposed only as path-free capability list/detail DTOs because it stores
injected live BaseWorker instances and has no safe persistence or API creation
contract. Worker code upload, registration, mutation, and deletion APIs are
therefore intentionally not invented. Department persistence, optimistic
revision, lifecycle, and cross-Workspace validation remain in the existing
DepartmentManager.

### Mission 112 — Artifact Lifecycle (Complete)

- Goal: define a reversible result lifecycle over existing Artifact metadata
  and content access.
- Scope: AVAILABLE/ARCHIVED/MISSING state policy, explicit soft archive and
  restore operations, result-to-artifact references, optimistic or
  idempotent transitions, and authenticated lifecycle queries.
- Excludes: deleting user files, binary streaming, object storage, retention
  automation, and CDN delivery.
- Completion: authorized lifecycle changes survive restart, never delete the
  underlying file, and remain path-free and Workspace-scoped.
- Prerequisites: Missions 107 and 110.
- Tests: archive/restore idempotency, MISSING behavior, result linkage,
  restart recovery, RBAC, path traversal, corrupt metadata, and
  cross-Workspace denial.

Mission 112 extends the existing manager, repository, application service, and
authenticated API rather than adding storage. `AVAILABLE` remains the
codebase's established active state; `ARCHIVED` is a reversible metadata-only
state and missing files remain `MISSING`. OWNER/ADMIN may archive or restore,
MEMBER may read and filter by status, and existing Job/History references
remain valid. No physical file is deleted.

### Mission 113 — Quota & Budget Enforcement (Complete)

- Goal: turn existing Usage and Workspace settings into pre-execution safety
  decisions.
- Scope: Workspace quota/budget contracts, period-aware counters, reservation
  and release semantics for accepted Jobs, enforcement before Worker/provider
  execution, and safe limit status reporting.
- Excludes: pricing inference, credits, invoicing, payments, subscriptions,
  and enabling paid providers.
- Completion: work exceeding an explicit limit is rejected before execution;
  concurrent/idempotent reservations cannot double count; missing Usage
  remains safe and no absent cost is invented.
- Prerequisites: Missions 97, 98, 109, and 110.
- Tests: limits below/at/above boundary, reservation idempotency, release,
  restart recovery, Workspace isolation, partial/missing Usage, clock-period
  rollover, and Logging failure isolation.

Mission 113 adds an injected QuotaEngine over the existing UsageEngine and
StateRepository. Explicit token, estimated-cost, and execution limits are
enforced before persistent Job submission and token/cost limits are rechecked
before the target callback. Workspace-qualified idempotent reservations survive
restart and use an in-process lock. The current local contract supports the
documented `ALL_TIME` period only; distributed quota and billing are absent.

### Mission 114 — Plans & Entitlements (Complete)

- Goal: define non-billing product plans and Workspace feature entitlements
  that quota enforcement can consume.
- Scope: injected plan catalog, Workspace plan assignment, versioned
  entitlements, feature/limit resolution, safe defaults, and audit records.
- Excludes: subscriptions, invoices, payment providers, tax, credits, and
  plan purchase flows.
- Completion: effective entitlements are deterministic, restart-safe,
  auditable, Workspace-scoped, and enforced by Mission 113 without embedding
  plan names in Pipelines.
- Prerequisites: Mission 113.
- Tests: default/custom plans, assignment revision, invalid entitlement,
  downgrade limits, restart recovery, RBAC, audit failure isolation, and
  cross-Workspace denial.

Mission 114 supplies an injected FREE/PRO/BUSINESS non-billing catalog and
Workspace plan assignment through the existing StateRepository. Effective
quota resolution is Workspace override, then Plan entitlement, then the
existing unlimited fallback when neither is composed. Artifact archive is the
single enforced feature entitlement. No price, purchase, subscription,
invoice, trial, or payment state exists.

### Mission 115 — Dashboard API (Complete)

- Goal: provide a stable Backend read model for a future Web Dashboard.
- Scope: authenticated Workspace overview combining Monitor, recent Jobs and
  executions, Batch progress, Usage/quota status, and Artifact summaries with
  bounded queries.
- Excludes: frontend code, WebSocket, charts, billing administration, and
  execution side effects.
- Completion: one safe read-only response supports the primary dashboard
  state without replacing Monitor or duplicating repositories.
- Prerequisites: Missions 110, 112, 113, and 114.

Mission 115 adds one authenticated, read-only Workspace overview assembled
from existing Workspace, Job/Execution, Artifact, Usage/Quota, Plan, and
Organization application services. It creates no repository or analytics
store. Current aggregation is bounded to the latest 100 records; no general
today/7-day/30-day filter is invented.
- Tests: empty/partial/full state, pagination, stale/missing records, Usage
  omission, read-only behavior, RBAC, restart recovery, and
  cross-Workspace denial.

### Mission 116 — Web Dashboard (Complete)

- Goal: deliver the first authenticated browser UI over existing Backend
  contracts.
- Scope: login/session use, Workspace selection, Job submission/status,
  execution results, safe Artifact access, Usage/quota visibility, and
  Department inspection.
- Excludes: subscription checkout, billing, admin console, real-time
  WebSocket, provider credentials, and production cloud deployment.
- Completion: a user can complete the principal Workspace workflow through
  the browser using only authorized API calls and without exposing secrets,
  prompts, raw errors, or internal paths.
- Prerequisites: Mission 115.
- Tests: frontend unit/contract tests, authenticated happy path, expired
  session, role restrictions, partial failure, safe rendering, and offline
  end-to-end execution.

### Mission 117 — Subscription Domain (Complete)

- Goal: model one Workspace product-subscription lifecycle without payment.
- Scope: TRIALING/ACTIVE/PAST_DUE/CANCELLED/EXPIRED transitions, plan changes,
  period-end cancellation/undo, shared local persistence, RBAC, and audit.
- Excludes: prices, invoices, checkout, proration, refunds, payment providers,
  and external network calls.
- Completion: active subscriptions apply the existing Plan; cancelled or
  expired records remain while effective entitlements fall back to FREE.
- Prerequisites: Missions 114 and 116.
- Tests: lifecycle, duplicate prevention, restart, Workspace isolation,
  fallback, validation, and redaction.

### Mission 118 — Billing Foundation (Complete)

- Goal: provide local invoice accounting for Subscription without real payment.
- Scope: Workspace BillingAccount, injected development-only Price contracts,
  idempotent period Invoice creation, and MANUAL/FAKE Payment records.
- Excludes: Stripe or other SDKs, checkout, cards/accounts, webhooks, tax,
  exchange, proration, refunds, and external network calls.
- Completion: integer-minor-unit invoices survive restart, duplicate periods
  and successful payments are blocked, and successful Fake/Manual records
  transition an Invoice to PAID.
- Prerequisites: Mission 117.
- Tests: account/price/invoice/payment contracts, validation, idempotency,
  restart, Workspace isolation, sensitive-field rejection, and Subscription
  regression.
- Completion level: **Manual/Fake Foundation**. A real payment Provider is not
  implemented.

### Mission 119 — Admin Operations (Complete)

- Goal: provide a narrow SaaS operator boundary distinct from Workspace roles.
- Scope: injected Platform ADMIN identities; safe Workspace/User,
  Subscription/Invoice, Usage/Quota, failed Job, audit, and Plan reads; plus
  Workspace activation, Subscription Plan change, failed Job retry, Invoice
  void, and FAKE payment recording.
- Excludes: impersonation, credential/secret access, physical deletion,
  arbitrary code, real payment/refund, and infrastructure control.
- Completion: non-platform users receive no Admin access; mutations are
  bounded and audited, while inactive Workspace data remains.
- Prerequisites: Missions 110, 117, and 118.
- Tests: role separation, aggregation, limited actions, data preservation,
  invalid actions, frontend visibility, regressions, and production build.

### Mission 120 — SaaS Beta Completion (Complete, Local/Fake)

- Goal: verify Missions 109-119 as one local, offline SaaS Beta boundary.
- Scope: explicit FREE onboarding composition, safe loopback CORS, debug-off
  FastAPI defaults, liveness/readiness, environment examples, local Backend
  and Frontend commands, Fake E2E regression, and completion documentation.
- Excludes: cloud deployment, real payment/provider credentials, Redis/broker,
  distributed Workers, object storage, real media providers, Workflow Builder,
  Marketplace, mobile, and Enterprise functionality.
- Completion: Backend/Frontend tests and production build pass; persistence,
  Job execution/recovery, Subscription/Plan, Manual/Fake Billing, Admin,
  Dashboard, security defaults, and paid-provider blocking are verified
  without external network calls.
- Prerequisites: Missions 109-119.
- Tests: 40-test integration selection, full Backend suite, Frontend suite,
  production build, readiness/CORS/onboarding/restart/security checks.
- Completion level: **Local/Fake SaaS Beta**.

### Mission 121 — Cloud Foundation (Complete, Provider-Neutral)

- Goal: run the local SaaS Beta as a reproducible Docker development stack.
- Scope: Backend and multi-stage Frontend images, Compose Backend/Frontend/
  PostgreSQL/Redis services, private networking, named volumes, loopback host
  ports, production configuration seams, and health checks.
- Excludes: Kubernetes, cloud deployment, managed services, real secrets, and
  replacing existing repositories with PostgreSQL/Redis adapters.
- Completion: all services build, start healthy, log safely, and stop cleanly;
  existing Backend and Frontend regressions pass.
- Prerequisites: Mission 120.
- Tests: Compose config/build/up/ps/logs/down, Backend 310 tests, Frontend two
  tests, and production build.
- Completion level: **Docker-based Local Development Foundation**.

### Mission 122 — CI Pipeline (Complete)

- Goal: provide repeatable GitHub pull-request and push verification without
  adding deployment authority.
- Scope: least-privilege GitHub Actions jobs for Backend syntax lint/tests and
  Frontend type lint/tests/build, with pip/npm caches and bounded build
  artifact upload.
- Excludes: deployment, write permissions, cloud credentials, production
  secrets, registry publication, and environment mutation.
- Completion: the workflow contract, local equivalents, full Backend suite,
  Frontend suite, type lint, and production build pass.
- Prerequisites: Mission 121.
- Tests: workflow safety contract, AST syntax lint, Backend 312 tests,
  Frontend two tests, `tsc --noEmit`, and production build.
- Completion level: **CI Verification Foundation**. Production deployment is
  not implemented.

### Mission 123 — Production Security (Complete, Single-Process)

- Goal: harden the current HTTP boundary and reject unsafe production
  configuration before serving requests.
- Scope: CSP and standard response headers, HSTS preparation, secure-cookie
  normalization, strict CORS, injected in-memory rate limiting, environment
  validation, and production signing-secret validation/injection.
- Excludes: certificates, TLS termination, cloud firewall/WAF, distributed
  rate limiting, external identity services, and secret storage.
- Completion: production requires HTTPS origins and a strong injected secret;
  safe 429 responses and security headers do not expose request data.
- Prerequisites: Mission 122.
- Tests: environment/secret/origin validation, headers/CSP/HSTS, Cookie policy,
  rate limiting, CORS denial, Auth regression, Backend 317 tests, Frontend
  tests, and production build.
- Completion level: **Single-process Security Foundation**. TLS termination,
  distributed enforcement, and managed secrets are not implemented.

### Mission 124 — Monitoring (Complete, Process-Local)

- Goal: provide safe operational visibility without replacing ExecutionHistory
  or the existing Workspace Monitor.
- Scope: injected aggregate request metrics, request and correlation IDs,
  structured request completion/failure events, health-probe metrics, bounded
  error summaries, and the read-only `/health/metrics` endpoint.
- Excludes: external monitoring SaaS, Prometheus exporters, distributed
  tracing, durable metric storage, alerting, and cloud monitoring.
- Completion: metrics and structured events contain only safe aggregates and
  identifiers; logger failure cannot change an HTTP result.
- Prerequisites: Mission 123.
- Tests: five focused tests and Backend 322 tests.
- Completion level: **Process-local Foundation**. Distributed metrics, tracing,
  and alerting are not implemented.

### Mission 125 — Backup & Recovery (Complete, Fake/Offline)

- Goal: export and restore Workspace-owned product metadata without adding a
  cloud storage dependency.
- Scope: an injectable BackupStore contract, deterministic in-memory Fake
  store, versioned bounded JSON export/restore, Workspace metadata, safe
  Artifact metadata, Subscription metadata, and Manual/Fake Billing metadata.
- Excludes: artifact file contents, user credentials or personal data, cloud
  or object storage, schedules, automation, retention, and destructive delete.
- Completion: restore validates schema and Workspace ownership before writes,
  rejects implicit overwrite, strips sensitive fields and absolute paths, and
  preserves partial product metadata through the existing repositories.
- Prerequisites: Mission 124.
- Tests: five focused tests, 23 related tests, and Backend 327 tests.
- Completion level: **Fake/In-memory Metadata Foundation**. Real cloud backup
  and operational disaster recovery are not implemented.

### Mission 126 — Production Infrastructure (Complete, Adapter Foundation)

- Goal: make the shared StateRepository selectable across local and
  production-oriented infrastructure without changing application services.
- Scope: PostgreSQL DB-API and Redis-client StateRepository adapters,
  RepositoryFactory, validated environment configuration, safe health probes,
  and lifespan-based graceful shutdown.
- Excludes: schema creation or migration, automatic production activation,
  cloud-specific code, destructive data operations, and bundled database
  drivers.
- Completion: injected adapters preserve Workspace reads, parameterized writes,
  safe health status, and deterministic resource closure.
- Tests: five focused tests, Backend 332 tests, Frontend tests/build, and
  healthy Compose verification.
- Completion level: **PostgreSQL/Redis Repository Adapter Foundation**.
  Schema, migration, drivers, and default application composition are not
  implemented.

### Mission 127 — Object Storage Abstraction (Complete, Offline)

- Goal: separate Artifact bytes from Artifact metadata behind an injectable
  storage contract.
- Scope: StorageProvider, safe local provider, in-memory Fake S3 provider,
  ArtifactStorageAdapter, bounded opaque signed-reference contract, and
  StorageFactory.
- Excludes: AWS/GCP/Azure SDKs, network access, real buckets, credential
  storage, and public URL delivery.
- Completion: content round trips locally/Fake, metadata remains
  Workspace-scoped, path escape is rejected, and references expose no absolute
  path.
- Tests: five focused tests, 16 related tests, Backend 337 tests, Frontend
  tests/build, and healthy Compose verification.
- Completion level: **Local/Fake Provider Foundation**. Real cloud Object
  Storage is not implemented.

### Mission 128 — Workflow Builder Foundation (Complete, Definition-Only)

- Goal: define portable workflows without expanding runtime execution.
- Scope: versioned Workflow and Step definitions, conditional branches,
  bounded retry policy, parallel-group contract, DAG validation, and
  deterministic JSON import/export.
- Excludes: UI, execution, scheduling, persistence, plugin resolution, and
  provider calls.
- Completion: duplicates, unknown references, cycles, invalid retry values,
  malformed JSON, and oversized definitions are rejected.
- Tests: five focused tests, Backend 342 tests, Frontend tests/build, and
  healthy Compose verification.
- Completion level: **Definition/Validation Foundation**. Execution,
  persistence, API, and UI are not implemented.

### Mission 129 — Plugin SDK Foundation (Complete, Local-Only)

- Goal: define a safe extension contract without loading external code.
- Scope: Plugin interface, validated Manifest and Capability contracts,
  semantic major-version compatibility, explicitly injected PluginLoader, and
  sanitized Fake Plugin.
- Excludes: filesystem discovery, dynamic imports, sandboxing arbitrary code,
  Marketplace, download, network access, secrets, and payment.
- Completion: only registered factories load; identity, version, capabilities,
  request shape, and sensitive-field removal are enforced.
- Tests: five focused tests, Backend 347 tests, Frontend tests/build, and
  healthy Compose verification.
- Completion level: **Local/Fake Contract Foundation**. External discovery,
  download, and isolated execution are not implemented.

### Mission 130 — Marketplace Foundation (Complete, Local/Fake)

- Goal: manage compatible package metadata without download or payment.
- Scope: Package and Dependency contracts, semantic compatibility,
  LocalMarketplaceRegistry, dependency resolution, Workspace-isolated Fake
  install/list/remove, and dependent-removal protection.
- Excludes: external registry, package bytes, download, code execution,
  publishing, reviews, licensing, pricing, checkout, and payment.
- Completion: local dependencies resolve deterministically; incompatible SDK,
  missing versions, cycles, duplicates, cross-Workspace state, and unsafe
  removal are rejected.
- Tests: six focused tests, 11 related tests, Backend 353 tests, Frontend
  tests/build, and healthy Compose verification.
- Completion level: **Local Metadata/Fake Install Foundation**. External
  Registry, package payload, distribution, and payment are not implemented.

### Mission 131 — PostgreSQL Production Persistence Integration (Complete, bounded Production Integration)

- Goal: connect the existing shared `StateRepository` PostgreSQL adapter to
  environment-driven application composition and make its schema reproducible.
- Scope: `memory`/`json`/`postgresql` selection, psycopg connection creation,
  an additive versioned SQL migration runner, Workspace-scoped shared-state
  table, automatic migration in the production app factory, existing Plan API
  composition, safe health details, graceful connection closure, and Docker
  PostgreSQL verification.
- Excludes: User/Workspace/Artifact repository rewrites, Redis Queue/Lock/
  Broker, distributed Workers, cloud database deployment, Secret Manager,
  TLS, and destructive migration.
- Completion: migrations apply to an empty database and rerun safely; shared
  state survives Backend restart; identical record IDs remain isolated across
  Workspaces; health distinguishes connection and migration state without
  exposing the database URL.
- Tests: six focused tests including real Docker PostgreSQL integration,
  Backend 359 tests, Frontend tests/build, and healthy Compose verification.
- Completion level: **bounded PostgreSQL Production Integration** for the
  shared StateRepository only. Project APEX remains not Production Ready.

### Mission 132 — Redis Queue Integration (Complete, bounded Production Integration)

- Reuses the existing Job and `StateRepository` contracts while Redis stores
  only Workspace-namespaced FIFO pending/processing Job IDs.
- `memory` remains the default; production Compose explicitly selects Redis.
- Bounded blocking reserve, acknowledge, safe connection errors, namespace
  isolation, PostgreSQL-backed restart reads, and Job API submission are
  verified. Distributed locking and external Worker consumption remain
  Mission 133 and Mission 134.
- Tests: six focused tests, Backend 365 tests, and real Docker Redis restart
  verification.

### Mission 133 — Redis Distributed Lock (Complete)

- Adds a shared owner-token/TTL Lock lease contract with memory and Redis
  implementations. Redis acquire is atomic, release/renew use owner-verified
  Lua scripts, stale locks expire, and Workspace/Job IDs scope keys.
- Redis outage fails closed with a safe category. Worker consumption remains
  Mission 134.
- Tests: four focused tests, Backend 369 tests, and real two-client Redis
  competition verification.

### Mission 134 — Distributed Worker Execution (Complete, Offline target)

- Adds a separately runnable Worker entrypoint that reserves Redis Jobs,
  obtains the owner-token Lock, executes through PersistentExecutionService,
  and records PipelineResult, ExecutionHistory, and Usage in PostgreSQL.
- Compose runs Backend and Worker separately. Polling is bounded, shutdown is
  graceful, and an abandoned processing item is minimally returned after its
  Lock expires. Only the deterministic `offline-success` target is registered;
  real providers remain disabled. Extended Retry/DLQ remains Mission 135.
- Tests: four focused tests, Backend 373 tests, and real Backend→Redis→Worker→
  PostgreSQL→Backend Docker verification.

### Mission 135 — Retry, Recovery, and DLQ (Complete, bounded Production Integration)

- Adds bounded exponential-backoff retry scheduling in a Redis sorted set and
  a Workspace-scoped Redis dead-letter list while PostgreSQL remains the
  authoritative Job and retry-state store.
- Retry attempts preserve their count across Worker executions. Terminal
  failures are no longer requeued; abandoned RUNNING Jobs whose Lock is absent
  are returned to pending and can be completed after Worker restart.
- Failure categories and messages remain sanitized, retry promotion is
  bounded, and no infinite retry or external Provider call is introduced.
- Tests: five focused recovery tests, Backend 378 tests, and real Docker Redis
  verification for three-attempt backoff/DLQ plus crashed-Worker recovery.
- Excludes multi-instance validation, which remains Mission 136.

### Mission 136 — Multi-instance Execution Validation (Complete, local Docker validation)

- Validates two Backend and two external Worker containers against one
  PostgreSQL and one Redis instance. A local Nginx gateway provides a stable
  loopback API endpoint and re-resolves scaled Backend containers.
- Redis idempotency ownership closes the concurrent cross-Backend submission
  race while PostgreSQL remains authoritative. Backend health exposes a safe
  container instance ID; Worker IDs default to container hostnames.
- Tests and Docker E2E cover one completion for concurrent duplicate submits,
  multiple Jobs in two Workspaces, both Worker containers consuming work,
  Backend and Worker loss, restart persistence, and Redis reconnect without
  terminating Workers.
- Memory/JSON/local modes remain compatible. This is local single-node
  validation, not Cloud deployment or Production Ready status. `/ready`
  remains `not_ready` until the existing Monitor probe is composed.
- Excludes Kubernetes, leader election, Redis Cluster, new Broker/framework,
  Cloud/TLS/Secret Manager, and work after Mission 136.

### Mission 137 — Production Readiness Integration (Complete, local integration)

- Production composition connects existing persistence, Redis Queue,
  process-local monitoring metrics, and expiring Redis Worker heartbeats to
  the existing readiness contract. Required and optional probes are explicit.
- Probe execution is bounded, liveness remains separate, shutdown immediately
  blocks readiness, and safe results omit dependency URLs and exceptions.
- Memory/JSON and in-process Queue modes remain supported without requiring an
  external Worker heartbeat.
- Tests: three focused tests, Backend 386 tests, and Docker verification of
  `ready` → Redis outage `not_ready` → recovered `ready`.
- This does not make Project APEX Production Ready and adds no external APM.

### Mission 138 — Artifact Object Storage Integration (Complete, local/Fake integration)

- Connects the existing StorageProvider and ArtifactStorageAdapter to
  ArtifactManager, safe content reads, production Backend/Worker composition,
  and readiness without replacing Artifact contracts.
- PostgreSQL StateRepository stores Workspace-qualified metadata/internal keys;
  LocalStorageProvider stores bytes in the shared Docker volume. Unconfigured
  local/test composition uses the memory-only Fake S3 provider.
- Storage failure occurs before metadata creation; missing objects report
  `MISSING`; archive/restore remains metadata state and metadata deletion uses
  a non-destructive tombstone.
- Tests: three focused tests, Backend 389 tests, and Docker create/read/restart/
  read verification with exact test Artifact cleanup.
- No real S3-compatible product, Cloud account, credential, or public URL is
  implemented.

### Mission 139 — Production Configuration Security (Complete, local contract)

- Adds production-only validation for PostgreSQL, authenticated Redis,
  Local Artifact storage, HTTPS origins, strong signing material, and the
  immutable paid-provider-off policy.
- Existing environment variables remain supported; matching `_FILE` inputs
  provide Docker-secret-compatible loading with duplicate-source, size,
  missing-file, and empty-value rejection. Files are reread on application
  restart so external rotation is not blocked.
- Local/test modes retain safe defaults. Errors expose categories only and no
  Secret value or source path.
- Tests: three focused tests and Backend 392 tests. No Secret Manager, Vault,
  real credential, or generated user Secret is included.

### Mission 140 — TLS and Gateway Security Foundation (Complete, local TLS validation)

- Hardens the existing Nginx Gateway with bounded request bodies/timeouts,
  overwritten forwarding headers, no-store sensitive routes, and standard
  security headers while preserving dynamic Backend failover.
- An optional Compose override mounts an externally supplied certificate/key,
  enables TLS 1.2/1.3 on loopback 8443, and redirects loopback HTTP to HTTPS.
- Tests: two focused tests, Backend 394 tests, and Docker verification with an
  ephemeral self-signed certificate: HTTPS readiness 200, HSTS present, and
  HTTP 308 redirect. Temporary certificate files were removed.
- No certificate/private key is tracked; public CA, DNS, external load
  balancer, WAF, CDN, and Cloud TLS remain unimplemented.

### Mission 141 — Single-host Production Deployment Validation (Complete, local Production Integration)

- Adds a production-like Compose overlay with external file-based Secret
  injection, a one-shot migration gate, named PostgreSQL/Redis/Artifact
  volumes, bounded resources/log rotation, restart policies, and independently
  scalable Backend and Worker services.
- Docker E2E verified two Backends and two Workers, HTTPS API/Frontend,
  readiness, Job execution, Workspace isolation, Local Artifact persistence,
  single-instance failover, dependency restart plus application reconnection,
  and PostgreSQL/Artifact backup restoration into separate verification
  targets.
- Tests: two focused contracts and Backend 396 tests. The operational procedure
  is documented in `OPERATIONS_RUNBOOK.md`; no Secret, certificate, backup, or
  validation data remains in Git or the running Docker environment.
- This is a single-host local Production Integration, not Cloud HA or a
  Production Ready declaration. Public TLS/DNS, Secret Manager, real Object
  Storage, managed databases, and the official next numbered Mission remain
  undefined.

## Real-use connection roadmap: @1-@10 (approved; @1-@4 complete)

This sequence is separate from the historical Mission numbering. Mission
1-141 records completed Foundation and bounded Production Integration work.
`@1`-`@10` defines the approved real Provider and content E2E product-completion
sequence and must not be renamed Mission 142 or marked complete in advance.

The v1 target is:

```text
Natural-language request
-> music plan and Suno input package
-> user performs the manual Suno generation
-> completed audio intake and project resume
-> shared content brief
-> real image generation
-> blog package
-> FFmpeg video
-> YouTube publication package and private upload
-> supported blog draft publication
-> Artifact, Usage, ExecutionHistory, and Dashboard verification
```

Suno execution remains the one manual step. A future official Suno API may be
added behind the existing Provider abstraction without changing the workflow.

### @1 — Real LLM foundation (Complete, Mock-verified; real smoke pending)

- Goal: connect one real LLM through the existing Provider Factory while
  retaining Fake Providers and tests.
- Scope: real and structured response validation, PipelineResult conversion,
  timeout/safe errors, available Usage and estimated cost, environment or
  `_FILE` Secret input, and credential-gated smoke verification.
- Completion: a real response is returned through existing common contracts.
- Implemented boundary: `OpenAITextProvider` uses the official Responses API
  behind the existing TextProvider/ProviderFactory contracts. It supports
  text and bounded JSON Schema output, safe response metadata, partial Usage,
  injected timeout/transport, `_FILE` Secret precedence, and safe error
  categories. `store=false` is sent on every request.
- Safety: Fake Text remains the default. OpenAI selection requires explicit
  `ALLOW_PAID_PROVIDER=true`, a model, and an API key. Production configuration
  continues to reject paid-provider enablement, so no production Compose call
  is possible by default.
- Verification: Mock/Fake tests cover selection, policy, Secret loading,
  conversion, validation, Usage, and failure categories. The opt-in smoke test
  is skipped unless all explicit paid/network/credential conditions are set.
  No real API request was made during @1 completion.
- Cost boundary: no verified shared pricing registry exists, so token counts
  are recorded when present and `estimated_cost_usd` remains unavailable
  rather than being invented.

### @2 — Music planning employee and Suno creation package (Complete, Mock-verified; real smoke pending)

- Goal: use the real LLM to produce everything required immediately before
  manual Suno execution.
- Scope: plan, title candidates, genre/mood, BPM/key suggestions, instruments,
  vocals, song structure, full lyrics, Suno and style prompts, exclusions,
  recommended settings, creation notes, and Artifact storage.
- Completion: the user can copy the package into Suno and create the audio.
- Implemented boundary: `MusicPlanningService` composes the existing
  TextProvider/ProviderFactory, a bounded `MUSIC_PLAN` JSON Schema, domain
  validation, Suno package formatting, PipelineResult, UsageMetadata,
  ArtifactManager, ExecutionHistory, Workspace isolation, and the new
  `WAITING_FOR_INPUT` Task/Pipeline state. It writes a structured plan JSON,
  a Suno package JSON, and a human-readable package Markdown file. A minimal
  `music-plan` CLI is the user entry point.
- Safety and verification: Fake Text remains the deterministic default;
  injected OpenAI transport verifies the real-provider DI path without a
  network or paid call. Prompts and user request text are not returned,
  logged, or written to History/Artifact metadata. Suno remains a manual user
  action. No actual OpenAI credential smoke was executed for @2.

### @3 — Completed audio intake and project connection (Complete, local integration)

- Goal: safely discover user-produced `mp3`, `wav`, `flac`, or `m4a` audio in
  a Workspace input boundary and attach it to the music project.
- Scope: name/project-ID discovery; missing, duplicate, unsupported, and
  damaged-file handling; Workspace isolation; path-escape prevention; no
  source mutation; duration/basic metadata; Artifact and Music Project links.
- Completion: a user command selects the correct audio for the next Pipeline.
- Implemented boundary: the `music-import` CLI resolves a name only inside
  `logs/music-plans/inputs/{workspace_id}/music`, accepts `mp3`, `wav`,
  `flac`, and `m4a`, and rejects missing, ambiguous, unsupported, empty,
  oversized, escaped, symlinked, signature-mismatched, or damaged input.
  `AudioInputValidator` combines a bounded signature check with timeout-bound
  `ffprobe` audio-stream validation and records safe format, duration, codec,
  optional sample rate/channels, size, and SHA-256 metadata.
- Project connection: @2 Artifact metadata plus its `WAITING_FOR_INPUT`
  ExecutionHistory record establish the existing project boundary. The source
  remains unchanged while a copy is registered as a Workspace-qualified
  `MUSIC_SOURCE_AUDIO` Artifact through Local Object Storage. A shared
  StateRepository persists the one-time audio link and `INPUT_READY` state;
  duplicate links and cross-Workspace access are rejected. @3 performs no AI
  call and creates no Usage. Suno execution remains manual.

### @4 — Content brief and project orchestration (Complete, Mock-verified local integration)

- Goal: make music, image, blog, video, and YouTube outputs share one project
  and content brief.
- Scope: audience, message, emotion/mood, visual/video/blog direction, SEO
  terms, platform guidance, prohibited expressions, Workspace checks,
  stage input/output references, failure records, successful-stage reuse,
  `waiting_for_input` for manual Suno, and same-project resume after intake.
- Completion: every downstream Pipeline consumes the same brief/project
  contract.
- Implemented boundary: one deterministic, Workspace-qualified Content Project
  is created from the existing @2 `MUSIC_PLAN` Artifact and @3
  `MUSIC_SOURCE_AUDIO`/`INPUT_READY` link. It persists revisioned
  `BRIEF_GENERATING`, retryable `FAILED`, and `READY_FOR_CONTENT` states in the
  shared StateRepository. Repeated or concurrent local requests converge on
  the same project; completed briefs are returned without regeneration.
- Brief and plan: the existing TextProvider JSON Schema path produces one
  provider-neutral brief for image, blog, video, YouTube, SEO, audience,
  message, safety, and prohibited elements. Existing WorkflowDefinition only
  validates a five-step PENDING plan (`IMAGE_PACKAGE`, `BLOG_PACKAGE`,
  `VIDEO_PACKAGE`, `YOUTUBE_PACKAGE`, `PUBLISHING`); it does not execute or
  enqueue those steps. Three path-free Artifacts, optional Usage, and safe
  ExecutionHistory are recorded. Fake Text is the default and OpenAI remains
  explicit/credential-gated; no real call was made for @4.

### @5 — Free local image generation and image package (Complete, Local Integration)

- Goal: connect one real image Provider through the existing abstraction.
- Scope: hero image, source thumbnail, video background, and blog images;
  purpose-specific prompt/aspect ratio; file validation; safe prompt/Provider
  metadata; partial failure; Artifact registration; Fake tests; and a
  credential-gated real smoke test.
- Completion: real image files are registered in the content project.
- Implemented Foundation boundary: `ComfyUIImageProvider` extends the existing
  ImageProvider/ProviderFactory contract with credential-free loopback-only
  HTTP, bounded polling, an allowlisted basic-node workflow, safe output
  references, and no automatic Fake fallback. `ImagePackageOrchestrator`
  deterministically formats the @4 brief into COVER (1:1),
  YOUTUBE_THUMBNAIL_SOURCE (16:9), VIDEO_BACKGROUND (16:9), and BLOG_INLINE
  (3:2), validates PNG/JPEG/WEBP bytes, registers four images plus one manifest,
  records zero API cost Usage and safe History, and advances only
  `IMAGE_PACKAGE` to COMPLETED. Workspace isolation, local idempotency, partial
  retry/reuse, restart recovery, and Mock ComfyUI transport are verified.
- Real verification: ComfyUI Desktop 0.29.2 on loopback was verified with an
  RTX 2070 SUPER (8 GiB) and the installed
  `sd_xl_turbo_1.0_fp16.safetensors` checkpoint. The tracked basic workflow
  generated and retrieved a real 512x512 PNG with 2 steps, CFG 1.0, Euler,
  normal scheduling, and a fixed seed. AICompany decoded dimensions, computed
  SHA-256, stored the image through managed Local Object Storage, recorded safe
  ExecutionHistory and zero external-API-cost Usage, and rejected foreign
  Workspace lookup. Fake/Mock remains the default regression boundary; no
  custom node, external endpoint, paid image API, or automatic fallback was
  used.

### @6 — Blog package with images

- Goal: create a directly reviewable and publishable package from the shared
  brief and generated images.
- Scope: SEO title/keywords, summary, introduction, headings/body, image
  placement, captions/alt text, conclusion, tags, Markdown, HTML, hero image,
  and metadata.
- Completion: a complete package is produced without missing image references.

### @7 — FFmpeg video and YouTube publication package

- Goal: produce a playable MP4 from the completed audio and images plus all
  YouTube publication material.
- Scope: FFmpeg availability, audio-based duration, default 1920x1080,
  bounded zoom/fade, audio/video duration match, timeout/exit/output checks,
  safe logs, Artifact registration, title, description, hashtags/tags,
  category, visibility recommendation, pinned comment, thumbnail, and upload
  manifest.
- Completion: a playable MP4 and complete upload package are available.

### @8 — Real publishing Providers

- Goal: place external publication behind common Provider boundaries.
- YouTube scope: OAuth, private-first upload, title/description/tags/thumbnail,
  upload ID/result metadata, duplicate prevention, and local-result retention
  on failure.
- Blog scope: one stable official API selected for repository/user fit,
  HTML/image upload, draft-first publication, publication ID, and isolated
  platform implementation.
- Completion: a real private YouTube upload and supported blog draft succeed.
- Gate: stop for explicit user approval before using a real account or any
  external cost.

### @9 — Real-use E2E and Dashboard integration

- Goal: join request, manual input, resumed automation, and result inspection.
- Scope: request start, music package, `waiting_for_input`, audio resume,
  image/blog/video/YouTube packages, optional publishing, stage status,
  error/retry/idempotency, Usage/cost, History, Artifact preview/download,
  Workspace isolation, and Dashboard exposure.
- Completion: the user starts work in the Dashboard, supplies audio, and
  inspects the completed results there.

### @10 — Real integration verification and v1 decision

- Goal: validate the product with real connections while retaining the full
  Fake automated suite.
- Required success path: real LLM Suno package, user audio intake, project
  resume, real image, blog package, FFmpeg MP4, private YouTube upload,
  supported blog draft, and Dashboard verification of status, Usage, History,
  and Artifacts.
- Required failure path: missing/duplicate/damaged audio, Provider timeout and
  errors, FFmpeg failure, OAuth/publication failure, stage retry, duplicate
  execution, cross-Workspace access, safe Secret/prompt/path handling, and
  real Usage/cost recording.
- Completion: one real-account content E2E succeeds and all Fake automation
  still passes.

### @ stage constitutional verification

Before an `@` stage is complete, verify in order that it:

1. directly contributes to the `@1`-`@10` target;
2. implements only the current stage's completion condition;
3. reuses Mission 1-141 Foundation contracts;
4. does not destroy or duplicate contracts or data;
5. retains multi-user and Workspace isolation;
6. retains Fake/Mock automation and explicit real-integration boundaries;
7. exposes no Secret, personal data, prompt text, or absolute path;
8. completes implementation, tests, docs, diff/status review, and commit;
9. does not mark unimplemented work complete; and
10. moves the product measurably closer to the real `@10` E2E.

Each stage must finish its target tests, Backend regression, relevant
Frontend/build and Docker/smoke checks, documents, diff/status review, and
local commit before the next stage starts. Push remains prohibited unless
explicitly requested.

### Other unapproved next-Phase candidates

No candidate below is an approved Phase or Mission:

1. Real Object Storage integrated with Artifact lifecycle.
2. Deployment, TLS, and Secret Manager integration.
3. Real Billing and approved Media Provider integrations.

Workflow, Plugin, and Marketplace expansion are not current priority
candidates. Any numbered Mission after Mission 141 requires explicit user
approval. The separately approved `@1`-`@10` sequence above is authoritative
for real-use product completion.

## Longer-term phases

- The constitution audit approved the dependent Production Operations sequence:
  Mission 138 Artifact Object Storage Integration, Mission 139 Production
  Configuration Security, Mission 140 TLS and Gateway Security Foundation,
  and Mission 141 Single-host Production Deployment Validation. A later
  numbered Mission remains undefined; the approved `@1`-`@10` product sequence
  is tracked separately.
- Historical Phase F themes included cloud/storage/broker choices, CI/CD,
  security hardening, Workflow Builder, Marketplace, Enterprise, and
  AICompany v1.0. Missions 121-130 implemented only their documented
  Foundation scopes; Enterprise and v1.0 were not completed.
- Real music/image/video/YouTube adapters remain separately blocked by
  explicit account, network, credential, legal, and cost approval. They are
  not prerequisites for the Fake/Offline SaaS contract work above.
- Enterprise and AICompany v1.0 remain unimplemented.

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
