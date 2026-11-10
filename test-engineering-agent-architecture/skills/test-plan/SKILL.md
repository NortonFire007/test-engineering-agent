---
name: test-plan
description: Discover a software feature's requirements, implementation context, risks, and test infrastructure; produce an evidence-based test plan with independent review. Use when planning tests or assessing test coverage. This workflow is analysis-only and must not modify project files.
---

# Test Planning Workflow

## Purpose

Produce a reviewed test plan before implementation. This workflow may be used independently when the user asks only for test analysis. It must not modify application or test source files.

## Preconditions

- Identify the feature/module and requested scope.
- Confirm the target repository is authorized.
- Read project-level instructions and existing architecture/test documentation.
- Create a run ID and an artifact workspace.

## Workflow

### Step 1 — Scope and risk

The Orchestrator records the user's goal, in-scope behavior, exclusions, risk level, and any supplied requirements. Do not infer permission to implement tests from a planning request.

### Step 2 — Discover the repository

Delegate to `test-codebase-explorer` with a bounded scope. Require a Repository Map containing evidence, exact test commands, current test conventions, dependencies, side effects, and unknowns.

Validate the output contract. If critical discovery data is missing, allow one targeted clarification/retry; do not restart the entire exploration by default.

### Step 3 — Analyze test conditions

Delegate to `test-analyst` with:
- original requirements and acceptance criteria;
- Repository Map;
- relevant contracts/invariants;
- scope and risk constraints;
- test-plan template.

Require traceability, exact values, preconditions, observable expected results, priorities, levels, assumptions, and open questions.

### Step 4 — Independent review

Delegate to `test-reviewer` with the authoritative evidence and draft plan. Ask it to challenge the plan independently, not merely proofread it.

### Step 5 — Resolve findings

The Orchestrator classifies each finding:
- valid and actionable: return only affected sections to the Analyst;
- invalid or duplicate: record rationale;
- unclear expected behavior: mark as open question and consider human clarification;
- out of scope: record separately without silently expanding scope.

Allow at most two plan revision cycles. If high-severity issues remain, stop and request a human decision. For lower-severity residuals, publish the plan with explicit caveats.

### Step 6 — Finalize the artifact

Use `templates/test-plan-template.md`. Include version, run ID, scope, evidence, requirements traceability, test cases, coverage matrix, open questions, and review verdict.

Save only to the approved artifact location. Do not write to project source/test paths.

## Quality gates

- Every important expected result has authoritative evidence or is marked as an open question.
- Exact units, precision, inclusive/exclusive boundaries, and representation have been investigated.
- Applicable positive, negative, boundary, state, failure, interaction, security, and non-functional cases are considered.
- Redundant test cases are removed without losing meaningful coverage.
- Reviewer findings are resolved, rejected with rationale, or retained as residual risks.
- No project files were modified.

## Stop conditions

Stop or escalate if the expected behavior for a critical scenario is ambiguous, required resources are outside authorized scope, tool permissions are insufficient, or workflow budgets are exhausted.

## Final response

Summarize the feature scope, main risks, number/categories of cases only as descriptive metadata, review verdict, open questions, and exact artifact location. Do not imply that tests have been implemented or executed.
