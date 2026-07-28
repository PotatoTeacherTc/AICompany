# AICompany

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

Missions 101-108 provide the local Backend composition, User and Workspace
lifecycle, session-backed Auth, Workspace RBAC, authenticated Task context,
safe Artifact reads, and read-only Usage reporting. The existing Task HTTP
routes can create, list, inspect, cancel, and retry in-process Tasks.

The repository also contains a restart-aware `PersistentJobQueue`,
`InProcessJobWorker`, `BatchManager`, Scheduler, ExecutionHistory, and Monitor.
Those persistent execution contracts are not yet connected to Backend Task
submission or exposed as authenticated Job/Execution APIs. AI Departments are
implemented as persistent domain services but likewise have no management
API. Artifact archive/restore, quota enforcement, plans, billing, Dashboard,
and production deployment are not implemented.

The next defined sequence is:

1. Mission 109 — Persistent Job Execution
2. Mission 110 — Job & Execution API
3. Mission 111 — AI Organization API
4. Mission 112 — Artifact Lifecycle
5. Mission 113 — Quota & Budget Enforcement
6. Mission 114 — Plans & Entitlements
7. Mission 115 — Dashboard API
8. Mission 116 — Web Dashboard

Ollama Text remains explicit and loopback-only. Music, Image, Video, and
YouTube remain Fake; no paid Provider or external media API is enabled.
