# AICompany

Official baseline: **Mission 131**. Current product status:
**Local/Fake SaaS Beta + Production Foundation with bounded PostgreSQL
Production Integration**. Completion through Mission 131 is limited to each Mission's documented Contract, Foundation,
Fake/Offline, or Local Integration scope; Project APEX is not Production Ready.
The official next Mission is undefined and requires user approval.

AI 기반 자동화와 콘텐츠 제작 시스템을 연구하는 개인 AI 프로젝트 워크스페이스입니다.

## 🎯 Vision

AI 기술을 활용하여 반복 작업을 자동화하고,
콘텐츠 제작과 디지털 프로젝트를 효율적으로 운영하는 시스템 구축을 목표로 합니다.

## 📂 Project Structure
- Archive
  - 이전 자료 보관

- Assets
  - 프로젝트 리소스 관리

- Automation
  - AI 자동화 스크립트

- Images
  - 이미지 자료

- Music
  - AI 음악 프로젝트

- Projects
  - 주요 프로젝트 개발 공간

- Prompt Library
  - AI 프롬프트 관리

- Temp
  - 임시 작업 공간

- Videos
  - 영상 콘텐츠 자료

## 🚀 Projects

### AI Music Factory
AI 기반 음악 제작 및 자동화 시스템

### YouTube Automation
영상 제작 과정 자동화 연구

### AI Dashboard
AI 프로젝트 관리 및 데이터 시각화

### Website Builder
AI 기반 웹 제작 자동화

## 🛠 Tech Stack

- Python
- Node.js
- Git / GitHub
- AI Tools
- Automation Systems

## 📌 Development Log

모든 개발 과정과 실험 기록을 GitHub에 저장합니다.

## 👤 Author

PotatoTeacherTc

## Automated tests

The Automation test suite uses only Python's standard-library `unittest`.
It creates files, music projects, and execution history in temporary
directories, so it does not alter `Automation/TestFiles`, `Automation/Music`,
or production execution history.

Run it from the Automation directory:

```powershell
cd Automation
python -m unittest discover -s tests -v
```

## Offline creative demo

Run the first integrated creative workflow with deterministic Fake providers:

```powershell
cd Automation
python main.py creative-demo
```

The command creates lyrics and a content plan, then runs the existing Fake
music, image, video, and YouTube stages. It prints only safe IDs, stage status,
title, and available usage fields. Local state is written beneath the
git-ignored `Automation/logs/creative-demo` directory.

An Ollama text model can be selected only explicitly:

```powershell
$env:AICOMPANY_TEXT_MODEL = "your-installed-model"
python main.py creative-demo --local-text
```

The endpoint must be loopback and Ollama must already be installed and running.
There is no automatic download, account login, API key, paid-provider fallback,
or external media call. Automated regression tests cover the local adapter
through an injected transport so they remain portable when Ollama is absent.

The loopback workflow has subsequently been verified with Ollama 0.32.5 and
`qwen2.5:1.5b`:

```powershell
$env:AICOMPANY_TEXT_PROVIDER = "ollama"
$env:AICOMPANY_TEXT_MODEL = "qwen2.5:1.5b"
$env:AICOMPANY_OLLAMA_ENDPOINT = "http://127.0.0.1:11434"
$env:AICOMPANY_TEXT_PROVIDER_TIMEOUT = "60"
python main.py creative-demo "한국어 발라드와 회복을 주제로 한 영상 콘텐츠를 구성해 주세요." --local-text
```

Only the lyrics and content-plan stages use the local model. Music, image,
video, and YouTube remain deterministic Fake stages. The verified run reported
zero estimated cost. A failed explicit Ollama run returns failure and never
falls back to Fake. Model installation, updates, and downloads remain explicit
user actions.

## Backend application foundation

Mission 101 provides an explicitly dependency-injected FastAPI application:

```python
from application.backend import BackendDependencies, create_backend_app

app = create_backend_app(BackendDependencies())
```

The verified HTTP smoke command does not open a network port:

```powershell
cd Automation
python -m unittest tests.test_backend_foundation -v
```

`GET /health` reports only service/schema status, safe
persistence/queue/monitor availability, and whether paid providers are enabled.
It never returns environment values, paths, headers, prompts, or raw errors.
Existing task, Workspace, User, authentication, membership, and audit routes
remain available through the same app. No ASGI server runtime is installed or
selected, so a localhost production-server command is not claimed. Media
providers remain Fake and paid providers remain disabled. Missions 102-108
subsequently added the documented User, Workspace, Auth, RBAC, authenticated
API, Artifact-read, and Usage-reporting boundaries.

## Subscription lifecycle

Mission 117 adds a local, Workspace-scoped subscription contract over the
existing FREE/PRO/BUSINESS Plan catalog. Members may read it; OWNER and ADMIN
roles may create it, change its plan, schedule or undo period-end cancellation,
and perform validated status transitions. JSON persistence supports restart.
Cancelled or expired records are preserved while entitlements fall back to
FREE. There is no checkout, pricing, proration, invoice, refund, card storage,
payment provider, or external network request.

## Local billing foundation

Mission 118 adds only development accounting records: a Workspace Billing
Account, explicitly development-only integer-minor-unit Prices, idempotent
period Invoices, and MANUAL/FAKE Payment records. A successful local record
marks an Invoice PAID. Plan changes are reflected when the next Invoice is
created; mid-period proration is not implemented.

No real price, checkout, card or account data, tax, exchange, refund, webhook,
payment SDK/provider, or billing network call is configured.

## Platform operations

Mission 119 adds a small Platform Admin boundary separate from Workspace roles.
Platform identities are injected at composition time. Authorized operators may
inspect existing SaaS state and perform only bounded, audited actions:
Workspace activation, Subscription Plan change, failed Job retry, Invoice void,
and FAKE payment recording. The browser shows Admin navigation only after
`/admin/me` succeeds.

There is no impersonation, password/token/secret access, physical deletion,
arbitrary code execution, real refund/payment, or infrastructure control.

## Local SaaS Beta

Mission 120 completes the verified local Fake/Offline Beta boundary. Install
the declared local dependencies, then start the dependency-injected FastAPI
factory on loopback:

```powershell
cd Automation
python -m pip install -r requirements.txt
python -m uvicorn application.backend:create_backend_app --factory --host 127.0.0.1 --port 8000
```

In another shell:

```powershell
cd Web
npm.cmd install
$env:VITE_API_BASE_URL="http://127.0.0.1:8000"
npm.cmd run dev
```

Production frontend verification uses `npm.cmd run build`. Copy only safe
values from `.env.example`; do not commit a populated `.env`. The default CORS
allowlist accepts `127.0.0.1:5173` and `localhost:5173`, FastAPI debug is off,
`/health` is liveness, and `/ready` reports whether injected persistence,
queue, and monitor probes are configured. The bare factory command is the
local adapter; a real deployment must inject persistent services and secrets
through its own composition root.

Completed locally: authenticated Workspace contracts, persistent Job execution
and recovery, History/Artifact/Usage/Quota, Plan/Subscription, Manual/Fake
Billing, Dashboard, and bounded Platform Admin operations. Not completed:
real payment, cloud deployment, distributed Workers, Redis/broker, production
object storage, real media providers, Workflow execution/API/UI, external
Plugin loading, external Marketplace, Enterprise, or AICompany v1.0.
Workflow Definition and Local/Fake Marketplace foundations exist but are not
Production Integration. `ALLOW_PAID_PROVIDER=False` remains mandatory.

## Docker development stack

Mission 121 adds a provider-neutral Compose environment:

```powershell
Copy-Item .env.example .env
# Replace only the local PostgreSQL placeholder in the untracked .env.
docker compose build
docker compose up -d
docker compose ps
docker compose logs --no-color
docker compose down
```

Backend is exposed at `127.0.0.1:8000` and Frontend at
`127.0.0.1:8080`; PostgreSQL and Redis remain private to the Compose network.
Named volumes preserve container data. `/health` is used for container health,
while the existing `/ready` contract remains available.

PostgreSQL and Redis are provisioned foundations, not active replacements for
the JSON/in-memory repositories. This is not Kubernetes, a cloud provider,
a managed database, or a production-secret configuration.

## Continuous integration

Mission 122 runs the same safe checks on GitHub pushes and pull requests:

```powershell
cd Automation
python -B -m unittest tests.test_source_syntax
python -B -m unittest discover -s tests

cd ..\Web
npm.cmd run lint
npm.cmd test
npm.cmd run build
```

The workflow uses read-only repository permissions, pip/npm caches, and a
seven-day static Web build artifact. It does not deploy, publish images, read
cloud secrets, or change a production environment.

## Production security boundary

Mission 123 validates production configuration before application composition.
Production requires HTTPS CORS origins and an injected, non-placeholder signing
secret of at least 32 characters. The secret is used by the existing signed
access-token provider and is never returned, logged, or persisted.

FastAPI and Nginx apply restrictive CSP and standard security headers. HSTS and
Secure/HttpOnly/SameSite Cookie normalization activate for production policy.
The default rate limiter is injected and single-process; it is suitable as a
safety boundary, not a distributed quota service. TLS certificates, HTTPS
termination, cloud WAF/firewall, distributed limiting, and secret management
remain external deployment responsibilities.

## Local monitoring foundation

Mission 124 adds process-local operational metrics and structured HTTP event
logging through dependency injection. Every response carries an independent
`X-Request-ID` and a reusable `X-Correlation-ID`; unsafe incoming values are
replaced. The read-only `GET /health/metrics` response contains request counts,
status counts, average duration, safe error categories, and dependency-health
aggregates only.

This layer does not record request bodies, query values, credentials, raw
errors, or absolute paths. Logging failures do not alter HTTP results.
ExecutionHistory remains the user execution record, while WorkspaceMonitor
continues to observe persisted product state. Metrics are in-memory and
single-process; external monitoring, durable time series, distributed tracing,
alerting, and cloud services are not implemented.

## Fake backup and recovery

Mission 125 provides an application-neutral `BackupService` over the existing
Workspace, Artifact, and State repository contracts. It can export a bounded,
versioned JSON document and restore it explicitly into injected repositories.
The current `InMemoryBackupStore` is a deterministic Fake only.

Exports include Workspace metadata, safe Artifact metadata, Subscription
metadata, and Manual/Fake Invoice, Payment, and non-personal BillingAccount
metadata. Artifact file contents, billing email, prompts, credentials, and
absolute paths are excluded. Restore validates the entire document and
Workspace ownership before writing and requires explicit permission to
overwrite an existing Workspace. Cloud/object storage, automatic schedules,
retention, and destructive cleanup are not implemented.

## Production infrastructure adapters

Mission 126 adds a `RepositoryFactory` for the existing shared StateRepository
contract. `memory` remains the safe default; `json`, injected PostgreSQL
DB-API, and injected Redis-client adapters can be selected through validated
configuration. Production resources expose safe health probes and are closed
through the FastAPI lifespan boundary.

Mission 131 completes a bounded PostgreSQL integration for this shared state
contract. Set `AICOMPANY_REPOSITORY_ADAPTER=postgresql` and `DATABASE_URL` in
the process environment; the explicit production app factory connects with
psycopg, applies additive migrations, and exposes safe connection/migration
health. Imports do not connect to a database, and memory remains the default.

```powershell
cd Automation
$env:AICOMPANY_REPOSITORY_ADAPTER = "postgresql"
$env:DATABASE_URL = "postgresql://..." # supply outside Git
python -m core.migrations upgrade
python -m uvicorn application.production:create_production_app --factory --host 127.0.0.1 --port 8000
```

Compose selects PostgreSQL automatically for Backend. `docker compose up -d`
waits for PostgreSQL, then Backend applies the current migration. `GET /health`
reports only configured, connected, and migration state. The database URL and
credentials are never returned. The current schema stores shared
StateRepository records only; the existing Plan API is its minimum composed
HTTP write/read path. User, Workspace, Artifact, and other dedicated
repositories retain their existing storage. Redis Queue/Broker, distributed
Workers, cloud database operations, TLS, and Secret Manager are not included.

## Object storage abstraction

Mission 127 adds an injectable StorageProvider while retaining
ArtifactRepository as the metadata authority. Local storage is confined to an
explicit root, and Fake S3 is memory-only. ArtifactStorageAdapter uses
Workspace-qualified internal references; the signed-reference contract is
bounded and opaque. No AWS, GCP, Azure, real bucket, credential, public URL,
or network operation is included.

## Workflow Builder foundation

Mission 128 defines versioned Workflow and Step contracts with dependency DAGs,
conditional branches, bounded Retry policy, and parallel-group labels.
Definitions validate and round-trip through deterministic JSON. This is not a
workflow runtime: UI, execution, scheduling, persistence, and provider calls
are intentionally absent.

## Plugin SDK foundation

Mission 129 defines local Plugin, Manifest, Capability, and version contracts.
The loader uses explicitly injected factories only, validates identity and
declared capabilities, and sanitizes request metadata. The bundled Fake Plugin
is offline. Filesystem discovery, dynamic import, arbitrary external code,
Marketplace download, networking, credentials, and payment are absent.

## Local Marketplace foundation

Mission 130 adds local Package and Dependency metadata with semantic version
compatibility. A LocalMarketplaceRegistry supports Workspace-isolated Fake
install/list/remove and prevents removal of packages still required by an
installed package. There is no external registry, download, package execution,
publishing, review, licensing, pricing, checkout, payment, or network access.

## User lifecycle

Mission 102 adds lifecycle state to the existing User implementation rather
than replacing its repositories, credentials, login, or API:

- New and legacy Users are normalized to `ACTIVE`.
- Self-deactivation changes the state to `INACTIVE` and persists it locally.
- Inactive Users cannot log in or receive a new Workspace membership.
- Passwords, hashes, access/refresh tokens, Authorization, and Cookie values
  are never returned in User data.

Authenticated self-deactivation uses:

```text
PATCH /users/{user_id}/deactivate
```

The endpoint does not trust a body-supplied User ID and returns 403 when the
authenticated principal targets another User. Administrative lifecycle
management, reactivation, and email verification remain future work.

## Workspace lifecycle

Mission 103 adds lifecycle and optimistic concurrency to the existing
Workspace implementation:

```text
PATCH /workspaces/{workspace_id}
```

The request may contain `name` or `status` plus the required
`expected_revision`. OWNER or ADMIN authorization is required when
authentication is enabled. Stale revisions return 409. An INACTIVE Workspace
retains its records but rejects authenticated access, Membership operations,
and new Task submission. File-backed Workspace repositories restore status and
revision after restart, while legacy records default safely to ACTIVE revision
0. Reactivation policy, destructive deletion, ownership transfer, and quotas
are not included.

## Authentication lifecycle

Mission 104 hardens the existing local authentication services. Signed access
tokens contain only a subject, timestamps, access-token type, schema version,
issuer/audience, and an optional session ID. The current ACTIVE User and
persisted session are checked on use; roles and Workspace state are not cached
inside the token.

Refresh tokens are returned only from login/refresh responses. Persistence
stores only their SHA-256 digests. Rotation keeps the session ID, increments a
revision, rejects reuse, and permits only one concurrent winner. Logout is
idempotent and User-scoped, while `POST /auth/logout-all` revokes every session
for the authenticated User. Self-deactivation also revokes all sessions.
In-memory and JSON-file repositories support restart recovery and ignore
malformed session records.

Secrets must be injected for real deployments; no secret, password, hash,
access token, refresh token, Authorization header, or Cookie is logged or
stored in audit metadata. Rate-limit infrastructure, email verification,
OAuth, MFA, administrative User management, and Mission 105 RBAC work remain
future scope.

## Workspace authorization

Mission 105 reuses OWNER, ADMIN, MEMBER, and the existing Workspace Membership
repositories through one injected `AuthorizationService`. Every protected
Workspace operation re-checks the current User and session, Workspace status,
Membership, and required role. Access tokens do not carry trusted roles, so a
role change or Membership removal takes effect immediately without waiting for
token expiry. Inactive Users, revoked sessions, inactive Workspaces, and
cross-Workspace access are rejected through safe responses.

Authentication remains opt-in for legacy API compatibility. Custom roles,
resource-level policy, organization-wide permissions, and Mission 106 API
expansion are not implemented.

## Authenticated API context

Mission 106 completes the current opt-in authentication integration for the
existing API adapter:

```text
GET /auth/me
GET /workspaces
GET /tasks?workspace_id={workspace_id}
POST /tasks/{task_id}/cancel
POST /tasks/{task_id}/retry
```

When `auth_required=True`, current-User data is self-only, Workspace
collections contain only active Workspaces with a current Membership, Task
collections require and filter an authorized Workspace, and Task controls
authorize the Workspace stored on the Task. Supplying another Workspace ID
does not expose its records. Authorization and Cookie values are never
returned. The default `auth_required=False` behavior remains for existing
local clients.

API versioning, an external ASGI deployment, signup/email verification, and
administrator User management remain future scope. Artifact access was added
in Mission 107.

## Workspace artifact access

Mission 107 exposes the existing ArtifactManager through authenticated,
Workspace-scoped application routes:

```text
GET /workspaces/{workspace_id}/artifacts
GET /workspaces/{workspace_id}/artifacts/{artifact_id}
GET /workspaces/{workspace_id}/artifacts/{artifact_id}/content
```

Lists support Artifact type, Mission, optional Task, `limit`, and `offset`.
Responses never include `path`, `internal_ref`, or repository locations.
Content access is available only for UTF-8 TEXT/JSON artifacts stored beneath a
configured FileArtifactRepository storage root. Reads are capped at 1 MiB and
return MIME type, byte size, and SHA-256 checksum. Sensitive JSON keys are
removed recursively.

Missing files return a safe MISSING response; traversal references and corrupt
metadata are ignored. OWNER, ADMIN, and MEMBER may read their current active
Workspace, while inactive Users/Workspaces and removed Memberships are
rejected. Binary download/streaming and artifact deletion/archive policy are
not implemented.

## Workspace usage reporting

Mission 108 adds authenticated, read-only reporting over the existing
Workspace UsageEngine:

```text
GET /workspaces/{workspace_id}/usage
GET /workspaces/{workspace_id}/usage/summary
```

Queries support provider, model, Mission, timezone-aware date range, and
bounded pagination filters. Summary values include only usage fields that
were recorded; missing metadata is not invented, while an explicit zero
estimated cost is preserved. Estimated cost is informational and is not a
billed amount. In-memory and JSON persistence remain supported. Pricing,
credits, billing, subscriptions, and external provider calls are not part of
this feature.

## Current Backend scope and next work

The official baseline is Mission 131. Mission 1-131 completion means completion
of each Mission's bounded Contract, Foundation, Fake/Offline, or Local
Integration scope; Project APEX is not Production Ready. The current product
state is **Local/Fake SaaS Beta + Production Foundation**.

The historical progression below records how Missions 109-116 closed earlier
Backend gaps. Those gaps are no longer the next-work list.

Mission 109 now connects the existing persistent Queue and in-process Worker
to a dependency-injected execution coordinator. It restores configured
Workspace Jobs after restart, prevents duplicate in-process claims, preserves
idempotent submission and retry metadata, and links terminal PipelineResult
summaries to existing History, Artifact metadata, and Usage storage. Pipelines
still register their own artifacts through ArtifactManager. No Job,
ExecutionHistory, or Batch API was added.

Mission 110 adds authenticated Workspace routes:

```text
POST/GET /workspaces/{workspace_id}/jobs
GET /workspaces/{workspace_id}/jobs/{job_id}
POST /workspaces/{workspace_id}/jobs/{job_id}/cancel
POST /workspaces/{workspace_id}/jobs/{job_id}/retry
GET /workspaces/{workspace_id}/executions
GET /workspaces/{workspace_id}/executions/{execution_id}
GET /workspaces/{workspace_id}/batches
GET /workspaces/{workspace_id}/batches/{batch_id}
```

These routes reuse persistent Queue state and existing RBAC. They do not
replace the in-memory Task API. Job results return safe History, Artifact, and
Usage references without task text or internal paths.

Mission 111 exposes Department management and read-only Worker capabilities:

```text
GET/POST /workspaces/{workspace_id}/departments
GET/PATCH /workspaces/{workspace_id}/departments/{department_id}
POST/DELETE /workspaces/{workspace_id}/departments/{department_id}/workers/...
GET /workspaces/{workspace_id}/workers
GET /workspaces/{workspace_id}/workers/{worker_id}
```

OWNER and ADMIN manage Departments and assignments; MEMBER may read. Worker
creation or mutation is not exposed because WorkerDirectory contains injected
live Worker implementations rather than persistent safe configuration.

Artifact lifecycle endpoints now support path-free Workspace reads plus
OWNER/ADMIN archive and restore. Archive changes persisted metadata only; it
does not delete content or invalidate Job/History references. `AVAILABLE` is
the active state, `ARCHIVED` is reversible, and missing content reports
`MISSING`.

Workspace quota endpoints expose current usage/remaining admission state to
members and allow OWNER/ADMIN to set explicit token, estimated-cost, and
execution limits. Enforcement occurs at persistent Job submission and before
target execution. Reservations are restart-safe and idempotent within the
current single process; the current period is `ALL_TIME`. This is not billing.

Plans are product-policy contracts, not purchases. The injected
FREE/PRO/BUSINESS catalog supplies quota defaults and feature entitlements;
Workspace overrides remain higher priority. Workspace members can read the
current plan and OWNER/ADMIN can assign an active plan. Artifact archive is the
current enforced feature entitlement. Subscription and Manual/Fake Billing
contracts now exist; real pricing, checkout, and payment Providers do not.

`GET /workspaces/{workspace_id}/dashboard` provides the authenticated,
read-only Dashboard API. It combines existing Workspace, persistent Job and
Execution, Artifact, Usage/Quota, Plan, Department, and Worker data. Summary
aggregation is bounded to 100 records; recent lists accept 1–20 items. No
analytics database, WebSocket, or control operation is part of this endpoint.

Mission 132 adds an environment-selected Redis Job Queue while preserving the
existing Job contract and PostgreSQL state. Use
`AICOMPANY_QUEUE_BACKEND=redis`, `REDIS_URL`, and an optional
`AICOMPANY_QUEUE_NAMESPACE`; memory remains the safe default. Redis contains
Workspace-scoped pending/processing IDs only. Distributed locking and an
external Worker are not part of Mission 132.

Mission 133 adds owner-token TTL distributed locking. Redis acquisition is
atomic and release/renew verify ownership; stale Locks expire and failures are
closed. Mission 134 adds the separate `python -m application.worker` process;
Compose starts it beside Backend. It consumes only the deterministic
`offline-success` target and writes Job History/Usage to PostgreSQL. The
approved next Mission is Mission 135 Recovery/DLQ. Later
Production Execution Layer work covers multi-instance validation and
multi-instance validation. Object Storage, deployment/TLS/Secret Manager, and
real Billing/approved Media Provider integration remain outside this bundle.
Workflow, Plugin, and Marketplace expansion is not a current priority.

The initial Web Dashboard is in `Web/`. Run `npm.cmd install`, set
`VITE_API_BASE_URL` to the Backend loopback URL, then use `npm.cmd run dev`.
Authentication tokens remain in memory and clear on refresh/logout. The
current release provides login, Workspace switching, overview metrics, and
responsive navigation; secondary administration screens remain intentionally
minimal. Subscription and Manual/Fake Billing are Backend contracts, not real
payment integrations.

Ollama Text remains explicit and loopback-only. Music, Image, Video, and
YouTube remain Fake; no paid Provider or external media API is enabled.
