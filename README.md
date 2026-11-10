# test-engineering — agentic test engineering for Claude Code

A Claude Code plugin that plans, implements and independently reviews automated tests for any repository, in any language. It learns once how a project is tested, then produces risk-based tests at the cheapest sufficient level (unit before integration before end-to-end), with enforced least-privilege agents, bounded loops and human approval gates.

It is a system, not a set of prompts: state, budgets, approvals, command execution and evidence are handled by a deterministic standard-library Python core; the models only do the analysis and the writing.

## Requirements

- Claude Code 2.1.x
- [`uv`](https://docs.astral.sh/uv/) on `PATH` and Python ≥ 3.11 available to it (hooks and the CLI are launched through `uv`; no third-party Python packages)
- Windows, macOS or Linux. **Read the isolation note below before using it on a repository you do not trust.**

## Install

```
claude plugin marketplace add <path-or-git-url-of-this-repository>
/plugin install test-engineering@test-engineering-marketplace
```

For a single session without installing: `claude --plugin-dir <this-repo>/plugin`.

The plugin's policy hook runs on every tool call in sessions where the plugin is enabled and **fails closed**: if `uv`/Python is unavailable, tool calls are blocked. Enable it per project if you prefer a smaller blast radius.

## Use

| Step | Command | What happens |
|---|---|---|
| 1. Once per repository | `/test-engineering:test-setup` | Detects stack, frameworks, commands and conventions; you review and approve `.test-engineering/profile.json`; commands are verified by actually running them. |
| 2. Plan | `/test-engineering:test-plan <feature> [quick\|standard\|deep]` | Discovery → **you confirm test levels and mode** → analysis → independent review → **you approve the plan**. Writes no source or test files. |
| 3. Implement | `/test-engineering:implement-tests` | Engineer writes tests inside approved test paths and runs them through the approved runner → scope check → independent review → bounded correction → report. In `deep` mode, a scripted mutation check. |

Commit `.test-engineering/profile.json`, `conventions.md` and `.gitignore`; `runs/` is ignored.

Nothing is ever committed, pushed or merged by the workflow. Final acceptance is yours.

### Cost

Set the session model yourself (`/model sonnet`); skills cannot pin it. Specialists are pinned: explorer and engineer on Haiku, analyst and reviewer on Sonnet; the engineer escalates to Sonnet for later correction cycles. Override per project in `.test-engineering/config.json`:

```json
{ "default_mode": "standard", "models": { "engineer": "haiku", "engineer_escalated": "sonnet" } }
```

## Isolation — what is and is not enforced

Hook policy controls which **tool calls** each agent may make, on every call, and holds even in `bypassPermissions` mode. It is **not an OS sandbox**: the project's tests, once allowed to run, execute with your privileges (unsandboxed on native Windows). The snapshot diff detects out-of-scope changes inside the project afterwards; it does not prevent them. For untrusted code, run Claude Code in WSL2/Linux with its sandbox, or in a container.

Details: [`docs/SECURITY_MODEL.md`](docs/SECURITY_MODEL.md). Evidence: [`docs/SECURITY_TEST_REPORT.md`](docs/SECURITY_TEST_REPORT.md).

## Repository layout

```
plugin/                      the plugin
  agents/                    4 specialists (tool allowlists, pinned models)
  skills/                    test-setup, test-plan, implement-tests
  hooks/hooks.json           PreToolUse policy + SubagentStop validation (exec form, fail closed)
  scripts/te.py, hook.py     CLI and hook entry points
  scripts/telib/             policy, workflow state machine, profile trust, runner, snapshot, mutation
  config/defaults.json       modes, budgets, default models
docs/                        architecture, security model, security test report
tests/                       unit + policy tests (unittest, standard library)
tests/live/                  live red-team permission matrix against real Claude Code sessions
fixtures/                    synthetic Rust (no tests, no git) and Go (with tests) projects
docker/                      Linux run of the unit suite
test-engineering-agent-architecture/   the original platform-neutral brief (unchanged)
```

## Develop and verify

```
uv run --no-project --python 3.11 python -m unittest discover -s tests -t tests     # unit + policy tests
claude plugin validate plugin --strict                                              # manifest, agents, skills, hooks
uv run --no-project tests/live/run_live.py                                          # live matrix (real model calls, disposable dirs only)
powershell -ExecutionPolicy Bypass -File docker\run-linux-checks.ps1                # same unit suite on Linux
uv run --no-project plugin/scripts/te.py doctor                                     # environment + isolation statement
```

Start with [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
