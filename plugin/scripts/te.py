"""test-engineering workflow CLI entry point.

Run as:  uv run --no-project "<plugin>/scripts/te.py" <command> ...
Standard library only; requires Python >= 3.11.
"""
import sys

if sys.version_info < (3, 11):
    sys.stderr.write("test-engineering: Python >= 3.11 is required.\n")
    sys.exit(2)
sys.dont_write_bytecode = True

from telib.cli import main  # noqa: E402

if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
