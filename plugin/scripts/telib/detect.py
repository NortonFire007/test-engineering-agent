"""Deterministic, language-agnostic stack detection.

Produces *hints* for the explorer (and a draft the human reviews). Nothing here
is trusted as policy until it lands in an approved profile. Unknown stacks are
reported as unknown instead of being forced into a known shape.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import tomllib

from . import paths, snapshot

MAX_FILES = 40000
TEST_DIR_NAMES = {"tests", "test", "__tests__", "spec", "specs", "e2e", "integration_tests"}
TEST_FILE_GLOBS = {
    "python": ["test_*.py", "*_test.py"],
    "javascript": ["*.test.js", "*.test.jsx", "*.spec.js", "*.spec.jsx", "*.test.mjs"],
    "typescript": ["*.test.ts", "*.test.tsx", "*.spec.ts", "*.spec.tsx"],
    "go": ["*_test.go"],
    "java": ["*Test.java", "*Tests.java", "*IT.java"],
    "kotlin": ["*Test.kt"],
    "csharp": ["*Tests.cs", "*Test.cs"],
    "ruby": ["*_spec.rb", "*_test.rb"],
    "php": ["*Test.php"],
}
CONFIG_GLOBS = [
    "pyproject.toml", "setup.cfg", "pytest.ini", "tox.ini", "conftest.py", "package.json", "tsconfig.json",
    "vitest.config.*", "jest.config.*", "playwright.config.*", "cypress.config.*", "Cargo.toml", "go.mod",
    "pom.xml", "build.gradle", "build.gradle.kts", "*.csproj", "Gemfile", "composer.json", "Makefile",
    "phpunit.xml", ".rspec",
]
PY_FRAMEWORKS = ["fastapi", "django", "flask", "starlette", "sqlalchemy", "pydantic", "celery", "aiohttp", "typer"]
PY_TEST = ["pytest", "pytest-asyncio", "pytest-cov", "hypothesis", "httpx", "respx", "factory-boy", "freezegun",
           "pytest-mock", "testcontainers", "anyio"]
JS_FRAMEWORKS = ["react", "next", "vue", "nuxt", "svelte", "@angular/core", "express", "fastify", "@nestjs/core",
                 "remix", "astro"]
JS_TEST = ["vitest", "jest", "@playwright/test", "cypress", "mocha", "@testing-library/react",
           "@testing-library/jest-dom", "msw", "supertest"]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:30] or "root"


def _read(path: str, limit: int = 400_000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def _cmd(cid, kind, stack, argv, cwd, selectors=True, flags=(), level=None, timeout=900):
    c = {"id": cid, "kind": kind, "stack": stack, "argv": argv, "cwd": cwd, "accepts_selectors": selectors,
         "allowed_flags": list(flags), "timeout_sec": timeout, "verified": False}
    if level:
        c["level"] = level
    return c


def _python(root, d, files, stack_id):
    text = _read(os.path.join(root, d, "pyproject.toml")) if "pyproject.toml" in files else ""
    try:
        data = tomllib.loads(text) if text else {}
    except tomllib.TOMLDecodeError:
        data = {}
    deps = " ".join([
        json.dumps(data.get("project", {}).get("dependencies", [])),
        json.dumps(data.get("project", {}).get("optional-dependencies", {})),
        json.dumps(data.get("dependency-groups", {})),
        json.dumps(data.get("tool", {}).get("poetry", {})),
        json.dumps(data.get("tool", {}).get("uv", {})),
        _read(os.path.join(root, d, "requirements.txt")), _read(os.path.join(root, d, "requirements-dev.txt")),
    ]).lower()
    has = lambda name: re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", deps) is not None
    pm = "uv" if "uv.lock" in files else "poetry" if "poetry.lock" in files else "pdm" if "pdm.lock" in files else "pip"
    prefix = {"uv": ["uv", "run", "--no-sync"], "poetry": ["poetry", "run"], "pdm": ["pdm", "run"]}.get(pm, ["python", "-m"])
    stack = {"id": stack_id, "language": "python", "root": d or ".", "package_manager": pm,
             "language_version": str(data.get("project", {}).get("requires-python", "")) or "unknown",
             "frameworks": [f for f in PY_FRAMEWORKS if has(f)],
             "test_frameworks": []}
    cmds = []
    cwd = d or "."
    if has("pytest") or "pytest.ini" in files or "conftest.py" in files or data.get("tool", {}).get("pytest"):
        cfgs = [f for f in ("pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg") if f in files]
        stack["test_frameworks"].append({"name": "pytest", "config_files": cfgs,
                                         "plugins": [p for p in PY_TEST if p != "pytest" and has(p)]})
        cmds.append(_cmd(f"{stack_id}-test", "test", stack_id, prefix + ["pytest"], cwd,
                         flags=["-k", "-x", "-q", "-m", "--maxfail", "--lf"]))
    elif "manage.py" in files:
        stack["test_frameworks"].append({"name": "django-test", "config_files": []})
        cmds.append(_cmd(f"{stack_id}-test", "test", stack_id, prefix[:-1] + ["python", "manage.py", "test"] if pm != "pip"
                         else ["python", "manage.py", "test"], cwd))
    if has("ruff"):
        cmds.append(_cmd(f"{stack_id}-lint", "lint", stack_id, prefix + ["ruff", "check"], cwd, flags=[]))
    if has("mypy"):
        cmds.append(_cmd(f"{stack_id}-typecheck", "typecheck", stack_id, prefix + ["mypy"], cwd, flags=[]))
    return stack, cmds


def _node(root, d, files, stack_id):
    try:
        pkg = json.loads(_read(os.path.join(root, d, "package.json")) or "{}")
    except ValueError:
        pkg = {}
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    ts = "typescript" in deps or "tsconfig.json" in files
    pm = ("pnpm" if "pnpm-lock.yaml" in files else "yarn" if "yarn.lock" in files
          else "bun" if ("bun.lockb" in files or "bun.lock" in files) else "npm")
    stack = {"id": stack_id, "language": "typescript" if ts else "javascript", "root": d or ".",
             "package_manager": pm, "language_version": str((pkg.get("engines") or {}).get("node", "unknown")),
             "frameworks": [f"{f}@{deps[f]}" for f in JS_FRAMEWORKS if f in deps], "test_frameworks": []}
    cwd = d or "."
    npx = ["npx", "--no-install"]
    cmds = []
    cfg = lambda stem: [f for f in files if fnmatch.fnmatchcase(f, stem + ".config.*")]
    if "vitest" in deps:
        stack["test_frameworks"].append({"name": "vitest", "version": deps["vitest"], "config_files": cfg("vitest")})
        cmds.append(_cmd(f"{stack_id}-unit", "test", stack_id, npx + ["vitest", "run"], cwd, flags=["-t", "--dir"]))
    if "jest" in deps:
        stack["test_frameworks"].append({"name": "jest", "version": deps["jest"], "config_files": cfg("jest")})
        cmds.append(_cmd(f"{stack_id}-jest", "test", stack_id, npx + ["jest"], cwd, flags=["-t", "--runInBand"]))
    if "@playwright/test" in deps:
        stack["test_frameworks"].append({"name": "playwright", "version": deps["@playwright/test"],
                                         "config_files": cfg("playwright")})
        cmds.append(_cmd(f"{stack_id}-e2e", "test", stack_id, npx + ["playwright", "test"], cwd,
                         flags=["-g", "--project"], level="end_to_end", timeout=1800))
    if "cypress" in deps:
        stack["test_frameworks"].append({"name": "cypress", "version": deps["cypress"], "config_files": cfg("cypress")})
    if "mocha" in deps:
        stack["test_frameworks"].append({"name": "mocha", "version": deps["mocha"], "config_files": []})
        cmds.append(_cmd(f"{stack_id}-mocha", "test", stack_id, npx + ["mocha"], cwd, flags=["-g"]))
    if "eslint" in deps:
        cmds.append(_cmd(f"{stack_id}-lint", "lint", stack_id, npx + ["eslint", "."], cwd, selectors=False))
    if ts:
        cmds.append(_cmd(f"{stack_id}-typecheck", "typecheck", stack_id, npx + ["tsc", "--noEmit"], cwd, selectors=False))
    stack["helper_libraries"] = [f for f in JS_TEST if f in deps and f not in ("vitest", "jest", "@playwright/test")]
    return stack, cmds


def _simple(language, pm, test_argv, extra=()):
    def build(root, d, files, stack_id):
        stack = {"id": stack_id, "language": language, "root": d or ".", "package_manager": pm,
                 "language_version": "unknown", "frameworks": [], "test_frameworks": [{"name": test_argv[0] + " test"
                                                                                      if len(test_argv) > 1 else test_argv[0],
                                                                                      "config_files": []}]}
        cmds = [_cmd(f"{stack_id}-test", "test", stack_id, list(test_argv), d or ".", flags=["-run", "-v", "--test"])]
        for cid, kind, argv in extra:
            cmds.append(_cmd(f"{stack_id}-{cid}", kind, stack_id, list(argv), d or ".", selectors=False))
        return stack, cmds
    return build


MANIFESTS = [
    ("pyproject.toml", "python", _python), ("setup.py", "python", _python), ("requirements.txt", "python", _python),
    ("package.json", "node", _node),
    ("Cargo.toml", "rust", _simple("rust", "cargo", ["cargo", "test"], [("fmt", "format_check", ["cargo", "fmt", "--check"])])),
    ("go.mod", "go", _simple("go", "go", ["go", "test", "./..."], [("vet", "lint", ["go", "vet", "./..."])])),
    ("pom.xml", "java", _simple("java", "maven", ["mvn", "-q", "-o", "test"])),
    ("build.gradle", "java", _simple("java", "gradle", ["gradle", "test", "--offline"])),
    ("build.gradle.kts", "kotlin", _simple("kotlin", "gradle", ["gradle", "test", "--offline"])),
    ("Gemfile", "ruby", _simple("ruby", "bundler", ["bundle", "exec", "rspec"])),
    ("composer.json", "php", _simple("php", "composer", ["vendor/bin/phpunit"])),
]


def detect(root: str) -> dict:
    root = paths.real(root)
    by_dir: dict = {}
    all_files = []
    truncated = False
    for rel, _full in snapshot.walk(root):
        all_files.append(rel)
        d, _, name = rel.rpartition("/")
        by_dir.setdefault(d, set()).add(name)
        if len(all_files) >= MAX_FILES:
            truncated = True
            break

    stacks, commands, seen = [], [], set()
    for d in sorted(by_dir, key=lambda x: (x.count("/"), x)):
        if d.count("/") > 3:
            continue
        files = by_dir[d]
        done_lang = set()
        for manifest, lang, builder in MANIFESTS:
            if manifest in files and lang not in done_lang:
                if lang == "python" and manifest != "pyproject.toml" and "pyproject.toml" in files:
                    continue
                done_lang.add(lang)
                sid = _slug(f"{lang}-{d}" if d else lang)
                if sid in seen:
                    continue
                seen.add(sid)
                stack, cmds = builder(root, d, files, sid)
                stacks.append(stack)
                commands += cmds
        for name in files:
            if name.endswith(".csproj") and "csharp" not in done_lang:
                done_lang.add("csharp")
                sid = _slug(f"csharp-{d}" if d else "csharp")
                if sid not in seen:
                    seen.add(sid)
                    stack, cmds = _simple("csharp", "dotnet", ["dotnet", "test", "--no-restore"])(root, d, files, sid)
                    stacks.append(stack)
                    commands += cmds

    # existing tests -> layout and suggested writable globs
    test_files, write_globs, layout = [], set(), {}
    for rel in all_files:
        name = rel.rsplit("/", 1)[-1]
        parts = rel.split("/")[:-1]
        lang = next((l for l, globs in TEST_FILE_GLOBS.items() if any(fnmatch.fnmatchcase(name, g) for g in globs)), None)
        in_test_dir = next((i for i, p in enumerate(parts) if p in TEST_DIR_NAMES), None)
        if lang is None and in_test_dir is None:
            continue
        if lang is None and not re.search(r"\.(py|ts|tsx|js|jsx|go|rs|java|kt|cs|rb|php)$", name):
            continue
        test_files.append(rel)
        if in_test_dir is not None:
            base = "/".join(parts[:in_test_dir + 1])
            write_globs.add(base + "/**")
            lvl = ("end_to_end" if any(p in ("e2e", "playwright", "cypress") for p in parts)
                   else "integration" if any("integration" in p for p in parts) else "unit")
            layout.setdefault((base, lvl), 0)
            layout[(base, lvl)] += 1
        else:
            suffix = next(g for g in TEST_FILE_GLOBS[lang] if fnmatch.fnmatchcase(name, g))
            top = parts[0] if parts else ""
            stack_root = next((s["root"] for s in sorted(stacks, key=lambda s: -len(s["root"]))
                               if s["root"] != "." and (rel.startswith(s["root"] + "/"))), "")
            prefix = (stack_root + "/") if stack_root else ((top + "/") if top and len(parts) > 1 and False else "")
            write_globs.add(f"{prefix}**/{suffix}")
            layout.setdefault((f"{prefix}**/{suffix}", "unit"), 0)
            layout[(f"{prefix}**/{suffix}", "unit")] += 1
    if not test_files:
        for s in stacks:
            pre = "" if s["root"] == "." else s["root"] + "/"
            write_globs.add({"go": f"{pre}**/*_test.go", "typescript": f"{pre}**/*.test.ts",
                             "javascript": f"{pre}**/*.test.js"}.get(s["language"], f"{pre}tests/**"))

    fp = sorted(r for r in all_files if r.count("/") <= 3 and
                any(fnmatch.fnmatchcase(r.rsplit("/", 1)[-1], g) for g in CONFIG_GLOBS))
    secrets = sorted(r for r in all_files if paths.secret_reason(r))
    notes = []
    if truncated:
        notes.append(f"file scan truncated at {MAX_FILES} files")
    if not stacks:
        notes.append("no known manifest found: stack is UNKNOWN to the detector; the explorer must identify it from evidence")
    if not test_files:
        notes.append("no existing tests found: conventions must be proposed, not discovered")
    return {
        "project_root": root, "has_git": os.path.isdir(os.path.join(root, ".git")),
        "stacks": stacks, "commands": commands,
        "test_layout": [{"paths": [p], "level": lvl, "file_count": n} for (p, lvl), n in sorted(layout.items())],
        "suggested_allowed_write_paths": sorted(write_globs),
        "fingerprint_files": fp, "secret_files": secrets,
        "existing_test_file_count": len(test_files), "sample_test_files": test_files[:25],
        "top_level": sorted(by_dir.get("", set()))[:80], "notes": notes,
    }
