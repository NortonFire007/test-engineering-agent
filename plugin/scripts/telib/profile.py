"""Project testing profile: schema validation, canonical hashing, trust, fingerprints.

`.test-engineering/profile.json` is the ONLY file that defines machine-level
execution permissions (commands, writable paths). It is repository content and
therefore untrusted until a human approves its exact security hash; the approval
record lives outside the repository (see state.py).
"""
from __future__ import annotations

import copy
import difflib
import json
import os
import re
import shutil
from pathlib import Path

from . import paths, state
from .common import POLICY_VERSION, TE_DIR, TEError, canonical_json, now_iso, read_json, sha256_file, sha256_text

SCHEMA_VERSION = 1
COMMAND_KINDS = {"test", "lint", "typecheck", "format_check"}
LEVELS = {"unit", "integration", "api", "contract", "end_to_end", "performance", "security"}
VOLATILE_TOP = {"generated_at", "last_verified_at"}
VOLATILE_CMD = {"verified", "verified_at", "last_verify_exit_code"}

TOP_KEYS = {
    "schema_version", "profile_version", "generated_at", "last_verified_at", "project", "stacks", "commands",
    "test_layout", "allowed_write_paths", "excluded_paths", "secret_paths", "secret_files", "snapshot_ignore",
    "fingerprints", "mutation", "env_passthrough", "notes",
}

FORBIDDEN_EXECUTABLES = {
    "sh", "bash", "zsh", "fish", "dash", "ksh", "csh", "cmd", "powershell", "pwsh", "wsl", "env", "sudo", "doas",
    "su", "xargs", "nohup", "eval", "exec", "ssh", "scp", "curl", "wget", "nc", "ncat", "rm", "del", "rmdir", "mv",
    "cp", "dd", "git", "gh", "docker", "podman", "kubectl", "helm", "terraform", "aws", "az", "gcloud", "start",
}
FORBIDDEN_SUBCOMMANDS = {
    "install", "i", "ci", "add", "remove", "rm", "uninstall", "publish", "deploy", "push", "upgrade", "update",
    "login", "logout", "sync", "lock", "init", "link", "dlx", "self", "tool", "pip", "global", "release", "clean",
}
INLINE_CODE_FLAGS = {"-c", "-e", "--eval", "--command", "--exec", "-Command", "-EncodedCommand"}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
FLAG_RE = re.compile(r"^--?[A-Za-z][\w-]*$")
ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def profile_path(root: str) -> Path:
    return Path(root) / TE_DIR / "profile.json"


def conventions_path(root: str) -> Path:
    return Path(root) / TE_DIR / "conventions.md"


def _rel_error(p, allow_glob: bool = True) -> str | None:
    if not isinstance(p, str) or not p:
        return "must be a non-empty string"
    if "\\" in p:
        return "use forward slashes"
    if p.startswith("/") or re.match(r"^[A-Za-z]:", p):
        return "must be relative to the project root"
    if any(part in ("..", ".", "") for part in p.split("/")):
        return "must not contain '..', '.' or empty path segments"
    if not allow_glob and any(c in p for c in "*?"):
        return "wildcards not allowed here"
    probe = p.replace("*", "x").replace("?", "x")
    return paths.suspicious_reason(probe)


def _str_list(obj, key, errs, where, required=False):
    v = obj.get(key)
    if v is None:
        if required:
            errs.append(f"{where}.{key}: required")
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
        errs.append(f"{where}.{key}: must be a list of non-empty strings")
        return []
    return v


def validate_command(cmd, root: str, errs: list, warns: list, where: str) -> None:
    if not isinstance(cmd, dict):
        errs.append(f"{where}: must be an object")
        return
    allowed = {"id", "kind", "stack", "argv", "cwd", "accepts_selectors", "allowed_flags", "timeout_sec",
               "env", "level", "requires", "description"} | VOLATILE_CMD
    for k in cmd:
        if k not in allowed:
            errs.append(f"{where}: unknown key '{k}'")
    cid = cmd.get("id")
    if not isinstance(cid, str) or not ID_RE.match(cid):
        errs.append(f"{where}.id: must match {ID_RE.pattern}")
    if cmd.get("kind") not in COMMAND_KINDS:
        errs.append(f"{where}.kind: must be one of {sorted(COMMAND_KINDS)}")
    argv = cmd.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
        errs.append(f"{where}.argv: must be a non-empty array of non-empty strings (no shell string)")
        argv = []
    for a in argv:
        if any(c in a for c in "\x00\n\r"):
            errs.append(f"{where}.argv: control character in argument")
        if a in INLINE_CODE_FLAGS:
            errs.append(f"{where}.argv: inline-code flag '{a}' is not allowed")
        if re.search(r"[;&|`$<>]", a):
            errs.append(f"{where}.argv: shell metacharacter in argument {a!r} (commands run without a shell)")
    if argv:
        exe = argv[0].replace("\\", "/")
        base = re.sub(r"\.(exe|cmd|bat|ps1|sh)$", "", exe.split("/")[-1].lower())
        if base in FORBIDDEN_EXECUTABLES:
            errs.append(f"{where}.argv[0]: executable '{base}' is not allowed as a test command")
        if "/" in exe:
            e = _rel_error(exe, allow_glob=False)
            if e:
                errs.append(f"{where}.argv[0]: path executable {e}")
            elif not paths.is_within(paths.resolve(exe, root), root):
                errs.append(f"{where}.argv[0]: executable escapes the project")
        elif shutil.which(argv[0]) is None:
            warns.append(f"{where}.argv[0]: '{argv[0]}' not found on PATH on this machine")
        for tok in argv[1:3]:
            if tok.lower() in FORBIDDEN_SUBCOMMANDS:
                errs.append(f"{where}.argv: subcommand '{tok}' (install/publish/mutating) is not allowed")
        if base in ("npx", "bunx") and "--no-install" not in argv and "--no" not in argv:
            warns.append(f"{where}: '{base}' without --no-install may download packages")
    cwd = cmd.get("cwd", ".")
    e = _rel_error(cwd, allow_glob=False) if cwd != "." else None
    if e:
        errs.append(f"{where}.cwd: {e}")
    else:
        real = paths.resolve(cwd, root)
        if not paths.is_within(real, root):
            errs.append(f"{where}.cwd: escapes the project")
        elif not os.path.isdir(real):
            errs.append(f"{where}.cwd: directory does not exist")
    if not isinstance(cmd.get("accepts_selectors", False), bool):
        errs.append(f"{where}.accepts_selectors: must be boolean")
    for f in _str_list(cmd, "allowed_flags", errs, where):
        if not FLAG_RE.match(f) or f in INLINE_CODE_FLAGS:
            errs.append(f"{where}.allowed_flags: invalid or forbidden flag {f!r}")
    t = cmd.get("timeout_sec", 600)
    if not isinstance(t, int) or isinstance(t, bool) or not 1 <= t <= 3600:
        errs.append(f"{where}.timeout_sec: integer 1..3600")
    env = cmd.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(k, str) and ENV_NAME_RE.match(k) and isinstance(v, str)
                                            for k, v in env.items()):
        errs.append(f"{where}.env: must map UPPER_CASE names to strings")
    if "level" in cmd and cmd["level"] not in LEVELS:
        errs.append(f"{where}.level: must be one of {sorted(LEVELS)}")


def validate(profile, root: str) -> tuple[list, list]:
    """Return (errors, warnings). Validation is independent of who produced the profile."""
    errs: list = []
    warns: list = []
    if not isinstance(profile, dict):
        return ["profile: must be a JSON object"], warns
    for k in profile:
        if k not in TOP_KEYS:
            errs.append(f"profile: unknown top-level key '{k}'")
    if profile.get("schema_version") != SCHEMA_VERSION:
        errs.append(f"schema_version: must be {SCHEMA_VERSION}")
    pv = profile.get("profile_version")
    if not isinstance(pv, int) or isinstance(pv, bool) or pv < 1:
        errs.append("profile_version: integer >= 1")
    if not isinstance(profile.get("generated_at"), str):
        errs.append("generated_at: required ISO timestamp string")
    if profile.get("last_verified_at") is not None and not isinstance(profile["last_verified_at"], str):
        errs.append("last_verified_at: string or null")
    proj = profile.get("project")
    if not isinstance(proj, dict) or not isinstance(proj.get("name"), str):
        errs.append("project.name: required")

    stack_ids = set()
    stacks = profile.get("stacks")
    if not isinstance(stacks, list) or not stacks:
        errs.append("stacks: non-empty array required")
        stacks = []
    for i, s in enumerate(stacks):
        w = f"stacks[{i}]"
        if not isinstance(s, dict):
            errs.append(f"{w}: must be an object")
            continue
        if not isinstance(s.get("id"), str) or not ID_RE.match(s.get("id", "")):
            errs.append(f"{w}.id: must match {ID_RE.pattern}")
        else:
            stack_ids.add(s["id"])
        if not isinstance(s.get("language"), str) or not s.get("language"):
            errs.append(f"{w}.language: required")
        r = s.get("root", ".")
        if r != "." and _rel_error(r, allow_glob=False):
            errs.append(f"{w}.root: {_rel_error(r, allow_glob=False)}")
        _str_list(s, "frameworks", errs, w)
        tfs = s.get("test_frameworks", [])
        if not isinstance(tfs, list) or not all(isinstance(t, dict) and isinstance(t.get("name"), str) for t in tfs):
            errs.append(f"{w}.test_frameworks: array of objects with 'name'")

    ids = set()
    cmds = profile.get("commands")
    if not isinstance(cmds, list):
        errs.append("commands: array required")
        cmds = []
    for i, c in enumerate(cmds):
        validate_command(c, root, errs, warns, f"commands[{i}]")
        if isinstance(c, dict):
            if c.get("id") in ids:
                errs.append(f"commands[{i}].id: duplicate '{c.get('id')}'")
            ids.add(c.get("id"))
            if c.get("stack") is not None and c.get("stack") not in stack_ids:
                errs.append(f"commands[{i}].stack: unknown stack '{c.get('stack')}'")

    layout = profile.get("test_layout", [])
    if not isinstance(layout, list):
        errs.append("test_layout: array")
        layout = []
    for i, t in enumerate(layout):
        w = f"test_layout[{i}]"
        if not isinstance(t, dict) or t.get("level") not in LEVELS:
            errs.append(f"{w}.level: one of {sorted(LEVELS)}")
            continue
        for p in _str_list(t, "paths", errs, w, required=True):
            if _rel_error(p):
                errs.append(f"{w}.paths: {p!r} {_rel_error(p)}")

    for key in ("allowed_write_paths", "excluded_paths", "secret_paths", "secret_files", "snapshot_ignore"):
        for p in _str_list(profile, key, errs, "profile", required=(key == "allowed_write_paths")):
            e = _rel_error(p)
            if e:
                errs.append(f"{key}: {p!r} {e}")
    for p in profile.get("allowed_write_paths") or []:
        if not isinstance(p, str) or _rel_error(p):
            continue
        probe = next((x for x in paths.BROAD_PROBES if paths.glob_match(x, p, ci=True)), None)
        if probe:
            errs.append(f"allowed_write_paths: {p!r} is too broad (it would allow writing '{probe}')")
        sample = p.replace("**", "d").replace("*", "f").replace("?", "c")
        if paths.match_any(sample, paths.CONTROL_GLOBS, ci=True) or paths.secret_reason(sample):
            errs.append(f"allowed_write_paths: {p!r} targets a control or secret location")

    fps = profile.get("fingerprints", {})
    if not isinstance(fps, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in fps.items()):
        errs.append("fingerprints: object mapping relative path -> sha256")
    else:
        for k in fps:
            if _rel_error(k, allow_glob=False):
                errs.append(f"fingerprints: {k!r} {_rel_error(k, allow_glob=False)}")
    mut = profile.get("mutation", {})
    if not isinstance(mut, dict):
        errs.append("mutation: object")
    else:
        for key in ("copy_excludes", "link_dirs"):
            for p in _str_list(mut, key, errs, "mutation"):
                if _rel_error(p, allow_glob=False):
                    errs.append(f"mutation.{key}: {p!r} {_rel_error(p, allow_glob=False)}")
    for n in _str_list(profile, "env_passthrough", errs, "profile"):
        if not ENV_NAME_RE.match(n):
            errs.append(f"env_passthrough: invalid variable name {n!r}")
    if not any(isinstance(c, dict) and c.get("kind") == "test" for c in cmds):
        warns.append("commands: no test command defined; implementation workflow will be unavailable")
    if not profile.get("allowed_write_paths"):
        warns.append("allowed_write_paths is empty; the engineer cannot write any test file")
    return errs, warns


def security_hash(profile: dict) -> str:
    """SHA-256 over canonical JSON of the profile minus verification metadata,
    bound to the plugin policy version."""
    p = copy.deepcopy(profile)
    for k in VOLATILE_TOP:
        p.pop(k, None)
    for c in p.get("commands", []) or []:
        if isinstance(c, dict):
            for k in VOLATILE_CMD:
                c.pop(k, None)
    return sha256_text(canonical_json({"policy_version": POLICY_VERSION, "profile": p}))


def load(root: str) -> dict:
    return read_json(profile_path(root))


def trust_status(root: str) -> dict:
    """Evaluate whether the on-disk profile is the human-approved one."""
    pp = profile_path(root)
    if not pp.is_file():
        return {"state": "missing", "detail": "no .test-engineering/profile.json; run the test-setup skill"}
    try:
        prof = load(root)
    except (OSError, ValueError) as e:
        return {"state": "invalid", "detail": f"profile.json is not valid JSON: {e}"}
    errs, warns = validate(prof, root)
    if errs:
        return {"state": "invalid", "detail": "; ".join(errs[:5]), "errors": errs}
    h = security_hash(prof)
    trust = state.load_trust(root)
    base = {"hash": h, "warnings": warns, "profile": prof}
    if not trust or not trust.get("approved_profile_hash"):
        return {**base, "state": "unapproved", "detail": "profile has never been approved on this machine"}
    if trust.get("policy_version") != POLICY_VERSION:
        return {**base, "state": "policy_changed", "detail": "plugin policy version changed; re-approve the profile"}
    if os.path.normcase(trust.get("project_root", "")) != os.path.normcase(paths.real(root)):
        return {**base, "state": "unapproved", "detail": "approval belongs to a different project path"}
    if trust["approved_profile_hash"] != h:
        return {**base, "state": "modified", "detail": "profile.json differs from the approved version"}
    return {**base, "state": "approved", "detail": "ok"}


def require_approved(root: str) -> dict:
    st = trust_status(root)
    if st["state"] != "approved":
        raise TEError(f"profile not trusted ({st['state']}): {st['detail']}. "
                      "Review it and run: te profile approve")
    return st["profile"]


def get_command(profile: dict, command_id: str) -> dict:
    for c in profile.get("commands", []):
        if c.get("id") == command_id:
            return c
    raise TEError(f"unknown command id '{command_id}'. Known: {[c.get('id') for c in profile.get('commands', [])]}")


# --- fingerprints ----------------------------------------------------------

def compute_fingerprints(root: str, rels) -> dict:
    out = {}
    for rel in sorted(set(rels)):
        if _rel_error(rel, allow_glob=False):
            continue
        real = paths.resolve(rel, root)
        if paths.is_within(real, root) and os.path.isfile(real) and not paths.secret_reason(real):
            out[rel] = sha256_file(real)
    return out


def fingerprint_drift(root: str, profile: dict) -> dict:
    changed, missing = [], []
    for rel, digest in (profile.get("fingerprints") or {}).items():
        real = paths.resolve(rel, root)
        if not os.path.isfile(real) or not paths.is_within(real, root):
            missing.append(rel)
        elif sha256_file(real) != digest:
            changed.append(rel)
    return {"changed": changed, "missing": missing}


def manually_edited(root: str) -> bool:
    """True when profile.json bytes differ from what the plugin last wrote."""
    trust = state.load_trust(root) or {}
    last = trust.get("last_generated_file_hash")
    pp = profile_path(root)
    return bool(pp.is_file() and last and sha256_file(pp) != last)


def text_diff(old_text: str, new_text: str, old_name: str, new_name: str) -> str:
    return "".join(difflib.unified_diff(old_text.splitlines(True), new_text.splitlines(True), old_name, new_name))


def dumps(profile: dict) -> str:
    return json.dumps(profile, indent=2, ensure_ascii=False) + "\n"


def record_generated(root: str, also_approve_hash: str | None = None) -> None:
    """Remember the exact bytes the plugin wrote (to detect later manual edits)."""
    trust = state.load_trust(root) or {}
    trust["last_generated_file_hash"] = sha256_file(profile_path(root))
    cp = conventions_path(root)
    if cp.is_file():
        trust.setdefault("last_generated_conventions_hash", sha256_file(cp))
    if also_approve_hash:
        trust.update({"approved_profile_hash": also_approve_hash, "policy_version": POLICY_VERSION,
                      "project_root": paths.real(root), "approved_at": now_iso()})
    state.save_trust(root, trust)


def write_denial(rel: str, prof: dict, extra_paths=()) -> str | None:
    """Why `rel` (posix, project-relative) is NOT writable by the engineer; None when it is."""
    if paths.match_any(rel, paths.CONTROL_GLOBS, ci=True):
        return "workflow/tooling control location"
    sec = paths.secret_reason(rel, rel, prof.get("secret_paths") or [])
    if sec:
        return sec
    if rel in (extra_paths or ()):
        return None  # exact path approved by a human for this run
    hit = paths.match_any(rel, prof.get("excluded_paths") or [], ci=True)
    if hit:
        return f"excluded path '{hit}'"
    hit = paths.match_any(rel, paths.PROTECTED_WRITE_GLOBS, ci=True)
    if hit:
        return f"protected project file '{hit}' (dependencies, build, CI, migrations, config)"
    if not paths.match_any(rel, prof.get("allowed_write_paths") or [], ci=paths.IS_WINDOWS):
        return "outside the approved test paths of the project profile"
    return None


DEFAULT_COPY_EXCLUDES = [".venv", "venv", "node_modules", "target", "dist", "build", ".next", "coverage"]


def build_from_draft(root: str, draft: dict) -> dict:
    """Turn an explorer draft into a complete profile. Security-relevant derived
    fields (fingerprints, secret file list, verification state) are computed
    here, never taken from the model."""
    from . import snapshot  # local import: snapshot imports this module

    src = draft.get("profile")
    if not isinstance(src, dict):
        raise TEError("profile-draft.profile must be an object")
    prof = {k: copy.deepcopy(v) for k, v in src.items()}
    old = None
    if profile_path(root).is_file():
        try:
            old = load(root)
        except (OSError, ValueError):
            old = None
    prof["schema_version"] = SCHEMA_VERSION
    prof["profile_version"] = (old.get("profile_version", 0) + 1) if isinstance(old, dict) else 1
    prof["generated_at"] = now_iso()
    prof["last_verified_at"] = (old or {}).get("last_verified_at")
    prof["fingerprints"] = compute_fingerprints(root, draft.get("fingerprint_files") or [])
    prof["secret_files"] = sorted(rel for rel, _ in snapshot.walk(root) if paths.secret_reason(rel))
    for key in ("excluded_paths", "secret_paths", "snapshot_ignore", "env_passthrough", "test_layout"):
        prof.setdefault(key, [])
    mut = prof.setdefault("mutation", {})
    if isinstance(mut, dict):
        mut.setdefault("copy_excludes", [d for d in DEFAULT_COPY_EXCLUDES if os.path.isdir(os.path.join(root, d))])
        mut.setdefault("link_dirs", [])
    old_cmds = {c.get("id"): c for c in (old or {}).get("commands", []) if isinstance(c, dict)}
    for c in prof.get("commands") or []:
        if not isinstance(c, dict):
            continue
        prev = old_cmds.get(c.get("id"))
        same = bool(prev) and all(prev.get(k) == c.get(k) for k in ("argv", "cwd", "env"))
        # verification is evidence owned by the CLI: a draft can never claim it
        c["verified"] = bool(same and prev.get("verified"))
        for k in ("verified_at", "last_verify_exit_code"):
            c.pop(k, None)
            if same and k in prev:
                c[k] = prev[k]
    return prof
