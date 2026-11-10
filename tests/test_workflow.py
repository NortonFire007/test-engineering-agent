"""Workflow state machine, guards and budgets, artifact validation, runner, snapshot,
profile lifecycle, CLI exit codes and stack detection.

Tests use the shared ProjectCase fixture; every run state lives in a disposable
TE_STATE_DIR. Tests that execute the fixture's test command need `uv` on PATH.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from support import (ProjectCase, artifacts, base_profile, finding, minimal_plan, minimal_review, profile, state,
                     workflow, write_json_atomic)
from telib import cli, detect, runner, snapshot
from telib.common import TEError

needs_uv = unittest.skipUnless(shutil.which("uv"), "uv is required to run the fixture test command")


def repo_map(run_id: str) -> dict:
    return {
        "run_id": run_id, "agent_role": "test-codebase-explorer", "status": "completed", "summary": "s",
        "evidence": [{"source": "src/app.py", "observation": "accept() compares n to LIMIT"}],
        "scope_examined": "src", "modules": [{"path": "src/app.py", "role": "logic"}],
        "entry_points": [{"symbol": "accept", "path": "src/app.py"}],
        "inputs_and_validation": [{"rule": "None is rejected", "source": "src/app.py"}],
        "state_and_side_effects": [], "failure_paths": ["n is None"],
        "requirements": [{"id": "REQ-01", "source": "user", "statement": "n <= 10 accepted",
                          "authority": "authoritative"}],
        "existing_tests": [], "reusable_fixtures": [], "unknowns": [], "suggested_analysis_areas": [],
        "level_recommendation": {"levels": ["unit"], "rationale": "pure function", "e2e_infrastructure_present": False},
        "estimated_case_count": 1,
    }


def impl_report(run_id: str, plan: dict) -> dict:
    return {
        "run_id": run_id, "agent_role": "test-engineer", "status": "completed", "summary": "s",
        "plan_hash": artifacts.plan_hash(plan), "files_created": ["tests/test_new.py"], "files_modified": [],
        "case_mapping": [{"test_case_id": "TC-001", "status": "implemented",
                          "tests": ["tests/test_new.py::T::test_limit"]}],
        "commands_run": [{"result_id": 1, "command_id": "py-test"}],
        "discrepancies": [], "residual_gaps": [],
    }


def fixture_command(**over) -> dict:
    c = copy.deepcopy(base_profile()["commands"][0])
    c.update(over)
    return c


class WorkflowCase(ProjectCase):
    """Drives workflow.set_phase with real artifacts written into the fixture project."""

    def move(self, run, target):
        return workflow.set_phase(self.root, run["run_id"], target)

    def assertRefused(self, run, target, fragment):
        with self.assertRaises(TEError) as cm:
            self.move(run, target)
        self.assertIn(fragment, str(cm.exception))

    def edit(self, run, fn):
        state.update_run(self.root, run["run_id"], fn)

    def reload(self, run):
        return state.load_run(self.root, run["run_id"])

    def write_plan(self, run, version=1, change=None):
        plan = minimal_plan(run["run_id"])
        plan["version"] = version
        if change:
            change(plan)
        self.artifact(run, "test-plan", plan)
        return plan

    def write_review(self, run, name, target, verdict="NEEDS_REVISION", findings=None):
        findings = [finding("high")] if findings is None else findings
        self.artifact(run, name, minimal_review(run["run_id"], target, verdict, findings))


# --- 1-2: phase graph and discovery / strategy guards -----------------------

class IllegalTransitions(WorkflowCase):
    def test_discovery_cannot_jump_to_implementation(self):
        run = self.start_run()
        self.assertRefused(run, "implementation", "illegal transition discovery -> implementation")
        self.assertEqual(self.reload(run)["phase"], "discovery")

    def test_planning_cannot_jump_to_implementation(self):
        run = self.start_run(phase="planning")
        self.assertRefused(run, "implementation", "illegal transition planning -> implementation")


class DiscoveryAndStrategyGuards(WorkflowCase):
    def test_strategy_gate_requires_valid_repo_map(self):
        run = self.start_run()
        self.assertRefused(run, "strategy_gate", "artifact not found")
        broken = repo_map(run["run_id"])
        del broken["modules"]
        self.artifact(run, "repo-map", broken)
        self.assertRefused(run, "strategy_gate", "modules: required field missing")
        self.artifact(run, "repo-map", repo_map(run["run_id"]))
        self.assertEqual(self.move(run, "strategy_gate")["phase"], "strategy_gate")

    def test_planning_requires_approved_strategy(self):
        run = self.start_run(phase="strategy_gate")
        self.assertRefused(run, "planning", "strategy not approved")
        self.edit(run, lambda r: r.__setitem__("strategy", {"levels": ["unit"]}))
        self.assertEqual(self.move(run, "planning")["phase"], "planning")


# --- 3-5: plan review budget, quick/standard/deep, version bump -------------

class PlanReviewBudget(WorkflowCase):
    def test_standard_allows_one_revision_cycle(self):
        run = self.start_run(phase="planning")
        self.write_plan(run, 1)
        self.move(run, "plan_review")
        self.write_review(run, "review-plan", "plan")
        self.move(run, "planning")
        self.write_plan(run, 2)
        self.move(run, "plan_review")
        self.write_review(run, "review-plan", "plan")
        self.assertRefused(run, "planning", "budget exhausted")

    def test_deep_allows_two_revision_cycles(self):
        run = self.start_run(mode="deep", phase="planning")
        for version in (1, 2, 3):
            self.write_plan(run, version)
            self.move(run, "plan_review")
            self.write_review(run, "review-plan", "plan")
            if version < 3:
                self.move(run, "planning")
        self.assertEqual(self.reload(run)["counters"]["plan_revisions"], 2)
        self.assertRefused(run, "planning", "budget exhausted (2/2)")

    def test_quick_mode_has_no_plan_review(self):
        run = self.start_run(mode="quick", phase="planning")
        self.write_plan(run, 1)
        self.assertRefused(run, "plan_review", "has no plan review")
        self.assertEqual(self.move(run, "awaiting_plan_approval")["phase"], "awaiting_plan_approval")

    def test_standard_refuses_skipping_plan_review(self):
        run = self.start_run(phase="planning")
        self.write_plan(run, 1)
        self.assertRefused(run, "awaiting_plan_approval", "requires an independent plan review")

    def test_revised_plan_must_bump_version(self):
        run = self.start_run(phase="planning")
        self.write_plan(run, 1)
        self.move(run, "plan_review")
        self.write_review(run, "review-plan", "plan")
        self.move(run, "planning")
        self.assertRefused(run, "plan_review", "'version' was not incremented")
        self.write_plan(run, 2)
        self.assertEqual(self.move(run, "plan_review")["phase"], "plan_review")


# --- 6: implementation gate (human approval, hash binding, authorization) ---

class ImplementationGate(WorkflowCase):
    def setUp(self):
        super().setUp()
        self.approve_profile()
        self.run = self.start_run(phase="awaiting_plan_approval")
        self.plan = self.write_plan(self.run, 1)

    def test_requires_human_approval_of_this_plan(self):
        self.assertRefused(self.run, "implementation", "not the human-approved version")

    def test_requires_explicit_authorization(self):
        self.edit(self.run, lambda r: r.__setitem__("plan_approval", {"hash": artifacts.plan_hash(self.plan)}))
        self.assertRefused(self.run, "implementation", "not explicitly authorized")

    def test_changed_plan_invalidates_approval(self):
        self.edit(self.run, lambda r: r.__setitem__("plan_approval", {
            "hash": artifacts.plan_hash(self.plan), "implementation_authorized": True}))
        self.write_plan(self.run, 1, change=lambda p: p["scope"].__setitem__("objective", "changed later"))
        self.assertRefused(self.run, "implementation", "not the human-approved version")

    def test_approved_plan_starts_implementation_and_snapshots_tree(self):
        self.edit(self.run, lambda r: r.__setitem__("plan_approval", {
            "hash": artifacts.plan_hash(self.plan), "implementation_authorized": True}))
        self.assertEqual(self.move(self.run, "implementation")["phase"], "implementation")
        manifest = state.run_state_dir(self.root, self.run["run_id"]) / "snapshot" / "manifest.json"
        self.assertTrue(manifest.is_file())


# --- 7: scope guard at implementation -> impl_review -------------------------

class ScopeGuard(WorkflowCase):
    def setUp(self):
        super().setUp()
        self.run = self.engineer_run()  # approved profile, implementation phase, snapshot taken
        self.plan = self.write_plan(self.run, 1)
        rdir = state.artifacts_dir(self.root, self.run["run_id"]) / "results"
        write_json_atomic(rdir / "001.json", {"result_id": 1, "command_id": "py-test", "outcome": "passed"})
        self.artifact(self.run, "impl-report", impl_report(self.run["run_id"], self.plan))

    def review_attempt(self):
        return workflow.set_phase(self.root, self.run["run_id"], "impl_review")

    def violation_paths(self, info):
        return [v["path"] for v in info["diff"]["violations"]]

    def test_modified_production_file_blocks_review(self):
        self.write("src/app.py", "LIMIT = 11\n")
        info = self.review_attempt()
        self.assertTrue(info["blocked"])
        self.assertEqual(self.reload(self.run)["status"], "blocked")
        self.assertIn("src/app.py", self.violation_paths(info))

    def test_deleted_existing_file_blocks_review(self):
        os.remove(self.path("tests/test_existing.py"))
        info = self.review_attempt()
        self.assertTrue(info["blocked"])
        self.assertIn("tests/test_existing.py", self.violation_paths(info))

    def test_new_file_outside_test_paths_blocks_review(self):
        self.write("lib/new.py", "X = 1\n")
        info = self.review_attempt()
        self.assertTrue(info["blocked"])
        self.assertIn("lib/new.py", self.violation_paths(info))

    def test_in_scope_new_test_file_passes_and_writes_evidence(self):
        self.write("tests/test_new.py", "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_limit(self):\n"
                                        "        self.assertTrue(True)\n")
        info = self.review_attempt()
        self.assertNotIn("blocked", info)
        self.assertEqual(info["phase"], "impl_review")
        adir = state.artifacts_dir(self.root, self.run["run_id"])
        self.assertTrue((adir / "diff.patch").is_file())
        with open(adir / "changes.json", encoding="utf-8") as f:
            self.assertIn("tests/test_new.py", json.load(f)["added"])


# --- 8: implementation correction budget and mutation gate ------------------

class ImplementationCorrections(WorkflowCase):
    def review_impl(self, run):
        self.write_review(run, "review-impl", "implementation", findings=[finding("medium")])

    def test_standard_allows_one_correction_cycle(self):
        run = self.start_run(phase="impl_review")
        self.review_impl(run)
        self.assertEqual(self.move(run, "implementation")["phase"], "implementation")
        self.assertEqual(self.reload(run)["counters"]["impl_corrections"], 1)
        self.force_phase(run["run_id"], "impl_review")
        self.review_impl(run)
        self.assertRefused(run, "implementation", "correction budget exhausted (1/1)")

    def test_mutation_check_refused_outside_deep(self):
        run = self.start_run(phase="impl_review")
        self.review_impl(run)
        self.assertRefused(run, "mutation", "does not include mutation checks")

    def test_deep_mode_allows_mutation_then_two_corrections(self):
        run = self.start_run(mode="deep", phase="impl_review")
        self.review_impl(run)
        self.assertEqual(self.move(run, "mutation")["phase"], "mutation")
        self.assertEqual(self.move(run, "implementation")["phase"], "implementation")
        self.force_phase(run["run_id"], "impl_review")
        self.review_impl(run)
        self.assertEqual(self.move(run, "implementation")["phase"], "implementation")
        self.force_phase(run["run_id"], "impl_review")
        self.review_impl(run)
        self.assertRefused(run, "implementation", "correction budget exhausted (2/2)")


# --- 9: engineer model escalation ------------------------------------------

class EngineerModelChoice(ProjectCase):
    def setUp(self):
        super().setUp()
        self.cfg = state.load_config(self.root)
        self.run = self.start_run(phase="impl_review")

    def model_after(self, corrections):
        run = copy.deepcopy(self.run)
        run["counters"]["impl_corrections"] = corrections
        return workflow.engineer_model(self.root, run, self.cfg)

    def review(self, *severities):
        findings = [{**finding("low"), "finding_id": f"F-{i:02d}", "severity": sev}
                    for i, sev in enumerate(severities, 1)]
        self.artifact(self.run, "review-impl",
                      minimal_review(self.run["run_id"], "implementation", "NEEDS_REVISION", findings))

    def test_first_attempt_uses_configured_engineer_model(self):
        self.assertNotEqual(self.cfg["models"]["engineer"], self.cfg["models"]["engineer_escalated"])
        self.assertEqual(self.model_after(0), self.cfg["models"]["engineer"])

    def test_one_correction_with_low_findings_stays_on_engineer(self):
        self.review("low", "medium")
        self.assertEqual(self.model_after(1), self.cfg["models"]["engineer"])

    def test_one_correction_with_high_finding_escalates(self):
        self.review("medium", "high")
        self.assertEqual(self.model_after(1), self.cfg["models"]["engineer_escalated"])

    def test_second_correction_always_escalates(self):
        self.review("low")
        self.assertEqual(self.model_after(2), self.cfg["models"]["engineer_escalated"])


# --- 10-12: artifact validation and JSON extraction ------------------------

RUN = {"run_id": "run-1", "strategy": {"levels": ["unit"]}}


class PlanValidation(unittest.TestCase):
    def assertPlanInvalid(self, mutate, fragment):
        plan = minimal_plan("run-1")
        mutate(plan)
        errs = artifacts.validate("test-plan", plan, None, RUN)
        self.assertTrue(any(fragment in e for e in errs), errs)

    def test_minimal_valid_plan_passes(self):
        self.assertEqual(artifacts.validate("test-plan", minimal_plan("run-1"), None, RUN), [])

    def test_duplicate_test_case_id(self):
        self.assertPlanInvalid(lambda p: p["test_cases"].append(copy.deepcopy(p["test_cases"][0])),
                               "duplicate test_case_id")

    def test_id_must_look_like_tc_001(self):
        self.assertPlanInvalid(lambda p: p["test_cases"][0].update(test_case_id="TC-1"), "must look like TC-001")

    def test_p1_case_needs_requirement_reference(self):
        self.assertPlanInvalid(lambda p: p["test_cases"][0].update(requirement_refs=[]),
                               "must reference a requirement")

    def test_unknown_requirement_reference(self):
        self.assertPlanInvalid(lambda p: p["test_cases"][0].update(requirement_refs=["REQ-99"]),
                               "not defined in requirements/risks")

    def test_requirement_neither_covered_nor_listed(self):
        def add_req(p):
            p["requirements"].append({"id": "REQ-02", "source": "user", "statement": "other",
                                      "authority": "authoritative"})
        self.assertPlanInvalid(add_req, "REQ-02: neither covered by a test case")

    def test_partial_coverage_requires_gap(self):
        self.assertPlanInvalid(lambda p: p["coverage"][0].update(status="partial"), "requires a 'gap' explanation")

    def test_level_must_be_approved_at_strategy_gate(self):
        self.assertPlanInvalid(lambda p: p["test_cases"][0].update(test_level="end_to_end"),
                               "was not approved at the strategy gate")

    def test_wrong_run_id_is_rejected(self):
        self.assertPlanInvalid(lambda p: p.update(run_id="run-2"), "run_id: must be 'run-1'")


class ReviewValidation(unittest.TestCase):
    def test_approve_with_high_finding_is_invalid(self):
        rev = minimal_review("run-1", "plan", "APPROVE", [finding("high")])
        errs = artifacts.validate("review-plan", rev, None, RUN)
        self.assertTrue(any("cannot be APPROVE" in e for e in errs), errs)

    def test_review_plan_must_target_plan(self):
        rev = minimal_review("run-1", "implementation", "APPROVE")
        errs = artifacts.validate("review-plan", rev, None, RUN)
        self.assertTrue(any("review_target: must be 'plan'" in e for e in errs), errs)

    def test_clean_approval_is_valid(self):
        self.assertEqual(artifacts.validate("review-plan", minimal_review("run-1", "plan", "APPROVE"), None, RUN), [])


class JsonExtraction(unittest.TestCase):
    def test_extracts_fenced_json_block(self):
        text = 'Done:\n```json\n{"verdict": "APPROVE"}\n```\nThanks.'
        self.assertEqual(artifacts.extract_json(text), {"verdict": "APPROVE"})

    def test_extracts_bare_json_from_prose(self):
        self.assertEqual(artifacts.extract_json('Result: {"a": {"b": 2}} end.'), {"a": {"b": 2}})

    def test_text_without_json_raises(self):
        with self.assertRaises(ValueError):
            artifacts.extract_json("no structured output here")


# --- 13: validate_extra -------------------------------------------------------

class SelectorValidation(ProjectCase):
    def test_rejects_arguments_when_selectors_not_accepted(self):
        cmd = fixture_command(accepts_selectors=False)
        with self.assertRaises(TEError):
            runner.validate_extra(cmd, ["tests/test_existing.py"], self.root)

    def test_rejects_flags_not_in_allowlist(self):
        with self.assertRaises(TEError):
            runner.validate_extra(fixture_command(), ["--bogus"], self.root)

    def test_rejects_shell_metacharacters(self):
        for bad in ("a;b", "a|b", "a&b", "a$b", "a`b", "a>b", 'a"b'):
            with self.subTest(arg=bad), self.assertRaises(TEError):
                runner.validate_extra(fixture_command(), [bad], self.root)

    def test_rejects_parent_directory_selector(self):
        with self.assertRaises(TEError):
            runner.validate_extra(fixture_command(), ["tests/../src/app.py::T"], self.root)

    def test_rejects_absolute_path_outside_project(self):
        outside = self.outside.replace("\\", "/") + "/test_x.py"
        with self.assertRaises(TEError):
            runner.validate_extra(fixture_command(), [outside], self.root)

    def test_accepts_selectors_and_allowed_flags(self):
        cmd = fixture_command(allowed_flags=["-k", "--maxfail"])
        extra = ["tests/test_existing.py", "tests/test_x.py::T::test_ok", "-k", "test_ok", "--maxfail=3"]
        self.assertEqual(runner.validate_extra(cmd, extra, self.root), extra)


# --- 14-16: exec_recorded (phases, verification, evidence, flaky, budget) ---

class ExecRecorded(ProjectCase):
    def setUp(self):
        super().setUp()
        self.approve_profile()

    def rdir(self, run):
        return state.artifacts_dir(self.root, run["run_id"]) / "results"

    def edit(self, run, fn):
        state.update_run(self.root, run["run_id"], fn)

    def test_refuses_unverified_command(self):
        self.approve_profile(commands=[fixture_command(verified=False)])
        run = self.start_run(phase="implementation")
        with self.assertRaisesRegex(TEError, "UNVERIFIED"):
            runner.exec_recorded(self.root, run["run_id"], "py-test", [], None)

    def test_refuses_outside_execution_phases(self):
        run = self.start_run(phase="planning")
        with self.assertRaisesRegex(TEError, "only available in phases"):
            runner.exec_recorded(self.root, run["run_id"], "py-test", [], None)

    @needs_uv
    def test_records_passing_run_with_log(self):
        run = self.start_run(phase="implementation")
        rec = runner.exec_recorded(self.root, run["run_id"], "py-test", [], None)
        self.assertEqual(rec["outcome"], "passed")
        with open(self.rdir(run) / "001.json", encoding="utf-8") as f:
            self.assertEqual(json.load(f)["outcome"], "passed")
        self.assertTrue((state.artifacts_dir(self.root, run["run_id"]) / rec["log"]).is_file())

    @needs_uv
    def test_records_failing_run(self):
        self.write("tests/test_fail.py", "import unittest\n\n\nclass T(unittest.TestCase):\n"
                                         "    def test_bad(self):\n        self.assertTrue(False)\n")
        run = self.start_run(phase="implementation")
        rec = runner.exec_recorded(self.root, run["run_id"], "py-test", [], None)
        self.assertEqual(rec["outcome"], "failed")
        self.assertNotEqual(rec["exit_code"], 0)

    @needs_uv
    def test_execution_budget_is_enforced(self):
        run = self.start_run(phase="implementation")
        self.edit(run, lambda r: r["limits"].__setitem__("exec_calls", 1))
        self.assertEqual(runner.exec_recorded(self.root, run["run_id"], "py-test", [], None)["outcome"], "passed")
        with self.assertRaisesRegex(TEError, "budget exhausted"):
            runner.exec_recorded(self.root, run["run_id"], "py-test", [], None)

    @needs_uv
    def test_flaky_rerun_marks_both_records_and_second_rerun_is_refused(self):
        run = self.start_run(phase="implementation")
        self.edit(run, lambda r: r["counters"].__setitem__("exec_calls", 1))
        write_json_atomic(self.rdir(run) / "001.json",
                          {"result_id": 1, "command_id": "py-test", "outcome": "failed", "extra": []})
        rec = runner.exec_recorded(self.root, run["run_id"], "py-test", [], 1)
        self.assertEqual(rec["outcome"], "passed")
        self.assertTrue(rec["flaky"])
        with open(self.rdir(run) / "001.json", encoding="utf-8") as f:
            self.assertTrue(json.load(f)["flaky"])
        with self.assertRaisesRegex(TEError, "rerun budget"):
            runner.exec_recorded(self.root, run["run_id"], "py-test", [], 1)


# --- 17: least-privilege environment ---------------------------------------

SECRET_VARS = {"AWS_SECRET_ACCESS_KEY": "x", "GITHUB_TOKEN": "x", "MY_API_KEY": "x", "DB_PASSWORD": "x"}


class BuildEnv(unittest.TestCase):
    def env(self, prof=None, cmd=None):
        with mock.patch.dict(os.environ, SECRET_VARS):
            return runner.build_env(prof or {"env_passthrough": []}, cmd or {})

    def test_drops_secret_looking_variables(self):
        env = self.env()
        for name in SECRET_VARS:
            self.assertNotIn(name, env)

    def test_keeps_path(self):
        self.assertTrue(any(k.upper() == "PATH" for k in self.env()))

    def test_profile_passthrough_keeps_named_variable_only(self):
        env = self.env({"env_passthrough": ["MY_API_KEY"]})
        self.assertIn("MY_API_KEY", env)
        self.assertNotIn("DB_PASSWORD", env)

    def test_command_env_is_added_and_marks_run(self):
        env = self.env(cmd={"env": {"CI_MODE": "1"}})
        self.assertEqual(env["CI_MODE"], "1")
        self.assertEqual(env["TE_RUN"], "1")


# --- 18: snapshot diff ------------------------------------------------------

class SnapshotDiff(ProjectCase):
    def setUp(self):
        super().setUp()
        self.prof = base_profile()
        self.state_dir = state.run_state_dir(self.root, "snap-1")
        snapshot.take(self.root, self.state_dir, self.prof)

    def diff(self, extra=()):
        return snapshot.diff(self.root, self.state_dir, self.prof, list(extra))

    def test_classifies_added_modified_and_deleted(self):
        self.write("tests/test_new.py", "x = 1\n")
        self.write("src/app.py", "LIMIT = 11\n")
        os.remove(self.path("tests/__init__.py"))
        d = self.diff()
        self.assertIn("tests/test_new.py", d["added"])
        self.assertIn("src/app.py", d["modified"])
        self.assertIn("tests/__init__.py", d["deleted"])
        bad = {v["path"]: v["change"] for v in d["violations"]}
        self.assertNotIn("tests/test_new.py", bad)
        self.assertEqual(bad["src/app.py"], "modified")
        self.assertEqual(bad["tests/__init__.py"], "deleted")

    def test_new_file_under_tests_is_in_scope(self):
        self.write("tests/test_new.py", "x = 1\n")
        self.assertEqual([v for v in self.diff()["violations"] if v["path"] == "tests/test_new.py"], [])

    def test_modified_preexisting_test_is_listed_but_not_a_violation(self):
        self.write("tests/test_existing.py", "# edited\n")
        d = self.diff()
        self.assertIn("tests/test_existing.py", d["preexisting_modified"])
        self.assertNotIn("tests/test_existing.py", [v["path"] for v in d["violations"]])

    def test_approved_extra_path_lifts_violation(self):
        self.write("src/app.py", "LIMIT = 11\n")
        self.assertIn("src/app.py", [v["path"] for v in self.diff()["violations"]])
        self.assertNotIn("src/app.py", [v["path"] for v in self.diff(["src/app.py"])["violations"]])

    def test_ignores_pycache_and_run_state(self):
        self.write("tests/__pycache__/mod.cpython-311.pyc", "x")
        self.write(".test-engineering/runs/other/x.json", "{}")
        d = self.diff()
        self.assertEqual(d["added"], [])

    def test_new_pyc_outside_test_paths_is_detected(self):
        # Suspected bug: snapshot.IGNORE_FILES drops "*.pyc" (and "*.log", "junit*.xml", "*.orig") in ANY
        # directory, not only caches. Python imports a sourceless src/evil.pyc with no .py beside it, so an
        # out-of-scope .pyc is executable code that the scope guard never reports.
        self.write("src/evil.pyc", "not-really-bytecode")
        self.assertIn("src/evil.pyc", [v["path"] for v in self.diff()["violations"]])


# --- 19-21: profile lifecycle and CLI exit codes ----------------------------

class ProfileLifecycle(ProjectCase):
    def test_fingerprint_drift_reports_changed_and_missing(self):
        fps = profile.compute_fingerprints(self.root, ["pyproject.toml", "tests/test_existing.py"])
        prof = self.approve_profile(fingerprints=fps)
        self.assertEqual(profile.fingerprint_drift(self.root, prof), {"changed": [], "missing": []})
        self.write("pyproject.toml", "[project]\nname='other'\n")
        os.remove(self.path("tests/test_existing.py"))
        self.assertEqual(profile.fingerprint_drift(self.root, prof),
                         {"changed": ["pyproject.toml"], "missing": ["tests/test_existing.py"]})

    def test_manual_edit_detected_after_plugin_write(self):
        self.approve_profile()
        self.assertFalse(profile.manually_edited(self.root))
        with open(profile.profile_path(self.root), "a", encoding="utf-8") as f:
            f.write("\n")
        self.assertTrue(profile.manually_edited(self.root))


class CliExitCodes(ProjectCase):
    def init_plan_run(self, *extra):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["--project", self.root, "run", "init", "--kind", "plan", "--title", "x", *extra])
        return code, buf.getvalue()

    def test_unapproved_profile_exits_2(self):
        code, out = self.init_plan_run()
        self.assertEqual(code, 2)
        self.assertIn("not trusted", out)

    def test_approved_clean_profile_exits_0(self):
        self.approve_profile()
        code, _ = self.init_plan_run()
        self.assertEqual(code, 0)

    def test_stale_fingerprint_exits_2_until_accepted(self):
        self.approve_profile(fingerprints=profile.compute_fingerprints(self.root, ["pyproject.toml"]))
        self.write("pyproject.toml", "[project]\nname='changed'\n")
        code, out = self.init_plan_run()
        self.assertEqual(code, 2)
        self.assertIn("STALE", out)
        code, _ = self.init_plan_run("--accept-stale")
        self.assertEqual(code, 0)


# --- 22: stack detection ----------------------------------------------------

class StackDetection(ProjectCase):
    def tree(self, files: dict) -> str:
        d = tempfile.mkdtemp(prefix="te-detect-")
        self.addCleanup(shutil.rmtree, d, True)
        for rel, text in files.items():
            p = os.path.join(d, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        return d

    def test_fixture_reports_python_and_secret_rules(self):
        d = detect.detect(self.root)
        self.assertIn("python", [s["language"] for s in d["stacks"]])
        self.assertIn(".env", d["secret_files"])
        self.assertNotIn(".env.example", d["secret_files"])

    def test_no_python_stack_without_manifest(self):
        os.remove(self.path("pyproject.toml"))
        self.assertEqual(detect.detect(self.root)["stacks"], [])

    def test_go_project(self):
        d = detect.detect(self.tree({"go.mod": "module example.com/x\n\ngo 1.22\n", "x_test.go": "package x\n"}))
        self.assertEqual([s["language"] for s in d["stacks"]], ["go"])
        self.assertIn(["go", "test", "./..."], [c["argv"] for c in d["commands"]])
        self.assertTrue(any(g.endswith("_test.go") for g in d["suggested_allowed_write_paths"]))

    def test_rust_project_with_cargo_manifest_only(self):
        d = detect.detect(self.tree({"Cargo.toml": "[package]\nname = 'x'\n"}))
        self.assertEqual([s["language"] for s in d["stacks"]], ["rust"])

    def test_empty_directory_has_unknown_stack(self):
        d = detect.detect(self.tree({}))
        self.assertEqual(d["stacks"], [])
        self.assertTrue(any("UNKNOWN" in n for n in d["notes"]), d["notes"])


if __name__ == "__main__":
    unittest.main()
