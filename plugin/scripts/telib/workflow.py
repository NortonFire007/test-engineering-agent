"""Deterministic workflow state machine: phases, guards, loop budgets, spawn gating.

Loop counters live here, in code, not in any model's memory. A model cannot
talk its way past an exhausted budget; it can only ask a human.
"""
from __future__ import annotations

import shutil
import time

from . import artifacts, profile, snapshot, state
from .common import TEError, now_iso, read_json

TRANSITIONS = {
    "setup": {
        "discovery": {"awaiting_profile_approval"},
        "awaiting_profile_approval": {"completed", "discovery"},
    },
    "plan": {
        "discovery": {"strategy_gate"},
        "strategy_gate": {"planning"},
        "planning": {"plan_review", "awaiting_plan_approval"},
        "plan_review": {"planning", "awaiting_plan_approval"},
        "awaiting_plan_approval": {"implementation", "planning", "completed"},
        "implementation": {"impl_review"},
        "impl_review": {"implementation", "mutation", "finalizing"},
        "mutation": {"implementation", "finalizing"},
        "finalizing": {"completed"},
    },
}

# role -> phases in which the orchestrator may spawn it / it may act
ROLE_PHASES = {
    "explorer": {"discovery"},
    "analyst": {"planning"},
    "reviewer": {"plan_review", "impl_review", "mutation"},
    "engineer": {"implementation"},
}


def _valid(root, run, name):
    obj = artifacts.load_artifact(root, run["run_id"], name)
    errs = artifacts.validate(name, obj, root, run)
    if errs:
        raise TEError(f"{name} is not valid:\n  - " + "\n  - ".join(errs))
    return obj


def _archive(root, run, name):
    adir = state.artifacts_dir(root, run["run_id"])
    src = adir / artifacts.ARTIFACT_FILES[name]
    if src.is_file():
        hist = adir / "history"
        hist.mkdir(exist_ok=True)
        shutil.move(str(src), str(hist / f"{src.stem}.visit{run['phase_visit']}{src.suffix}"))


def blocking_questions(plan: dict) -> list:
    return [q["id"] for q in plan.get("open_questions", []) if q.get("blocks_priority") in ("P0", "P1")]


def set_phase(root: str, run_id: str, target: str) -> dict:
    """Validate guards and move the run to `target`. Returns a summary dict."""
    info: dict = {}

    def mutate(run):
        if run["status"] != "active":
            raise TEError(f"run is {run['status']}, not active")
        cur = run["phase"]
        allowed = TRANSITIONS[run["kind"]].get(cur, set())
        if target not in allowed:
            raise TEError(f"illegal transition {cur} -> {target}. Allowed from {cur}: {sorted(allowed) or 'none'}")
        mode, cnt = run["mode_cfg"], run["counters"]
        edge = (cur, target)

        if edge == ("discovery", "awaiting_profile_approval"):
            _valid(root, run, "profile-draft")
        elif edge == ("discovery", "strategy_gate"):
            _valid(root, run, "repo-map")
        elif edge == ("strategy_gate", "planning"):
            if not run.get("strategy"):
                raise TEError("strategy not approved: ask the user, then run `te strategy approve`")
        elif cur == "planning" and target in ("plan_review", "awaiting_plan_approval"):
            plan = _valid(root, run, "test-plan")
            if target == "plan_review" and not mode["plan_review"]:
                raise TEError(f"mode '{run['mode']}' has no plan review; go to awaiting_plan_approval")
            if target == "awaiting_plan_approval" and mode["plan_review"] and not run.get("human_plan_revision"):
                raise TEError("this mode requires an independent plan review first (plan_review)")
            if plan["version"] <= run.get("last_reviewed_plan_version", 0):
                raise TEError("plan was sent back for revision but its 'version' was not incremented")
            if target == "plan_review":
                _archive(root, run, "review-plan")
                run["last_reviewed_plan_version"] = plan["version"]
        elif edge == ("plan_review", "planning"):
            _valid(root, run, "review-plan")
            if cnt["plan_revisions"] >= mode["plan_revision_cycles"]:
                raise TEError(f"plan revision budget exhausted ({cnt['plan_revisions']}/{mode['plan_revision_cycles']}). "
                              "Do not loop: move to awaiting_plan_approval and present unresolved findings to the human.")
            cnt["plan_revisions"] += 1
        elif edge == ("plan_review", "awaiting_plan_approval"):
            _valid(root, run, "review-plan")
        elif edge == ("awaiting_plan_approval", "planning"):
            run["human_plan_revision"] = True  # human-requested change; bounded by the human, not by a counter
            run["plan_approval"] = None
        elif edge == ("awaiting_plan_approval", "implementation"):
            plan = _valid(root, run, "test-plan")
            appr = run.get("plan_approval") or {}
            if appr.get("hash") != artifacts.plan_hash(plan):
                raise TEError("the current test-plan.json is not the human-approved version (`te plan approve`)")
            if not appr.get("implementation_authorized"):
                raise TEError("implementation was not explicitly authorized (`te plan approve --authorize-implementation`)")
            prof = profile.require_approved(root)
            if not run.get("snapshot_taken"):
                info["snapshot_files"] = snapshot.take(root, state.run_state_dir(root, run_id), prof)
                run["snapshot_taken"] = True
        elif edge == ("implementation", "impl_review"):
            _valid(root, run, "impl-report")
            prof = profile.require_approved(root)
            diff = snapshot.diff(root, state.run_state_dir(root, run_id), prof, run.get("approved_extra_paths", []))
            snapshot.write_evidence(root, run_id, diff)
            info["diff"] = {k: diff[k] for k in ("added", "modified", "deleted", "violations", "preexisting_modified")}
            if diff["violations"]:
                run["status"] = "blocked"
                run["blocked_reason"] = {"type": "scope_violation", "paths": diff["violations"]}
                run["history"].append({"at": now_iso(), "event": "blocked", "detail": f"scope violation: {diff['violations']}"})
                info["blocked"] = True
                return
            _archive(root, run, "review-impl")
        elif cur in ("impl_review", "mutation") and target == "implementation":
            if cur == "impl_review":
                _valid(root, run, "review-impl")
            if cnt["impl_corrections"] >= mode["impl_correction_cycles"]:
                raise TEError(f"implementation correction budget exhausted ({cnt['impl_corrections']}/"
                              f"{mode['impl_correction_cycles']}). Do not loop: go to finalizing and report residual findings.")
            cnt["impl_corrections"] += 1
            _archive(root, run, "impl-report")
        elif edge == ("impl_review", "mutation"):
            _valid(root, run, "review-impl")
            if not mode["mutation"]:
                raise TEError(f"mode '{run['mode']}' does not include mutation checks")
        elif edge == ("impl_review", "finalizing"):
            _valid(root, run, "review-impl")
        if target == "completed":
            run["status"] = "completed"

        run["phase"] = target
        run["phase_visit"] += 1
        run["visit_started_epoch"] = time.time()
        run["spawns"] = {}
        run["history"].append({"at": now_iso(), "event": "phase", "detail": f"{cur} -> {target}"})

    state.update_run(root, run_id, mutate)
    run = state.load_run(root, run_id)
    if run["status"] in ("completed", "aborted"):
        state.set_active_run(root, None)
    return {"run_id": run_id, "phase": run["phase"], "status": run["status"], **info}


def gate_spawn(root: str, run: dict, role: str) -> str | None:
    """Count a specialist spawn. Returns a denial reason, or None when allowed."""
    reason: list = []

    def mutate(r):
        if r["phase"] not in ROLE_PHASES[role]:
            reason.append(f"{role} may only be spawned in phase(s) {sorted(ROLE_PHASES[role])}; current phase is "
                          f"'{r['phase']}'. Advance the workflow with `te phase <name>` when its guard is satisfied.")
            return
        base = r["limits"]["explorer_spawns"] if role == "explorer" else 1
        used = r["spawns"].get(role, 0)
        if used >= base:
            if r["counters"]["transient_retries_used"] >= r["limits"]["transient_retries"]:
                reason.append(f"spawn budget for {role} in phase '{r['phase']}' is exhausted and no transient-retry "
                              "budget remains. Stop and report to the human; do not retry.")
                return
            r["counters"]["transient_retries_used"] += 1
        r["spawns"][role] = used + 1
        r["spawn_total"][role] = r["spawn_total"].get(role, 0) + 1

    state.update_run(root, run["run_id"], mutate)
    return reason[0] if reason else None


def engineer_model(root: str, run: dict, cfg: dict) -> str:
    """Cheap model first; escalate by correction cycle and finding severity."""
    n = run["counters"]["impl_corrections"]
    if n == 0:
        return cfg["models"]["engineer"]
    if n >= 2:
        return cfg["models"]["engineer_escalated"]
    try:
        hist = state.artifacts_dir(root, run["run_id"])
        review = read_json(hist / "review-impl.json")
        severe = any(f.get("severity") in ("critical", "high") for f in review.get("findings", []))
    except (OSError, ValueError):
        severe = False
    return cfg["models"]["engineer_escalated"] if severe else cfg["models"]["engineer"]
