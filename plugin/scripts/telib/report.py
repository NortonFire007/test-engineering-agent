"""Final run report assembled from recorded evidence, not from agent claims."""
from __future__ import annotations

import json
from pathlib import Path

from . import artifacts, profile, state
from .common import now_iso, read_json, write_text_atomic


def _load(path):
    try:
        return read_json(path)
    except (OSError, ValueError):
        return None


def _audit_stats(root: str, run_id: str) -> dict:
    stats = {"allow": 0, "deny": 0, "ask": 0, "none": 0, "denials": []}
    p = state.project_dir(root) / "audit.jsonl"
    if not p.is_file():
        return stats
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("run_id") != run_id or rec.get("event") != "tool_call":
            continue
        d = rec.get("decision", "none")
        stats[d] = stats.get(d, 0) + 1
        if d == "deny" and len(stats["denials"]) < 30:
            stats["denials"].append({k: rec.get(k) for k in ("ts", "role", "tool", "target", "reason")})
    return stats


def build(root: str, run: dict):
    adir = state.artifacts_dir(root, run["run_id"])
    plan = _load(adir / "test-plan.json")
    impl = _load(adir / "impl-report.json")
    rplan = _load(adir / "review-plan.json")
    rimpl = _load(adir / "review-impl.json")
    changes = _load(adir / "changes.json")
    mut = _load(adir / "mutation-report.json")
    results = [r for r in (_load(p) for p in sorted((adir / "results").glob("*.json"))) if r] \
        if (adir / "results").is_dir() else []
    audit = _audit_stats(root, run["run_id"])
    trust = profile.trust_status(root)
    appr = run.get("plan_approval") or {}
    plan_ok = bool(plan and appr.get("hash") == artifacts.plan_hash(plan))
    outcomes = {}
    for r in results:
        key = "flaky" if r.get("flaky") else r["outcome"]
        outcomes[key] = outcomes.get(key, 0) + 1
    mapping = {}
    for m in (impl or {}).get("case_mapping", []):
        mapping[m["status"]] = mapping.get(m["status"], 0) + 1

    def unresolved(rev):
        return [f for f in (rev or {}).get("findings", []) if f["severity"] in ("critical", "high", "medium")]

    L = [f"# Test Engineering Run Report — {run.get('title', '')}", "",
         f"- Run: `{run['run_id']}`  |  kind: {run['kind']}  |  mode: {run['mode']}  |  status: **{run['status']}** "
         f"(phase `{run['phase']}`)",
         f"- Generated: {now_iso()}  |  policy version {run['policy_version']}  |  profile trust: {trust['state']}",
         "", "## Approvals actually recorded", ""]
    if run["approvals"]:
        L += [artifacts._table(["Type", "At", "Recorded by", "Detail"],
                               [[a["type"], a["at"], a.get("recorded_by", ""), json.dumps(a["detail"])[:160]]
                                for a in run["approvals"]])]
    else:
        L += ["_None._"]
    L += ["", f"- Plan approved by a human: **{'yes' if plan_ok else 'no'}**; implementation authorized: "
              f"**{'yes' if appr.get('implementation_authorized') else 'no'}**",
          "- Final acceptance (merge/release): **not recorded — this is the human's decision**", "",
          "## Budgets", "",
          artifacts._table(["Budget", "Used", "Limit"], [
              ["Plan revision cycles", run["counters"]["plan_revisions"], run["mode_cfg"]["plan_revision_cycles"]],
              ["Implementation correction cycles", run["counters"]["impl_corrections"], run["mode_cfg"]["impl_correction_cycles"]],
              ["Transient retries", run["counters"]["transient_retries_used"], run["limits"]["transient_retries"]],
              ["Command executions", run["counters"]["exec_calls"], run["limits"]["exec_calls"]],
              ["Flaky diagnostic reruns", run["counters"]["flaky_reruns"], "1 per result"],
              ["Specialist spawns", json.dumps(run["spawn_total"]), "per phase"],
          ]), ""]
    if plan:
        by_level = {}
        for t in plan["test_cases"]:
            by_level[t["test_level"]] = by_level.get(t["test_level"], 0) + 1
        L += ["## Plan", "", f"- Cases by level (descriptive only): {by_level}",
              f"- Plan review verdict: {(rplan or {}).get('verdict', 'no plan review in this mode')}",
              f"- Open questions: {len(plan['open_questions'])}; coverage gaps: {len(plan['coverage_gaps'])}", ""]
    if results:
        L += ["## Commands executed (recorded by the runner)", "",
              artifacts._table(["#", "Command", "Extra args", "Outcome", "Exit", "Flaky", "Seconds"],
                               [[r["result_id"], r["command_id"], " ".join(r.get("extra", [])), r["outcome"],
                                 r["exit_code"], "yes" if r.get("flaky") else "", r["duration_sec"]] for r in results]),
              "", f"- Outcome totals: {outcomes}. A rerun that passes does not turn a flaky test into a pass.", ""]
    if impl:
        L += ["## Implementation", "", f"- Case mapping: {mapping}",
              f"- Implementation review verdict: {(rimpl or {}).get('verdict', 'not reviewed')}", ""]
        if impl["discrepancies"]:
            L += ["### Discrepancies between plan and observed behaviour", "",
                  artifacts._table(["Case", "Expected", "Observed", "Classification"],
                                   [[d["test_case_id"], d["expected"], d["observed"], d["classification"]]
                                    for d in impl["discrepancies"]]), ""]
    if changes:
        L += ["## File scope check (snapshot diff)", "",
              f"- Added: {len(changes['added'])}; modified: {len(changes['modified'])}; deleted: {len(changes['deleted'])}",
              f"- **Out-of-scope changes: {len(changes['violations'])}**",
              *[f"  - `{v['path']}` ({v['change']}): {v['reason']}" for v in changes["violations"]],
              f"- Pre-existing test files modified (needs human attention): {changes['preexisting_modified'] or 'none'}", ""]
    if mut:
        L += ["## Mutation check", "", f"- Totals: {mut['counts']}",
              artifacts._table(["ID", "File:line", "Operator", "Status", "Note"],
                               [[r["id"], f"{r['file']}:{r['line']}", r["operator"], r["status"], r.get("reason", "")]
                                for r in mut["results"]]),
              "", f"- {mut['interpretation']}", f"- {mut['isolation_note']}", ""]
    open_findings = unresolved(rplan) + unresolved(rimpl)
    L += ["## Unresolved review findings (medium and above)", ""]
    L += ([artifacts._table(["ID", "Severity", "Type", "Problem"],
                            [[f["finding_id"], f["severity"], f["type"], f["problem"]] for f in open_findings])]
          if open_findings else ["_None recorded in the latest reviews._"])
    L += ["", "## Tool policy", "",
          f"- Decisions for this run: allow {audit['allow']}, deny {audit['deny']}, ask {audit['ask']}",
          *[f"  - DENY {d['role']} {d['tool']} `{str(d['target'])[:80]}` — {d['reason'][:140]}" for d in audit["denials"]],
          "", "## Limits of this evidence", "",
          "- Hook policy governs tool calls; it is not an OS sandbox. Test code ran with the user's privileges.",
          "- The snapshot diff detects changes inside the project tree only; it does not prevent or revert them.",
          "- Test counts are read from runner output by agents; only exit codes and logs are recorded mechanically.", ""]
    path = adir / "final-report.md"
    write_text_atomic(path, "\n".join(L) + "\n")
    return path, {"status": run["status"], "phase": run["phase"], "command_outcomes": outcomes,
                  "case_mapping": mapping, "scope_violations": len((changes or {}).get("violations", [])),
                  "policy_denials": audit["deny"], "unresolved_findings": len(open_findings),
                  "plan_approved_by_human": plan_ok}
