"""Claude Code hook entry point (PreToolUse, SubagentStop, SessionStart).

Launched without a shell:  uv run --no-project --python ">=3.11" hook.py <event>
Fails CLOSED for PreToolUse: any internal error blocks the tool call (exit 2).
"""
import json
import sys

if sys.version_info < (3, 11):
    sys.stderr.write("test-engineering: Python >= 3.11 is required for policy hooks; blocking.\n")
    sys.exit(2)
sys.dont_write_bytecode = True


def _emit(obj) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=True))
    sys.stdout.flush()


def main() -> int:
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    blocking = event == "pre-tool-use"
    try:
        ev = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(ev, dict):
            raise ValueError("hook input is not an object")
        from telib import policy, state

        if event == "pre-tool-use":
            decision, ctx = policy.decide(ev)
            if ctx.get("log") or decision.action in ("deny", "ask"):
                ti = ev.get("tool_input") if isinstance(ev.get("tool_input"), dict) else {}
                target = (ti.get("file_path") or ti.get("notebook_path") or ti.get("command") or ti.get("path")
                          or ti.get("pattern") or ti.get("subagent_type") or "")
                state.audit(ctx["root"], {
                    "event": "tool_call", "session_id": ev.get("session_id"), "agent_type": ev.get("agent_type"),
                    "agent_id": ev.get("agent_id"), "role": ctx.get("role") or "orchestrator",
                    "tool": ev.get("tool_name"), "target": str(target)[:400], "decision": decision.action,
                    "reason": decision.reason[:300], "run_id": ctx.get("run_id"), "phase": ctx.get("phase"),
                    "permission_mode": ev.get("permission_mode"),
                })
            if decision.action != "none":
                _emit({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                              "permissionDecision": decision.action,
                                              "permissionDecisionReason": "[test-engineering policy] " + decision.reason}})
            return 0
        if event == "subagent-stop":
            out = policy.on_subagent_stop(ev)
            if out:
                _emit(out)
            return 0
        if event == "session-start":
            return 0
        raise ValueError(f"unknown hook event '{event}'")
    except Exception as e:  # noqa: BLE001 - fail closed on anything unexpected
        sys.stderr.write(f"test-engineering policy hook error ({event}): {type(e).__name__}: {e}\n")
        return 2 if blocking else 1


if __name__ == "__main__":
    sys.exit(main())
