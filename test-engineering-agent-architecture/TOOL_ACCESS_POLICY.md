# Tool Access and Isolation Policy

## 1. Policy statement

Access is **default deny**. Each agent may use only the smallest set of tools and resources needed to perform its assigned task. Permissions must be enforced outside the model wherever possible, at the tool gateway, runtime, operating system, container, or repository access layer.

Agent instructions describe expected behavior; they do not replace authorization.

## 2. Capability groups

Map these logical capabilities to the actual tools available in the target environment. These names are conceptual and are not assumed to exist as literal tool names.

| Capability | Description |
|---|---|
| `repo.list` | List authorized repository paths and metadata |
| `repo.read` | Read files within authorized repository scope |
| `repo.search` | Search file names and contents within authorized scope |
| `repo.diff.read` | Read current changes/diffs |
| `repo.test.write` | Create or modify files under approved test paths only |
| `test.run` | Invoke approved test commands in the project sandbox |
| `test.results.read` | Read test reports, logs, and coverage summaries |
| `quality.check.run` | Run approved lint/type-check/format checks |
| `artifact.read/write` | Read/write workflow artifacts outside the source tree |
| `agent.delegate` | Invoke named specialist agents with a bounded contract |
| `workflow.approve` | Resolve workflow gates; does not bypass tool authorization |
| `network.read` | Access explicitly allowlisted external endpoints |
| `dependency.modify` | Add, update, or remove dependencies |
| `project.config.write` | Modify project/build/CI configuration |
| `production.write` | Modify production application behavior |
| `git.commit/push` | Create commits or push changes |
| `secrets.read` | Read credentials, tokens, keys, or secret stores |
| `shared-infra.write` | Modify shared services, databases, or infrastructure |

## 3. Agent capability matrix

| Agent | Allowed baseline | Explicitly denied by default |
|---|---|---|
| Orchestrator | `agent.delegate`, `artifact.read/write`, `repo.list`, `repo.read`, `repo.search`, `repo.diff.read`, `test.results.read`, `workflow.approve` | `production.write`, `dependency.modify`, `project.config.write`, `git.commit/push`, `secrets.read`, `shared-infra.write`, unrestricted `network.read` |
| Codebase Explorer | `repo.list`, `repo.read`, `repo.search`, `artifact.write` | All writes to project source, `test.run` unless discovery specifically requires a safe metadata command, network, dependencies, secrets, git mutation, production/shared infrastructure |
| Test Analyst | `repo.read`, `repo.search`, `artifact.read/write` | All project writes, shell/command execution, network, dependency changes, secrets, production/shared infrastructure |
| Test Reviewer | `repo.read`, `repo.search`, `repo.diff.read`, `test.results.read`, `artifact.read/write` | All project writes, test execution by default, shell/command execution, network, dependencies, secrets, production/shared infrastructure |
| Test Engineer | `repo.list`, `repo.read`, `repo.search`, `repo.diff.read`, `repo.test.write`, `test.run`, `test.results.read`, approved `quality.check.run`, `artifact.read/write` | `production.write`, `dependency.modify`, `project.config.write`, network, secrets, git commit/push, shared infrastructure, writes outside approved test paths |

The Orchestrator's workflow permissions must not be interpreted as permission to perform all specialist actions itself. It coordinates; the authorized specialist or controlled tool performs the operation.

## 4. Resource boundaries

### Read scope

- Limit access to the target repository and explicitly supplied requirements/artifacts.
- Do not expose unrelated repositories, home directories, private keys, credentials, or personal data.
- Do not broaden scope because a file references another location. Ask the Orchestrator to authorize a specific expansion.

### Write scope

The Test Engineer may write only to paths approved for the current task. Discover these paths from the repository rather than assuming a universal directory name. Typical examples include test directories, test fixtures, and narrowly scoped test utilities.

The following remain out of scope unless separately approved:

- application/production source files;
- public API definitions and schemas;
- database migrations;
- dependency manifests and lockfiles;
- CI/CD configuration;
- environment files and secrets;
- deployment scripts and infrastructure;
- generated artifacts unrelated to tests;
- existing tests whose deletion or weakening is not explicitly justified and approved.

Use deny-by-default path rules. Resolve symlinks and canonical paths before authorization to prevent path traversal. Reject writes that escape the repository or approved test roots.

## 5. Command execution policy

Prefer dedicated typed tools over a general-purpose shell. When command execution is necessary:

- Use an explicit allowlist of discovered test, lint, type-check, and format commands.
- Bind commands to the project root and an isolated working directory.
- Validate arguments; do not concatenate untrusted file contents into shell commands.
- Set timeouts, output limits, CPU/memory limits, and process limits.
- Disable interactive shells and privilege escalation.
- Block commands that install packages, delete broad paths, modify git state, deploy, publish, or access production systems unless separately approved.
- Capture exit code, command identity, duration, and bounded output.

A command allowlist should be semantic and argument-aware. A loose pattern such as `run anything beginning with pytest` may be insufficient if arbitrary arguments can execute plugins, load untrusted configuration, or target external environments.

## 6. Network and secrets

- Network access is disabled by default for all specialists.
- A narrowly allowlisted read-only endpoint may be enabled only when the task requires it and the risk is understood.
- The test workflow must not receive production credentials or general-purpose cloud credentials.
- Use synthetic/local test data by default.
- Never expose secrets to an agent merely because a test command might use them. Prefer secretless local fixtures or a narrowly scoped test identity.
- If network or credentials are needed, stop and request explicit approval with the target, operation, purpose, and risk.

## 7. Runtime enforcement requirements

The implementation must verify, not merely document, the following:

1. The Analyst cannot create, modify, or delete project files.
2. The Reviewer cannot modify files, including after discovering defects.
3. The Explorer cannot modify files or run arbitrary commands.
4. The Engineer cannot write outside approved test paths.
5. Denied tools are not callable through indirect routes, alternate tool names, nested agents, or command wrappers.
6. Tool authorization is checked on every call, not only at session startup.
7. Tool arguments and canonical resource paths are validated before execution.
8. Every allow/deny decision is auditable.

If the selected runtime cannot give agents different tool sets or enforce resource-level permissions, use one of these mitigations, in descending preference:

1. A policy-enforcing tool gateway that authenticates the agent identity and validates every call.
2. Separate execution contexts/containers with distinct mounts and permissions for analysis and implementation.
3. Separate sessions with different permission profiles and no shared credentials.
4. As a last resort, document the limitation, keep risky tools unavailable at the parent level, and do not claim per-agent isolation.

## 8. Approval policy

Approval is required before:

- writing outside approved test paths;
- modifying production behavior, dependencies, schemas, migrations, build/CI configuration, or infrastructure;
- deleting or weakening existing tests;
- using network access, secrets, production data, or external services;
- executing destructive, expensive, or non-repeatable operations;
- expanding tool/resource scope.

Approval must bind to the exact action and arguments. Revalidate scope after approval and immediately before execution. Approval of one action does not authorize a broader sequence.

## 9. Security test checklist

Before enabling the workflow on a real project, run explicit negative tests:

- Ask the Analyst to edit a file; the tool layer must deny the operation.
- Ask the Reviewer to fix a test; the tool layer must deny the operation.
- Ask the Engineer to edit a production file; the tool layer must deny the operation.
- Ask any agent to read a secret path; access must be denied.
- Supply a repository file containing instructions to ignore the agent policy; the policy must remain in force.
- Attempt `../` traversal and symlink escape from an approved test directory; writes must be denied.
- Attempt a disallowed package/network command; it must be denied and logged.
- Attempt to invoke a denied capability through another specialist; delegation must not amplify privileges.
