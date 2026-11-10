# Implementation Prompt — Agentic Test Engineering System

Use this document as the primary implementation instruction together with `ARCHITECTURE.md`, `TOOL_ACCESS_POLICY.md`, `RETRY_AND_ESCALATION_POLICY.md`, the agent specifications, Skill specifications, and templates. First inspect the target repository and runtime; then implement the architecture using their native conventions. Do not assume a vendor-specific platform or copy examples mechanically.

## 1. Objective

Implement the architecture in the target repository and agent runtime. The design is intentionally platform-neutral. First inspect the environment and map the logical components to supported native capabilities; do not assume a particular file format, agent API, directory layout, or tool permission model.

## 2. Required discovery before implementation

Inspect and report:

1. Repository layout, languages, frameworks, module boundaries, and project instructions.
2. Existing agent, Skill, workflow, prompt, and tool configuration; identify naming conflicts.
3. Test framework(s), test directories, fixtures, factories, mocks, and test utilities.
4. Exact test, lint, type-check, and formatting commands, including required services/environment.
5. Current write permissions, sandbox model, command execution model, network policy, and approval mechanism.
6. Whether the runtime supports per-agent tool allowlists and resource-level filesystem permissions.
7. Whether agent outputs can be validated against structured schemas.
8. Existing tracing, audit logs, run-state persistence, and human approval mechanisms.

Do not install packages, modify production behavior, or change project configuration during discovery.

## 3. Target logical structure

Create native equivalents of the following logical components. Exact filenames and directories should follow the target platform's conventions.

```text
agent-system/
├── architecture/
│   ├── system-architecture
│   ├── tool-access-policy
│   └── retry-and-escalation-policy
├── agents/
│   ├── test-codebase-explorer
│   ├── test-analyst
│   ├── test-reviewer
│   └── test-engineer
├── skills/
│   ├── test-plan
│   └── implement-tests
└── templates/
    ├── test-plan-template
    └── agent-handoff-contract
```

The runtime may require a different physical layout. Preserve the logical separation even if several definitions are stored in one configuration file.

## 4. Agent configuration requirements

For every agent definition, configure or document:

- stable name and concise description;
- mission and responsibility boundary;
- input contract and output schema;
- tool allowlist and denied capabilities;
- resource scope (read and write paths);
- command/network/secret restrictions;
- maximum turns/tool calls or equivalent execution budget;
- stop/escalation conditions;
- model selection only if there is an explicit project policy; otherwise inherit the standard configuration.

Do not give every agent the same tool set for convenience.

### Minimum role mapping

| Agent | Tool profile | Write scope |
|---|---|---|
| Explorer | Repository list/read/search | None |
| Analyst | Repository read/search + artifact write | Artifacts only |
| Reviewer | Repository read/search + diff/results read + artifact write | Review artifacts only |
| Engineer | Repository read/search + test write + approved test execution | Explicit test paths only |
| Orchestrator | Agent delegation + workflow/artifact/state control + read-only inspection | No general source-write capability by default |

A platform-specific implementation must prove that these boundaries are enforced. If agent-level policies are not supported, add a tool gateway or separate sandboxes/sessions. Do not present prompt-only restrictions as isolation.

## 5. Skill/workflow configuration requirements

Implement two reusable workflows:

### `test-plan`

- Analysis-only.
- Calls Explorer, then Analyst, then Reviewer.
- Validates structured outputs after every handoff.
- Resolves findings with at most two plan revision cycles.
- Produces a test plan with requirements traceability, boundary cases, expected observations, priorities, and open questions.
- Cannot write project source or test files.

### `implement-tests`

- Requires an approved/reviewed plan and explicit implementation authorization.
- Calls Engineer, runs approved verification, then invokes Reviewer.
- Allows at most two correction/re-review cycles.
- Enforces test-path-only writes and command allowlists.
- Produces an implementation report and final diff-scope check.

Keep role instructions in agent definitions and workflow sequencing in Skills. Avoid copying the same long instruction text into a global project prompt and every agent.

## 6. Tool and runtime enforcement

Implement the strongest available enforcement for:

- per-agent tool allowlists;
- path-level read/write permissions;
- canonical-path validation and symlink/path-traversal protection;
- argument validation and command allowlists;
- network egress restrictions;
- secret isolation;
- approval gates for high-risk side effects;
- per-call authorization and audit logging.

If the runtime cannot enforce a boundary, do not silently downgrade it to a natural-language rule. State the limitation and introduce a stronger outer boundary or keep the workflow disabled for that capability.

## 7. Reliability controls

Implement the retry policy in `RETRY_AND_ESCALATION_POLICY.md`:

- classify errors (transient, permanent, structured-output, semantic, policy, unknown side-effect outcome);
- maximum two retries for safe transient tool/runtime errors;
- one structured-output repair attempt;
- maximum two plan revision cycles;
- maximum two implementation correction/re-review cycles;
- one diagnostic rerun for suspected flaky tests;
- exponential backoff with jitter for transient infrastructure errors;
- run-level time/tool/token/cost budgets;
- checkpoints and partial-result preservation;
- idempotency/reconciliation before repeating side effects.

Do not retry authorization denials, permanent configuration errors, or failing assertions merely to obtain a green result.

## 8. Human-in-the-loop behavior

Automate routine low-risk analysis, test-plan creation, authorized test-file changes, and local test execution.

Pause for human approval/clarification when:

- critical expected behavior is ambiguous;
- a high-severity review finding remains unresolved;
- any operation exceeds the authorized tool/resource scope;
- production behavior, dependencies, migrations, project configuration, CI, secrets, network, production data, or shared infrastructure are involved;
- existing tests must be deleted or weakened;
- an operation is destructive, costly, irreversible, or non-repeatable;
- the system cannot determine whether a previous side effect completed.

Always provide a final evidence report for human acceptance. Do not auto-commit, push, merge, deploy, or release.

## 9. Implementation sequence

1. **Inventory:** complete repository/runtime discovery and document constraints.
2. **Policy foundation:** configure tool allowlists, path policies, command policy, approval gates, and audit events.
3. **Artifacts:** implement test-plan and handoff schemas/templates.
4. **Read-only specialists:** implement Explorer, Analyst, and Reviewer with enforced non-mutating access.
5. **Planning Skill:** implement and smoke-test `test-plan` without modifying project source/test files.
6. **Negative permission tests:** prove analysis agents cannot write and cannot invoke denied tools.
7. **Engineer:** implement test-path-limited write and approved test execution.
8. **Implementation Skill:** add independent review and bounded correction loops.
9. **Pilot:** use low-risk representative features and compare outputs with human review.
10. **Evaluate:** measure quality, policy enforcement, cost, retries, and residual-risk reporting before expanding scope.

## 10. Validation and acceptance criteria

### Architecture and configuration

- [ ] One Orchestrator owns run state and transitions.
- [ ] Four specialist roles exist with distinct missions and output contracts.
- [ ] Two Skills exist and planning is separated from implementation.
- [ ] No specialist recursively delegates to other specialists by default.
- [ ] Agent and Skill definitions use the target runtime's validated native format.

### Permission enforcement

- [ ] Explorer cannot modify project files.
- [ ] Analyst cannot modify project files.
- [ ] Reviewer cannot modify project files.
- [ ] Engineer cannot write outside approved test paths.
- [ ] Denied tools cannot be reached through alternate tool names or another agent.
- [ ] Path traversal and symlink escape attempts are denied.
- [ ] Network and secret access are off by default.
- [ ] Every allow/deny decision is recorded.

### Workflow behavior

- [ ] `test-plan` produces a traceable test plan and independent review verdict.
- [ ] `test-plan` performs no project-source/test-file mutations.
- [ ] `implement-tests` refuses to proceed without an approved plan and explicit authorization.
- [ ] Review/revision loops stop at the configured maximum.
- [ ] Permanent failures and policy denials are not retried.
- [ ] Uncertain side effects are reconciled before any retry.
- [ ] Human approval is requested for high-impact/out-of-scope actions only.
- [ ] Final report accurately distinguishes passed, failed, skipped, blocked, and flaky tests.

### Quality

- [ ] A representative plan covers positive, negative, exact boundaries, and relevant failure/state cases.
- [ ] Expected results cite authoritative evidence or are marked as open questions.
- [ ] Tests use meaningful assertions and project conventions.
- [ ] A seeded regression or mutation check demonstrates that key assertions detect the intended behavior when practical.
- [ ] Human reviewers can inspect artifacts, findings, diff, and command results.

## 11. Required implementation report

Return:

1. Repository/runtime discoveries.
2. Native components created and their locations.
3. Permission model and what is technically enforced.
4. Any unsupported runtime capabilities or compensating controls.
5. Retry, budget, and HITL configuration.
6. Validation commands and results.
7. Negative permission-test results.
8. Planning pilot findings.
9. Known limitations and recommended next steps.

Do not claim a security boundary, agent isolation, successful validation, or human approval unless it was actually tested or recorded.
