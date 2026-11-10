"""Handoff artifact schemas, cross-artifact checks and Markdown rendering.

A small hand-rolled validator keeps this dependency-free. Artifacts are data
produced by agents and are never treated as instructions or as policy.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import state
from .common import TEError, canonical_json, read_json, sha256_text

STATUS = ["completed", "partial", "blocked", "failed"]
PRIORITY = ["P0", "P1", "P2", "P3"]
LEVEL = ["unit", "integration", "api", "contract", "end_to_end", "performance", "security", "manual"]
CATEGORY = ["positive", "negative", "boundary", "state", "interaction", "failure", "security", "performance",
            "concurrency", "other"]
SEVERITY = ["critical", "high", "medium", "low", "note"]
FINDING_TYPE = ["coverage_gap", "incorrect_expectation", "weak_assertion", "determinism", "isolation", "scope",
                "security", "maintainability", "reporting", "test_level"]
VERDICT = ["APPROVE", "APPROVE_WITH_RESIDUAL_RISKS", "NEEDS_REVISION"]
AUTHORITY = ["authoritative", "supporting", "observed_behavior"]
OPERATORS = ["rel_boundary", "eq_negate", "const_shift", "remove_null_check", "negate_condition", "bool_flip",
             "remove_line"]

S = {"t": "str"}
SL = {"t": "list", "items": S}
B = {"t": "bool"}
I = {"t": "int"}


def E(*values):
    return {"t": "str", "enum": list(values)}


def L(items, minlen=0):
    return {"t": "list", "items": items, "min": minlen}


def O(req, opt=None):
    return {"t": "obj", "req": req, "opt": opt or {}}


EVIDENCE = L(O({"source": S, "observation": S}, {"confidence": E("high", "medium", "low")}))
REQUIREMENTS = L(O({"id": S, "source": S, "statement": S, "authority": E(*AUTHORITY)}, {"notes": S}))
OPEN_QUESTIONS = L(O({"id": S, "question": S, "why_it_matters": S},
                     {"affected_cases": SL, "blocks_priority": E("P0", "P1", "P2", "P3", "none")}))
ENVELOPE = {"run_id": S, "agent_role": S, "status": E(*STATUS), "summary": S}

SCHEMAS = {
    "repo-map": O({
        **ENVELOPE, "evidence": EVIDENCE, "scope_examined": S,
        "modules": L(O({"path": S, "role": S})),
        "entry_points": L(O({"symbol": S, "path": S}, {"kind": S})),
        "inputs_and_validation": L(O({"rule": S, "source": S}, {"limit": S})),
        "state_and_side_effects": SL, "failure_paths": SL, "requirements": REQUIREMENTS,
        "existing_tests": L(O({"path": S, "level": S}, {"covers": S})),
        "reusable_fixtures": L(O({"name": S, "path": S}, {"purpose": S})),
        "unknowns": SL, "suggested_analysis_areas": SL,
        "level_recommendation": O({"levels": L(E(*LEVEL), 1), "rationale": S, "e2e_infrastructure_present": B}),
        "estimated_case_count": I,
    }, {"open_questions": OPEN_QUESTIONS, "assumptions": SL, "suspicious_content": SL}),
    "profile-draft": O({
        **ENVELOPE, "profile": {"t": "any"}, "conventions_md": S, "fingerprint_files": SL,
    }, {"notes": SL, "unverified_assumptions": SL, "suspicious_content": SL}),
    "test-plan": O({
        **ENVELOPE, "plan_id": S, "version": I,
        "scope": O({"objective": S, "in_scope": SL, "out_of_scope": SL}),
        "requirements": REQUIREMENTS,
        "risks": L(O({"id": S, "failure_mode": S, "impact": S, "priority": E(*PRIORITY)}, {"likelihood": S, "rationale": S})),
        "strategy": L(O({"technique": S, "rationale": S}), 1),
        "test_cases": L(O({
            "test_case_id": S, "requirement_refs": SL, "scenario": S, "category": E(*CATEGORY),
            "priority": E(*PRIORITY), "test_level": E(*LEVEL), "level_rationale": S,
            "preconditions": SL, "inputs_and_actions": L(S, 1), "expected_observations": L(S, 1),
        }, {"technique": S, "cleanup_and_side_effects": SL, "assumptions": SL, "target_test_path": S,
            "command_id": S, "expected_result_evidence": S}), 1),
        "coverage": L(O({"ref": S, "test_cases": SL, "status": E("covered", "partial", "open")}, {"gap": S})),
        "coverage_gaps": L(O({"description": S, "reason": S}, {"recommended_level": S})),
        "open_questions": OPEN_QUESTIONS,
        "environment": O({"fixtures": SL, "external_dependencies": SL, "isolation": S}),
    }, {"boundary_semantics": L(O({"item": S, "unit": S, "inclusivity": S, "evidence": S})),
        "execution_order": SL, "assumptions": SL, "changes_since_previous": SL}),
    "review": O({
        **ENVELOPE, "review_target": E("plan", "implementation"), "verdict": E(*VERDICT),
        "findings": L(O({
            "finding_id": S, "severity": E(*SEVERITY), "type": E(*FINDING_TYPE), "evidence_ref": S,
            "problem": S, "impact": S, "recommendation": S, "human_decision_required": B,
        })),
        "residual_risks": SL,
    }, {"mutation_spec": L(O({
            "id": S, "file": S, "line": I, "operator": E(*OPERATORS), "command_id": S, "rationale": S,
        }, {"occurrence": I, "delta": I, "selectors": SL, "test_case_refs": SL})),
        "mutation_triage": L(O({"mutation_id": S, "classification": E("equivalent", "real_gap", "undetermined"),
                                "justification": S})),
        "open_questions": OPEN_QUESTIONS}),
    "impl-report": O({
        **ENVELOPE, "plan_hash": S, "files_created": SL, "files_modified": SL,
        "case_mapping": L(O({"test_case_id": S, "status": E("implemented", "deferred", "not_applicable", "blocked"),
                             "tests": SL}, {"reason": S}), 1),
        "commands_run": L(O({"result_id": I, "command_id": S}, {"purpose": S})),
        "discrepancies": L(O({"test_case_id": S, "expected": S, "observed": S,
                              "classification": E("suspected_product_defect", "plan_error", "environment", "flaky",
                                                  "unknown")})),
        "residual_gaps": SL,
    }, {"new_fixtures": SL, "open_questions": OPEN_QUESTIONS, "escalations": SL}),
}

ARTIFACT_FILES = {
    "repo-map": "repo-map.json", "profile-draft": "profile-draft.json", "test-plan": "test-plan.json",
    "review-plan": "review-plan.json", "review-impl": "review-impl.json", "impl-report": "impl-report.json",
}


def _check(v, spec, where, errs):
    t = spec["t"]
    if t == "any":
        return
    if t == "str":
        if not isinstance(v, str) or not v.strip():
            errs.append(f"{where}: non-empty string required")
        elif "enum" in spec and v not in spec["enum"]:
            errs.append(f"{where}: must be one of {spec['enum']} (got {v!r})")
    elif t == "bool":
        if not isinstance(v, bool):
            errs.append(f"{where}: boolean required")
    elif t == "int":
        if not isinstance(v, int) or isinstance(v, bool):
            errs.append(f"{where}: integer required")
    elif t == "list":
        if not isinstance(v, list):
            errs.append(f"{where}: array required")
            return
        if len(v) < spec.get("min", 0):
            errs.append(f"{where}: at least {spec['min']} item(s) required")
        for i, item in enumerate(v):
            _check(item, spec["items"], f"{where}[{i}]", errs)
    elif t == "obj":
        if not isinstance(v, dict):
            errs.append(f"{where}: object required")
            return
        for k, sub in spec["req"].items():
            if k not in v:
                errs.append(f"{where}.{k}: required field missing")
            else:
                _check(v[k], sub, f"{where}.{k}", errs)
        for k, sub in spec["opt"].items():
            if k in v and v[k] is not None:
                _check(v[k], sub, f"{where}.{k}", errs)


def schema_name(artifact: str) -> str:
    return "review" if artifact.startswith("review-") else artifact


def plan_hash(plan: dict) -> str:
    return sha256_text(canonical_json(plan))


def validate(artifact: str, obj, root: str | None = None, run: dict | None = None) -> list:
    """Schema plus semantic cross-checks. Returns a list of error strings."""
    if artifact not in ARTIFACT_FILES:
        raise TEError(f"unknown artifact '{artifact}'. Known: {sorted(ARTIFACT_FILES)}")
    errs: list = []
    _check(obj, SCHEMAS[schema_name(artifact)], artifact, errs)
    if errs or not isinstance(obj, dict):
        return errs[:40]
    if run and obj.get("run_id") != run["run_id"]:
        errs.append(f"run_id: must be '{run['run_id']}'")
    if artifact == "test-plan":
        errs += _check_plan(obj, run)
    elif artifact.startswith("review-"):
        want = "plan" if artifact == "review-plan" else "implementation"
        if obj["review_target"] != want:
            errs.append(f"review_target: must be '{want}' for {artifact}")
        blocking = [f["finding_id"] for f in obj["findings"] if f["severity"] in ("critical", "high")]
        if blocking and obj["verdict"] == "APPROVE":
            errs.append(f"verdict: cannot be APPROVE with critical/high findings {blocking}")
        ids = [f["finding_id"] for f in obj["findings"]]
        if len(ids) != len(set(ids)):
            errs.append("findings: duplicate finding_id")
    elif artifact == "impl-report" and root and run:
        errs += _check_impl(obj, root, run)
    return errs[:40]


def _check_plan(plan: dict, run: dict | None) -> list:
    errs = []
    req_ids = {r["id"] for r in plan["requirements"]} | {r["id"] for r in plan["risks"]}
    seen = set()
    approved_levels = set(((run or {}).get("strategy") or {}).get("levels") or [])
    for tc in plan["test_cases"]:
        tid = tc["test_case_id"]
        if not re.match(r"^TC-\d{3,}$", tid):
            errs.append(f"{tid}: test_case_id must look like TC-001")
        if tid in seen:
            errs.append(f"{tid}: duplicate test_case_id")
        seen.add(tid)
        unknown = [r for r in tc["requirement_refs"] if r not in req_ids]
        if unknown:
            errs.append(f"{tid}: requirement_refs {unknown} are not defined in requirements/risks")
        if tc["priority"] in ("P0", "P1") and not tc["requirement_refs"]:
            errs.append(f"{tid}: P0/P1 case must reference a requirement, contract, invariant or risk")
        if approved_levels and tc["test_level"] not in approved_levels:
            errs.append(f"{tid}: test_level '{tc['test_level']}' was not approved at the strategy gate "
                        f"(approved: {sorted(approved_levels)}). Move it to coverage_gaps or pick an approved level.")
    covered = {r for tc in plan["test_cases"] for r in tc["requirement_refs"]}
    cov_refs = {c["ref"] for c in plan["coverage"]}
    for r in plan["requirements"]:
        if r["id"] not in covered and r["id"] not in cov_refs:
            errs.append(f"requirement {r['id']}: neither covered by a test case nor listed in coverage as a gap")
    for c in plan["coverage"]:
        missing = [t for t in c["test_cases"] if t not in seen]
        if missing:
            errs.append(f"coverage[{c['ref']}]: unknown test cases {missing}")
        if c["status"] != "covered" and not c.get("gap"):
            errs.append(f"coverage[{c['ref']}]: status '{c['status']}' requires a 'gap' explanation")
    return errs


def _check_impl(rep: dict, root: str, run: dict) -> list:
    errs = []
    adir = state.artifacts_dir(root, run["run_id"])
    try:
        plan = read_json(adir / ARTIFACT_FILES["test-plan"])
    except (OSError, ValueError):
        return ["impl-report: test-plan.json is missing or unreadable"]
    if rep["plan_hash"] != plan_hash(plan):
        errs.append("plan_hash: does not match the current test-plan.json (see `te run status`)")
    plan_ids = {tc["test_case_id"] for tc in plan["test_cases"]}
    mapped = {m["test_case_id"] for m in rep["case_mapping"]}
    if plan_ids - mapped:
        errs.append(f"case_mapping: planned cases without a mapping: {sorted(plan_ids - mapped)}")
    if mapped - plan_ids:
        errs.append(f"case_mapping: cases not in the approved plan: {sorted(mapped - plan_ids)}")
    for m in rep["case_mapping"]:
        if m["status"] == "implemented" and not m["tests"]:
            errs.append(f"{m['test_case_id']}: status 'implemented' requires at least one test reference")
        if m["status"] != "implemented" and not m.get("reason"):
            errs.append(f"{m['test_case_id']}: status '{m['status']}' requires a reason")
    for c in rep["commands_run"]:
        if not (adir / "results" / f"{c['result_id']:03d}.json").is_file():
            errs.append(f"commands_run: result_id {c['result_id']} has no recorded execution "
                        "(only commands run through `te exec` count as evidence)")
    if not rep["commands_run"] and any(m["status"] == "implemented" for m in rep["case_mapping"]):
        errs.append("commands_run: implemented tests must be executed through `te exec` at least once")
    return errs


def extract_json(text: str):
    """Pull the JSON object out of an agent's final message."""
    if not isinstance(text, str):
        raise ValueError("no text")
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    candidate = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    if not candidate:
        raise ValueError("no JSON object found in the final message")
    return json.loads(candidate)


def load_artifact(root: str, run_id: str, artifact: str):
    p = state.artifacts_dir(root, run_id) / ARTIFACT_FILES[artifact]
    if not p.is_file():
        raise TEError(f"artifact not found: {p}")
    try:
        return read_json(p)
    except ValueError as e:
        raise TEError(f"{p.name} is not valid JSON: {e}")


def _cell(v) -> str:
    if isinstance(v, list):
        v = "<br>".join(str(x) for x in v)
    return str(v if v is not None else "").replace("|", "\\|").replace("\n", " ")


def _table(headers, rows) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def render_plan(plan: dict, run: dict, review: dict | None) -> str:
    """Human-readable test plan following templates/test-plan-template.md."""
    appr = run.get("plan_approval") or {}
    parts = [
        f"# Test Plan: {run.get('title', plan['plan_id'])}", "",
        "## 1. Metadata", "",
        _table(["Field", "Value"], [
            ["Plan ID", plan["plan_id"]], ["Version", plan["version"]], ["Run ID", plan["run_id"]],
            ["Mode", run.get("mode")], ["Plan hash", plan_hash(plan)[:16]],
            ["Status", "Approved" if appr.get("hash") == plan_hash(plan) else "Awaiting human approval"],
            ["Approved levels", ", ".join((run.get("strategy") or {}).get("levels") or [])],
        ]), "",
        "## 2. Objective and scope", "", plan["scope"]["objective"], "",
        "**In scope**", *[f"- {x}" for x in plan["scope"]["in_scope"]], "",
        "**Out of scope**", *[f"- {x}" for x in plan["scope"]["out_of_scope"]], "",
        "## 3. Evidence and requirements", "",
        _table(["ID", "Source", "Statement", "Authority", "Notes"],
               [[r["id"], r["source"], r["statement"], r["authority"], r.get("notes", "")] for r in plan["requirements"]]),
        "", "## 4. Risk assessment", "",
        _table(["ID", "Failure mode", "Impact", "Likelihood", "Priority", "Rationale"],
               [[r["id"], r["failure_mode"], r["impact"], r.get("likelihood", ""), r["priority"], r.get("rationale", "")]
                for r in plan["risks"]]),
        "", "## 5. Test strategy", "", *[f"- **{s['technique']}** — {s['rationale']}" for s in plan["strategy"]], "",
    ]
    if plan.get("boundary_semantics"):
        parts += ["### Exact boundary semantics", "",
                  _table(["Item", "Unit", "Inclusivity", "Evidence"],
                         [[b["item"], b["unit"], b["inclusivity"], b["evidence"]] for b in plan["boundary_semantics"]]), ""]
    parts += ["## 6. Test cases", "",
              _table(["ID", "Refs", "Scenario", "Category", "Priority", "Level", "Why this level", "Preconditions",
                      "Inputs / actions", "Expected observable result", "Cleanup", "Notes"],
                     [[t["test_case_id"], ", ".join(t["requirement_refs"]), t["scenario"], t["category"], t["priority"],
                       t["test_level"], t["level_rationale"], t["preconditions"], t["inputs_and_actions"],
                       t["expected_observations"], t.get("cleanup_and_side_effects", []), t.get("assumptions", [])]
                      for t in plan["test_cases"]]),
              "", "## 7. Requirements-to-tests coverage", "",
              _table(["Requirement / risk", "Test cases", "Status", "Gap"],
                     [[c["ref"], ", ".join(c["test_cases"]), c["status"], c.get("gap", "")] for c in plan["coverage"]]),
              "", "## 8. Coverage gaps (deliberately not automated)", "",
              _table(["Description", "Reason", "Recommended level"],
                     [[g["description"], g["reason"], g.get("recommended_level", "")] for g in plan["coverage_gaps"]]),
              "", "## 9. Test data and environment", "",
              f"- Fixtures: {', '.join(plan['environment']['fixtures']) or 'none'}",
              f"- External dependencies: {', '.join(plan['environment']['external_dependencies']) or 'none'}",
              f"- Isolation: {plan['environment']['isolation']}", "",
              "## 10. Open questions", "",
              _table(["ID", "Question", "Why it matters", "Affected cases", "Blocks"],
                     [[q["id"], q["question"], q["why_it_matters"], ", ".join(q.get("affected_cases", [])),
                       q.get("blocks_priority", "none")] for q in plan["open_questions"]]), ""]
    if review:
        parts += ["## 11. Independent review", "", f"**Verdict:** {review['verdict']}", "",
                  _table(["Finding", "Severity", "Type", "Problem", "Recommendation", "Human decision?"],
                         [[f["finding_id"], f["severity"], f["type"], f["problem"], f["recommendation"],
                           "yes" if f["human_decision_required"] else "no"] for f in review["findings"]]),
                  "", "**Residual risks**", *[f"- {x}" for x in review["residual_risks"]], ""]
    else:
        parts += ["## 11. Independent review", "", "_No plan review in this mode._", ""]
    if plan.get("execution_order"):
        parts += ["## 12. Execution order", "", *[f"{i}. {x}" for i, x in enumerate(plan["execution_order"], 1)], ""]
    return "\n".join(parts) + "\n"
