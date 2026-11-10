"""File-tree snapshots: detect (not prevent) changes outside the approved scope.

This is a DETECTIVE control. Test code executes with the user's OS privileges,
so on a host without an OS sandbox it can touch anything the user can. The
snapshot diff makes such changes visible and blocks the run; it cannot undo or
prevent them, and it only observes the project tree.
"""
from __future__ import annotations

import difflib
import fnmatch
import os
import shutil
from pathlib import Path

from . import paths, profile, state
from .common import TE_DIR, read_json, sha256_file, write_json_atomic, write_text_atomic

IGNORE_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "target",
    "dist", "build", ".next", ".turbo", "coverage", ".idea", ".vscode", ".tox", ".gradle", ".nuxt", ".cache",
    "htmlcov", "playwright-report", "test-results", ".nyc_output", ".hypothesis", ".swc", "bin", "obj",
}
# Only inert tool droppings are ignored by name. Compiled or loadable files (e.g. a stray *.pyc next to
# sources) are deliberately tracked: Python imports a sourceless .pyc. Projects that generate other files
# during test runs list them in the approved profile's `snapshot_ignore`.
IGNORE_FILES = [".coverage", ".coverage.*", ".DS_Store", "Thumbs.db", "*.tsbuildinfo"]
BIG_FILE = 20 * 1024 * 1024


def walk(root: str, extra_ignore_dirs=()):
    """Yield (posix_rel, abs_path) for tracked files; never follows links."""
    root = paths.real(root)
    skip = IGNORE_DIRS | set(extra_ignore_dirs)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        keep = []
        for d in dirnames:
            full = os.path.join(dirpath, d)
            rel = f"{rel_dir}/{d}" if rel_dir else d
            if d in skip or rel == f"{TE_DIR}/runs":
                continue
            if os.path.islink(full) or (hasattr(os.path, "isjunction") and os.path.isjunction(full)):
                yield rel, full  # record the link itself, do not descend
                continue
            keep.append(d)
        dirnames[:] = keep
        for f in filenames:
            if any(fnmatch.fnmatchcase(f, g) for g in IGNORE_FILES):
                continue
            yield (f"{rel_dir}/{f}" if rel_dir else f), os.path.join(dirpath, f)


def _digest(path: str) -> str:
    try:
        if os.path.islink(path) or (hasattr(os.path, "isjunction") and os.path.isjunction(path)):
            return "L:" + paths.real(path)
        st = os.stat(path)
        if st.st_size > BIG_FILE:
            return f"S:{st.st_size}:{st.st_mtime_ns}"
        return sha256_file(path)
    except OSError as e:
        return f"E:{type(e).__name__}"


def manifest(root: str) -> dict:
    return {rel: _digest(full) for rel, full in walk(root)}


def take(root: str, run_state_dir: Path, prof: dict) -> int:
    snap = Path(run_state_dir) / "snapshot"
    if snap.exists():
        shutil.rmtree(snap)
    files = snap / "files"
    man = {}
    for rel, full in walk(root):
        man[rel] = _digest(full)
        # keep originals of writable files so reviewers get a real diff without git
        if profile.write_denial(rel, prof) is None and os.path.isfile(full) and os.path.getsize(full) < 2_000_000:
            dest = files / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(full, dest)
    write_json_atomic(snap / "manifest.json", man)
    return len(man)


def diff(root: str, run_state_dir: Path, prof: dict, extra_paths=()) -> dict:
    snap = Path(run_state_dir) / "snapshot"
    old = read_json(snap / "manifest.json")
    new = manifest(root)
    ignore = prof.get("snapshot_ignore") or []

    def ignored(rel):
        return bool(paths.match_any(rel, ignore, ci=True))

    added = sorted(r for r in new if r not in old and not ignored(r))
    deleted = sorted(r for r in old if r not in new and not ignored(r))
    modified = sorted(r for r in new if r in old and new[r] != old[r] and not ignored(r))
    violations = []
    for rel in added + modified:
        why = profile.write_denial(rel, prof, extra_paths)
        if why:
            violations.append({"path": rel, "change": "added" if rel in added else "modified", "reason": why})
    for rel in deleted:
        violations.append({"path": rel, "change": "deleted", "reason": "deleting pre-existing files is never in scope"})
    bad = {v["path"] for v in violations}
    return {
        "added": added, "modified": modified, "deleted": deleted, "violations": violations,
        "preexisting_modified": [r for r in modified if r not in bad],
        "snapshot_dir": str(snap),
    }


def _read_text(p) -> list:
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read().splitlines(True)
    except (OSError, UnicodeDecodeError):
        return []


def write_evidence(root: str, run_id: str, d: dict) -> None:
    """Persist change list and a unified diff for the reviewer (no git needed)."""
    adir = state.artifacts_dir(root, run_id)
    bad = {v["path"] for v in d["violations"]}
    chunks, size = [], 0
    for rel in d["added"] + d["modified"]:
        if rel in bad:
            continue
        old_lines = _read_text(Path(d["snapshot_dir"]) / "files" / rel) if rel in d["modified"] else []
        new_lines = _read_text(os.path.join(root, rel))
        text = "".join(difflib.unified_diff(old_lines, new_lines, f"a/{rel}", f"b/{rel}"))
        size += len(text)
        if size > 600_000:
            chunks.append(f"\n[diff truncated at {rel}; read the files directly]\n")
            break
        chunks.append(text)
    write_text_atomic(adir / "diff.patch", "".join(chunks))
    write_json_atomic(adir / "changes.json", {k: d[k] for k in ("added", "modified", "deleted", "violations",
                                                                 "preexisting_modified")})
