# Test Plan: [Feature / Module]

## 1. Metadata

| Field | Value |
|---|---|
| Plan ID | [stable identifier] |
| Version | [version] |
| Run ID | [run identifier] |
| Status | Draft / Under Review / Reviewed / Approved / Blocked |
| Owner | [role or person] |
| Scope | [feature/module and boundaries] |
| Date | [date] |

## 2. Objective and scope

### Objective

[What behavior and risk this plan is intended to verify.]

### In scope

- [Behavior/module]

### Out of scope

- [Explicit exclusions]

## 3. Evidence and requirements

| Reference ID | Source/path | Requirement, contract, or invariant | Authority | Notes / uncertainty |
|---|---|---|---|---|
| REQ-01 | [path/reference] | [statement] | Authoritative / Supporting / Observed behavior | [notes] |

Distinguish authoritative expected behavior from current implementation behavior. Mark conflicts and unknowns explicitly.

## 4. System understanding

- Entry points:
- Relevant modules/dependencies:
- Inputs and validation:
- State and persistence:
- External side effects:
- Error paths:
- Concurrency/idempotency considerations:
- Existing test infrastructure:

## 5. Risk assessment

| Risk ID | Failure mode | Impact | Likelihood | Priority | Rationale |
|---|---|---|---|---|---|
| R-01 | [failure mode] | [impact] | [likelihood] | P0/P1/P2/P3 | [reason] |

### Priority guidance

- **P0 — Critical:** data loss/corruption, severe security/privacy failure, privilege escalation, irreversible correctness failure, or core workflow unavailable.
- **P1 — High:** major business flow or contract failure, significant state inconsistency, or broad regression risk.
- **P2 — Medium:** constrained edge case, recoverable failure, or behavior with a workaround.
- **P3 — Low:** low-impact/cosmetic behavior or unlikely combination.

## 6. Test strategy

List only techniques applicable to this feature and briefly explain why:

- Equivalence partitioning:
- Boundary value analysis:
- Decision tables:
- State transitions:
- Pairwise/selected combinations:
- Negative/failure-path testing:
- Idempotency/concurrency:
- Security/privacy:
- Non-functional testing:

Record exact units, precision, rounding, comparison inclusivity, timezone, and representations where applicable.

## 7. Test cases

| ID | Requirement / risk | Scenario | Category / technique | Priority | Level | Preconditions | Exact input / actions | Expected observable result | Cleanup / side effects | Assumptions / notes |
|---|---|---|---|---|---|---|---|---|---|---|
| TC-001 | REQ-01 | [scenario] | Positive | P1 | Unit | [state] | [exact inputs] | [observable result] | [cleanup] | [notes] |

## 8. Requirements-to-tests coverage matrix

| Requirement / invariant | Planned test cases | Coverage status | Gap / rationale |
|---|---|---|---|
| REQ-01 | TC-001, TC-002 | Covered / Partial / Open | [notes] |

## 9. Test data and environment

- Required fixtures/data:
- External dependencies:
- Environment variables/secrets (must be avoided or explicitly approved):
- Test isolation strategy:
- Exact test commands:
- Known environmental constraints:

## 10. Open questions and assumptions

| ID | Question / assumption | Why it matters | Affected cases / risk | Decision owner | Status |
|---|---|---|---|---|---|
| OQ-01 | [question] | [impact] | [cases] | [owner] | Open / Resolved |

## 11. Review findings

| Finding ID | Severity | Finding | Evidence | Resolution / residual risk |
|---|---|---|---|---|

## 12. Approval and readiness

- Analyst status:
- Reviewer verdict:
- Human approval required? Why?
- Human decision / date (if applicable):
- Ready for implementation? Yes / No / With conditions

## 13. Execution order

[Suggested sequence, dependencies, and risk-first ordering.]
