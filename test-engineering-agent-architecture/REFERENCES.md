# Architecture References

The architecture is platform-neutral. The following public materials were consulted for design patterns and operational safeguards. They are references, not runtime dependencies.

## Multi-agent workflow patterns

1. **Anthropic — Building effective agents**  
   https://www.anthropic.com/research/building-effective-agents  
   Design lessons: start with the simplest workflow that works; orchestrator-workers fit tasks with dynamically discovered subtasks; evaluator-optimizer loops are useful when explicit evaluation criteria exist. This supports one orchestrator, bounded specialists, and independent review rather than an unrestricted agent swarm.

2. **OpenAI — Agents SDK: Agent definitions and orchestration concepts**  
   https://developers.openai.com/api/docs/guides/agents/define-agents  
   https://developers.openai.com/tracks/building-agents  
   Design lessons: keep agent responsibilities, instructions, tools, and handoff behavior explicit; orchestration owns multi-step flow and context management.

3. **AWS Builder Center — Building Multi-Agent AI Systems on AWS**  
   https://builder.aws.com/content/3K90wVAxoDopIixLVr1fnLAXWsQ  
   Design lessons: specialist agents should have distinct responsibilities and permission boundaries; tool validation, human approval, audit logging, and observability are important parts of a multi-agent system.

## Security, permissions, and human oversight

4. **OWASP — AI Agent and MCP Security**  
   https://github.com/OWASP/DevSecOpsGuideline/blob/master/current-version/2-Process/2-2-Develop/2-2-5-AI-Agent-and-MCP-Security.md  
   Design lessons: least agency; limit tools and permissions to the task; treat repository files and tool outputs as untrusted; contain prompt-injection impact through sandboxing and strict access boundaries.

5. **Microsoft — AI agent shared responsibility model**  
   https://learn.microsoft.com/en-us/azure/security/fundamentals/shared-responsibility-ai-agent  
   Design lessons: validate untrusted inputs and inter-agent messages; enforce tool-level least privilege and per-action authorization; use step/iteration/cost limits; add human approval for consequential actions.

6. **Microsoft — Secure autonomous agentic AI systems**  
   https://learn.microsoft.com/en-us/security/zero-trust/sfi/secure-agentic-systems  
   Design lessons: treat agents as isolated components with narrowly scoped tools; use explicit action schemas; enforce deterministic human approval in orchestration logic for high-risk actions; default to least privilege.

7. **Microsoft — Least privilege for AI agents**  
   https://learn.microsoft.com/en-us/security/zero-trust/sfi/least-privilege-for-ai-agents  
   Design lessons: define agent identity, scope, tool access, and auditability; avoid permission creep and excessive aggregate privileges; authorize each action against the resource it targets.

8. **OpenAI — Guardrails and human review**  
   https://developers.openai.com/api/docs/guides/agents/guardrails-approvals  
   Design lessons: automatic guardrails validate inputs, outputs, and tool behavior; human review pauses consequential actions for explicit approval; checks must be placed at the tool boundary where side effects occur.

9. **Google Cloud — Choose a design pattern for your agentic AI system**  
   https://docs.cloud.google.com/architecture/choose-design-pattern-agentic-ai-system  
   Design lessons: use a human-in-the-loop checkpoint for workflows requiring subjective judgment, oversight, or final approval of critical actions.

## Observability and evaluation

10. **OpenAI — Integrations and observability**  
    https://developers.openai.com/api/docs/guides/agents/integrations-observability  
    Design lessons: trace model calls, tool calls, handoffs, guardrails, and custom workflow spans so multi-step behavior can be understood and debugged.

## How the references were applied

- The design uses one orchestrator and specialist agents rather than a large, recursively delegating agent network.
- It separates workflow instructions from role-specific agent responsibilities.
- It applies default-deny, per-agent capabilities, resource scoping, and per-action authorization.
- It uses human approval selectively for high-impact or ambiguous decisions.
- It defines bounded retries, explicit stop conditions, structured artifacts, and observability.
- It evaluates quality through traceability, meaningful assertions, determinism, and independent review—not line coverage alone.
