"""Offline stand-in for the exaroton skill, used only by the test runner.

bot.py imports exaroton_ops.py, which imports the real skill from
~/.claude/skills/exaroton (or EXAROTON_SKILL_DIR) at module load - so on a
machine without Tyler's skill, importing bot.py fails before any test runs.
run_tests.py points EXAROTON_SKILL_DIR here when the real skill is absent.

Deliberately inert: the suites are offline by design, so any test that reached
the network through this would be a test doing something it should not. api()
raising ExarotonError is the same failure a network outage would produce, which
the callers already handle.
"""


class ExarotonError(Exception):
    pass


def api(method, path):
    raise ExarotonError("offline test stub: the exaroton skill is not installed")


def status_label(code):
    return f"status-{code}"
