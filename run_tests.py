"""
run_tests.py - every suite, one command, one exit code.

The suites are standalone scripts on purpose: assertions execute at import and
each file ends in sys.exit(), which is exactly why this runner uses subprocesses
rather than importing them (and why pointing pytest at this directory would run
every suite during collection and die on the first SystemExit). One process per
suite also means no shared module state - each suite gets the same fresh
interpreter it was written against.

Two things a fresh checkout needs before the suites can run, and this refuses
loudly rather than creating them: control.json and exaroton_watch.json are
hand-edited config, and a test runner that silently writes config is the wrong
kind of helpful. The exaroton SKILL is different - that is Tyler's tooling, not
this repo's config - so when it is absent the runner points EXAROTON_SKILL_DIR
at the offline stub in test_stubs/, which exists only so bot.py can be imported.

    python run_tests.py
"""

import glob
import os
import subprocess
import sys
import time

import outbox

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SUITE_TIMEOUT = 300          # seconds; the slowest suite is well under a minute
FAIL_TAIL_LINES = 30         # how much of a failing suite's output to replay

# (required file, the command that creates it)
_REQUIRED_CONFIG = (
    ("control.json", "cp control.json.example control.json"),
    ("exaroton_watch.json", "cp exaroton_watch.json.example exaroton_watch.json"),
)


def _check_config():
    missing = [(f, fix) for f, fix in _REQUIRED_CONFIG
               if not os.path.exists(os.path.join(BASE_DIR, f))]
    if not missing:
        return True
    print("Cannot run the suites - required config is missing:\n")
    for f, fix in missing:
        print(f"  {f} not found. Create it with:\n      {fix}\n")
    print("Both are gitignored, hand-edited files; this runner deliberately")
    print("does not create config on its own.")
    return False


def _child_env():
    """The suites' environment, made self-sufficient on machines without the bot's
    real surroundings. A variable already set always wins - this only fills gaps."""
    env = dict(os.environ)
    # bot.py refuses to import without a token; no test ever uses it.
    env.setdefault("BOT_KEY", "test-token-not-used")
    # exaroton_ops.py imports the exaroton skill at module load. When neither the
    # override nor the real skill directory exists, fall back to the offline stub.
    if "EXAROTON_SKILL_DIR" not in env:
        real = os.path.join(os.path.expanduser("~"), ".claude", "skills", "exaroton")
        if not os.path.isdir(real):
            env["EXAROTON_SKILL_DIR"] = os.path.join(BASE_DIR, "test_stubs")
    return env


def main():
    outbox.console_utf8()
    if not _check_config():
        return outbox.EXIT_USAGE

    suites = sorted(os.path.basename(p)
                    for p in glob.glob(os.path.join(BASE_DIR, "test_*.py")))
    if not suites:
        print("No test_*.py suites found - that is itself a failure.")
        return outbox.EXIT_FAIL

    env = _child_env()
    failed = []
    print(f"Running {len(suites)} suite(s)\n")
    for suite in suites:
        started = time.monotonic()
        try:
            proc = subprocess.run(
                [sys.executable, os.path.join(BASE_DIR, suite)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", env=env, cwd=BASE_DIR, timeout=SUITE_TIMEOUT)
            ok, note = proc.returncode == 0, f"exit {proc.returncode}"
            output = (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired as e:
            ok, note = False, f"timed out after {SUITE_TIMEOUT}s"
            output = ((e.stdout or b"").decode("utf-8", "replace")
                      + (e.stderr or b"").decode("utf-8", "replace"))
        took = time.monotonic() - started
        print(f"  {'PASS' if ok else 'FAIL'}  {suite:<24} ({took:.1f}s)")
        if not ok:
            failed.append(suite)
            tail = output.strip().splitlines()[-FAIL_TAIL_LINES:]
            print(f"        --- last {len(tail)} line(s) of {suite} ({note}) ---")
            for line in tail:
                print(f"        {line}")

    print(f"\n{'ALL PASS' if not failed else str(len(failed)) + ' suite(s) FAILED: ' + ', '.join(failed)}"
          f"  ({len(suites) - len(failed)}/{len(suites)} passed)")
    return outbox.EXIT_OK if not failed else outbox.EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
