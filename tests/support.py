"""Shared fixtures for the policy test-suite (standard library unittest)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugin" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from telib import artifacts, policy, profile, state, workflow  # noqa: E402
from telib.common import cli_script, now_iso, write_json_atomic, write_text_atomic  # noqa: E402

PLUGIN = "test-engineering"
AGENT = {r: f"{PLUGIN}:{n}" for n, r in {
    "test-codebase-explorer": "explorer", "test-analyst": "analyst",
    "test-reviewer": "reviewer", "test-engineer": "engineer"}.items()}
SESSION = "sess-1"
PY_TEST = ["uv", "run", "--no-project", "python", "-m", "unittest", "discover", "-s", "tests", "-t", "."]


def base_profile(**over) -> dict:
    p = {
        "schema_version": 1, "profile_version": 1, "generated_at": now_iso(), "last_verified_at": None,
        "project": {"name": "demo"},
        "stacks": [{"id": "py", "language": "python", "root": ".", "frameworks": [],
                    "test_frameworks": [{"name": "unittest"}]}],
        "commands": [{"id": "py-test", "kind": "test", "stack": "py", "argv": list(PY_TEST), "cwd": ".",
                      "accepts_selectors": True, "allowed_flags": ["-k", "-v"], "timeout_sec": 120, "verified": True}],
        "test_layout": [{"stack": "py", "level": "unit", "paths": ["tests/**"]}],
        "allowed_write_paths": ["tests/**"], "excluded_paths": ["tests/golden/**"], "secret_paths": ["config/prod/**"],
        "secret_files": [".env"], "snapshot_ignore": [], "fingerprints": {},
        "mutation": {"copy_excludes": [], "link_dirs": []}, "env_passthrough": [],
    }
    p.update(over)
    return p


class ProjectCase(unittest.TestCase):
    """A disposable project with an approved profile and isolated external state."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="te-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_dir = os.path.join(self.tmp, "state")
        self._old_env = {k: os.environ.get(k) for k in ("TE_STATE_DIR", "CLAUDE_PROJECT_DIR")}
        os.environ["TE_STATE_DIR"] = self.state_dir
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        self.addCleanup(self._restore_env)
        self.root = os.path.realpath(os.path.join(self.tmp, "proj"))
        self.outside = os.path.realpath(os.path.join(self.tmp, "outside"))
        for d in ("src", "tests", "tests/golden", ".test-engineering", "config/prod", ".git"):
            os.makedirs(os.path.join(self.root, d))
        os.makedirs(self.outside)
        self.write("src/app.py", "LIMIT = 10\n\n\ndef accept(n):\n    if n is None:\n        raise ValueError('n')\n    return n <= LIMIT\n")
        self.write("tests/__init__.py", "")
        self.write("tests/test_existing.py", "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n")
        self.write(".env", "API_TOKEN=DUMMY_SECRET_VALUE_123\n")
        self.write(".env.example", "API_TOKEN=\n")
        self.write("pyproject.toml", "[project]\nname='demo'\n")
        self.write(".git/config", "[core]\n")
        self.write("config/prod/settings.yml", "password: DUMMY\n")
        self.write("../outside/secret.txt", "outside\n")

    def _restore_env(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # -- helpers --
    def path(self, rel: str) -> str:
        return os.path.join(self.root, rel)

    def write(self, rel: str, text: str) -> None:
        p = os.path.normpath(os.path.join(self.root, rel))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    def approve_profile(self, **over) -> dict:
        prof = base_profile(**over)
        errs, _ = profile.validate(prof, self.root)
        self.assertEqual(errs, [])
        write_text_atomic(profile.profile_path(self.root), profile.dumps(prof))
        profile.record_generated(self.root, also_approve_hash=profile.security_hash(prof))
        return prof

    def start_run(self, kind="plan", mode="standard", phase=None, session=SESSION) -> dict:
        cfg = state.load_config(self.root)
        run = state.new_run(self.root, kind, mode, "demo feature", cfg["limits"], cfg["modes"][mode], session)
        if phase:
            self.force_phase(run["run_id"], phase)
        return state.load_run(self.root, run["run_id"])

    def force_phase(self, run_id: str, phase: str) -> None:
        import time

        def m(r):
            r["phase"] = phase
            r["phase_visit"] += 1
            r["visit_started_epoch"] = time.time()
            r["spawns"] = {}
        state.update_run(self.root, run_id, m)

    def engineer_run(self) -> dict:
        """Approved profile + run in implementation phase with a snapshot."""
        from telib import snapshot
        prof = self.approve_profile()
        run = self.start_run(phase="implementation")
        snapshot.take(self.root, state.run_state_dir(self.root, run["run_id"]), prof)
        return run

    def ev(self, tool, tool_input, role=None, agent_type=None, session=SESSION, mode="default", cwd=None, agent_id="a1"):
        e = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "session_id": session,
             "cwd": cwd or self.root, "permission_mode": mode}
        at = agent_type or (AGENT[role] if role else None)
        if at:
            e["agent_type"], e["agent_id"] = at, agent_id
        return e

    def decide(self, *a, **kw) -> str:
        d, _ = policy.decide(self.ev(*a, **kw))
        self.last_reason = d.reason
        return d.action

    def assertDenied(self, *a, **kw):
        self.assertEqual(self.decide(*a, **kw), "deny", f"expected DENY for {a} {kw}")

    def assertAllowed(self, *a, **kw):
        act = self.decide(*a, **kw)
        self.assertEqual(act, "allow", f"expected ALLOW for {a} {kw}; got {act}: {getattr(self, 'last_reason', '')}")

    def te(self, *args) -> str:
        return f'uv run --no-project "{cli_script().as_posix()}" ' + " ".join(args)

    def artifact(self, run, name, obj) -> Path:
        p = state.artifacts_dir(self.root, run["run_id"]) / artifacts.ARTIFACT_FILES[name]
        write_json_atomic(p, obj)
        return p


def make_dir_link(link: str, target: str) -> bool:
    """Directory symlink (POSIX) or junction (Windows, needs no privilege)."""
    try:
        if os.name == "nt":
            # mklink parses forward slashes as switches: always pass native separators
            r = subprocess.run(["cmd", "/c", "mklink", "/J", os.path.normpath(link), os.path.normpath(target)],
                               capture_output=True)
            return r.returncode == 0
        os.symlink(target, link, target_is_directory=True)
        return True
    except OSError:
        return False


def make_file_symlink(link: str, target: str) -> bool:
    try:
        os.symlink(target, link)
        return True
    except (OSError, NotImplementedError):
        return False


def minimal_plan(run_id: str, level="unit") -> dict:
    return {
        "run_id": run_id, "agent_role": "test-analyst", "status": "completed", "summary": "s",
        "plan_id": "p1", "version": 1,
        "scope": {"objective": "o", "in_scope": ["accept()"], "out_of_scope": []},
        "requirements": [{"id": "REQ-01", "source": "user", "statement": "n <= 10 accepted", "authority": "authoritative"}],
        "risks": [{"id": "R-01", "failure_mode": "off by one", "impact": "wrong accept", "priority": "P1"}],
        "strategy": [{"technique": "bva", "rationale": "numeric limit"}],
        "test_cases": [{
            "test_case_id": "TC-001", "requirement_refs": ["REQ-01"], "scenario": "limit accepted",
            "category": "boundary", "priority": "P1", "test_level": level, "level_rationale": "pure logic",
            "preconditions": [], "inputs_and_actions": ["accept(10)"], "expected_observations": ["True"]}],
        "coverage": [{"ref": "REQ-01", "test_cases": ["TC-001"], "status": "covered"}],
        "coverage_gaps": [], "open_questions": [],
        "environment": {"fixtures": [], "external_dependencies": [], "isolation": "pure"},
    }


def minimal_review(run_id: str, target: str, verdict="APPROVE", findings=None) -> dict:
    return {"run_id": run_id, "agent_role": "test-reviewer", "status": "completed", "summary": "s",
            "review_target": target, "verdict": verdict, "findings": findings or [], "residual_risks": []}


def finding(sev="high") -> dict:
    return {"finding_id": "F-01", "severity": sev, "type": "coverage_gap", "evidence_ref": "REQ-01",
            "problem": "p", "impact": "i", "recommendation": "r", "human_decision_required": False}
