#!/usr/bin/env bash
# Runs inside the container. The repository is mounted read-only at /work.
# Tests run on a private copy so they never write into the mount.
set -euo pipefail

WORK="$(mktemp -d)"
TE_STATE_DIR="$(mktemp -d)"
export TE_STATE_DIR
trap 'rm -rf "$WORK" "$TE_STATE_DIR"' EXIT

cp -a /work/plugin /work/tests "$WORK"/
if [ -d /work/fixtures ]; then
  cp -a /work/fixtures "$WORK"/
fi
cd "$WORK"

RESULT_NAMES=()
RESULT_STATUS=()
FAILED=0

# run_step NAME FUNC: runs FUNC; return 0 = PASS, 3 = SKIP, anything else = FAIL.
# Step functions use explicit "|| return 1" because set -e is ignored in this context.
run_step() {
  local name="$1"
  shift
  local rc=0
  echo
  echo "=== $name ==="
  "$@" || rc=$?
  case "$rc" in
    0) RESULT_NAMES+=("$name"); RESULT_STATUS+=("PASS") ;;
    3) RESULT_NAMES+=("$name"); RESULT_STATUS+=("SKIP") ;;
    *) RESULT_NAMES+=("$name"); RESULT_STATUS+=("FAIL"); FAILED=1 ;;
  esac
}

step_env() {
  uname -a || return 1
  python --version || return 1
  uv --version || return 1
  git --version || return 1
  go version || return 1
  cargo --version || return 1
  id || return 1
}

step_unit_tests() {
  uv run --no-project python -m unittest discover -s tests -t tests -v || return 1
}

step_doctor() {
  uv run --no-project plugin/scripts/te.py doctor || return 1
}

step_detect() {
  local rc=0 found=0 f
  for f in fixtures/rust-no-tests fixtures/go-with-tests; do
    if [ -d "$f" ]; then
      found=1
      echo "--- detect: $f"
      uv run --no-project plugin/scripts/te.py --project "$f" detect || rc=1
    else
      echo "--- detect: $f not present, skipped"
    fi
  done
  if [ "$found" -eq 0 ]; then
    return 3
  fi
  return "$rc"
}

step_fixture_toolchain() {
  local rc=0 found=0
  if [ -d fixtures/go-with-tests ]; then
    found=1
    (cd fixtures/go-with-tests && go vet ./... && go test ./...) || rc=1
  fi
  if [ -d fixtures/rust-no-tests ]; then
    found=1
    (cd fixtures/rust-no-tests && cargo build --offline) || rc=1
  fi
  if [ "$found" -eq 0 ]; then
    echo "no fixtures present, skipped"
    return 3
  fi
  return "$rc"
}

step_hook_fail_closed() {
  local rc=0
  printf 'not json' | uv run --no-project plugin/scripts/hook.py pre-tool-use || rc=$?
  if [ "$rc" -eq 2 ]; then
    echo "PASS: hook exited 2 on invalid input"
    return 0
  fi
  echo "FAIL: expected exit code 2 from hook on invalid input, got $rc"
  return 1
}

run_step "1. environment info" step_env
run_step "2. unit and policy tests" step_unit_tests
run_step "3. CLI doctor" step_doctor
run_step "4. stack detection on fixtures" step_detect
run_step "5. fixture toolchain sanity" step_fixture_toolchain
run_step "6. hook fail-closed smoke test" step_hook_fail_closed

echo
echo "=== summary ==="
printf '%-40s %s\n' "step" "result"
printf '%-40s %s\n' "----------------------------------------" "------"
for i in "${!RESULT_NAMES[@]}"; do
  printf '%-40s %s\n' "${RESULT_NAMES[$i]}" "${RESULT_STATUS[$i]}"
done

if [ "$FAILED" -ne 0 ]; then
  echo
  echo "Overall: FAIL"
  exit 1
fi
echo
echo "Overall: PASS"
exit 0
