# Security Model — tool access, isolation, trust and their limits

Policy version `1`. Evidence for each claim marked **[tested]** is in `SECURITY_TEST_REPORT.md`.

## 1. The one distinction that matters

| | Claude Code hook enforcement (what this plugin provides) | OS-level isolation (what it does **not** provide) |
|---|---|---|
| Controls | Which **tool calls** an agent may make: tool, target path, command form, phase | What a **running process** may touch: files, network, other processes |
| Mechanism | `PreToolUse` hook decides allow/deny before the call runs | Sandbox, container, separate user, VM |
| Bypassable by a model | No — a deny is applied by the harness, including in `bypassPermissions` mode **[tested]** | — |
| Bypassable by code the model is allowed to run | **Yes** | No |

The engineer is allowed to write test files and run the project's test command. Test code is code: when it runs, it runs **with your OS privileges**, unsandboxed on native Windows. It can read `.env`, open network connections, modify any file you can modify — including this plugin's own state and audit log. Hook policy does not see or stop any of that. The snapshot diff *detects* changes inside the project tree afterwards; it does not prevent or revert them and it does not look outside the project.

Therefore:

- Read-only roles (explorer, analyst, reviewer) have **no execution path at all** — for them hook enforcement is a complete boundary over the tools they have.
- The engineer role is **least-privilege at the tool layer plus detective controls**, not isolation. For untrusted repositories, or when that residual risk is not acceptable, run Claude Code inside WSL2/Linux with the Claude Code sandbox enabled, or inside a container, and keep secrets out of the environment.
- This plugin never claims that production code is "guaranteed protected".

## 2. Enforcement layers

1. **Agent tool allowlist** (`tools:` in each agent file) — the tool is simply absent.
2. **Hook policy on every call** (`plugin/hooks/hooks.json` → `scripts/hook.py`) — default deny for the plugin's agents; independent of layer 1 **[tested with layer 1 removed]**.
3. **Canonical path authorization** — symlinks and junctions resolved before the decision; traversal, UNC/device paths, alternate data streams, 8.3 names, reserved device names, trailing dot/space and hard links rejected.
4. **Constrained runner** — the only shell command the engineer may issue is `te exec`; it runs an approved argv array without a shell, with a timeout, a flag allowlist and a conservative character set for selectors.
5. **Workflow gating** — a role can act only in its phase; spawns are counted; phase guards require valid artifacts and recorded approvals.
6. **Human approval prompts** for decisions that widen scope or accept risk.
7. **Detective controls** — snapshot diff, mechanical run records, audit log.

Hooks are launched in exec form (`uv` + argument array, no shell) so one definition behaves the same on Windows, macOS and Linux. The mandatory hook has `onFailure: "block"` and the script exits 2 on any internal error: a missing interpreter, a crash, a timeout or malformed input blocks the call **[tested]**.

## 3. Capability matrix

| Role | Read | Write | Shell | Delegate | Active in phases |
|---|---|---|---|---|---|
| Explorer | project (no secrets, no `.git`) | — | — | — | `discovery` |
| Analyst | project | `runs/<run>/test-plan.json` only | — | — | `planning` |
| Reviewer | project | `runs/<run>/review-plan.json`, `review-impl.json` only | — | — | `plan_review`, `impl_review`, `mutation` |
| Engineer | project | approved test paths + `runs/<run>/impl-report.json` | `te exec --run <run> <command-id> [-- selectors]` only | — | `implementation` |
| Orchestrator (while a run is current) | unrestricted (it is your session) | `.test-engineering/` except profile and evidence files | `te` CLI only | the four specialists, phase- and budget-gated | — |

Always denied for every specialist: PowerShell, NotebookEdit, web tools, MCP tools, the Agent tool, any tool not listed for the role, any action outside an active run or outside the role's phase.

### Write scope of the engineer

A path is writable only if all hold: it resolves inside the project; it is not a control location (`.git/`, `.claude/`, `.claude-plugin/`, `.test-engineering/`); it is not secret-like; it matches `allowed_write_paths` and not `excluded_paths`; it is not a protected project file (dependency manifests and lockfiles, build and CI configuration, migrations, Dockerfiles, test-runner configuration). `Write` may only create new files; a file that existed before the run can only be changed with `Edit`, and such files are listed for the human in the final report.

The only extension is a **human-approved exact path** for the current run. Control and secret locations can never be approved.

Profile validation independently rejects `allowed_write_paths` that are too broad (for example `**`, `src/**`, `**/*.py`): a pattern that would match typical production or configuration files is refused.

### Read scope and secrets

Denied for all specialists: anything outside the project; `.env` and `.env.*` (except `.example/.sample/.template/.dist`), key and certificate files, credential files and directories (`.ssh`, `.aws`, `.gnupg`, `.azure`, `.kube`, `.docker`), `.git` internals, the profile's `secret_paths`, the plugin's external state. Search tools are covered: globs aimed at secrets are denied, and a directory-wide content search is denied when it would scan a secret file that is not git-ignored (Claude Code's Grep returns matching lines from any non-ignored file — observed during development).

The runner strips environment variables that look like credentials (`*TOKEN*`, `*SECRET*`, `*PASSWORD*`, `*API_KEY*`, `AWS_*`, `GITHUB_*`, `ANTHROPIC_*`, …) unless the approved profile lists them in `env_passthrough`.

## 4. Profile trust

`profile.json` is repository content and defines permissions, so it is untrusted by default.

- The trust record lives outside the repository, under `~/.claude/test-engineering/projects/<project-id>/trust.json`, protected by user-level filesystem permissions (`0700` on POSIX; user-profile ACLs on Windows).
- The approved value is the SHA-256 of the **canonical JSON** (sorted keys, no insignificant whitespace) of the profile, bound to the plugin **policy version**; the record is bound to the **project path**. Only verification metadata (`generated_at`, `last_verified_at`, per-command `verified*`) is excluded from the hash.
- The profile is validated independently of who produced it: schema, executable (no shells, package installers, network or VCS tools; no absolute or escaping paths), argv arrays (no shell strings, metacharacters or inline-code flags), working directories, write paths.
- The current hash is compared on **every** specialist tool call and every spawn. Unapproved, modified, invalid, or approved under another policy version ⇒ all workflows are blocked until a human re-approves.
- Derived fields (fingerprints, secret file list, verification state) are computed by the CLI, never taken from a model's draft.

## 5. Human approvals

`te profile approve`, `te plan approve`, `te scope approve` and `te run unblock` are intercepted by the hook and turned into a permission prompt. They are **denied** when issued from a subagent or when the session is in `bypassPermissions`/`dontAsk` mode — an approval that nobody was asked for is not an approval. Approvals are bound to what was approved (profile hash, plan hash, exact path) and recorded in the run state and the report.

## 6. Trust boundaries and prompt injection

Repository files, artifacts, tool output and other agents' reports are data. The policy is code and state outside the conversation, so text in a file cannot change it **[tested with an injected instruction file]**. Agent prompts additionally tell specialists to treat embedded instructions as findings.

## 7. Known limitations

1. **No OS isolation** (see §1). Test code and anything it spawns run with your privileges; on native Windows Claude Code has no sandbox.
2. **Snapshot diff is detective and project-local.** It will not notice a file changed outside the project, a network call, or a change that was reverted before the check.
3. **External state is protected from agents' tools, not from executed code.** Test code could alter the trust record, counters or audit log. The audit log is append-only by convention, not tamper-proof.
4. **The orchestrator is your session.** The lock stops it from editing the project or running shell commands while a run is current, but it can end the run (`te run abort`, audited) and then act with your normal permissions. Its reads are not restricted.
5. **Approval prompts depend on Claude Code honouring the hook's `ask`.** Verified: denied in bypass mode and not auto-approved in non-interactive mode. Not verified: interaction with a user-configured allow rule that matches the same command; do not add allow rules for the approval subcommands.
6. **Command policy is a denylist plus human review.** The profile validator blocks obvious dangers, but an approved command is trusted: `make test` or `npm test` can run anything the project defines. Read the commands before approving the profile.
7. **Secret detection is name-based.** A secret in an unusually named file is not recognised unless listed in `secret_paths`. The Grep exposure check looks two directory levels deep plus the profile's recorded secret files.
8. **Language-agnostic mutation is textual.** Compile-time failures count as `killed`; operators are regex-based and can be `unviable` on unusual syntax; equivalence is a human/reviewer judgement.
9. **In-source unit tests** (Rust `#[cfg(test)]` modules, Go tests are fine) cannot be expressed as a path rule; they need a per-file human scope approval.
10. **Global hook blast radius.** The hook runs for every tool call in every session where the plugin is enabled. If `uv` or Python ≥ 3.11 is unavailable, all tool calls are blocked (fail closed). Enable the plugin per project if that is a concern.
11. **Token/cost budgets are indirect.** Spawns, cycles and command executions are bounded; tokens are not measured.
