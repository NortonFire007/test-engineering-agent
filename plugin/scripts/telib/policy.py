"""Per-call authorization for every tool call (PreToolUse) and artifact
validation at agent stop (SubagentStop).

Default deny for the plugin's specialists: a call is allowed only when the
role, the workflow phase, the tool, and the canonical target all match policy.
Checked on every call, never cached from session start.

These are Claude Code hook decisions. They constrain which *tool calls* run.
They are not an OS sandbox: code executed by an allowed command (for example
the project's own tests) runs with the user's privileges.
"""
from __future__ import annotations

import fnmatch
import os
import re
import shlex
import shutil
import subprocess
import time

from . import artifacts, paths, profile, state, workflow
from .common import PLUGIN_NAME, ROLES, TE_DIR, TEError, cli_script, plugin_root, read_json, write_json_atomic

READ_TOOLS = {"Read", "Grep", "Glob"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}
SPAWN_TOOLS = {"Agent", "Task"}
# Harness-internal tools a subagent needs in order to finish; they touch no project resource.
INTERNAL_TOOLS = {"SubagentHandback", "TodoWrite", "StructuredOutput"}
ROLE_TOOLS = {
    "explorer": {"Read", "Grep", "Glob"},
    "analyst": {"Read", "Grep", "Glob", "Write"},
    "reviewer": {"Read", "Grep", "Glob", "Write"},
    "engineer": {"Read", "Grep", "Glob", "Write", "Edit", "Bash"},
}
# The one artifact each role may write inside the run directory.
ROLE_ARTIFACTS = {
    "analyst": {"test-plan.json"},
    "reviewer": {"review-plan.json", "review-impl.json"},
    "engineer": {"impl-report.json"},
}
# CLI subcommands that record a human decision: always surfaced as a permission prompt.
ASK_SUBCOMMANDS = {("profile", "approve"), ("plan", "approve"), ("scope", "approve"), ("run", "unblock")}
BYPASS_MODES = {"bypassPermissions", "dontAsk"}
SHELL_META = re.compile(r"[;&|`$<>()\n\r^%!]")


class Decision:
    __slots__ = ("action", "reason")

    def __init__(self, action: str, reason: str = ""):
        self.action, self.reason = action, reason


def allow(reason=""):
    return Decision("allow", reason)


def deny(reason):
    return Decision("deny", reason)


NONE = Decision("none")


def role_of(agent_type) -> str | None:
    if not isinstance(agent_type, str) or not agent_type:
        return None
    name = agent_type.split(":")[-1]
    if ":" in agent_type and not agent_type.startswith(PLUGIN_NAME + ":"):
        return None
    return ROLES.get(name)


def parse_cli(command) -> list | None:
    """Return the te CLI arguments iff `command` is exactly one direct invocation
    of this plugin's CLI with no shell syntax around it; else None."""
    if not isinstance(command, str) or SHELL_META.search(command):
        return None
    try:
        toks = shlex.split(command, posix=True)
    except ValueError:
        return None
    if len(toks) < 5 or toks[0] not in ("uv", "uv.exe") or toks[1:3] != ["run", "--no-project"]:
        return None
    if paths.suspicious_reason(paths.to_native(toks[3])):
        return None
    try:
        same = os.path.normcase(paths.real(paths.to_native(toks[3]))) == os.path.normcase(paths.real(cli_script()))
    except OSError:
        return None
    return toks[4:] if same else None


def _sub(cli: list) -> tuple:
    words = [a for a in cli if not a.startswith("-")]
    return tuple(words[:2])


def _opt(cli: list, name: str) -> str | None:
    for i, a in enumerate(cli):
        if a == name and i + 1 < len(cli):
            return cli[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


def _git_ignored(path: str) -> bool:
    git = shutil.which("git")
    if not git:
        return False
    try:
        r = subprocess.run([git, "-C", os.path.dirname(path), "check-ignore", "-q", "--", path],
                           capture_output=True, timeout=5, stdin=subprocess.DEVNULL)
        return r.returncode == 0
    except Exception:
        return False


def grep_secret_exposure(search_dir: str, root: str, prof: dict | None, glob: str | None) -> str | None:
    """A directory-wide content search returns lines from any file that is not
    git-ignored, including secret files. Return such a file, if one is in range."""
    candidates = set()
    for rel in (prof or {}).get("secret_files") or []:
        full = os.path.join(root, rel)
        if paths.is_within(paths.real(full), search_dir):
            candidates.add(full)
    try:  # cheap live look, two levels deep, for secrets created after the profile
        for name in os.listdir(search_dir):
            full = os.path.join(search_dir, name)
            if paths.is_secret_name(name):
                candidates.add(full)
            elif os.path.isdir(full) and name not in (".git", "node_modules", ".venv"):
                try:
                    for sub in os.listdir(full):
                        if paths.is_secret_name(sub):
                            candidates.add(os.path.join(full, sub))
                except OSError:
                    pass
    except OSError:
        pass
    last = (glob or "").replace("\\", "/").split("/")[-1]
    for full in sorted(candidates):
        if not os.path.isfile(full):
            continue
        base = os.path.basename(full)
        if last and not last.startswith("!") and not fnmatch.fnmatch(base.lower(), last.lower()):
            continue  # the caller's glob filter excludes this file
        if not _git_ignored(full):
            return full
    return None


def check_read(tool: str, ti: dict, cwd: str, root: str, prof: dict | None) -> Decision:
    if tool == "Read":
        target = ti.get("file_path")
    elif tool == "Grep":
        if ti.get("glob") and paths.pattern_targets_secret(str(ti["glob"])):
            return deny("search glob targets secret-like files")
        target = ti.get("path") or cwd
    else:  # Glob
        pat = str(ti.get("pattern") or "")
        norm = paths.to_native(pat).replace("\\", "/")
        if paths.pattern_targets_secret(pat):
            return deny("glob pattern targets secret-like files")
        if ".." in norm.split("/"):
            return deny("'..' is not allowed in glob patterns")
        target = ti.get("path") or cwd
        if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm):
            literal = re.split(r"[*?\[{]", norm, maxsplit=1)[0]
            target = literal.rsplit("/", 1)[0] or "/"
    if not isinstance(target, str) or not target:
        return deny("missing path")
    why = paths.suspicious_reason(paths.to_native(target))
    if why:
        return deny(f"rejected path spelling: {why}")
    real = paths.resolve(target, cwd)
    in_project = paths.is_within(real, root)
    if not in_project and not paths.is_within(real, str(plugin_root() / "templates")):
        return deny("path is outside the authorized project workspace")
    if state.is_state_path(real):
        return deny("workflow state is not readable by specialists")
    rel = paths.rel_posix(real, root) if in_project else None
    if rel and ".git" in rel.split("/"):
        return deny(".git internals are not readable by specialists")
    sec = paths.secret_reason(real, rel, (prof or {}).get("secret_paths") or [])
    if sec:
        return deny(f"secret access is denied: {sec}")
    if tool == "Grep" and os.path.isdir(real) and not ti.get("type"):
        leak = grep_secret_exposure(real, root, prof, ti.get("glob"))
        if leak:
            return deny(f"this search would scan the secret file '{os.path.basename(leak)}' (not git-ignored). "
                        "Narrow it with `glob`/`type` or search a subdirectory that holds no secrets.")
    return allow("read within project scope")


def check_write(role: str, tool: str, ti: dict, cwd: str, root: str, run: dict, prof: dict | None) -> Decision:
    target = ti.get("file_path") or ti.get("notebook_path")
    if not isinstance(target, str) or not target:
        return deny("missing file path")
    why = paths.suspicious_reason(paths.to_native(target))
    if why:
        return deny(f"rejected path spelling: {why}")
    real = paths.resolve(target, cwd)  # symlinks and junctions resolved before authorization
    adir = paths.real(state.artifacts_dir(root, run["run_id"]))
    if paths.is_within(real, adir):
        rel = paths.rel_posix(real, adir)
        if rel in ROLE_ARTIFACTS.get(role, ()):
            return allow("role artifact in run directory")
        return deny(f"{role} may write only {sorted(ROLE_ARTIFACTS.get(role, ()))} in the run directory; "
                    "evidence files are produced by the te CLI")
    if role != "engineer":
        return deny(f"{role} is read-only for project files (run artifacts only)")
    if not paths.is_within(real, root):
        return deny("write target resolves outside the project (path traversal / link escape)")
    if prof is None:
        return deny("no approved project profile")
    rel = paths.rel_posix(real, root)
    reason = profile.write_denial(rel, prof, run.get("approved_extra_paths") or [])
    if reason:
        return deny(f"'{rel}' is not writable: {reason}. If this change is truly required, stop and ask the "
                    "orchestrator for a human scope decision; do not try another route.")
    if os.path.lexists(real):
        try:
            if os.stat(real).st_nlink > 1:
                return deny("target is a hard link to another file")
        except OSError:
            pass
        if tool == "Write":
            try:
                existed = rel in read_json(state.run_state_dir(root, run["run_id"]) / "snapshot" / "manifest.json")
            except (OSError, ValueError):
                existed = True
            if existed:
                return deny("overwriting a pre-existing file with Write is not allowed; use Edit for a targeted change "
                            "(deleting or weakening existing tests needs human approval)")
    return allow("approved test path")


def _lock_applies(run: dict, ev: dict) -> bool:
    if run.get("session_id") not in (None, ev.get("session_id")):
        return False
    max_age = run["limits"].get("run_max_age_hours", 12) * 3600
    return time.time() - run.get("updated_epoch", 0) < max_age


def _trusted_profile(root: str, run: dict):
    if run.get("kind") == "setup":
        try:
            return profile.load(root), None
        except (OSError, ValueError):
            return None, None
    st = profile.trust_status(root)
    if st["state"] != "approved":
        return None, f"project profile is not trusted ({st['state']}): {st['detail']}"
    return st["profile"], None


def decide(ev: dict) -> tuple[Decision, dict]:
    """Return (decision, audit_context)."""
    tool = ev.get("tool_name") or ""
    ti = ev.get("tool_input") if isinstance(ev.get("tool_input"), dict) else {}
    cwd = ev.get("cwd") or os.getcwd()
    agent_type = ev.get("agent_type")
    role = role_of(agent_type)
    root = state.find_project_root(os.environ.get("CLAUDE_PROJECT_DIR") or cwd)
    ctx = {"root": root, "role": role, "log": role is not None}

    if role is None:
        return _decide_orchestrator(ev, tool, ti, cwd, root, ctx), ctx

    # ---- plugin specialist: default deny ----
    if tool in INTERNAL_TOOLS:
        if tool == "SubagentHandback" and role == "explorer":
            msg = capture_explorer(ev, root, ti.get("message"))
            if msg:
                return deny(msg), ctx
        return NONE, ctx
    if tool not in ROLE_TOOLS[role]:
        return deny(f"tool '{tool}' is not in the {role} allowlist {sorted(ROLE_TOOLS[role])}"), ctx
    run = state.active_run(root)
    if not run or run["status"] != "active":
        return deny("no active test-engineering run for this project; specialists act only inside a run"), ctx
    ctx["run_id"], ctx["phase"] = run["run_id"], run["phase"]
    if run["phase"] not in workflow.ROLE_PHASES[role]:
        return deny(f"{role} is not permitted to act in phase '{run['phase']}'"), ctx
    prof, problem = _trusted_profile(root, run)
    if problem:
        return deny(problem + ". All workflows are blocked until a human approves the profile."), ctx
    if tool in READ_TOOLS:
        return check_read(tool, ti, cwd, root, prof), ctx
    if tool in WRITE_TOOLS:
        return check_write(role, tool, ti, cwd, root, run, prof), ctx
    if tool == "Bash":
        cli = parse_cli(ti.get("command"))
        if cli is None:
            return deny("the engineer may run exactly one thing in the shell: the te CLI `exec` subcommand, as a single "
                        "command with no pipes, redirects, variables or chaining"), ctx
        if _sub(cli)[:1] != ("exec",) or _opt(cli, "--run") != run["run_id"]:
            return deny(f"only `exec --run {run['run_id']} <command-id> [-- selectors]` is permitted for the engineer"), ctx
        return allow("approved test-runner invocation"), ctx
    return deny(f"tool '{tool}' is not permitted"), ctx


def _decide_orchestrator(ev, tool, ti, cwd, root, ctx) -> Decision:
    """Main session and any non-plugin subagent."""
    is_subagent = bool(ev.get("agent_type") or ev.get("agent_id"))
    known = state.project_known(root)
    cli = parse_cli(ti.get("command")) if tool == "Bash" else None
    if cli is not None:
        ctx["log"] = True
        if not is_subagent:
            state.note_session(root, ev.get("session_id"))
        if _sub(cli) in ASK_SUBCOMMANDS:
            if is_subagent:
                return deny("human approvals can only be recorded from the main session")
            if ev.get("permission_mode") in BYPASS_MODES:
                return deny("a human approval cannot be recorded while permission prompts are bypassed "
                            f"(mode: {ev.get('permission_mode')}). Switch to a prompting permission mode.")
            return Decision("ask", "test-engineering: this records a HUMAN approval (" + " ".join(_sub(cli)) +
                            "). Approve only if you reviewed what is being approved.")
        return NONE
    if not known:
        return NONE
    run = state.active_run(root)

    if tool in SPAWN_TOOLS:
        role = role_of(ti.get("subagent_type"))
        if role:
            ctx["log"] = True
            if is_subagent:
                return deny("specialists are spawned only by the orchestrator (no nested delegation)")
            if not run or run["status"] != "active":
                return deny("no active test-engineering run; start one with the test-setup or test-plan skill")
            ctx["run_id"], ctx["phase"] = run["run_id"], run["phase"]
            if run.get("kind") != "setup":
                _, problem = _trusted_profile(root, run)
                if problem:
                    return deny(problem)
            problem = workflow.gate_spawn(root, run, role)
            return deny(problem) if problem else NONE
        return NONE

    if not run or not _lock_applies(run, ev):
        return NONE
    ctx["run_id"], ctx["phase"] = run["run_id"], run["phase"]
    # Orchestrator lock: while a run is current, this session coordinates; it does not edit the project.
    if tool in WRITE_TOOLS:
        target = ti.get("file_path") or ti.get("notebook_path")
        if not isinstance(target, str):
            return NONE
        real = paths.resolve(target, cwd)
        if not paths.is_within(real, root):
            return NONE
        ctx["log"] = True
        rel = paths.rel_posix(real, root)
        if rel.startswith(TE_DIR + "/"):
            if rel in (f"{TE_DIR}/profile.json", f"{TE_DIR}/profile.proposed.json"):
                return deny("the profile is changed only through `te profile propose` and a human `te profile approve`")
            if "/results/" in rel or rel.endswith(("/changes.json", "/diff.patch", "/mutation-report.json")):
                return deny("evidence files are produced by the te CLI, not written by hand")
            return NONE
        return deny(f"a test-engineering run ({run['run_id']}, {run['status']}) is current: the orchestrator does not "
                    "write project files. Delegate to the test-engineer, or end the run with `te run abort`.")
    if tool in SHELL_TOOLS:
        ctx["log"] = True
        return deny(f"a test-engineering run ({run['run_id']}, {run['status']}) is current: only te CLI commands may be "
                    "run from the shell (one command, no chaining). End the run with `te run abort` to do other work.")
    return NONE


# ---- artifact validation at stop -------------------------------------------

def expected_artifact(role: str, run: dict) -> str | None:
    if role == "explorer":
        return "profile-draft" if run["kind"] == "setup" else "repo-map"
    if role == "analyst":
        return "test-plan"
    if role == "reviewer":
        return "review-plan" if run["phase"] == "plan_review" else "review-impl"
    if role == "engineer":
        return "impl-report"
    return None


def _fresh(path, run) -> bool:
    try:
        return os.path.getmtime(path) >= run["visit_started_epoch"] - 2
    except OSError:
        return False


def _validate_text(text, name, root, run) -> tuple[list, object]:
    try:
        obj = artifacts.extract_json(text)
    except ValueError as e:
        return [f"final message must be a single JSON object ({e})"], None
    errs = artifacts.validate(name, obj, root, run)
    if not errs and name == "profile-draft":
        try:
            built = profile.build_from_draft(root, obj)
            perrs, _ = profile.validate(built, root)
            errs = [f"profile: {e}" for e in perrs]
        except TEError as e:
            errs = [str(e)]
    return errs, obj


def _repair_allowed(root, run, agent_id) -> bool:
    box = {}

    def bump(r):
        used = r["repairs"].get(agent_id, 0)
        box["ok"] = used < r["limits"]["output_repairs"]
        if box["ok"]:
            r["repairs"][agent_id] = used + 1

    state.update_run(root, run["run_id"], bump)
    return box["ok"]


def _check_artifact(ev, root, text) -> tuple[list, dict | None, str | None]:
    role = role_of(ev.get("agent_type"))
    run = state.active_run(root)
    if not role or not run or run["status"] != "active":
        return [], None, None
    name = expected_artifact(role, run)
    path = state.artifacts_dir(root, run["run_id"]) / artifacts.ARTIFACT_FILES[name]
    if role == "explorer":
        if _fresh(path, run):
            return [], run, name
        errs, obj = _validate_text(text, name, root, run)
        if not errs:
            write_json_atomic(path, obj)  # the explorer has no write tool; the hook persists its map
        return errs, run, name
    if not _fresh(path, run):
        return [f"required artifact {path.name} was not written in this phase (write it to {path})"], run, name
    try:
        obj = read_json(path)
    except ValueError as e:
        return [f"{path.name} is not valid JSON: {e}"], run, name
    return artifacts.validate(name, obj, root, run), run, name


def _repair_message(name, errs) -> str:
    return (f"Your output artifact '{name}' failed validation. Fix exactly these problems and finish again "
            "(this is your single repair attempt):\n- " + "\n- ".join(errs[:25]))


def capture_explorer(ev, root, text) -> str | None:
    """PreToolUse(SubagentHandback) for the explorer: validate + persist, or ask for one repair."""
    errs, run, name = _check_artifact(ev, root, text)
    if errs and run and _repair_allowed(root, run, ev.get("agent_id") or "main"):
        return _repair_message(name, errs)
    return None


def on_subagent_stop(ev: dict) -> dict | None:
    cwd = ev.get("cwd") or os.getcwd()
    root = state.find_project_root(os.environ.get("CLAUDE_PROJECT_DIR") or cwd)
    if not state.project_known(root):
        return None
    errs, run, name = _check_artifact(ev, root, ev.get("last_assistant_message"))
    if not run:
        return None
    aid = ev.get("agent_id") or "main"
    if errs and _repair_allowed(root, run, aid):
        state.audit(root, {"event": "artifact_repair_requested", "agent_type": ev.get("agent_type"), "agent_id": aid,
                           "artifact": name, "errors": errs[:10], "run_id": run["run_id"]})
        return {"decision": "block", "reason": _repair_message(name, errs)}
    state.audit(root, {"event": "artifact_checked", "agent_type": ev.get("agent_type"), "agent_id": aid,
                       "artifact": name, "valid": not errs, "errors": errs[:10], "run_id": run["run_id"]})
    return None
