"""`te` command line: the only way the workflow changes state, runs commands,
records approvals or produces evidence. Output is compact JSON for agents."""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
import time
from pathlib import Path

from . import artifacts, detect, mutation, paths, profile, runner, snapshot, state, workflow
from .common import (MIN_PYTHON, POLICY_VERSION, TE_DIR, TEError, cli_script, now_iso, plugin_root, read_json,
                     sha256_file, write_json_atomic, write_text_atomic)


def out(obj) -> None:
    sys.stdout.write(json.dumps(obj, indent=1, ensure_ascii=False) + "\n")


def cli_hint() -> str:
    return f'uv run --no-project "{cli_script().as_posix()}"'


def _root(args) -> str:
    start = args.project or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return state.find_project_root(start)


def _run(args, root, statuses=("active",)) -> dict:
    rid = getattr(args, "run", None) or state.active_run_id(root)
    if not rid:
        raise TEError("no active run. Start one with `run init` or pass --run <id>.")
    run = state.load_run(root, state.check_run_id(rid))
    if not run:
        raise TEError(f"unknown run '{rid}'")
    if statuses and run["status"] not in statuses:
        raise TEError(f"run {rid} is {run['status']}; expected {list(statuses)}")
    return run


def _record_approval(root, run_id, kind, detail) -> None:
    def m(r):
        r["approvals"].append({"type": kind, "detail": detail, "at": now_iso(), "recorded_by": "human via permission prompt"})
        r["history"].append({"at": now_iso(), "event": f"approval:{kind}", "detail": json.dumps(detail)[:300]})
    state.update_run(root, run_id, m)


# ---- commands ----------------------------------------------------------------

def cmd_doctor(args):
    root = _root(args)
    checks = {
        "python": {"ok": sys.version_info >= MIN_PYTHON, "value": platform.python_version()},
        "uv": {"ok": shutil.which("uv") is not None, "value": shutil.which("uv")},
        "git": {"ok": True, "value": shutil.which("git") or "not found (optional)"},
        "state_dir": {"ok": os.access(state.state_root(), os.W_OK), "value": str(state.state_root())},
        "plugin_root": {"ok": cli_script().is_file(), "value": str(plugin_root())},
    }
    native_windows = os.name == "nt"
    st = profile.trust_status(root)
    out({
        "ok": all(c["ok"] for c in checks.values()), "checks": checks, "platform": platform.platform(),
        "policy_version": POLICY_VERSION, "project_root": root, "profile_trust": st["state"],
        "profile_detail": st["detail"], "active_run": state.active_run_id(root), "cli": cli_hint(),
        "isolation": {
            "hook_enforcement": "per-call PreToolUse policy (tool allowlists, path ACL, command form, phase gating)",
            "os_sandbox": ("NOT AVAILABLE on native Windows: commands and test code run unsandboxed with your privileges"
                           if native_windows else
                           "not provided by this plugin; enable the Claude Code sandbox or run inside a container"),
            "detective_controls": "file-tree snapshot diff after implementation and around mutation runs",
        },
    })
    return 0 if all(c["ok"] for c in checks.values()) else 1


def cmd_detect(args):
    out(detect.detect(_root(args)))


def cmd_config(args):
    out(state.load_config(_root(args)))


def cmd_run_init(args):
    root = _root(args)
    cfg = state.load_config(root)
    mode = args.mode or cfg["default_mode"]
    if mode not in cfg["modes"]:
        raise TEError(f"unknown mode '{mode}'; choose from {sorted(cfg['modes'])}")
    cur = state.active_run(root)
    if cur:
        raise TEError(f"run {cur['run_id']} is already current ({cur['status']}, phase {cur['phase']}). "
                      "Continue it, or end it with `run abort --reason ...`.")
    warnings = []
    if args.kind == "plan":
        prof = profile.require_approved(root)
        drift = profile.fingerprint_drift(root, prof)
        if (drift["changed"] or drift["missing"]) and not args.accept_stale:
            raise TEError("the project profile is STALE: fingerprinted configuration changed "
                          f"(changed={drift['changed']}, missing={drift['missing']}). Refresh it with the test-setup skill, "
                          "or re-run with --accept-stale if the human confirms the changes are irrelevant to testing.")
        if drift["changed"] or drift["missing"]:
            warnings.append({"stale_profile_accepted": drift})
    run = state.new_run(root, args.kind, mode, args.title, cfg["limits"], cfg["modes"][mode], state.pending_session(root))
    adir = state.artifacts_dir(root, run["run_id"])
    if args.kind == "setup":
        write_json_atomic(adir / "detect.json", detect.detect(root))
    gi = Path(root) / TE_DIR / ".gitignore"
    if not gi.exists():
        write_text_atomic(gi, "runs/\nprofile.proposed.json\nconventions.proposed.md\n")
    out({"run_id": run["run_id"], "kind": args.kind, "mode": mode, "phase": run["phase"],
         "artifacts_dir": adir.as_posix(), "session_bound": bool(run["session_id"]), "models": cfg["models"],
         "mode_config": cfg["modes"][mode], "limits": cfg["limits"], "cli": cli_hint(), "warnings": warnings})


def cmd_run_status(args):
    root = _root(args)
    run = _run(args, root, statuses=())
    cfg = state.load_config(root)
    adir = state.artifacts_dir(root, run["run_id"])
    plan_h = None
    try:
        plan_h = artifacts.plan_hash(read_json(adir / "test-plan.json"))
    except (OSError, ValueError):
        pass
    st = profile.trust_status(root)
    out({
        "run_id": run["run_id"], "kind": run["kind"], "mode": run["mode"], "status": run["status"],
        "phase": run["phase"], "next_phases": sorted(workflow.TRANSITIONS[run["kind"]].get(run["phase"], [])),
        "counters": run["counters"], "mode_config": run["mode_cfg"], "limits": run["limits"],
        "spawns_this_phase": run["spawns"], "spawn_total": run["spawn_total"],
        "strategy": run.get("strategy"), "plan_hash": plan_h, "plan_approval": run.get("plan_approval"),
        "approved_extra_paths": run.get("approved_extra_paths"), "blocked_reason": run.get("blocked_reason"),
        "profile_trust": st["state"], "artifacts_dir": adir.as_posix(),
        "models": {**cfg["models"], "engineer_next_spawn": workflow.engineer_model(root, run, cfg)},
        "artifacts_present": sorted(p.name for p in adir.glob("*") if p.is_file()), "cli": cli_hint(),
    })


def cmd_run_list(args):
    root = _root(args)
    d = state.project_dir(root) / "runs"
    rows = []
    for p in sorted(d.glob("*/state.json")):
        r = read_json(p)
        rows.append({k: r.get(k) for k in ("run_id", "kind", "mode", "status", "phase", "title", "updated_at")})
    out({"active": state.active_run_id(root), "runs": rows[-20:]})


def cmd_run_resume(args):
    root = _root(args)
    cur = state.active_run(root)
    if cur and cur["run_id"] != args.run_id:
        raise TEError(f"run {cur['run_id']} is current; abort it first")
    run = state.load_run(root, state.check_run_id(args.run_id))
    if not run or run["status"] not in ("active", "blocked"):
        raise TEError("run not found or already finished")
    sess = state.pending_session(root)

    def m(r):
        r["session_id"] = sess
        r["history"].append({"at": now_iso(), "event": "resumed", "detail": ""})
    state.update_run(root, args.run_id, m)
    state.set_active_run(root, args.run_id)
    out({"run_id": args.run_id, "status": run["status"], "phase": run["phase"], "session_bound": bool(sess)})


def cmd_run_abort(args):
    root = _root(args)
    run = _run(args, root, statuses=("active", "blocked"))

    def m(r):
        r["status"] = "aborted"
        r["history"].append({"at": now_iso(), "event": "aborted", "detail": args.reason})
    state.update_run(root, run["run_id"], m)
    state.set_active_run(root, None)
    state.audit(root, {"event": "run_aborted", "run_id": run["run_id"], "reason": args.reason})
    out({"run_id": run["run_id"], "status": "aborted", "note": "artifacts preserved; orchestrator lock released"})


def cmd_run_unblock(args):
    root = _root(args)
    run = _run(args, root, statuses=("blocked",))

    def m(r):
        r["status"] = "active"
        r["history"].append({"at": now_iso(), "event": "unblocked", "detail": args.reason})
        r.pop("blocked_reason", None)
    state.update_run(root, run["run_id"], m)
    _record_approval(root, run["run_id"], "unblock", {"reason": args.reason})
    out({"run_id": run["run_id"], "status": "active", "phase": run["phase"]})


def cmd_phase(args):
    root = _root(args)
    run = _run(args, root)
    res = workflow.set_phase(root, run["run_id"], args.target)
    out(res)
    return 3 if res.get("blocked") else 0


def cmd_strategy_approve(args):
    root = _root(args)
    run = _run(args, root)
    if run["phase"] != "strategy_gate":
        raise TEError("strategy can only be approved in phase strategy_gate")
    cfg = state.load_config(root)
    mode = args.mode or run["mode"]
    if mode not in cfg["modes"]:
        raise TEError(f"unknown mode '{mode}'")
    levels = [x.strip() for x in args.levels.split(",") if x.strip()]
    bad = [x for x in levels if x not in artifacts.LEVEL]
    if bad or not levels:
        raise TEError(f"invalid levels {bad}; choose from {artifacts.LEVEL}")
    cap = cfg["modes"][mode].get("levels")
    if cap and [x for x in levels if x not in cap]:
        raise TEError(f"mode '{mode}' is limited to levels {cap}")

    def m(r):
        r["mode"], r["mode_cfg"] = mode, cfg["modes"][mode]
        r["strategy"] = {"levels": levels, "mode": mode, "note": args.note or "", "at": now_iso()}
        r["approvals"].append({"type": "strategy", "detail": r["strategy"], "at": now_iso(),
                               "recorded_by": "orchestrator on the user's answer"})
    state.update_run(root, run["run_id"], m)
    out({"run_id": run["run_id"], "mode": mode, "approved_levels": levels})


def cmd_validate(args):
    root = _root(args)
    run = _run(args, root, statuses=())
    obj = artifacts.load_artifact(root, run["run_id"], args.artifact)
    errs = artifacts.validate(args.artifact, obj, root, run)
    out({"artifact": args.artifact, "valid": not errs, "errors": errs})
    return 1 if errs else 0


def cmd_render_plan(args):
    root = _root(args)
    run = _run(args, root, statuses=())
    plan = artifacts.load_artifact(root, run["run_id"], "test-plan")
    review = None
    try:
        review = artifacts.load_artifact(root, run["run_id"], "review-plan")
    except TEError:
        pass
    dest = state.artifacts_dir(root, run["run_id"]) / "test-plan.md"
    write_text_atomic(dest, artifacts.render_plan(plan, run, review))
    by_level, by_prio = {}, {}
    for t in plan["test_cases"]:
        by_level[t["test_level"]] = by_level.get(t["test_level"], 0) + 1
        by_prio[t["priority"]] = by_prio.get(t["priority"], 0) + 1
    out({"path": dest.as_posix(), "plan_hash": artifacts.plan_hash(plan), "cases_by_level": by_level,
         "cases_by_priority": by_prio, "open_questions": len(plan["open_questions"]),
         "blocking_open_questions": workflow.blocking_questions(plan),
         "review_verdict": (review or {}).get("verdict")})


def cmd_plan_approve(args):
    root = _root(args)
    run = _run(args, root)
    if run["phase"] != "awaiting_plan_approval":
        raise TEError("a plan can only be approved in phase awaiting_plan_approval")
    plan = artifacts.load_artifact(root, run["run_id"], "test-plan")
    errs = artifacts.validate("test-plan", plan, root, run)
    if errs:
        raise TEError("plan is not valid: " + "; ".join(errs[:5]))
    blocking = workflow.blocking_questions(plan)
    if blocking and not args.accept_open_questions:
        raise TEError(f"open questions {blocking} block P0/P1 behaviour. Get the human's answers into the plan, or pass "
                      "--accept-open-questions if the human explicitly accepts them as residual risk.")
    h = artifacts.plan_hash(plan)
    appr = {"hash": h, "version": plan["version"], "implementation_authorized": bool(args.authorize_implementation),
            "accepted_open_questions": blocking if args.accept_open_questions else [], "at": now_iso()}

    def m(r):
        r["plan_approval"] = appr
    state.update_run(root, run["run_id"], m)
    _record_approval(root, run["run_id"], "plan", appr)
    out({"run_id": run["run_id"], "approved_plan_hash": h, "implementation_authorized": appr["implementation_authorized"]})


def cmd_scope_approve(args):
    root = _root(args)
    run = _run(args, root, statuses=("active", "blocked"))
    rel = args.path.replace("\\", "/")
    err = profile._rel_error(rel, allow_glob=False)
    if err:
        raise TEError(f"path {err}")
    real = paths.resolve(rel, root)
    if not paths.is_within(real, root):
        raise TEError("path escapes the project")
    rel = paths.rel_posix(real, root)
    if paths.match_any(rel, paths.CONTROL_GLOBS, ci=True) or paths.secret_reason(rel):
        raise TEError("control and secret locations can never be approved for writing")

    def m(r):
        if rel not in r["approved_extra_paths"]:
            r["approved_extra_paths"].append(rel)
    state.update_run(root, run["run_id"], m)
    _record_approval(root, run["run_id"], "scope", {"path": rel, "reason": args.reason})
    out({"run_id": run["run_id"], "approved_exact_path": rel, "note": "exact path only; valid for this run only"})


def cmd_exec(args):
    root = _root(args)
    run = _run(args, root)
    rec = runner.exec_recorded(root, run["run_id"], args.command_id, args.extra or [], args.rerun_of)
    log = state.artifacts_dir(root, run["run_id"]) / rec["log"]
    out({k: rec.get(k) for k in ("result_id", "command_id", "outcome", "exit_code", "timed_out", "launch_error",
                                 "duration_sec", "flaky", "rerun_of", "log")})
    sys.stdout.write("--- output tail ---\n" + runner.tail(log, 6000) + "\n")
    return 0


def _proposed(root):
    return Path(root) / TE_DIR / "profile.proposed.json", Path(root) / TE_DIR / "conventions.proposed.md"


def cmd_profile_propose(args):
    root = _root(args)
    run = _run(args, root)
    draft = artifacts.load_artifact(root, run["run_id"], "profile-draft")
    errs = artifacts.validate("profile-draft", draft, root, run)
    if errs:
        raise TEError("profile-draft is not valid: " + "; ".join(errs[:8]))
    prof = profile.build_from_draft(root, draft)
    perrs, warns = profile.validate(prof, root)
    if perrs:
        raise TEError("proposed profile failed validation:\n  - " + "\n  - ".join(perrs))
    pp, cp = _proposed(root)
    write_text_atomic(pp, profile.dumps(prof))
    write_text_atomic(cp, draft["conventions_md"].rstrip() + "\n")
    cur = profile.profile_path(root)
    cur_text = cur.read_text(encoding="utf-8") if cur.is_file() else ""
    out({
        "proposed": pp.as_posix(), "conventions_proposed": cp.as_posix(), "warnings": warns,
        "is_first_profile": not cur.is_file(), "current_profile_manually_edited": profile.manually_edited(root),
        "security_hash": profile.security_hash(prof),
        "commands": [{"id": c["id"], "argv": c["argv"], "cwd": c.get("cwd", "."), "verified": c["verified"]}
                     for c in prof["commands"]],
        "allowed_write_paths": prof["allowed_write_paths"], "excluded_paths": prof["excluded_paths"],
        "secret_files": prof["secret_files"],
        "note": "NOT applied. Show the human the diff, then run `profile approve` (prompts the human).",
    })
    if cur_text:
        sys.stdout.write("--- diff current -> proposed ---\n" +
                         profile.text_diff(cur_text, profile.dumps(prof), "profile.json", "profile.proposed.json"))


def cmd_profile_diff(args):
    root = _root(args)
    pp, cp = _proposed(root)
    cur = profile.profile_path(root)
    if not pp.is_file():
        raise TEError("no proposed profile")
    sys.stdout.write(profile.text_diff(cur.read_text(encoding="utf-8") if cur.is_file() else "",
                                       pp.read_text(encoding="utf-8"), "profile.json", "profile.proposed.json") or "(no differences)\n")
    conv = profile.conventions_path(root)
    if cp.is_file() and conv.is_file():
        sys.stdout.write(profile.text_diff(conv.read_text(encoding="utf-8"), cp.read_text(encoding="utf-8"),
                                           "conventions.md", "conventions.proposed.md"))


def cmd_profile_approve(args):
    root = _root(args)
    pp, cp = _proposed(root)
    cur = profile.profile_path(root)
    source = "proposed" if pp.is_file() else "current"
    target = pp if pp.is_file() else cur
    if not target.is_file():
        raise TEError("nothing to approve: no profile.json and no proposed profile")
    try:
        prof = read_json(target)
    except ValueError as e:
        raise TEError(f"{target.name} is not valid JSON: {e}")
    errs, warns = profile.validate(prof, root)
    if errs:
        raise TEError("profile failed validation and cannot be approved:\n  - " + "\n  - ".join(errs))
    trust = state.load_trust(root) or {}
    conv = profile.conventions_path(root)
    conv_note = "unchanged"
    if source == "proposed":
        os.replace(pp, cur)
        if cp.is_file():
            edited = conv.is_file() and trust.get("last_generated_conventions_hash") not in (None, sha256_file(conv))
            unknown_origin = conv.is_file() and trust.get("last_generated_conventions_hash") is None
            if (edited or unknown_origin) and not args.replace_conventions:
                conv_note = ("conventions.md has manual edits: kept as is; the new text is in conventions.proposed.md "
                             "(merge by hand, or re-run with --replace-conventions)")
            else:
                os.replace(cp, conv)
                trust["last_generated_conventions_hash"] = sha256_file(conv)
                conv_note = "written"
    h = profile.security_hash(prof)
    trust.update({"approved_profile_hash": h, "policy_version": POLICY_VERSION, "project_root": paths.real(root),
                  "approved_at": now_iso(), "last_generated_file_hash": sha256_file(cur)})
    state.save_trust(root, trust)
    state.audit(root, {"event": "profile_approved", "hash": h, "source": source})
    rid = state.active_run_id(root)
    if rid:
        _record_approval(root, rid, "profile", {"hash": h, "source": source})
    out({"approved_profile_hash": h, "source": source, "profile_version": prof["profile_version"],
         "conventions": conv_note, "warnings": warns,
         "unverified_commands": [c["id"] for c in prof["commands"] if not c.get("verified")]})


def cmd_profile_check(args):
    root = _root(args)
    st = profile.trust_status(root)
    res = {"state": st["state"], "detail": st["detail"], "warnings": st.get("warnings", [])}
    prof = st.get("profile")
    if prof:
        res.update({
            "profile_version": prof["profile_version"], "generated_at": prof.get("generated_at"),
            "last_verified_at": prof.get("last_verified_at"), "fingerprint_drift": profile.fingerprint_drift(root, prof),
            "manually_edited_since_generated": profile.manually_edited(root),
            "verified_commands": [c["id"] for c in prof["commands"] if c.get("verified")],
            "unverified_commands": [c["id"] for c in prof["commands"] if not c.get("verified")],
            "stacks": [{"id": s["id"], "language": s["language"], "root": s.get("root", ".")} for s in prof["stacks"]],
        })
        known = set(prof.get("fingerprints") or {})
        res["new_config_files"] = [f for f in detect.detect(root)["fingerprint_files"] if f not in known]
    out(res)
    return 0 if st["state"] == "approved" else 1


def cmd_profile_verify(args):
    root = _root(args)
    prof = profile.require_approved(root)
    cmd = profile.get_command(prof, args.command_id)
    logdir = state.project_dir(root) / "verify-logs"
    log = logdir / f"{args.command_id}.log"
    res = runner.execute(root, cmd, prof, [], log)
    oc = runner.outcome(res)
    accepted = oc == "passed" or (oc == "failed" and args.accept_exit_code is not None
                                  and res["exit_code"] == args.accept_exit_code)
    cmd["last_verify_exit_code"] = res["exit_code"]
    if accepted:
        cmd["verified"], cmd["verified_at"] = True, now_iso()
        prof["last_verified_at"] = now_iso()
    else:
        cmd["verified"] = False
    edited_before = profile.manually_edited(root)
    write_text_atomic(profile.profile_path(root), profile.dumps(prof))
    if not edited_before:
        profile.record_generated(root)
    else:
        trust = state.load_trust(root) or {}
        trust["last_generated_file_hash"] = sha256_file(profile.profile_path(root))
        state.save_trust(root, trust)
    out({"command_id": args.command_id, "outcome": oc, "exit_code": res["exit_code"], "verified": accepted,
         "duration_sec": res["duration_sec"], "launch_error": res["launch_error"],
         "note": ("verified" if accepted else
                  "NOT verified. If the non-zero exit is caused by pre-existing failing tests and the human confirms the "
                  f"command itself is correct, re-run with --accept-exit-code {res['exit_code']}.")})
    sys.stdout.write("--- output tail ---\n" + runner.tail(log, 5000) + "\n")
    return 0


def cmd_snapshot_diff(args):
    root = _root(args)
    run = _run(args, root, statuses=("active", "blocked"))
    prof = profile.require_approved(root)
    d = snapshot.diff(root, state.run_state_dir(root, run["run_id"]), prof, run.get("approved_extra_paths", []))
    snapshot.write_evidence(root, run["run_id"], d)
    out({k: d[k] for k in ("added", "modified", "deleted", "violations", "preexisting_modified")})
    return 3 if d["violations"] else 0


def cmd_mutate(args):
    root = _root(args)
    run = _run(args, root)
    if run["phase"] != "mutation":
        raise TEError("mutation checks run only in phase 'mutation' (deep mode)")
    review = artifacts.load_artifact(root, run["run_id"], "review-impl")
    spec = review.get("mutation_spec") or []
    rep = mutation.run(root, run["run_id"], spec, run["limits"]["max_mutations"])
    out({"counts": rep["counts"], "copy_deleted": rep["copy_deleted"],
         "original_tree_changed_during_mutation": rep["original_tree_changed_during_mutation"],
         "results": [{k: r.get(k) for k in ("id", "file", "line", "operator", "status", "reason", "test_case_refs")}
                     for r in rep["results"]],
         "report": (state.artifacts_dir(root, run["run_id"]) / "mutation-report.json").as_posix(),
         "interpretation": rep["interpretation"]})


def cmd_report(args):
    root = _root(args)
    run = _run(args, root, statuses=())
    from . import report
    path, summary = report.build(root, run)
    out({"path": path.as_posix(), **summary})


def cmd_audit_tail(args):
    root = _root(args)
    p = state.project_dir(root) / "audit.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines()[-args.n:] if p.is_file() else []
    sys.stdout.write("\n".join(lines) + "\n")


def build_parser():
    p = argparse.ArgumentParser(prog="te", description="test-engineering workflow CLI")
    p.add_argument("--project", help="project root (default: nearest ancestor with .test-engineering, else cwd)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(parent, name, fn, **kw):
        sp = parent.add_parser(name, **kw)
        sp.set_defaults(fn=fn)
        return sp

    add(sub, "doctor", cmd_doctor)
    add(sub, "detect", cmd_detect)
    add(sub, "config", cmd_config)

    run = sub.add_parser("run").add_subparsers(dest="sub", required=True)
    s = add(run, "init", cmd_run_init)
    s.add_argument("--kind", choices=["setup", "plan"], required=True)
    s.add_argument("--mode")
    s.add_argument("--title", required=True)
    s.add_argument("--accept-stale", action="store_true")
    add(run, "status", cmd_run_status).add_argument("--run")
    add(run, "list", cmd_run_list)
    add(run, "resume", cmd_run_resume).add_argument("run_id")
    s = add(run, "abort", cmd_run_abort)
    s.add_argument("--run")
    s.add_argument("--reason", required=True)
    s = add(run, "unblock", cmd_run_unblock)
    s.add_argument("--run")
    s.add_argument("--reason", required=True)

    s = add(sub, "phase", cmd_phase)
    s.add_argument("target")
    s.add_argument("--run")

    strat = sub.add_parser("strategy").add_subparsers(dest="sub", required=True)
    s = add(strat, "approve", cmd_strategy_approve)
    s.add_argument("--levels", required=True)
    s.add_argument("--mode")
    s.add_argument("--note")
    s.add_argument("--run")

    s = add(sub, "validate", cmd_validate)
    s.add_argument("artifact", choices=sorted(artifacts.ARTIFACT_FILES))
    s.add_argument("--run")

    rend = sub.add_parser("render").add_subparsers(dest="sub", required=True)
    add(rend, "plan", cmd_render_plan).add_argument("--run")

    plan = sub.add_parser("plan").add_subparsers(dest="sub", required=True)
    s = add(plan, "approve", cmd_plan_approve)
    s.add_argument("--run")
    s.add_argument("--authorize-implementation", action="store_true")
    s.add_argument("--accept-open-questions", action="store_true")

    scope = sub.add_parser("scope").add_subparsers(dest="sub", required=True)
    s = add(scope, "approve", cmd_scope_approve)
    s.add_argument("--run")
    s.add_argument("--path", required=True)
    s.add_argument("--reason", required=True)

    s = add(sub, "exec", cmd_exec)
    s.add_argument("--run")
    s.add_argument("--rerun-of", type=int)
    s.add_argument("command_id")
    s.add_argument("extra", nargs=argparse.REMAINDER)

    prof = sub.add_parser("profile").add_subparsers(dest="sub", required=True)
    add(prof, "propose", cmd_profile_propose).add_argument("--run")
    add(prof, "diff", cmd_profile_diff)
    add(prof, "approve", cmd_profile_approve).add_argument("--replace-conventions", action="store_true")
    add(prof, "check", cmd_profile_check)
    s = add(prof, "verify", cmd_profile_verify)
    s.add_argument("command_id")
    s.add_argument("--accept-exit-code", type=int)

    snap = sub.add_parser("snapshot").add_subparsers(dest="sub", required=True)
    add(snap, "diff", cmd_snapshot_diff).add_argument("--run")

    add(sub, "mutate", cmd_mutate).add_argument("--run")
    add(sub, "report", cmd_report).add_argument("--run")
    aud = sub.add_parser("audit").add_subparsers(dest="sub", required=True)
    add(aud, "tail", cmd_audit_tail).add_argument("-n", type=int, default=30)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "extra", None) and args.extra and args.extra[0] == "--":
        args.extra = args.extra[1:]
    try:
        return int(args.fn(args) or 0)
    except TEError as e:
        sys.stdout.write(json.dumps({"error": str(e)}, ensure_ascii=False) + "\n")
        return 2
