"""Path canonicalization, containment, glob matching and sensitive-path rules."""
from __future__ import annotations

import fnmatch
import functools
import os
import re

IS_WINDOWS = os.name == "nt"

_RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}


def to_native(raw: str) -> str:
    """Map Git-Bash style /c/dir paths to C:/dir on Windows."""
    if IS_WINDOWS:
        m = re.match(r"^/([A-Za-z])(/.*|$)", raw)
        if m:
            return f"{m.group(1)}:{m.group(2) or '/'}"
    return raw


def suspicious_reason(raw: str) -> str | None:
    """Reject path spellings that can alias another file or defeat canonicalization."""
    if not isinstance(raw, str) or not raw:
        return "empty path"
    if any(c in raw for c in "\x00\n\r"):
        return "control character in path"
    body = raw.replace("\\", "/")
    if re.match(r"^[A-Za-z]:", body):
        body = body[2:]
    if body.startswith("//"):
        return "UNC or device path"
    if ":" in body:
        return "colon in path (alternate data stream / drive-relative)"
    for comp in body.split("/"):
        if comp in ("", ".", ".."):
            continue
        if comp.endswith((".", " ")):
            return "path component with trailing dot or space"
        if comp.split(".")[0].lower() in _RESERVED:
            return "reserved device name"
        if re.search(r"~\d", comp):
            return "8.3 short-name style component"
    return None


_EXT_UNC = "\\\\?\\UNC\\"
_EXT = "\\\\?\\"


def real(p) -> str:
    """os.path.realpath with one stable spelling.

    On Windows realpath can return an extended-length spelling of the very same
    directory (prefixed with backslash-backslash-question-backslash). Every
    identity derived from a path (project id, containment checks) must not
    depend on which spelling came back, so the prefix is always removed.
    """
    r = os.path.realpath(os.fspath(p))
    if IS_WINDOWS:
        if r.startswith(_EXT_UNC):
            r = "\\\\" + r[len(_EXT_UNC):]
        elif r.startswith(_EXT):
            r = r[len(_EXT):]
    return r


def resolve(raw: str, base: str) -> str:
    """Canonical absolute path with symlinks and junctions resolved."""
    p = to_native(raw)
    if not os.path.isabs(p):
        p = os.path.join(base, p)
    return real(p)


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


def is_within(child_real: str, parent: str) -> bool:
    c = _norm(real(child_real))
    p = _norm(real(parent))
    return c == p or c.startswith(p.rstrip("\\/") + os.sep)


def rel_posix(child_real: str, parent: str) -> str:
    return os.path.relpath(real(child_real), real(parent)).replace("\\", "/")


@functools.lru_cache(maxsize=512)
def _glob_regex(pattern: str, ci: bool):
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if pattern[i:i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
                continue
            if pattern[i:i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE if ci else 0)


def glob_match(rel: str, pattern: str, ci: bool = False) -> bool:
    return bool(_glob_regex(pattern, ci).match(rel))


def match_any(rel: str, patterns, ci: bool = False) -> str | None:
    for p in patterns or ():
        if glob_match(rel, p, ci):
            return p
    return None


# --- sensitive paths -------------------------------------------------------

SECRET_NAME_GLOBS = [
    ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", ".npmrc", ".pypirc", ".netrc",
    ".git-credentials", "credentials", "credentials.*", "secrets.*", "secret.*",
    "*.tfvars", "service-account*.json", ".htpasswd", "*.kdbx",
]
SECRET_NAME_ALLOW = {".env.example", ".env.sample", ".env.template", ".env.dist", ".env.defaults"}
SECRET_DIRS = {".ssh", ".aws", ".gnupg", ".azure", ".kube", ".docker"}


def is_secret_name(name: str) -> bool:
    low = name.lower()
    if low in SECRET_NAME_ALLOW:
        return False
    return any(fnmatch.fnmatchcase(low, g) for g in SECRET_NAME_GLOBS)


def secret_reason(path: str, rel: str | None = None, extra_globs=()) -> str | None:
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    for comp in parts[:-1]:
        if comp.lower() in SECRET_DIRS:
            return f"credential directory '{comp}'"
    if parts and (is_secret_name(parts[-1]) or parts[-1].lower() in SECRET_DIRS):
        return f"secret-like file '{parts[-1]}'"
    if rel is not None:
        hit = match_any(rel, extra_globs, ci=True)
        if hit:
            return f"profile secret path '{hit}'"
    return None


def pattern_targets_secret(pattern: str) -> bool:
    """True when a search glob explicitly aims at secret-like files."""
    last = pattern.replace("\\", "/").split("/")[-1]
    literal = last.replace("*", "").replace("?", "").lower()
    if is_secret_name(last) or any(tok in literal for tok in (".env", "id_rsa", "id_ed25519", ".pem", "secret", "credential")):
        return last.lower() not in SECRET_NAME_ALLOW
    return False


# Files the engineer may never write, whatever the profile says. A human-approved
# exact-path scope expansion can lift this floor; CONTROL_GLOBS can never be lifted.
PROTECTED_WRITE_GLOBS = [
    "**/package.json", "**/package-lock.json", "**/pnpm-lock.yaml", "**/yarn.lock", "**/bun.lockb", "**/bun.lock",
    "**/pyproject.toml", "**/uv.lock", "**/poetry.lock", "**/pdm.lock", "**/requirements*.txt", "**/Pipfile",
    "**/Pipfile.lock", "**/setup.py", "**/setup.cfg", "**/Cargo.toml", "**/Cargo.lock", "**/go.mod", "**/go.sum",
    "**/go.work", "**/pom.xml", "**/build.gradle", "**/build.gradle.kts", "**/settings.gradle*", "**/*.csproj",
    "**/*.sln", "**/Gemfile", "**/Gemfile.lock", "**/composer.json", "**/composer.lock",
    "**/Dockerfile*", "**/docker-compose*.yml", "**/docker-compose*.yaml", "**/compose.yml", "**/compose.yaml",
    "**/.github/**", "**/.gitlab-ci.yml", "**/.circleci/**", "**/Jenkinsfile", "**/azure-pipelines.yml",
    "**/migrations/**", "**/alembic/**", "**/alembic.ini", "**/.gitignore", "**/.gitattributes",
    "**/pytest.ini", "**/tox.ini", "**/noxfile.py", "**/Makefile", "**/justfile",
    "**/*.config.js", "**/*.config.ts", "**/*.config.mjs", "**/*.config.cjs", "**/*.config.mts",
    "**/tsconfig*.json", "**/.eslintrc*", "**/.prettierrc*", "**/.babelrc*",
]
CONTROL_GLOBS = ["**/.git/**", "**/.claude/**", "**/.claude-plugin/**", "**/.test-engineering/**"]

# Canonical production/config locations. An allowed-write glob matching any of
# these is considered too broad to be a test path.
BROAD_PROBES = [
    "main.py", "app.py", "src/main.py", "app/main.py", "services/api/app/main.py", "src/index.ts", "src/index.js",
    "src/app/page.tsx", "src/components/Button.tsx", "pages/index.tsx", "src/lib.rs", "src/main.rs", "main.go",
    "cmd/app/main.go", "internal/service.go", "pkg/core/core.go", "src/main/java/App.java", "lib/core.rb",
    "README.md", "package.json", "Program.cs", "index.php", "docs/readme.md",
]
