"""Constrained command runner: approved argv arrays only, never a shell string.

Commands come exclusively from the human-approved profile. Extra arguments
(test selectors) are validated against a conservative character set and the
command's flag allowlist, then passed as discrete argv elements.
"""
from __future__ import annotations

import os
import random
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

from . import paths, profile, state
from .common import TEError, now_iso, read_json, write_json_atomic

SAFE_ARG = re.compile(r"^[\w@./:\[\]\-,=+ ]+$")
SECRET_ENV = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|PRIVATE_?KEY|CREDENTIAL|ACCESS_?KEY)", re.I)
DROP_ENV_PREFIXES = ("AWS_", "AZURE_", "GOOGLE_APPLICATION_", "ANTHROPIC_", "OPENAI_", "GH_", "GITHUB_")
EXEC_PHASES = {"implementation", "impl_review", "mutation", "finalizing"}
MAX_EXTRA = 40
TAIL_CHARS = 12000
MAX_LOG = 2_000_000


def build_env(prof: dict, cmd: dict) -> dict:
    """Least-privilege environment: strip secret-looking variables unless the
    approved profile explicitly passes them through."""
    keep = set(prof.get("env_passthrough") or [])
    env = {k: v for k, v in os.environ.items()
           if k in keep or not (SECRET_ENV.search(k) or k.upper().startswith(DROP_ENV_PREFIXES))}
    env.update(cmd.get("env") or {})
    env["TE_RUN"] = "1"
    return env


def validate_extra(cmd: dict, extra: list, root: str) -> list:
    if not extra:
        return []
    if not cmd.get("accepts_selectors"):
        raise TEError(f"command '{cmd['id']}' does not accept extra arguments")
    if len(extra) > MAX_EXTRA:
        raise TEError("too many extra arguments")
    allowed = set(cmd.get("allowed_flags") or [])
    for a in extra:
        if len(a) > 300 or not SAFE_ARG.match(a):
            raise TEError(f"unsafe characters in argument {a!r}; allowed: letters, digits, _ @ . / : [ ] - , = + space")
        if a.startswith("-"):
            if a.split("=", 1)[0] not in allowed:
                raise TEError(f"flag {a!r} is not in this command's allowed_flags {sorted(allowed)}")
            continue
        norm = a.replace("\\", "/")
        head = norm.split("::")[0]
        if ".." in head.split("/"):
            raise TEError(f"'..' is not allowed in selector {a!r}")
        if head.startswith("/") or re.match(r"^[A-Za-z]:", head):
            if not paths.is_within(paths.resolve(head, root), root):
                raise TEError(f"selector {a!r} points outside the project")
    return list(extra)


def resolve_argv(cmd: dict, root: str) -> list:
    argv = list(cmd["argv"])
    exe = argv[0]
    if "/" in exe.replace("\\", "/"):
        real = paths.resolve(exe, root)
        if not paths.is_within(real, root) or not os.path.isfile(real):
            raise TEError(f"permanent: executable '{exe}' not found inside the project")
        argv[0] = real
    else:
        found = shutil.which(exe)
        if not found:
            raise TEError(f"permanent: executable '{exe}' not found on PATH (configuration error; do not retry)")
        argv[0] = found
    return argv


def _kill_tree(proc) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=15)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def run_argv(argv: list, cwd: str, env: dict, timeout: int, log_path: Path) -> dict:
    """Run without a shell. Retries only launch failures (transient), with jittered backoff."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    attempt = 0
    while True:
        started = time.monotonic()
        try:
            with open(log_path, "wb") as log:
                kw = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                      else {"start_new_session": True})
                proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                        stderr=subprocess.STDOUT, shell=False, **kw)
                try:
                    code, timed_out = proc.wait(timeout=timeout), False
                except subprocess.TimeoutExpired:
                    _kill_tree(proc)
                    try:
                        proc.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        pass
                    code, timed_out = None, True
            break
        except (FileNotFoundError, PermissionError, NotADirectoryError) as e:
            return {"exit_code": None, "timed_out": False, "launch_error": f"permanent: {e}", "duration_sec": 0.0,
                    "launch_attempts": attempt + 1}
        except OSError as e:
            if attempt >= 2:
                return {"exit_code": None, "timed_out": False, "launch_error": f"transient, retries exhausted: {e}",
                        "duration_sec": 0.0, "launch_attempts": attempt + 1}
            time.sleep(random.uniform(0, min(10.0, 1.0 * 2 ** attempt)))
            attempt += 1
    size = log_path.stat().st_size
    if size > MAX_LOG:
        with open(log_path, "rb") as f:
            head = f.read(200_000)
            f.seek(size - (MAX_LOG - 200_000))
            rest = f.read()
        with open(log_path, "wb") as f:
            f.write(head + b"\n...[log truncated]...\n" + rest)
    return {"exit_code": code, "timed_out": timed_out, "launch_error": None,
            "duration_sec": round(time.monotonic() - started, 2), "launch_attempts": attempt + 1}


def tail(log_path, chars: int = TAIL_CHARS) -> str:
    try:
        data = Path(log_path).read_bytes()[-chars * 2:]
        return data.decode("utf-8", errors="replace")[-chars:]
    except OSError:
        return ""


def execute(root: str, cmd: dict, prof: dict, extra: list, log_path: Path, cwd_root: str | None = None) -> dict:
    """Run one approved command. `cwd_root` remaps the working dir into a disposable copy."""
    argv = resolve_argv(cmd, root) + validate_extra(cmd, extra, root)
    base = cwd_root or root
    cwd = paths.real(os.path.join(base, cmd.get("cwd", ".")))
    if not paths.is_within(cwd, base) or not os.path.isdir(cwd):
        raise TEError(f"permanent: working directory '{cmd.get('cwd')}' is invalid")
    res = run_argv(argv, cwd, build_env(prof, cmd), int(cmd.get("timeout_sec", 600)), log_path)
    res.update({"argv": list(cmd["argv"]) + list(extra), "cwd": cmd.get("cwd", ".")})
    return res


def outcome(res: dict) -> str:
    if res["launch_error"]:
        return "launch_failed"
    if res["timed_out"]:
        return "timed_out"
    return "passed" if res["exit_code"] == 0 else "failed"


def exec_recorded(root: str, run_id: str, command_id: str, extra: list, rerun_of: int | None) -> dict:
    """`te exec`: run an approved, verified command inside a run and record evidence."""
    prof = profile.require_approved(root)
    cmd = profile.get_command(prof, command_id)
    if not cmd.get("verified"):
        raise TEError(f"command '{command_id}' is UNVERIFIED in the profile. The orchestrator must verify it first "
                      f"(`te profile verify {command_id}`); specialists may not run unverified commands.")
    validate_extra(cmd, extra, root)
    box = {}

    def reserve(run):
        if run["status"] != "active" or run["phase"] not in EXEC_PHASES:
            raise TEError(f"`te exec` is only available in phases {sorted(EXEC_PHASES)} of an active run "
                          f"(now: {run['status']}/{run['phase']})")
        c = run["counters"]
        if c["exec_calls"] >= run["limits"]["exec_calls"]:
            raise TEError(f"command execution budget exhausted ({c['exec_calls']}). Stop and report.")
        if rerun_of is not None:
            used = run.setdefault("reruns", {}).get(str(rerun_of), 0)
            if used >= run["limits"]["flaky_reruns"]:
                raise TEError(f"flaky-diagnosis rerun budget for result {rerun_of} is exhausted. "
                              "Report the test as flaky/failing; do not rerun until green.")
            run["reruns"][str(rerun_of)] = used + 1
            c["flaky_reruns"] += 1
        c["exec_calls"] += 1
        box["id"] = c["exec_calls"]

    state.update_run(root, run_id, reserve)
    rid = box["id"]
    rdir = state.artifacts_dir(root, run_id) / "results"
    res = execute(root, cmd, prof, extra, rdir / f"{rid:03d}.log")
    record = {"result_id": rid, "command_id": command_id, "extra": extra, "started_at": now_iso(),
              "rerun_of": rerun_of, "log": f"results/{rid:03d}.log", **res}
    record["outcome"] = outcome(res)
    if rerun_of is not None:
        try:
            orig_path = rdir / f"{rerun_of:03d}.json"
            orig = read_json(orig_path)
            if orig.get("outcome") != record["outcome"]:
                record["flaky"] = orig["flaky"] = True  # inconsistent outcomes: flaky, never "passed"
                write_json_atomic(orig_path, orig)
        except (OSError, ValueError):
            pass
    write_json_atomic(rdir / f"{rid:03d}.json", record)
    return record
