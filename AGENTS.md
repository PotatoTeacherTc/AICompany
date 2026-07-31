# AICompany - AGENTS.md

## Constitution

`PROJECT_CONSTITUTION.md` is the highest-priority Project APEX rule. Read and
apply it before this file and all roadmap, status, architecture, and Mission
instructions.

## Project

Project APEX / AICompany

Goal:
Build a production-ready multi-user SaaS where AI employees collaborate to complete user requests.

Current priority:

1. Stable Core
2. End-to-End Personal AICompany
3. SaaS Expansion

Always optimize for long-term architecture rather than temporary automation.

---

# Development Principles

Always work inside:

D:\AICompany

Never:

- push unless explicitly requested
- reset/revert/delete user changes
- expose secrets
- store API keys
- perform unrelated refactoring
- duplicate existing implementations

Always:

- inspect existing code first
- reuse common abstractions
- keep backward compatibility
- finish one mission completely before starting another
- update documents when implementation changes
- commit only after tests pass

Implement the minimum changes required.

Avoid speculative features or future missions.

If actual code differs from the roadmap, follow the codebase and update documentation accordingly.

If tests fail:

- attempt to fix
- if unresolved, do not commit
- report the root cause and remaining work

---

# Architecture

Maintain:

- Dependency Injection
- Provider Abstraction
- PipelineResult contract
- Usage Metadata contract
- ExecutionHistory
- Repository abstraction
- Workspace isolation
- Mock/Fake testing
- timeout/error safety
- SaaS scalability

Usage metadata should support:

- provider
- model
- input_tokens
- output_tokens
- total_tokens
- estimated_cost

Missing usage metadata must never crash pipelines.

---

# Workflow

Each mission:

1. Inspect code
2. Implement minimum scope
3. Target tests
4. Full tests
5. Update docs
6. Review git diff/status
7. Local commit

Never leave unfinished work.

---

# AI Roles

ChatGPT

- CTO
- Architecture
- Mission planning
- Review

Codex

- Main developer
- Code
- Tests
- Docs
- Commit

Claude

- Optional code review
- Complex implementation

Gemini

- Research
- External API investigation

---

# Collaboration Core

Collaboration is a product feature.

Target flow:

User
↓

Manager

↓

Mission

↓

Worker Assignment

↓

Context Builder

↓

Worker

↓

Validation

↓

Review

↓

ExecutionHistory

↓

Human Approval

↓

Commit

Automatic push is forbidden.

---

# Mission Order

Phase A

70 Collaboration Mission Contract

71 Mission State + Lock

72 Context Builder

73 WorkerResult

74 Worker Abstraction

75 Workspace / Worktree

76 Validator

77 Claude Worker

78 Gemini Worker

79 Collaboration Orchestrator

80 Collaboration End-to-End

Phase B

81 Music Provider

82 Music Pipeline

83 Music History

84 Image Pipeline

85 Video Pipeline

86 YouTube Provider

87 Content End-to-End

88 Scheduler

89 Retry & Recovery

90 Personal AICompany Complete

Phase C

91 Persistence

92 Artifact Manager

93 Queue

94 Batch

95 Monitor

96 Logging

97 Usage Engine

98 Settings

99 AI Departments

100 Personal Operating System

Phase D

101 Backend

102 User

103 Workspace

104 Auth

105 RBAC

106 API

107 Artifact

108 Usage

109 Persistent Job Execution

110 Job & Execution API

Phase E

111 AI Organization API

112 Artifact Lifecycle

113 Quota & Budget

114 Plans & Entitlements

115 Dashboard API

116 Web Dashboard

117 Subscription

118 Billing

119 Admin

120 SaaS Beta

Phase F

121~130

Cloud  
CI/CD  
Security  
Workflow Builder  
Marketplace  
Enterprise  
AICompany v1.0

Follow the roadmap naturally.

If a prerequisite appears missing, do not silently implement a future mission. Confirm whether it already exists in another form. If Mission 70 cannot be completed without a prerequisite, report the blocker instead of expanding scope.

---

# Documentation

Keep synchronized:

- AGENTS.md
- PROJECT_ROADMAP.md
- CURRENT_STATUS.md
- ARCHITECTURE.md

Never mark unimplemented features as complete.

---

# Mission Rule

Only implement the requested mission.

Never begin the next mission.

If blocked, report:

- completed
- remaining
- reason
- suggested next step

Never pretend work is finished.

---

# Real-use Product Roadmap

Mission 1-141 is the completed Foundation and bounded Production Integration
history. The separately approved `@1` through `@10` sequence is the real
Provider and content E2E product-completion roadmap; an `@` stage must never be
renumbered as Mission 142 or described as complete before implementation and
real verification.

The approved v1 target flow is:

User request -> LLM-generated music/Suno package -> manual Suno creation ->
safe audio intake and project resume -> shared content brief -> real image ->
blog package -> FFmpeg video -> private YouTube upload and supported blog draft
-> Artifact, Usage, ExecutionHistory, and Dashboard verification.

Suno execution remains manual. Every `@` stage must reuse existing DI,
Provider Factory, PipelineResult, UsageMetadata, ExecutionHistory,
ArtifactManager, Persistence, Queue, Workspace isolation, API, Dashboard,
security, and Fake/Mock tests. Real credentials, accounts, network calls, or
cost require the stage's explicit authorization and must never be inferred.

Before completion, verify that the work:

1. directly advances the `@1`-`@10` target;
2. stays within the current `@` stage;
3. reuses the Mission 1-141 Foundation;
4. neither duplicates nor breaks contracts or data;
5. preserves multi-user Workspace isolation;
6. preserves Fake/Mock automation and explicit real-integration boundaries;
7. exposes no Secret, personal data, prompt text, or absolute path;
8. completes implementation, tests, docs, diff/status review, and commit;
9. does not claim unimplemented work; and
10. measurably moves the product toward the real `@10` E2E.

The complete stage definitions and acceptance criteria are authoritative in
`PROJECT_ROADMAP.md`.

---

# Git

Before work:

- inspect git status
- inspect recent commits

Before commit:

- review diff
- run tests
- ensure no secrets

Never push unless instructed.
