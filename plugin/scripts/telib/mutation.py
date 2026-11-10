"""Controlled mutation check (deep mode).

The model never edits production code. It only proposes `(file, line, operator)`
triples; this script validates each against a fixed operator catalog and applies
it inside a disposable copy of the project, runs the approved test command there
and deletes the copy.

Honest limits:
* The copy is a convenience boundary, not a sandbox. Tests run with the user's
  OS privileges.
* "killed" means the command exited non-zero. A mutant that fails to compile is
  indistinguishable from an assertion failure here, so compile-time kills are
  not separated out.
* "survived" is a potential coverage gap, not a proven defect. "equivalent" is a
  judgement recorded later by the reviewer, never by this script.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import paths, profile, runner, snapshot, state
from .common import TEError, now_iso, write_json_atomic

PY_LIKE = {".py", ".pyi"}
REL_SWAP = {">=": ">", "<=": "<", ">": ">=", "<": "<="}
EQ_SWAP = {"===": "!==", "!==": "===", "==": "!=", "!=": "=="}
REL_RE = re.compile(r"(?<![<>=\-!+*/|&^%])(<=|>=|<|>)(?![<>=])")
EQ_RE = re.compile(r"(?<![=!<>])(===|!==|==|!=)(?!=)")
INT_RE = re.compile(r"(?<![\w.])\d+(?![\w.])")
BOOL_RE = re.compile(r"\b(True|False|true|false)\b")
OPERAND = r"[\w.\[\]'\"]+(?:\([^()]*\))?"
CANARY = "@@TE_CANARY_SYNTAX_ERROR@@ ((("


def _fully_parenthesized(expr: str) -> bool:
    """True when the first '(' closes at the very last character."""
    if not (expr.startswith("(") and expr.endswith(")")):
        return False
    depth = 0
    for i, ch in enumerate(expr):
        depth += ch == "("
        depth -= ch == ")"
        if depth == 0:
            return i == len(expr) - 1
    return False


def _nth(regex, line, occurrence, repl):
    matches = list(regex.finditer(line))
    if len(matches) < occurrence:
        return None
    m = matches[occurrence - 1]
    new = repl(m)
    return None if new is None else line[:m.start()] + new + line[m.end():]


def _null_patterns(py: bool):
    t, f = ("True", "False") if py else ("true", "false")
    return [
        (re.compile(OPERAND + r"\s+is\s+not\s+None\b"), t),
        (re.compile(OPERAND + r"\s+is\s+None\b"), f),
        (re.compile(OPERAND + r"\s*!==?\s*(?:null|undefined|nil|None|nullptr)\b"), t),
        (re.compile(OPERAND + r"\s*===?\s*(?:null|undefined|nil|None|nullptr)\b"), f),
        (re.compile(OPERAND + r"\.is_none\(\)"), f),
        (re.compile(OPERAND + r"\.is_some\(\)"), t),
    ]


def apply_operator(line: str, operator: str, ext: str, occurrence: int = 1, delta: int = 1) -> str | None:
    """Return the mutated line, or None when the operator does not apply (unviable)."""
    py = ext in PY_LIKE
    body = line.rstrip("\r\n")
    eol = line[len(body):]
    occurrence = max(1, int(occurrence or 1))
    new = None
    if operator == "rel_boundary":
        new = _nth(REL_RE, body, occurrence, lambda m: REL_SWAP[m.group(1)])
    elif operator == "eq_negate":
        new = _nth(EQ_RE, body, occurrence, lambda m: EQ_SWAP[m.group(1)])
    elif operator == "const_shift":
        d = 1 if delta not in (1, -1) else delta
        new = _nth(INT_RE, body, occurrence, lambda m: str(int(m.group(0)) + d))
    elif operator == "bool_flip":
        flip = {"True": "False", "False": "True", "true": "false", "false": "true"}
        new = _nth(BOOL_RE, body, occurrence, lambda m: flip[m.group(1)])
    elif operator == "remove_null_check":
        for rx, lit in _null_patterns(py):
            new = _nth(rx, body, occurrence, lambda m, lit=lit: lit)
            if new is not None:
                break
    elif operator == "negate_condition":
        if py:
            m = re.match(r"^(\s*)(if|elif|while)\s+(.+):(\s*(?:#.*)?)$", body)
            if m:
                new = f"{m.group(1)}{m.group(2)} not ({m.group(3)}):{m.group(4)}"
        else:
            m = re.match(r"^(\s*(?:\}\s*else\s+)?(?:if|while)\s*)(.+?)(\s*\{\s*)$", body)
            if m:
                cond = m.group(2)
                if _fully_parenthesized(cond):  # C-like: if (a > b) {  ->  if (!(a > b)) {
                    new = f"{m.group(1)}(!{cond}){m.group(3)}"
                else:                           # Go/Rust: if a > b {  ->  if !(a > b) {
                    new = f"{m.group(1)}!({cond}){m.group(3)}"
    elif operator == "remove_line":
        indent = re.match(r"^\s*", body).group(0)
        if body.strip() and not body.strip().startswith(("#", "//", "}", "{")):
            new = indent + ("pass" if py else ";")
    else:
        raise TEError(f"unknown mutation operator '{operator}'")
    if new is None or new == body:
        return None
    return new + eol


def _mutable_target(root: str, prof: dict, rel: str) -> str:
    err = profile._rel_error(rel, allow_glob=False)
    if err:
        raise TEError(f"mutation file {rel!r}: {err}")
    real = paths.resolve(rel, root)
    if not paths.is_within(real, root) or not os.path.isfile(real):
        raise TEError(f"mutation file {rel!r} is not a file inside the project")
    r = paths.rel_posix(real, root)
    if paths.secret_reason(r, r, prof.get("secret_paths") or []) or paths.match_any(r, paths.CONTROL_GLOBS, ci=True):
        raise TEError(f"mutation file {rel!r} is a secret/control file")
    if profile.write_denial(r, prof) is None:
        raise TEError(f"mutation file {rel!r} is a test file; mutate production code under test instead")
    return r


def _link_dir(src: str, dst: str) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.name == "nt":
        # mklink parses forward slashes as switches: always pass native separators
        subprocess.run(["cmd", "/c", "mklink", "/J", os.path.normpath(dst), os.path.normpath(src)],
                       capture_output=True, check=True, timeout=30)
    else:
        os.symlink(src, dst, target_is_directory=True)


def _unlink_links(copy_root: str, link_dirs) -> None:
    """Remove links first so deleting the copy can never follow them into the original."""
    for rel in link_dirs:
        p = os.path.join(copy_root, rel)
        try:
            if os.name == "nt":
                os.rmdir(p)  # removes a junction without touching its target
            else:
                os.unlink(p)
        except OSError:
            pass


def make_copy(root: str, prof: dict) -> tuple[str, str]:
    mut = prof.get("mutation") or {}
    link_dirs = list(mut.get("link_dirs") or [])
    excludes = set(mut.get("copy_excludes") or []) | set(link_dirs)
    tmp = tempfile.mkdtemp(prefix="te-mutation-")
    dest = os.path.join(tmp, "project")

    def ignore(dirpath, names):
        rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
        out = []
        for n in names:
            rel = n if rel_dir == "." else f"{rel_dir}/{n}"
            if rel in excludes or n in (".git", "__pycache__", ".pytest_cache") or rel == ".test-engineering/runs":
                out.append(n)
        return out

    shutil.copytree(root, dest, ignore=ignore, symlinks=True)
    for rel in link_dirs:
        src = os.path.join(root, rel)
        if os.path.isdir(src):
            _link_dir(paths.real(src), os.path.join(dest, rel))
    return tmp, dest


def run(root: str, run_id: str, spec: list, max_mutations: int) -> dict:
    prof = profile.require_approved(root)
    if not isinstance(spec, list) or not spec:
        raise TEError("mutation spec is empty")
    if len(spec) > max_mutations:
        raise TEError(f"too many mutations ({len(spec)} > {max_mutations}); keep only P0/P1-relevant ones")
    items = []
    for m in spec:
        cmd = profile.get_command(prof, m["command_id"])
        if cmd.get("kind") != "test" or not cmd.get("verified"):
            raise TEError(f"mutation {m['id']}: command '{m['command_id']}' must be a verified test command")
        runner.validate_extra(cmd, m.get("selectors") or [], root)
        items.append((m, cmd, _mutable_target(root, prof, m["file"])))

    adir = state.artifacts_dir(root, run_id)
    logs = adir / "mutation-logs"
    link_dirs = list((prof.get("mutation") or {}).get("link_dirs") or [])
    before = snapshot.manifest(root)
    tmp, copy_root = make_copy(root, prof)
    results, env_status = [], {}
    try:
        def run_cmd(cmd, selectors, name):
            return runner.execute(root, cmd, prof, selectors, logs / f"{name}.log", cwd_root=copy_root)

        # 1) baseline per distinct (command, selectors): tests must pass on unmodified code
        # 2) canary per distinct file: a guaranteed-broken file must make tests fail,
        #    otherwise the tests are not exercising the copy (e.g. editable install).
        for m, cmd, rel in items:
            key = (m["command_id"], tuple(m.get("selectors") or []), rel)
            if key in env_status:
                continue
            base = run_cmd(cmd, m.get("selectors") or [], f"baseline-{m['id']}")
            if runner.outcome(base) != "passed":
                env_status[key] = f"baseline_{runner.outcome(base)}"
                continue
            target = os.path.join(copy_root, rel)
            original = Path(target).read_bytes()
            try:
                Path(target).write_bytes(CANARY.encode() + b"\n" + original)
                canary = run_cmd(cmd, m.get("selectors") or [], f"canary-{m['id']}")
            finally:
                Path(target).write_bytes(original)
            env_status[key] = "ok" if runner.outcome(canary) != "passed" else "environment_invalid"

        for m, cmd, rel in items:
            key = (m["command_id"], tuple(m.get("selectors") or []), rel)
            row = {"id": m["id"], "file": rel, "line": m["line"], "operator": m["operator"],
                   "test_case_refs": m.get("test_case_refs", []), "command_id": m["command_id"]}
            if env_status[key] != "ok":
                row.update(status="not_run", reason=env_status[key])
                results.append(row)
                continue
            target = os.path.join(copy_root, rel)
            original = Path(target).read_bytes()
            lines = original.decode("utf-8", errors="surrogateescape").splitlines(True)
            idx = m["line"] - 1
            mutated = (apply_operator(lines[idx], m["operator"], os.path.splitext(rel)[1].lower(),
                                      m.get("occurrence", 1), m.get("delta", 1))
                       if 0 <= idx < len(lines) else None)
            if mutated is None:
                row.update(status="unviable", reason="operator does not apply to that line")
                results.append(row)
                continue
            row.update(original_line=lines[idx].strip(), mutated_line=mutated.strip())
            try:
                lines[idx] = mutated
                Path(target).write_bytes("".join(lines).encode("utf-8", errors="surrogateescape"))
                res = run_cmd(cmd, m.get("selectors") or [], f"mutant-{m['id']}")
            finally:
                Path(target).write_bytes(original)
            oc = runner.outcome(res)
            row.update(status={"passed": "survived", "failed": "killed", "timed_out": "killed"}.get(oc, "not_run"),
                       reason={"timed_out": "killed by timeout", "launch_failed": res.get("launch_error")}.get(oc, ""),
                       exit_code=res["exit_code"])
            results.append(row)
    finally:
        _unlink_links(copy_root, link_dirs)
        shutil.rmtree(tmp, ignore_errors=True)
    after = snapshot.manifest(root)
    original_touched = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    report = {
        "run_id": run_id, "generated_at": now_iso(), "counts": counts, "results": results,
        "copy_deleted": not os.path.exists(tmp),
        "original_tree_changed_during_mutation": original_touched,
        "isolation_note": ("Mutations were applied only inside a disposable copy by a script. This is not an OS "
                           "sandbox: tests ran with the user's privileges. The original tree was compared before "
                           "and after as a detective check."),
        "interpretation": ("survived = potential coverage gap, not a proven defect; killed includes compile-time "
                           "failures; equivalent is decided by reviewer triage."),
    }
    write_json_atomic(adir / "mutation-report.json", report)
    return report
