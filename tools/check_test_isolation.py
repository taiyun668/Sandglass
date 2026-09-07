"""Prove the test suite writes no product state, instead of remembering to.

`meter_home()` resolves to `%LOCALAPPDATA%\\sandglass` when SANDGLASS_HOME is
unset -- the same directory the running Sandglass uses. A test that calls a
real read path reaches it, and several of those read paths legitimately write:
grok_identity_runs() persists its merge because the vendor's CLI log rotates,
SessionCache() creates the database, available_update() caches its check.

That was not theoretical. A full suite run against an empty isolated home left
cache.sqlite at 8.9 MB, a Grok identity ledger carrying real account ids, and
quota-readings.jsonl carrying real used% and reset times -- so with the
variable unset those went into the live product's state directory, and the
requests behind them came out of the user's own quota. quota-observations.json
drives reset detection and full-window inference, which is to say a test run
could move the numbers the product reports.

There is no single switch to fix this. `tests/__init__.py` does not execute
under `python -m unittest discover -s tests`, the documented command (measured:
it runs under `-t .` and not without it), so any harness-level redirect depends
on how the suite happens to be invoked. Isolation therefore lives in the tests,
and this exists so that a regression is caught by a program rather than by
someone remembering.

    python -m tools.check_test_isolation

Exit code is the number of modules that wrote something. A module that fails to
run at all counts as a failure here too: a check that cannot execute has not
passed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"


def modules() -> list[str]:
    return sorted(path.stem for path in TESTS.glob("test_*.py"))


def run_isolated(module: str, home: Path) -> tuple[bool, list[str], str]:
    """Run one test module against `home`, and report what it left there."""
    home.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, SANDGLASS_HOME=str(home), PYTHONIOENCODING="utf-8")
    finished = subprocess.run(
        [sys.executable, "-m", "unittest", f"tests.{module}"],
        cwd=str(ROOT), env=environment, capture_output=True, text=True,
    )
    written = sorted(path.name for path in home.iterdir())
    return finished.returncode == 0, written, (finished.stderr or finished.stdout or "")


def main() -> int:
    offenders = 0
    with tempfile.TemporaryDirectory(prefix="sandglass-isolation-") as tmp:
        for module in modules():
            ran, written, output = run_isolated(module, Path(tmp) / module)
            if written:
                offenders += 1
                print(f"WROTE {module}: {' '.join(written)}")
            elif not ran:
                offenders += 1
                # A module that cannot run tells us nothing about isolation.
                # Print why: this runs each module alone in its own process,
                # which is where an order-dependent failure is worth the most,
                # and a bare "did not run" throws that occurrence away.
                print(f"FAILED {module}: did not run, so it proved nothing")
                for line in output.strip().splitlines()[-25:]:
                    print(f"  | {line}")
            else:
                print(f"clean {module}")
    print()
    if offenders:
        print(f"{offenders} module(s) touched the state directory or could not run")
    else:
        print("no module wrote into the state directory")
    return offenders


if __name__ == "__main__":
    raise SystemExit(main())
