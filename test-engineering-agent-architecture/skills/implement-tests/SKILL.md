---
name: implement-tests
description: Implement an approved, reviewed test plan using the repository's existing test framework and conventions, then run scoped verification and obtain an independent review. Use when the user explicitly requests test implementation.
---

# Test Implementation Workflow

## Purpose

Turn an approved test plan into reliable automated tests. Keep changes within the authorized test scope and report actual verification evidence.

## Preconditions

- The user explicitly requested implementation, or implementation authorization is otherwise recorded.
- A reviewed test plan exists. If not, run `test-plan` first.
- The plan version and writable test paths are known.
- The repository's test command and execution environment have been discovered.

## Workflow

### Step 1 — Validate plan and permissions

The Orchestrator verifies plan status, open questions, scope, allowed write paths, command allowlist, and approval requirements. Any production or configuration change remains out of scope unless separately authorized.

### Step 2 — Implement tests

Delegate to `test-engineer` with the approved plan, Repository Map, relevant source context, test conventions, write-path ACL, and permitted commands.

Require a mapping from each planned test case ID to the implemented test(s), or a reason for deferral/non-applicability.

### Step 3 — Run targeted verification

Run the narrowest relevant test selection first. Then run broader relevant suites and approved quality checks when practical. Capture exact commands, exit codes, pass/fail/skip counts, and environment blockers.

Do not treat a flaky test as passing because a later run succeeded. Do not suppress, delete, or weaken tests to obtain a green result.

### Step 4 — Independent implementation review

Delegate to `test-reviewer` with the approved plan, implementation diff, and test evidence. The Reviewer must not edit files or run commands.

### Step 5 — Bounded correction

If review identifies actionable issues, send a minimal correction request to the Engineer. Run affected tests again and request re-review. Allow at most two correction/re-review cycles.

If the implementation reveals that expected behavior is wrong or unclear, pause and return to the Orchestrator for a requirements decision. Do not silently rewrite the plan's expected result.

### Step 6 — Final scope and quality verification

The Orchestrator inspects the final diff and confirms:
- only authorized test-related paths changed;
- no production, dependency, schema, CI, secret, or infrastructure changes occurred without approval;
- commands/results are accurately reported;
- unresolved failures and gaps are explicit.

### Step 7 — Human handoff

Produce the final report for human acceptance. Do not commit, push, merge, deploy, or release unless the user separately authorizes the exact action.

## Quality gates

- Every implemented test maps to an approved case or a clearly justified addition.
- Assertions verify meaningful observable outcomes.
- Tests are deterministic, isolated, and maintainable.
- Test failures are diagnosed rather than hidden.
- Reviewer findings are resolved or listed as residual risks.
- File and command scope is respected.

## Stop conditions

Stop before an out-of-scope write, dependency/configuration change, destructive operation, network/secret use, or ambiguous high-risk expectation. Stop when retry/review budgets are exhausted. Preserve partial results and ask for the exact human decision required.

## Final response

Report files changed, test case mapping, exact commands and results, reviewer verdict, pre-existing/environment failures, flaky tests, unresolved coverage gaps, and any approvals used. Never claim a command ran unless it actually ran.
