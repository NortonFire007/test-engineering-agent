---
name: implement-tests
description: Implement a human-approved test plan as real test code inside approved test paths, run the tests through the approved runner, and get an independent review (plus a controlled mutation check in deep mode). Use only when the user explicitly asks to implement or write the planned tests. Requires an approved plan from the test-plan skill. Never changes production code, dependencies or configuration.
argument-hint: "[run-id]"
---

# Test implementation workflow

You are the **orchestrator**. The test-engineer writes and runs tests; the test-reviewer judges them; you enforce gates and report facts. You do not write project files (denied while a run is current) and you do not run anything but `TE`.

`TE` means this exact prefix (one command per Bash call; no pipes, `&&`, redirects or variables):

```
uv run --no-project "${CLAUDE_PLUGIN_ROOT}/scripts/te.py"
```

## 0. Preconditions — all enforced by the CLI, check them first

1. The user explicitly asked for implementation. If not, stop.
2. `TE run status` (or `TE run list` then `TE run resume <run-id>` for a plan approved in an earlier session). Required: phase `awaiting_plan_approval`, `plan_approval.hash == plan_hash`.
   - No reviewed plan → run the `test-plan` skill first.
   - Plan approved without implementation authorization → present the plan summary again and, on the human's explicit yes, `TE plan approve --authorize-implementation` (raises the human permission prompt).
3. `profile_trust` must be `approved`. Every test command the plan uses (`command_id` values) must be **verified**: `TE profile check`; for unverified ones `TE profile verify <id>` (see `test-setup` for handling non-zero exits). Specialists cannot run unverified commands.
4. `TE phase implementation`. This takes a file snapshot of the project (used later to detect out-of-scope changes).

## 1. Implement — phase `implementation`

Spawn `test-engineering:test-engineer` with model = `models.engineer_next_spawn` from `TE run status` (cheap model first; the CLI escalates it for later correction cycles). Prompt with only:

- `run_id`, artifacts dir, the plan hash;
- the exact runner prefix: `<TE> exec --run <run_id> <command-id> -- <selectors>` (give the fully resolved prefix as printed in `cli` by `TE run status`/`run init`);
- the command ids it may use and their allowed flags;
- "read `test-plan.json`, `.test-engineering/conventions.md`, `.test-engineering/profile.json`".

It writes tests inside approved paths and `impl-report.json`.

**If it escalates** (needs a path outside the approved test paths, a production change, a dependency, config, network, secrets, or to delete/weaken an existing test): that is a human decision. Present the exact file and change, why it is needed, the risk, the safer alternative, and what happens if denied. Only for a single exact file the human approves: `TE scope approve --path <rel/path> --reason "<why>"` (permission prompt), then re-spawn. Production-behaviour changes are never part of this workflow: record them as a suspected defect instead.

## 2. Scope check and evidence — moving to `impl_review`

`TE phase impl_review`. The CLI validates the report, diffs the tree against the snapshot and writes `changes.json` + `diff.patch`.

- **Exit code 3 / `"blocked": true`** → files outside the approved scope changed. The run is blocked. Stop. Show the human the exact paths and reasons. Do not revert or "fix" anything yourself. The human either reverts them or accepts each one (`TE scope approve` for exact paths) and then `TE run unblock --reason "<decision>"`.
- `preexisting_modified` non-empty → existing test files were edited. Always surface this to the human in the final report; the reviewer will check they were not weakened.

## 3. Independent review — phase `impl_review`

Spawn `test-engineering:test-reviewer` (model `models.reviewer`) with: `run_id`, artifacts dir, "implementation review", and in `deep` mode "also propose a `mutation_spec` (max `limits.max_mutations`) for P0/P1 cases". Do not pass the engineer's narrative. It writes `review-impl.json`.

## 4. Bounded correction

Actionable findings (verdict `NEEDS_REVISION`, or valid findings of medium+ severity):

- `TE phase implementation` (consumes a correction cycle) → re-spawn the engineer with **only** the accepted findings and "minimal targeted corrections, rerun affected tests, rewrite the report" (model from `models.engineer_next_spawn`) → `TE phase impl_review` → re-spawn the reviewer.
- If the CLI says the correction budget is exhausted: **do not loop**. Continue; residual findings go into the report.

A test that fails because observed behaviour contradicts the approved expectation is **not** a finding to "correct". It is either a suspected product defect or a plan error → the human decides. Never let anyone change an expectation, skip, xfail or delete a test to get green.

## 5. Mutation check — `deep` mode only

`TE phase mutation`, then `TE mutate`. A script applies each proposed catalog mutation in a disposable copy of the project, runs the given tests there, deletes the copy. Results: `killed`, `survived`, `unviable`, `not_run` (with reason: `baseline_failed`, `environment_invalid` …).

- `survived` = potential coverage gap, **not** a proven bug. Optionally spawn the reviewer once (phase `mutation`) to triage survivors as `equivalent` / `real_gap`.
- Real gaps: if correction budget remains, `TE phase implementation` and send the engineer those gaps; otherwise report them.
- `environment_invalid` / `baseline_failed`: report as "mutation check could not be performed" with the reason. Do not present it as passed.
- If `original_tree_changed_during_mutation` is non-empty, tell the human exactly which files.

## 6. Finalize

`TE phase finalizing`, `TE report`, `TE phase completed`.

Final message to the human, facts only, taken from the report and recorded results:

- files created/modified; case mapping (implemented / deferred / blocked / not applicable);
- every command with its **recorded** outcome — distinguish passed, failed, timed out, launch-failed, blocked, flaky. A rerun that passed does not make a flaky test "passing";
- pre-existing or environment failures, kept separate from new failures;
- reviewer verdict and unresolved findings; discrepancies (suspected defects / plan errors);
- scope check result, pre-existing test files touched, approvals actually recorded;
- mutation results in deep mode, with the isolation caveat;
- path to `final-report.md`.

State that nothing was committed. Do not commit, push, merge or release; do not claim human acceptance — that is the human's decision.

## Failure handling

| Situation | Response |
|---|---|
| Policy denial (tool, path, command, spawn, phase) | Final. No retry by another route. Ask the human only if a precise decision can resolve it. |
| Runner `launch_error: permanent …` (missing binary, bad cwd) | Configuration error. Stop; fix via `test-setup`. |
| Runner transient launch failure | The runner already retried with backoff. Report. |
| Specialist crash / transient | 2 extra spawns per run in total (CLI-enforced). |
| Artifact invalid after automatic repair | `TE validate <artifact>`; one re-spawn with the errors if budget remains; else stop. |
| Unsure whether a previous step completed | `TE run status` and `TE snapshot diff` first; never repeat a write blindly. |
| Any budget exhausted | Stop, keep artifacts, report completed vs not completed stages and the exact decision needed. |

To stop: `TE run abort --reason "<why>"`.
