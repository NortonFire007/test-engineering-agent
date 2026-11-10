"""Shared constants and helpers. Standard library only (security-critical code)."""
from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import os
import time
from pathlib import Path

POLICY_VERSION = "1"
PLUGIN_NAME = "test-engineering"
MIN_PYTHON = (3, 11)
TE_DIR = ".test-engineering"

# agent file name -> short role
ROLES = {
    "test-codebase-explorer": "explorer",
    "test-analyst": "analyst",
    "test-reviewer": "reviewer",
    "test-engineer": "engineer",
}


class TEError(Exception):
    """Expected, user-facing failure (bad input, guard not satisfied)."""


def plugin_root() -> Path:
    return Path(__file__).resolve().parents[2]


def cli_script() -> Path:
    return plugin_root() / "scripts" / "te.py"


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_text_atomic(path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def write_json_atomic(path, obj) -> None:
    write_text_atomic(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


@contextlib.contextmanager
def file_lock(lock_path, timeout: float = 10.0, stale: float = 30.0):
    """Cross-platform advisory lock based on exclusive file creation."""
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > stale:
                    lock_path.unlink(missing_ok=True)
                    continue
            except OSError:
                continue
            if time.monotonic() > deadline:
                raise TEError(f"could not acquire state lock: {lock_path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            lock_path.unlink()
