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
| Task model | `core/task.py`, `core/status.py`, `core/result.py` | Task owns ID, text, optional structured parameters, an optional parent Task ID, lifecycle timestamps, result, task type, and pipeline. PipelineResult serializes the common result fields. |
| Queue and execution | `core/task_queue.py`, `core/worker.py` | FIFO queue and status transitions. Worker records every completed or failed task in history. |
| Routing | `agent/manager.py`, `agent/classifier.py`, `agent/goal_task_planner.py`, `core/registry.py` | Classifier selects a task type; GoalTaskPlanner converts structured goal steps into parent-linked Tasks only when their task types are registered. Manager preserves that validated declared type (or classifies an untyped Task), resolves it through the registry, and calls one Pipeline. Registry accepts only non-empty task types and BasePipeline implementations, rejects duplicate type registrations, and exposes registered task-type/pipeline/capability metadata for planning. Manager accepts only PipelineResult dictionaries with every required key, an allowed PipelineStatus value, and metadata matching the current Task and selected Pipeline. |
| Pipeline contract | `core/base_pipeline.py` | Each Pipeline receives a Task and returns a PipelineResult dictionary. |
| Implemented pipelines | `core/pipeline.py` (FILE), `music_pipeline.py`, `content_pipeline.py`, `research_pipeline.py`, `history_pipeline.py`, `main.py` (intentional FAIL) | Produce files or history data and return common results. FILE, MUSIC, CONTENT, and RESEARCH accept test output path injection. |
| Persistence and analysis | `core/execution_history.py`, `core/history_analyzer.py` | Stores JSON execution records, including Task parameters, and calculates status/type summaries. |
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
- Backend API and web dashboard layers will access workspace-scoped services,
  never filesystem Pipelines or provider credentials directly.
- Authentication, subscription, credit, and payment components belong outside
  the current Pipeline contract and will be added only after storage and API
  boundaries exist.
