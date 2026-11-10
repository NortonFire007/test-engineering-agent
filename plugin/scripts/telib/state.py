"""External (out-of-repository) state: trust records, run state, audit log.

Everything here lives under the user's home directory so that repository
content and repository-scoped agents cannot alter approvals or counters.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from . import paths
from .common import (PLUGIN_NAME, POLICY_VERSION, TE_DIR, TEError, file_lock, now_iso, plugin_root, read_json,
                     sha256_text, write_json_atomic)

RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,60}$")


def state_root() -> Path:
    override = os.environ.get("TE_STATE_DIR")
    root = Path(override) if override else Path.home() / ".claude" / PLUGIN_NAME
    root.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(root, 0o700)
        except OSError:
            pass
    return root


def find_project_root(start: str) -> str:
    """Nearest ancestor containing .test-engineering, else `start` itself."""
    cur = paths.real(start)
    probe = cur
    while True:
        if os.path.isdir(os.path.join(probe, TE_DIR)):
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            return cur
        probe = parent


def project_id(root: str) -> str:
    return sha256_text(os.path.normcase(paths.real(root)))[:16]


def project_dir(root: str, create: bool = True) -> Path:
    d = state_root() / "projects" / project_id(root)
    if create and not d.exists():
        d.mkdir(parents=True, exist_ok=True)
        write_json_atomic(d / "project.json", {"project_root": paths.real(root), "created_at": now_iso()})
    return d


def project_known(root: str) -> bool:
    return (state_root() / "projects" / project_id(root)).is_dir()


# --- trust -----------------------------------------------------------------

def load_trust(root: str) -> dict | None:
    p = project_dir(root, create=False) / "trust.json"
    try:
        return read_json(p)
    except (OSError, ValueError):
        return None


def save_trust(root: str, trust: dict) -> None:
    write_json_atomic(project_dir(root) / "trust.json", trust)


# --- configuration ---------------------------------------------------------

def load_config(root: str) -> dict:
    cfg = read_json(plugin_root() / "config" / "defaults.json")
    proj = Path(root) / TE_DIR / "config.json"
    if proj.is_file():
        try:
            user = read_json(proj)
        except (OSError, ValueError) as e:
            raise TEError(f"invalid {proj}: {e}")
        # Project config may tune cost, never safety budgets or permissions.
        if isinstance(user.get("models"), dict):
            for k, v in user["models"].items():
                if k in cfg["models"] and v in ("haiku", "sonnet", "opus", "inherit"):
                    cfg["models"][k] = v
        if user.get("default_mode") in cfg["modes"]:
            cfg["default_mode"] = user["default_mode"]
    return cfg


# --- runs ------------------------------------------------------------------

def check_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
        raise TEError(f"invalid run id: {run_id!r}")
    return run_id


def artifacts_dir(root: str, run_id: str) -> Path:
    return Path(root) / TE_DIR / "runs" / check_run_id(run_id)


def run_state_dir(root: str, run_id: str) -> Path:
    return project_dir(root) / "runs" / check_run_id(run_id)


def _lock(root: str):
    return file_lock(project_dir(root) / "state.lock")


def load_run(root: str, run_id: str) -> dict | None:
    try:
        return read_json(run_state_dir(root, run_id) / "state.json")
    except (OSError, ValueError):
        return None


def save_run(root: str, run: dict) -> None:
    write_json_atomic(run_state_dir(root, run["run_id"]) / "state.json", run)


def update_run(root: str, run_id: str, fn):
    """Atomically mutate run state. `fn(run)` may raise TEError to reject."""
    with _lock(root):
        run = load_run(root, run_id)
        if run is None:
            raise TEError(f"unknown run: {run_id}")
        result = fn(run)
        run["updated_at"] = now_iso()
        run["updated_epoch"] = time.time()
        save_run(root, run)
        return result


def active_run_id(root: str) -> str | None:
    if not project_known(root):
        return None
    try:
        return read_json(project_dir(root, create=False) / "active_run.json").get("run_id")
    except (OSError, ValueError):
        return None


def set_active_run(root: str, run_id: str | None) -> None:
    write_json_atomic(project_dir(root) / "active_run.json", {"run_id": run_id, "at": now_iso()})


def active_run(root: str) -> dict | None:
    rid = active_run_id(root)
    if not rid:
        return None
    run = load_run(root, rid)
    # A blocked run stays "current": it keeps the orchestrator lock until a human decides.
    if run and run.get("status") in ("active", "blocked"):
        return run
    return None


def new_run(root: str, kind: str, mode: str, title: str, limits: dict, mode_cfg: dict, session_id: str | None) -> dict:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:24] or kind
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + slug
    run = {
        "run_id": run_id, "kind": kind, "mode": mode, "title": title,
        "project_root": paths.real(root), "session_id": session_id,
        "policy_version": POLICY_VERSION, "created_at": now_iso(), "updated_at": now_iso(),
        "updated_epoch": time.time(), "status": "active",
        "phase": "discovery", "phase_visit": 1, "visit_started_epoch": time.time(),
        "limits": limits, "mode_cfg": mode_cfg,
        "counters": {"plan_revisions": 0, "impl_corrections": 0, "transient_retries_used": 0,
                     "exec_calls": 0, "flaky_reruns": 0},
        "spawns": {}, "spawn_total": {}, "repairs": {},
        "strategy": None, "plan_approval": None, "approved_extra_paths": [],
        "approvals": [], "history": [{"at": now_iso(), "event": "run_created", "detail": title}],
    }
    with _lock(root):
        save_run(root, run)
        set_active_run(root, run_id)
    artifacts_dir(root, run_id).mkdir(parents=True, exist_ok=True)
    return run


# --- session binding -------------------------------------------------------

def note_session(root: str, session_id: str | None) -> None:
    if session_id:
        write_json_atomic(project_dir(root) / "pending_session.json", {"session_id": session_id, "epoch": time.time()})


def pending_session(root: str, max_age: float = 300.0) -> str | None:
    try:
        d = read_json(project_dir(root, create=False) / "pending_session.json")
    except (OSError, ValueError):
        return None
    if time.time() - d.get("epoch", 0) <= max_age:
        return d.get("session_id")
    return None


# --- audit -----------------------------------------------------------------

def audit(root: str, record: dict) -> None:
    """Append-only decision log. Never raises: auditing must not change a decision."""
    try:
        rec = {"ts": now_iso(), **record}
        line = json.dumps(rec, ensure_ascii=True, separators=(",", ":"))
        with open(project_dir(root) / "audit.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def is_state_path(real: str) -> bool:
    return paths.is_within(real, str(state_root()))
