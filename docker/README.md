# Linux test harness

Runs the Python policy tests and CLI checks on Linux inside a Docker container, so a Windows developer can check cross-platform behaviour without a Linux machine.

## Prerequisites

- Docker Desktop installed and **running**.

## Run

Windows (PowerShell, from the repo root):

```
powershell -ExecutionPolicy Bypass -File docker\run-linux-checks.ps1
```

Bash hosts (Git Bash, macOS, Linux, from the repo root):

```
bash docker/run-linux-checks-host.sh
```

Both build the image `te-linux-checks` from `docker/Dockerfile` and run it. The exit code of the container is passed through: 0 means every step passed.

## What each step checks

1. **Environment info**: prints the kernel, Python, uv, git, Go, Cargo and user identity, so results can be traced to a toolchain.
2. **Unit and policy tests**: runs the `unittest` suite under `tests/` with the image's Python.
3. **CLI doctor**: runs `te.py doctor` on Linux.
4. **Stack detection**: runs `te.py detect` on `fixtures/rust-no-tests` and `fixtures/go-with-tests` when present and prints the JSON.
5. **Fixture toolchain sanity**: runs `go vet` and `go test` on the Go fixture and `cargo build --offline` on the Rust fixture when present.
6. **Hook fail-closed smoke test**: pipes invalid input into `hook.py pre-tool-use` and requires exit code 2.

A summary table of PASS, FAIL or SKIP per step is printed at the end.

## Container properties

- Runs as a non-root user `tester` (uid 1000).
- Runs with `--network none`, which proves the test run needs no network.
- The repository is mounted read-only at `/work`. The script copies `plugin/`, `tests/` and `fixtures/` to a temporary directory and runs there, so nothing is written into the repo.
- uv is configured to use the image's system Python only, with no downloads and no cache.

## Limitation

This validates the Python policy code and the CLI on Linux. It does **not** test Claude Code itself running on Linux. Live agent tests are separate.
