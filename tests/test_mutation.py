"""Mutation operators (unit level) and the end-to-end controlled mutation run.

The end-to-end tests execute the fixture's test command, so they need `uv` on PATH.
"""
from __future__ import annotations

import os
import shutil
import unittest

from support import ProjectCase, base_profile, state
from telib import mutation
from telib.common import TEError

needs_uv = unittest.skipUnless(shutil.which("uv"), "uv is required to run the fixture test command")


class Operators(unittest.TestCase):
    def apply(self, line, operator, ext=".py", **kw):
        return mutation.apply_operator(line, operator, ext, **kw)

    # rel_boundary
    def test_rel_boundary_flips_each_comparison(self):
        self.assertEqual(self.apply("    if n <= LIMIT:\n", "rel_boundary"), "    if n < LIMIT:\n")
        self.assertEqual(self.apply("    if n >= LIMIT:\n", "rel_boundary"), "    if n > LIMIT:\n")
        self.assertEqual(self.apply("    if n > LIMIT:\n", "rel_boundary"), "    if n >= LIMIT:\n")
        self.assertEqual(self.apply("    if n < LIMIT:\n", "rel_boundary"), "    if n <= LIMIT:\n")

    def test_rel_boundary_ignores_arrows_and_shifts(self):
        self.assertIsNone(self.apply("def f(x) -> bool:\n", "rel_boundary"))
        self.assertIsNone(self.apply("y = x << 2\n", "rel_boundary"))
        self.assertIsNone(self.apply("y = x >> 2\n", "rel_boundary"))
        self.assertIsNone(self.apply("const f = (x) => g(x)\n", "rel_boundary", ".js"))

    def test_rel_boundary_occurrence_selects_second_operator(self):
        line = "if a < b and c > d:\n"
        self.assertEqual(self.apply(line, "rel_boundary", occurrence=2), "if a < b and c >= d:\n")
        self.assertEqual(self.apply(line, "rel_boundary", occurrence=1), "if a <= b and c > d:\n")

    def test_rel_boundary_on_line_without_comparison_is_unviable(self):
        self.assertIsNone(self.apply("x = 1\n", "rel_boundary"))

    # eq_negate
    def test_eq_negate_swaps_equality_forms(self):
        self.assertEqual(self.apply("if a == b:\n", "eq_negate"), "if a != b:\n")
        self.assertEqual(self.apply("if a != b:\n", "eq_negate"), "if a == b:\n")
        self.assertEqual(self.apply("if a === b) {\n", "eq_negate", ".js"), "if a !== b) {\n")
        self.assertEqual(self.apply("if a !== b) {\n", "eq_negate", ".js"), "if a === b) {\n")

    def test_eq_negate_ignores_ordering_operators(self):
        self.assertIsNone(self.apply("if a >= b:\n", "eq_negate"))

    # const_shift
    def test_const_shift_increments_and_decrements_integer(self):
        self.assertEqual(self.apply("LIMIT = 10\n", "const_shift"), "LIMIT = 11\n")
        self.assertEqual(self.apply("LIMIT = 10\n", "const_shift", delta=-1), "LIMIT = 9\n")

    def test_const_shift_leaves_identifiers_and_floats_alone(self):
        self.assertIsNone(self.apply("x2 = y2\n", "const_shift"))
        self.assertEqual(self.apply("x2 = 7\n", "const_shift", occurrence=1), "x2 = 8\n")
        self.assertIsNone(self.apply("ratio = 1.5\n", "const_shift"))

    # bool_flip
    def test_bool_flip_swaps_python_and_js_literals(self):
        self.assertEqual(self.apply("    return True\n", "bool_flip"), "    return False\n")
        self.assertEqual(self.apply("flag = False\n", "bool_flip"), "flag = True\n")
        self.assertEqual(self.apply("x = true;\n", "bool_flip", ".js"), "x = false;\n")
        self.assertEqual(self.apply("x = false;\n", "bool_flip", ".js"), "x = true;\n")

    # remove_null_check
    def test_remove_null_check_python(self):
        self.assertEqual(self.apply("    if n is None:\n", "remove_null_check"), "    if False:\n")
        self.assertEqual(self.apply("    if n is not None:\n", "remove_null_check"), "    if True:\n")

    def test_remove_null_check_js_strict_and_loose(self):
        self.assertIn("false", self.apply("if (x === null) {\n", "remove_null_check", ".js"))
        self.assertIn("true", self.apply("if (x != null) {\n", "remove_null_check", ".js"))

    def test_remove_null_check_js_keeps_parentheses(self):
        # Suspected bug: the OPERAND pattern in mutation._null_patterns includes "(" and ")", so for
        # "if (x === null) {" the match starts at "(x" and swallows the opening paren. Result is
        # "if false) {" (unbalanced, does not parse). Expected "if (false) {".
        self.assertEqual(self.apply("if (x === null) {\n", "remove_null_check", ".js"), "if (false) {\n")

    def test_remove_null_check_go_and_rust(self):
        self.assertEqual(self.apply("if err != nil {\n", "remove_null_check", ".go"), "if true {\n")
        self.assertEqual(self.apply("if opt.is_none() {\n", "remove_null_check", ".rs"), "if false {\n")

    # negate_condition
    def test_negate_condition_python_keeps_indent_and_colon(self):
        self.assertEqual(self.apply("    if a > b:\n", "negate_condition"), "    if not (a > b):\n")

    def test_negate_condition_brace_language_wraps_in_negation(self):
        self.assertIn("!(", self.apply("if (a > b) {\n", "negate_condition", ".js"))

    def test_negate_condition_brace_language_is_valid_syntax(self):
        # Suspected bug: negate_condition's brace branch captures "(a > b)" as the condition and wraps it
        # again, giving "if  !((a > b)) {". C-like `if` needs its own parentheses, so the output is not
        # valid JavaScript/Java/C#. Expected "if (!(a > b)) {".
        self.assertEqual(self.apply("if (a > b) {\n", "negate_condition", ".js"), "if (!(a > b)) {\n")

    # remove_line
    def test_remove_line_python_statement_becomes_pass(self):
        self.assertEqual(self.apply("        total = compute()\n", "remove_line"), "        pass\n")

    def test_remove_line_other_language_becomes_semicolon(self):
        self.assertEqual(self.apply("    total += 1;\n", "remove_line", ".js"), "    ;\n")

    def test_remove_line_on_comment_or_blank_is_unviable(self):
        self.assertIsNone(self.apply("    # note\n", "remove_line"))
        self.assertIsNone(self.apply("\n", "remove_line"))
        self.assertIsNone(self.apply("    // note\n", "remove_line", ".js"))

    # line endings and errors
    def test_line_endings_are_preserved(self):
        self.assertEqual(self.apply("if n <= LIMIT:\r\n", "rel_boundary"), "if n < LIMIT:\r\n")
        self.assertEqual(self.apply("if n <= LIMIT:\n", "rel_boundary"), "if n < LIMIT:\n")
        self.assertEqual(self.apply("        total = compute()\r\n", "remove_line"), "        pass\r\n")

    def test_unknown_operator_raises(self):
        with self.assertRaises(TEError):
            self.apply("x = 1\n", "no_such_operator")


APP_SOURCE = "LIMIT = 10\n\n\ndef accept(n):\n    if n is None:\n        raise ValueError('n')\n    return n <= LIMIT\n"

FULL_TEST = """import unittest

from src.app import accept


class T(unittest.TestCase):
    def test_limits(self):
        self.assertTrue(accept(10))
        self.assertFalse(accept(11))
        with self.assertRaises(ValueError):
            accept(None)
"""

WEAK_TEST = """import unittest

from src.app import accept


class T(unittest.TestCase):
    def test_small(self):
        self.assertTrue(accept(5))
"""

ORIGINAL_PATH_TEST = """import importlib.util
import unittest

spec = importlib.util.spec_from_file_location("app_orig", {path!r})
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class T(unittest.TestCase):
    def test_limits(self):
        self.assertTrue(app.accept(10))
        self.assertFalse(app.accept(11))
"""


@needs_uv
class MutationRun(ProjectCase):
    def setUp(self):
        super().setUp()
        self.write("src/__init__.py", "")
        self.write("src/app.py", APP_SOURCE)
        self.write("tests/test_app.py", FULL_TEST)
        self.approve_profile()
        self.run = self.start_run(mode="deep", phase="mutation")

    def line_no(self, snippet, rel="src/app.py"):
        with open(self.path(rel), encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                if snippet in line:
                    return n
        raise AssertionError(f"{snippet!r} not found in {rel}")

    def item(self, snippet, operator, mid="M-01", file="src/app.py", **kw):
        return {"id": mid, "file": file, "line": self.line_no(snippet, file), "operator": operator,
                "command_id": "py-test", "selectors": [], "test_case_refs": ["TC-001"], "rationale": "x", **kw}

    def run_mutation(self, spec, max_mutations=12):
        return mutation.run(self.root, self.run["run_id"], spec, max_mutations)

    @staticmethod
    def status(report, mid):
        return next(r for r in report["results"] if r["id"] == mid)["status"]

    def test_boundary_mutant_on_return_is_killed(self):
        report = self.run_mutation([self.item("return n <= LIMIT", "rel_boundary")])
        self.assertEqual(self.status(report, "M-01"), "killed")

    def test_removed_raise_is_killed(self):
        report = self.run_mutation([self.item("raise ValueError", "remove_line")])
        self.assertEqual(self.status(report, "M-01"), "killed")

    def test_inapplicable_operator_is_unviable(self):
        report = self.run_mutation([self.item("LIMIT = 10", "bool_flip")])
        self.assertEqual(self.status(report, "M-01"), "unviable")

    def test_weak_tests_let_boundary_mutant_survive(self):
        self.write("tests/test_app.py", WEAK_TEST)
        report = self.run_mutation([self.item("return n <= LIMIT", "rel_boundary")])
        self.assertEqual(self.status(report, "M-01"), "survived")

    def test_original_tree_is_untouched_and_report_written(self):
        original = self.path("src/app.py")
        with open(original, "rb") as f:
            before = f.read()
        spec = [self.item("return n <= LIMIT", "rel_boundary", mid="M-01"),
                self.item("raise ValueError", "remove_line", mid="M-02"),
                self.item("LIMIT = 10", "bool_flip", mid="M-03")]
        report = self.run_mutation(spec)
        with open(original, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(report["original_tree_changed_during_mutation"], [])
        self.assertTrue(report["copy_deleted"])
        self.assertEqual(report["counts"], {"killed": 2, "unviable": 1})
        self.assertTrue((state.artifacts_dir(self.root, self.run["run_id"]) / "mutation-report.json").is_file())

    def test_mutating_a_test_file_is_refused(self):
        spec = [self.item("import", "bool_flip", file="tests/test_app.py", line=1)]
        with self.assertRaisesRegex(TEError, "test file"):
            self.run_mutation(spec)

    def test_mutating_secret_file_is_refused(self):
        spec = [self.item("API_TOKEN", "bool_flip", file=".env", line=1)]
        with self.assertRaisesRegex(TEError, "secret"):
            self.run_mutation(spec)

    def test_path_with_parent_segment_is_refused(self):
        spec = [self.item("return n <= LIMIT", "rel_boundary", file="src/../src/app.py")]
        with self.assertRaises(TEError):
            self.run_mutation(spec)

    def test_spec_longer_than_limit_is_refused(self):
        spec = [self.item("return n <= LIMIT", "rel_boundary", mid=f"M-0{i}") for i in range(1, 4)]
        with self.assertRaisesRegex(TEError, "too many mutations"):
            self.run_mutation(spec, max_mutations=2)

    def test_unverified_command_is_refused(self):
        cmd = dict(base_profile()["commands"][0], verified=False)
        self.approve_profile(commands=[cmd])
        with self.assertRaisesRegex(TEError, "verified test command"):
            self.run_mutation([self.item("return n <= LIMIT", "rel_boundary")])

    def test_tests_loading_original_source_are_environment_invalid(self):
        self.write("tests/test_app.py", ORIGINAL_PATH_TEST.format(path=self.path("src/app.py")))
        report = self.run_mutation([self.item("return n <= LIMIT", "rel_boundary")])
        row = report["results"][0]
        self.assertEqual(row["status"], "not_run")
        self.assertEqual(row["reason"], "environment_invalid")


if __name__ == "__main__":
    unittest.main()
