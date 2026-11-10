"""Negative permission tests: every case here is an attempt that MUST be denied
(or a legitimate action that must still work). Run on Windows, macOS and Linux."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from support import (AGENT, SCRIPTS, SESSION, ProjectCase, finding, make_dir_link, make_file_symlink, minimal_plan,
                     minimal_review, policy, profile, state)


class ReadOnlyRoles(ProjectCase):
    def setUp(self):
        super().setUp()
        self.approve_profile()

    def test_analyst_cannot_modify_project_files(self):
        run = self.start_run(phase="planning")
        for rel in ("src/app.py", "tests/test_new.py", "pyproject.toml", "README.md"):
            self.assertDenied("Write", {"file_path": self.path(rel), "content": "x"}, role="analyst")
        self.assertDenied("Edit", {"file_path": self.path("src/app.py")}, role="analyst")
        self.assertDenied("Bash", {"command": "echo hi > src/app.py"}, role="analyst")
        self.assertDenied("PowerShell", {"command": "Set-Content src/app.py x"}, role="analyst")
        adir = state.artifacts_dir(self.root, run["run_id"])
        self.assertAllowed("Write", {"file_path": str(adir / "test-plan.json"), "content": "{}"}, role="analyst")
        # ...but not another role's artifact or CLI-owned evidence
        self.assertDenied("Write", {"file_path": str(adir / "review-plan.json"), "content": "{}"}, role="analyst")
        self.assertDenied("Write", {"file_path": str(adir / "results" / "001.json"), "content": "{}"}, role="analyst")

    def test_reviewer_cannot_fix_tests_or_run_commands(self):
        run = self.start_run(phase="impl_review")
        self.assertDenied("Write", {"file_path": self.path("tests/test_existing.py"), "content": "x"}, role="reviewer")
        self.assertDenied("Edit", {"file_path": self.path("tests/test_existing.py")}, role="reviewer")
        self.assertDenied("Bash", {"command": self.te("exec", "--run", run["run_id"], "py-test")}, role="reviewer")
        adir = state.artifacts_dir(self.root, run["run_id"])
        self.assertAllowed("Write", {"file_path": str(adir / "review-impl.json"), "content": "{}"}, role="reviewer")
        self.assertDenied("Write", {"file_path": str(adir / "impl-report.json"), "content": "{}"}, role="reviewer")

    def test_explorer_cannot_write_or_execute(self):
        run = self.start_run(phase="discovery")
        adir = state.artifacts_dir(self.root, run["run_id"])
        self.assertDenied("Write", {"file_path": str(adir / "repo-map.json"), "content": "{}"}, role="explorer")
        self.assertDenied("Write", {"file_path": self.path("src/app.py"), "content": "x"}, role="explorer")
        self.assertDenied("Bash", {"command": "ls"}, role="explorer")
        self.assertAllowed("Read", {"file_path": self.path("src/app.py")}, role="explorer")

    def test_no_role_may_use_unlisted_or_alternate_tools(self):
        self.start_run(phase="implementation")
        for role in AGENT:
            for tool in ("PowerShell", "NotebookEdit", "MultiEdit", "WebFetch", "WebSearch", "Agent", "Task",
                         "mcp__some__tool", "Skill", "CronCreate"):
                if role == "engineer" and tool in ():
                    continue
                self.force_phase(state.active_run_id(self.root),
                                 {"explorer": "discovery", "analyst": "planning", "reviewer": "impl_review",
                                  "engineer": "implementation"}[role])
                self.assertDenied(tool, {"command": "x", "file_path": self.path("tests/a.py"),
                                         "subagent_type": AGENT["engineer"]}, role=role)

    def test_specialist_acts_only_inside_an_active_run_and_its_phase(self):
        self.assertDenied("Read", {"file_path": self.path("src/app.py")}, role="analyst")  # no run at all
        self.start_run(phase="discovery")
        self.assertDenied("Read", {"file_path": self.path("src/app.py")}, role="analyst")  # wrong phase
        self.assertDenied("Write", {"file_path": self.path("tests/t.py"), "content": ""}, role="engineer")

    def test_unknown_plugin_agent_name_gets_no_privileges(self):
        self.start_run(phase="implementation")
        # an agent from another plugin that merely reuses our agent name is not one of our roles
        self.assertIsNone(policy.role_of("other-plugin:test-engineer"))
        self.assertEqual(policy.role_of("test-engineering:test-engineer"), "engineer")


class EngineerWriteScope(ProjectCase):
    def setUp(self):
        super().setUp()
        self.run_ = self.engineer_run()

    def w(self, rel, tool="Write"):
        return self.decide(tool, {"file_path": self.path(rel), "content": "x"}, role="engineer")

    def test_can_create_new_test_and_edit_existing_one(self):
        self.assertEqual(self.w("tests/test_new.py"), "allow")
        self.assertEqual(self.w("tests/unit/deep/test_new.py"), "allow")
        self.assertEqual(self.w("tests/test_existing.py", tool="Edit"), "allow")

    def test_cannot_overwrite_preexisting_test_with_write(self):
        self.assertEqual(self.w("tests/test_existing.py"), "deny")
        self.assertIn("Edit", self.last_reason)

    def test_cannot_write_production_config_or_dependencies(self):
        for rel in ("src/app.py", "pyproject.toml", "README.md", "package.json", ".github/workflows/ci.yml",
                    "Dockerfile", "migrations/001.sql", "tests/pytest.ini", "tests/vitest.config.ts",
                    "tests/golden/expected.json", "tests/package.json", "tests/migrations/x.py"):
            self.assertEqual(self.w(rel), "deny", rel)
            self.assertEqual(self.w(rel, tool="Edit"), "deny", rel)

    def test_cannot_write_secrets_or_control_files(self):
        for rel in (".env", "tests/.env", "tests/id_rsa", "tests/secrets.json", "config/prod/x.yml",
                    ".git/hooks/pre-commit", ".claude/settings.json", ".test-engineering/profile.json",
                    ".test-engineering/config.json", "tests/.git/config"):
            self.assertEqual(self.w(rel), "deny", rel)

    def test_path_traversal_is_denied(self):
        for target in (self.path("tests/../src/app.py"), self.path("tests/sub/../../pyproject.toml"),
                       os.path.join(self.root, "tests", "..", "..", "outside", "x.py"),
                       "tests/../src/app.py", "../outside/x.py", os.path.join(self.outside, "x.py")):
            self.assertDenied("Write", {"file_path": target, "content": "x"}, role="engineer")
            self.assertDenied("Edit", {"file_path": target}, role="engineer")

    def test_directory_link_escape_is_denied(self):
        # junction on Windows / symlink elsewhere: tests/link -> src and -> outside the project
        for name, target in (("link_src", self.path("src")), ("link_out", self.outside)):
            link = self.path(f"tests/{name}")
            if not make_dir_link(link, target):
                self.skipTest("cannot create directory links on this host")
            self.assertEqual(self.w(f"tests/{name}/app.py"), "deny", name)
            self.assertEqual(self.w(f"tests/{name}/new_file.py"), "deny", name)
            self.assertEqual(self.w(f"tests/{name}/app.py", tool="Edit"), "deny", name)

    def test_file_symlink_escape_is_denied(self):
        link = self.path("tests/test_link.py")
        if not make_file_symlink(link, self.path("src/app.py")):
            self.skipTest("file symlinks need privilege on this host")
        self.assertEqual(self.w("tests/test_link.py", tool="Edit"), "deny")
        self.assertEqual(self.w("tests/test_link.py"), "deny")

    def test_hard_link_to_production_file_is_denied(self):
        link = self.path("tests/test_hard.py")
        try:
            os.link(self.path("src/app.py"), link)
        except OSError:
            self.skipTest("hard links unavailable")
        self.assertEqual(self.w("tests/test_hard.py", tool="Edit"), "deny")

    def test_path_aliasing_spellings_are_denied(self):
        for target in (self.path("tests/test_a.py") + ":stream", self.path("tests/test_a.py."),
                       self.path("tests/nul"), self.path("tests/TEST_E~1.PY"), self.path("tests/a\x00b.py"),
                       "//server/share/tests/a.py"):
            self.assertDenied("Write", {"file_path": target, "content": "x"}, role="engineer")

    def test_missing_or_malformed_target_is_denied(self):
        self.assertDenied("Write", {"content": "x"}, role="engineer")
        self.assertDenied("Write", {"file_path": 42, "content": "x"}, role="engineer")
        self.assertDenied("Write", {"file_path": "", "content": "x"}, role="engineer")

    def test_writes_require_implementation_phase(self):
        self.force_phase(self.run_["run_id"], "impl_review")
        self.assertEqual(self.w("tests/test_new.py"), "deny")

    def test_human_approved_exact_path_is_the_only_extension(self):
        self.assertEqual(self.w("tests/golden/expected.json", tool="Edit"), "deny")

        def m(r):
            r["approved_extra_paths"].append("tests/golden/expected.json")
        state.update_run(self.root, self.run_["run_id"], m)
        self.assertEqual(self.w("tests/golden/expected.json", tool="Edit"), "allow")
        self.assertEqual(self.w("tests/golden/other.json", tool="Edit"), "deny")
        # secrets and control files can never be approved
        state.update_run(self.root, self.run_["run_id"], lambda r: r["approved_extra_paths"].append(".env"))
        self.assertEqual(self.w(".env", tool="Edit"), "deny")


class ProfileTrust(ProjectCase):
    def test_modified_profile_blocks_every_specialist_action(self):
        run = self.engineer_run()
        self.assertAllowed("Write", {"file_path": self.path("tests/test_new.py"), "content": ""}, role="engineer")
        prof = profile.load(self.root)
        prof["allowed_write_paths"].append("lib/**")  # e.g. arrives via git pull, or an agent edits it
        with open(profile.profile_path(self.root), "w", encoding="utf-8") as f:
            f.write(profile.dumps(prof))
        self.assertDenied("Write", {"file_path": self.path("tests/test_new.py"), "content": ""}, role="engineer")
        self.assertDenied("Write", {"file_path": self.path("lib/x.py"), "content": ""}, role="engineer")
        self.assertDenied("Read", {"file_path": self.path("src/app.py")}, role="engineer")
        self.assertDenied("Bash", {"command": self.te("exec", "--run", run["run_id"], "py-test")}, role="engineer")
        self.assertIn("not trusted", self.last_reason)

    def test_unapproved_profile_blocks_spawns(self):
        prof = self.approve_profile()
        run = self.start_run(phase="planning")
        state.save_trust(self.root, {})
        self.assertDenied("Agent", {"subagent_type": AGENT["analyst"]})

    def test_approval_is_bound_to_project_path_and_policy_version(self):
        self.approve_profile()
        self.assertEqual(profile.trust_status(self.root)["state"], "approved")
        t = state.load_trust(self.root)
        state.save_trust(self.root, {**t, "policy_version": "0"})
        self.assertEqual(profile.trust_status(self.root)["state"], "policy_changed")
        state.save_trust(self.root, {**t, "project_root": self.outside})
        self.assertEqual(profile.trust_status(self.root)["state"], "unapproved")

    def test_hash_is_canonical_and_ignores_only_verification_metadata(self):
        prof = self.approve_profile()
        h = profile.security_hash(prof)
        reordered = json.loads(json.dumps(dict(reversed(list(prof.items())))))
        self.assertEqual(profile.security_hash(reordered), h)
        v = json.loads(json.dumps(prof))
        v["last_verified_at"] = "2030-01-01T00:00:00Z"
        v["commands"][0]["verified"] = False
        self.assertEqual(profile.security_hash(v), h)
        for mutate in (lambda p: p["commands"][0]["argv"].append("--x"),
                       lambda p: p["allowed_write_paths"].append("spec/**"),
                       lambda p: p["commands"][0].__setitem__("cwd", "src"),
                       lambda p: p["excluded_paths"].clear(),
                       lambda p: p["env_passthrough"].append("AWS_SECRET_ACCESS_KEY")):
            c = json.loads(json.dumps(prof))
            mutate(c)
            self.assertNotEqual(profile.security_hash(c), h)


class ProfileValidation(ProjectCase):
    def errs(self, **over):
        from support import base_profile
        return profile.validate(base_profile(**over), self.root)[0]

    def cmd(self, **over):
        c = {"id": "t", "kind": "test", "argv": ["uv", "run", "pytest"], "cwd": "."}
        c.update(over)
        return self.errs(commands=[c])

    def test_valid_profile_passes(self):
        self.assertEqual(self.errs(), [])

    def test_rejects_shells_installers_and_inline_code(self):
        for argv in (["bash", "-lc", "pytest"], ["sh", "run.sh"], ["cmd", "/c", "pytest"], ["powershell", "x"],
                     ["npm", "install"], ["pip", "install", "x"], ["uv", "add", "x"], ["cargo", "publish"],
                     ["git", "push"], ["curl", "http://x"], ["python", "-c", "import os"], ["node", "-e", "1"],
                     ["docker", "compose", "up"], ["rm", "-rf", "."], ["sudo", "pytest"], ["uv", "run", "pytest; rm x"],
                     ["pytest", "$(id)"], "pytest tests", [], ["pytest", ""]):
            self.assertTrue(self.cmd(argv=argv), f"should reject {argv}")

    def test_rejects_bad_executable_paths_and_cwd(self):
        self.assertTrue(self.cmd(argv=["/usr/bin/pytest"]))
        self.assertTrue(self.cmd(argv=["C:/tools/pytest.exe"]))
        self.assertTrue(self.cmd(argv=["../outside/run"]))
        self.assertTrue(self.cmd(cwd=".."))
        self.assertTrue(self.cmd(cwd="/tmp"))
        self.assertTrue(self.cmd(cwd="nope"))
        self.assertTrue(self.cmd(allowed_flags=["-c"]))
        self.assertTrue(self.cmd(allowed_flags=["--ok; rm"]))
        self.assertTrue(self.cmd(timeout_sec=999999))
        self.assertTrue(self.cmd(unknown_key=1))

    def test_rejects_broad_or_dangerous_write_paths(self):
        for pat in ("**", "*", "**/*", "src/**", "**/*.py", "**/*.ts", "./**", "../x/**", "/abs/**", "C:/x/**",
                    "tests\\**", ".git/**", ".test-engineering/**", "**/.env", ".claude/**"):
            self.assertTrue(self.errs(allowed_write_paths=[pat]), f"should reject {pat!r}")
        for pat in ("tests/**", "**/*_test.go", "**/*.test.tsx", "services/api/tests/**", "**/__tests__/**", "e2e/**"):
            self.assertEqual(self.errs(allowed_write_paths=[pat]), [], pat)

    def test_rejects_unknown_top_level_keys(self):
        self.assertTrue(self.errs(permissions={"allow": ["*"]}))

    def test_draft_cannot_claim_verification(self):
        draft = {"profile": {"project": {"name": "d"}, "stacks": [{"id": "py", "language": "python"}],
                             "commands": [{"id": "t", "kind": "test", "argv": ["uv", "run", "pytest"], "verified": True,
                                           "verified_at": "x"}],
                             "allowed_write_paths": ["tests/**"]}, "fingerprint_files": ["pyproject.toml", ".env"]}
        built = profile.build_from_draft(self.root, draft)
        self.assertFalse(built["commands"][0]["verified"])
        self.assertIn("pyproject.toml", built["fingerprints"])
        self.assertNotIn(".env", built["fingerprints"])  # secrets are never fingerprinted/read
        self.assertIn(".env", built["secret_files"])


class SecretsAndReadScope(ProjectCase):
    def setUp(self):
        super().setUp()
        self.approve_profile()
        self.start_run(phase="implementation")

    def test_secret_files_are_unreadable_for_every_role(self):
        phases = {"explorer": "discovery", "analyst": "planning", "reviewer": "impl_review", "engineer": "implementation"}
        home = os.path.expanduser("~")
        for role, phase in phases.items():
            self.force_phase(state.active_run_id(self.root), phase)
            for target in (self.path(".env"), ".env", self.path("tests/../.env"), self.path("config/prod/settings.yml"),
                           os.path.join(home, ".ssh", "id_rsa"), os.path.join(home, ".aws", "credentials"),
                           self.path(".git/config"), os.path.join(self.outside, "secret.txt"),
                           os.path.join(self.state_dir, "projects"), self.path(".env") + ":x"):
                self.assertDenied("Read", {"file_path": target}, role=role)
            self.assertAllowed("Read", {"file_path": self.path(".env.example")}, role=role)
            self.assertAllowed("Read", {"file_path": self.path("src/app.py")}, role=role)

    def test_search_cannot_be_used_to_reach_secrets(self):
        for pat in ("**/.env", ".env*", "**/*.pem", "**/id_rsa*", "../**/*", "**/secrets.*"):
            self.assertDenied("Glob", {"pattern": pat}, role="engineer")
        self.assertDenied("Glob", {"pattern": "**/*.py", "path": self.outside}, role="engineer")
        self.assertDenied("Glob", {"pattern": os.path.join(self.outside, "**").replace("\\", "/")}, role="engineer")
        self.assertDenied("Grep", {"pattern": "TOKEN", "glob": ".env"}, role="engineer")
        self.assertDenied("Grep", {"pattern": "TOKEN", "path": self.path(".env")}, role="engineer")
        self.assertDenied("Grep", {"pattern": "x", "path": self.outside}, role="engineer")
        self.assertAllowed("Glob", {"pattern": "**/*.py"}, role="engineer")

    def test_directory_grep_that_would_scan_a_non_ignored_secret_is_denied(self):
        # .env exists at the root and is not git-ignored: an unfiltered content search would print it
        self.assertDenied("Grep", {"pattern": "DUMMY_SECRET"}, role="engineer")
        self.assertIn("secret file", self.last_reason)
        self.assertDenied("Grep", {"pattern": "DUMMY_SECRET", "path": self.root}, role="engineer")
        # filtered or scoped searches are fine
        self.assertAllowed("Grep", {"pattern": "LIMIT", "glob": "*.py"}, role="engineer")
        self.assertAllowed("Grep", {"pattern": "LIMIT", "type": "py"}, role="engineer")
        self.assertAllowed("Grep", {"pattern": "LIMIT", "path": self.path("src")}, role="engineer")


class EngineerShell(ProjectCase):
    def setUp(self):
        super().setUp()
        self.run_ = self.engineer_run()
        self.rid = self.run_["run_id"]

    def bash(self, command, role="engineer"):
        return self.decide("Bash", {"command": command}, role=role)

    def test_only_the_runner_exec_form_is_allowed(self):
        self.assertEqual(self.bash(self.te("exec", "--run", self.rid, "py-test")), "allow")
        self.assertEqual(self.bash(self.te("exec", "--run", self.rid, "py-test", "--", "tests/test_existing.py", "-v")), "allow")

    def test_arbitrary_and_wrapped_commands_are_denied(self):
        ok = self.te("exec", "--run", self.rid, "py-test")
        for cmd in ("ls", "pytest", "uv run pytest", "python -c \"open('src/app.py','w')\"", "git status",
                    "pip install requests", "npm install", "curl http://example.com", "cat .env", "rm -rf src",
                    ok + " && rm -rf src", ok + "; cat .env", ok + " | tee out.txt", ok + " > src/app.py",
                    "FOO=1 " + ok, "cd src && " + ok, "$(" + ok + ")", "`" + ok + "`", ok + "\ncat .env",
                    "bash -c '" + ok + "'", "env " + ok, "uv run --no-project other.py exec",
                    "uv run --with requests --no-project \"" + str(SCRIPTS / "te.py").replace("\\", "/") + "\" exec",
                    "uv run --no-project \"" + self.path("tests/te.py").replace("\\", "/") + f"\" exec --run {self.rid} py-test",
                    "", None, 42):
            self.assertEqual(self.bash(cmd), "deny", repr(cmd))

    def test_engineer_cannot_use_other_cli_subcommands(self):
        for args in (("plan", "approve"), ("profile", "approve"), ("scope", "approve", "--path", "src/app.py", "--reason", "x"),
                     ("run", "abort", "--reason", "x"), ("phase", "finalizing"), ("profile", "verify", "py-test"),
                     ("mutate",), ("exec", "--run", "other-run", "py-test"), ("exec", "py-test"),
                     ("--project", self.outside, "exec", "--run", self.rid, "py-test"),
                     ("exec", "--run", "other", "py-test", "--", "--run", self.rid)):
            self.assertEqual(self.bash(self.te(*args)), "deny", args)

    def test_powershell_tool_is_denied_for_engineer(self):
        self.assertDenied("PowerShell", {"command": self.te("exec", "--run", self.rid, "py-test")}, role="engineer")


class OrchestratorPolicy(ProjectCase):
    def setUp(self):
        super().setUp()
        self.approve_profile()

    def test_no_run_means_no_interference(self):
        self.assertEqual(self.decide("Write", {"file_path": self.path("src/app.py"), "content": "x"}), "none")
        self.assertEqual(self.decide("Bash", {"command": "git status"}), "none")

    def test_orchestrator_cannot_edit_project_or_run_shell_during_a_run(self):
        run = self.start_run(phase="planning")
        self.assertDenied("Write", {"file_path": self.path("src/app.py"), "content": "x"})
        self.assertDenied("Edit", {"file_path": self.path("tests/test_existing.py")})
        self.assertDenied("Bash", {"command": "git checkout -- ."})
        self.assertDenied("PowerShell", {"command": "Remove-Item src -Recurse"})
        self.assertDenied("Write", {"file_path": self.path(".test-engineering/profile.json"), "content": "{}"})
        adir = state.artifacts_dir(self.root, run["run_id"])
        self.assertDenied("Write", {"file_path": str(adir / "results" / "001.json"), "content": "{}"})
        self.assertEqual(self.decide("Write", {"file_path": str(adir / "notes.md"), "content": "x"}), "none")
        self.assertEqual(self.decide("Bash", {"command": self.te("run", "status")}), "none")
        self.assertEqual(self.decide("Read", {"file_path": self.path("src/app.py")}), "none")

    def test_blocked_run_keeps_the_lock(self):
        run = self.start_run(phase="implementation")
        state.update_run(self.root, run["run_id"], lambda r: r.__setitem__("status", "blocked"))
        self.assertDenied("Write", {"file_path": self.path("src/app.py"), "content": "x"})
        self.assertDenied("Bash", {"command": "git checkout -- ."})
        self.assertDenied("Agent", {"subagent_type": AGENT["engineer"]})

    def test_other_sessions_are_not_locked(self):
        self.start_run(phase="planning")
        self.assertEqual(self.decide("Write", {"file_path": self.path("src/app.py"), "content": "x"}, session="other"), "none")

    def test_non_plugin_subagent_cannot_be_used_to_bypass_the_lock(self):
        self.start_run(phase="implementation")
        for at in ("general-purpose", "other-plugin:test-engineer", "Explore"):
            self.assertDenied("Write", {"file_path": self.path("src/app.py"), "content": "x"}, agent_type=at)
            self.assertDenied("Bash", {"command": "sed -i s/10/11/ src/app.py"}, agent_type=at)

    def test_human_approvals_always_prompt_and_never_in_bypass_mode(self):
        self.start_run(phase="awaiting_plan_approval")
        for args in (("plan", "approve"), ("plan", "approve", "--authorize-implementation"), ("profile", "approve"),
                     ("scope", "approve", "--path", "src/app.py", "--reason", "x"), ("run", "unblock", "--reason", "x")):
            cmd = self.te(*args)
            self.assertEqual(self.decide("Bash", {"command": cmd}), "ask", args)
            self.assertEqual(self.decide("Bash", {"command": cmd}, mode="acceptEdits"), "ask", args)
            self.assertEqual(self.decide("Bash", {"command": cmd}, mode="bypassPermissions"), "deny", args)
            self.assertEqual(self.decide("Bash", {"command": cmd}, mode="dontAsk"), "deny", args)
            self.assertEqual(self.decide("Bash", {"command": cmd}, agent_type="general-purpose"), "deny", args)
            for role in AGENT:
                self.assertEqual(self.decide("Bash", {"command": cmd}, role=role), "deny", (role, args))

    def test_spawn_is_gated_by_phase(self):
        run = self.start_run(phase="discovery")
        self.assertEqual(self.decide("Agent", {"subagent_type": AGENT["explorer"]}), "none")
        for role in ("analyst", "reviewer", "engineer"):
            self.assertDenied("Agent", {"subagent_type": AGENT[role]})
        self.force_phase(run["run_id"], "planning")
        self.assertDenied("Agent", {"subagent_type": AGENT["engineer"]})
        self.assertEqual(self.decide("Agent", {"subagent_type": AGENT["analyst"]}), "none")

    def test_spawn_budget_stops_runaway_loops(self):
        run = self.start_run(phase="planning")
        results = [self.decide("Agent", {"subagent_type": AGENT["analyst"]}) for _ in range(6)]
        # 1 planned spawn + 2 transient retries for the whole run, then hard stop
        self.assertEqual(results, ["none", "none", "none", "deny", "deny", "deny"])
        self.force_phase(run["run_id"], "plan_review")
        self.assertEqual(self.decide("Agent", {"subagent_type": AGENT["reviewer"]}), "none")
        self.assertDenied("Agent", {"subagent_type": AGENT["reviewer"]})  # retry budget already spent

    def test_specialists_cannot_be_spawned_without_a_run_or_by_subagents(self):
        self.assertDenied("Agent", {"subagent_type": AGENT["engineer"]})
        self.start_run(phase="implementation")
        self.assertDenied("Agent", {"subagent_type": AGENT["engineer"]}, agent_type="general-purpose")
        self.assertDenied("Agent", {"subagent_type": AGENT["engineer"]}, role="reviewer")


class ArtifactValidationAtStop(ProjectCase):
    def setUp(self):
        super().setUp()
        self.approve_profile()

    def stop(self, role, msg=None, agent_id="a1"):
        return policy.on_subagent_stop({"hook_event_name": "SubagentStop", "agent_type": AGENT[role], "agent_id": agent_id,
                                        "cwd": self.root, "session_id": SESSION, "last_assistant_message": msg})

    def test_invalid_artifact_gets_exactly_one_repair_request(self):
        run = self.start_run(phase="planning")
        self.artifact(run, "test-plan", {"run_id": run["run_id"]})
        first = self.stop("analyst")
        self.assertEqual(first["decision"], "block")
        self.assertIn("failed validation", first["reason"])
        self.assertIsNone(self.stop("analyst"))  # budget is external and exhausted: no endless loop

    def test_missing_artifact_is_reported(self):
        self.start_run(phase="planning")
        self.assertEqual(self.stop("analyst")["decision"], "block")

    def test_valid_artifact_passes(self):
        run = self.start_run(phase="planning")
        self.artifact(run, "test-plan", minimal_plan(run["run_id"]))
        self.assertIsNone(self.stop("analyst"))

    def test_explorer_output_is_persisted_by_the_hook_not_by_the_agent(self):
        run = self.start_run(phase="discovery")
        self.assertEqual(self.stop("explorer", "here is my map: not json")["decision"], "block")
        good = {"run_id": run["run_id"], "agent_role": "test-codebase-explorer", "status": "completed", "summary": "s",
                "evidence": [], "scope_examined": "src", "modules": [], "entry_points": [], "inputs_and_validation": [],
                "state_and_side_effects": [], "failure_paths": [], "requirements": [], "existing_tests": [],
                "reusable_fixtures": [], "unknowns": [], "suggested_analysis_areas": [],
                "level_recommendation": {"levels": ["unit"], "rationale": "pure", "e2e_infrastructure_present": False},
                "estimated_case_count": 3}
        self.assertIsNone(self.stop("explorer", "```json\n" + json.dumps(good) + "\n```"))
        self.assertTrue((state.artifacts_dir(self.root, run["run_id"]) / "repo-map.json").is_file())


class HookProcess(ProjectCase):
    """The real entry point, as Claude Code launches it."""

    def hook(self, event, payload: bytes):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=self.root)
        return subprocess.run([sys.executable, str(SCRIPTS / "hook.py"), event], input=payload, capture_output=True,
                              env=env, timeout=60)

    def test_malformed_input_fails_closed(self):
        for payload in (b"", b"not json", b"[]", b"\xff\xfe"):
            r = self.hook("pre-tool-use", payload)
            self.assertEqual(r.returncode, 2, payload)

    def test_internal_error_fails_closed(self):
        self.approve_profile()
        run = self.start_run(phase="implementation")
        # corrupt run state: the hook must block, not allow
        with open(state.run_state_dir(self.root, run["run_id"]) / "state.json", "w") as f:
            f.write('{"run_id": "x", "status": "active"}')
        ev = self.ev("Write", {"file_path": self.path("src/app.py"), "content": "x"}, role="engineer")
        r = self.hook("pre-tool-use", json.dumps(ev).encode())
        self.assertEqual(r.returncode, 2)

    def test_deny_is_emitted_as_hook_json_and_audited(self):
        self.approve_profile()
        self.start_run(phase="planning")
        ev = self.ev("Write", {"file_path": self.path("src/app.py"), "content": "x"}, role="analyst")
        r = self.hook("pre-tool-use", json.dumps(ev).encode())
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual((out["hookEventName"], out["permissionDecision"]), ("PreToolUse", "deny"))
        log = (state.project_dir(self.root) / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        rec = json.loads(log[-1])
        self.assertEqual((rec["decision"], rec["tool"], rec["role"]), ("deny", "Write", "analyst"))

    def test_allow_is_audited_too(self):
        self.approve_profile()
        self.start_run(phase="planning")
        ev = self.ev("Read", {"file_path": self.path("src/app.py")}, role="analyst")
        r = self.hook("pre-tool-use", json.dumps(ev).encode())
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], "allow")
        rec = json.loads((state.project_dir(self.root) / "audit.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(rec["decision"], "allow")

    def test_unrelated_project_passes_through_silently(self):
        ev = self.ev("Bash", {"command": "ls"})
        r = self.hook("pre-tool-use", json.dumps(ev).encode())
        self.assertEqual((r.returncode, r.stdout), (0, b""))


if __name__ == "__main__":
    unittest.main()


class ExtendedLengthPaths(ProjectCase):
    """Windows may spell one directory with or without the extended-length prefix."""

    @unittest.skipUnless(os.name == "nt", "Windows path spelling")
    def test_project_identity_ignores_extended_length_prefix(self):
        from telib import paths
        bs = chr(92)
        ext = bs + bs + "?" + bs + self.root
        self.assertTrue(os.path.isdir(ext))
        self.assertEqual(paths.real(ext), paths.real(self.root))
        self.assertFalse(paths.real(ext).startswith(bs + bs + "?"))
        self.assertEqual(state.project_id(ext), state.project_id(self.root))
        self.assertTrue(paths.is_within(ext + bs + "src", self.root))
        self.assertEqual(state.find_project_root(ext), state.find_project_root(self.root))
