"""
benham/core/rotlog.py - self-rotating stdout/stderr, OFF unless asked for.

On the PC the supervisor owns the log file: supervise_bot.ps1 redirects the
bot's stdout and rotation rides the relaunch. Under launchd (cazzy-mac,
migration Phase 4) there is no supervisor script - StandardOutPath appends
forever and launchd never rotates, and newsyslog cannot fix that from outside:
it renames the file but the process keeps the old descriptor, so the "new"
log stays empty until the next respawn. The console solved this exact problem
by owning its own log (banker.py's _RotatingLog); this is that shape for the
bot, sized to the measured volume (codex writes ~19 KB/day, so the 1 MB cap
is about seven weeks per generation).

BENHAM_LOG_FILE absent or empty means install() is never called and nothing
changes - every PC launch byte-identical. The gate lives in main().

The writer never raises, for any reason - log()'s own doctrine: a logging
failure must not corrupt the loop that was logging.
"""

import os
import sys
import threading

CAP = 1_000_000   # bytes per generation
KEEP = 2          # <file>.1 (newest rotated) and <file>.2


class _RotatingWriter:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self._f = open(path, "a", encoding="utf-8", errors="replace")

    def write(self, s):
        try:
            with self.lock:
                self._f.write(s)
                self._f.flush()
                if self._f.tell() >= CAP:
                    self._rotate()
        except Exception:  # noqa: BLE001 - never raise into the caller's loop
            pass
        return len(s)

    def _rotate(self):
        # Called under self.lock. Shift the generations up, newest first, then
        # reopen fresh. A missing generation is normal (first ever rotation).
        self._f.close()
        for i in range(KEEP, 0, -1):
            src = self.path if i == 1 else f"{self.path}.{i - 1}"
            try:
                os.replace(src, f"{self.path}.{i}")
            except OSError:
                pass
        self._f = open(self.path, "a", encoding="utf-8", errors="replace")

    def flush(self):
        try:
            with self.lock:
                self._f.flush()
        except Exception:  # noqa: BLE001
            pass

    def isatty(self):
        return False


def install(path):
    """Point sys.stdout AND sys.stderr at one rotating file; returns the writer.

    stderr too, deliberately: discord.py's gateway thread prints tracebacks
    there, and under launchd an un-redirected stream would land in a file
    nothing rotates - the exact hole this module closes for stdout.
    """
    w = _RotatingWriter(path)
    sys.stdout = sys.stderr = w
    return w
