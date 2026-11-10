#!/usr/bin/env bash
# Host launcher for Git Bash, macOS and Linux.
# Builds the harness image and runs the Linux checks with no network and a read-only repo mount.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# On Git Bash, pwd -W yields a Windows path (E:/...) that Docker Desktop accepts.
if HOST_REPO="$(cd "$REPO_ROOT" && pwd -W 2>/dev/null)"; then
  :
else
  HOST_REPO="$REPO_ROOT"
fi

cd "$REPO_ROOT"

docker build -t te-linux-checks -f docker/Dockerfile docker
# MSYS_NO_PATHCONV stops Git Bash from rewriting the container path /work.
MSYS_NO_PATHCONV=1 docker run --rm --network none -v "${HOST_REPO}:/work:ro" te-linux-checks
exit $?
