# Development Rules

These rules apply to future Codex changes in AICompany.

1. Analyze the current code, tests, and these management documents before
   changing behavior.
2. Do not duplicate an implemented capability; extend or reuse the existing
   architecture when it satisfies the requirement.
3. Add or update tests before, or alongside, every behavior change. Do not
   weaken existing tests to make a change pass.
4. Run the full test suite after implementation and leave all existing tests
   passing.
5. Preserve the common pipeline contract: Pipeline input is a Task and output
   is a PipelineResult dictionary with common fields.
6. Do not arbitrarily break Task lifecycle fields, task type/pipeline metadata,
   or PipelineResult serialization.
7. Do not hide failures. Return/record FAILED results with actionable error
   details and test failure behavior explicitly.
8. Never hard-code test outcomes, fixture-specific paths, or timestamps to
   satisfy a test.
9. Add external packages, AI services, or APIs only when necessary; document
   the reason, configuration, failure policy, and new dependency.
10. Resolve paths from the project configuration and validate targets before
    filesystem operations.
11. Keep production data separate from test data. Tests must use temporary
    roots or injected dependencies and clean up after themselves.
12. Keep CONTENT, RESEARCH, and any new Pipeline outputs verifiable: generated
    files must be non-empty and result metadata must describe them.
13. Before a large architectural change, verify compatibility with the current
    Task → Worker → Manager → Registry → Pipeline → Result → History flow.
14. After code changes, verify actual execution in addition to unit tests when
    the changed feature has runtime side effects.
15. Update `CURRENT_STATUS.md` after a completed change so it reflects tested
    reality, including test count and unfinished work.
16. Maintain a Git-backup-friendly working tree. Do not use destructive Git
    commands or discard unrelated user changes.
17. Prefer small, reversible changes over unnecessary rewrites. Record any
    deliberate contract change in `ARCHITECTURE.md` and affected tests.
