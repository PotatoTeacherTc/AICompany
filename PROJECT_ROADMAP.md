# AICompany Project Roadmap

## Final goal

AICompany is intended to become a multi-user SaaS automation platform. Users
will submit natural-language goals through a website; AICompany will plan,
execute, validate, and present the work while recording per-user work,
artifacts, usage, and costs. The product will ultimately support accounts,
workspaces, subscriptions, and credit-based billing.

## Current baseline: Mission 52

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
