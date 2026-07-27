# AICompany Project Roadmap

## Final goal

AICompany is intended to become an AI-assisted automation platform where a user
submits a natural-language goal and the system analyzes it, breaks it into
work, selects and runs pipelines, validates results, records execution, and
eventually presents the outcome through a UI.

## Current baseline: Mission 21

The verified implementation currently provides a Task/Queue/Worker execution
path, keyword-based classification, a registry of FILE, MUSIC, CONTENT,
RESEARCH, HISTORY, and intentional FAIL pipelines, structured PipelineResult
objects, JSON execution history, and automated regression tests. CONTENT and
RESEARCH create local starter projects only; neither calls an AI provider nor
the web.

## Development stages

| Stage | Goal | Required capabilities | Completion condition |
|---|---|---|---|
| 1. Core architecture stabilization | Keep the execution contract dependable. | Typed/documented Task and PipelineResult schemas, registry validation, deterministic status handling, test coverage for success/failure/not-implemented paths. | Every registered pipeline follows the same contract and regression tests cover its boundary behavior. |
| 2. Task, agent, and pipeline expansion | Support richer work decomposition. | Structured task parameters, subtask relationships, planner output consumed by executors, pipeline capability metadata. | A compound user goal can be represented as validated executable tasks. |
| 3. CONTENT and RESEARCH enrichment | Make local project scaffolds useful production workflows. | Templates, configurable research/content formats, source records, review checkpoints, artifact validation. | Generated projects are configurable and have complete reviewable artifacts. |
| 4. External AI and API integration | Add providers only behind stable interfaces. | AI provider adapter, credential handling, rate/error policy, source attribution, offline fallback. | A configured provider can be used without changing Pipeline/Task contracts. |
| 5. Automatic planning and execution | Turn goals into executable plans. | Goal parser, planner, task dependency graph, execution policy, approval points. | A natural-language goal produces an inspectable plan and runs approved tasks. |
| 6. Failure analysis and recovery | Recover safely from expected operational errors. | Failure classification, retry policy, compensating actions, idempotency rules, recovery history. | Recoverable failures are retried or surfaced with a clear final cause and audit trail. |
| 7. Long-running jobs and queues | Run work reliably beyond one process. | Persistent queue, job states, scheduling, cancellation, concurrency controls, resumability. | Jobs survive process restart and expose durable progress/status. |
| 8. UI and API layer | Present and control the system safely. | API service, authentication/authorization, project/job views, logs/artifact browsing, user controls. | Users can submit, inspect, and control jobs without invoking Python directly. |
| 9. User goal input system | Make goal intake usable for real users. | Goal form/chat interface, validation, plan preview, consent for external/destructive work. | A user can submit a goal, approve a plan, and understand the returned result. |
| 10. Production-ready application | Operate AICompany as a complete application. | Deployment, configuration management, monitoring, backup/restore, security review, user documentation. | A documented, monitored deployment supports real workloads safely. |

No stage beyond the current baseline is considered complete merely because a
placeholder module or roadmap item exists.
