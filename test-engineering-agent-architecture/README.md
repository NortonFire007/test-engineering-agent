# Agentic Test Engineering System

**Document type:** Architecture and implementation proposal  
**Status:** Platform-neutral reference design  
**Primary use case:** Risk-based test planning, test implementation, and independent quality review for software projects  
**Language:** English

## Purpose

This pack defines a reusable multi-agent system for producing meaningful, maintainable, and risk-focused automated tests. It is intended to be adapted to the agent runtime and repository in which it will be used.

It is an architecture and implementation brief. It is not a pre-installed configuration for a particular vendor, IDE, model, or agent framework.

## Design at a glance

- One **Orchestrator** owns the workflow, state, budgets, approvals, and final report.
- Two **Skills** define repeatable workflows: `test-plan` and `implement-tests`.
- Four **specialist agents** have narrow responsibilities: `test-codebase-explorer`, `test-analyst`, `test-reviewer`, and `test-engineer`.
- Tool access follows **default deny / least privilege**. An agent receives only the tools and resource scopes required for its role.
- Analysis and review roles are non-mutating. The implementation role can write only within approved test-related paths.
- Retries are bounded, classified, and observable. The workflow must stop rather than loop indefinitely.
- Human review is risk-triggered and mandatory at defined decision gates; routine analysis and test execution remain automated.

## Contents

| Path | Purpose |
|---|---|
| `ARCHITECTURE.md` | System architecture, components, diagrams, workflow, contracts, gates, and operating model |
| `IMPLEMENTATION_PROMPT.md` | Platform-neutral instructions for implementing this design in a real repository and agent runtime |
| `TOOL_ACCESS_POLICY.md` | Agent capability matrix, default-deny rules, and technical enforcement requirements |
| `RETRY_AND_ESCALATION_POLICY.md` | Retry budgets, transient/permanent failure handling, loop prevention, and escalation rules |
| `agents/test-codebase-explorer.md` | Specialist role specification for repository and test-infrastructure discovery |
| `agents/test-analyst.md` | Specialist role specification for test analysis and test-case design |
| `agents/test-reviewer.md` | Independent review role for test plans and implemented tests |
| `agents/test-engineer.md` | Narrowly scoped test implementation and verification role |
| `skills/test-plan/SKILL.md` | Workflow for discovery, analysis, and test-plan review |
| `skills/implement-tests/SKILL.md` | Workflow for implementing an approved plan and verifying test quality |
| `templates/test-plan-template.md` | Standard test-plan artifact template |
| `templates/agent-handoff-contract.md` | Structured contract for passing work between agents |
| `REFERENCES.md` | External design references informing the architecture |

## Recommended reading order

1. `ARCHITECTURE.md`
2. `TOOL_ACCESS_POLICY.md`
3. `RETRY_AND_ESCALATION_POLICY.md`
4. Agent specifications
5. Skill specifications
6. `IMPLEMENTATION_PROMPT.md`

## Important adaptation rule

Do not copy platform-specific examples mechanically. First identify the target runtime's actual capabilities for agent definitions, tool allowlists, filesystem isolation, command execution, approvals, state persistence, and observability. Map this design to native capabilities where possible. Where a capability is missing, document the gap and enforce the control at a stronger boundary (for example, an orchestrator, tool gateway, sandbox, or separate execution environment).

A natural-language instruction such as "do not edit files" is not a security boundary. Read-only behavior must be enforced by tool exposure, runtime permissions, filesystem policy, or an isolated execution environment.
