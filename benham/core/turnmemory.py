"""
turnmemory.py - the six lines where the twelve-day bug lived.

Stage 4, narrowed. The original plan said "unify the agent loops", and by the time
it came round its premise had dissolved: stage 2 archived two of the three loops,
and the two that remain are separated for a reason guest.py argues at length -

    agent.py's whole job is handing the model a tool list and running the loop;
    "the same thing but the list is empty" is one wrong conditional away from not
    being empty. Two files that do different things cannot be collapsed by
    accident.

That is a security boundary, and merging it would trade a property the code HAS
for a property a conditional CLAIMS. So the loops stay apart. What actually
duplicated was this: six lines of store-a-turn-pair, written twice.

Which is exactly where the damage was. agent.py's copy stored Benham's own reply
as Tyler's message for twelve days (f06b79b, fixed in aacf21e); guest.py's copy
was fine - not by design, but because it happened to name a variable `raw`
instead of `text`. One shared implementation means the next bug of that shape
cannot be fixed in one place and left standing in the other.

SEPARATE FILES, SHARED LOGIC. Each caller brings its own path, so
agent_memory.json and guest_memory.json stay distinct on disk. That separation is
deliberate and load-bearing (guest.py: "a prefix means Tyler's history and a
guest's history are one typo apart; a different path is not"), and this module
takes the path as an argument precisely so it cannot erode it.

NO CACHE, which is a change from agent.py's old behaviour. It kept the whole
store in a module-level global, and that turned out to be a hazard rather than an
optimisation: repairing the corrupted file required stopping the bot first,
because the running process held a stale copy that would write back over any fix
made underneath it. guest.py never cached and never had the problem. Reading a
small JSON file per DM is not a cost worth that.
"""

import threading

from benham.core import jsonio


def is_echo_pair(user_turn, assistant_turn):
    """True if this stored pair is f06b79b damage - a reply saved as the user's message.

    Two shapes, because the reply is assembled and the clobbered variable was not:

    - **Exact.** One round produced text, so the reply is that single part and the
      overwritten variable is the same string. user == assistant.
    - **Suffix.** Several rounds produced text, so the reply is
      `"\\n\\n".join(parts)` while the clobbered variable held only the LAST part.
      The user turn is then the tail of the assistant turn, and equality misses it
      entirely - which it did, on the first pass of the repair.

    Matching the join boundary rather than a bare `endswith` is deliberate: a real
    message can coincidentally end with a short reply ("ok", "yes"), and dropping
    a genuine exchange to be thorough would be its own corruption.
    """
    if not (user_turn.get("role") == "user" and assistant_turn.get("role") == "assistant"):
        return False
    u, a = user_turn.get("content"), assistant_turn.get("content")
    if not isinstance(u, str) or not isinstance(a, str) or not u.strip():
        return False
    return a == u or a.endswith("\n\n" + u)


class TurnMemory:
    """One conversation store. Bring your own path and window.

    Instantiated per surface rather than shared, so the two stores stay two files.
    The lock is per-instance for the same reason: guest turns run in worker
    threads (bot.py hands respond() to asyncio.to_thread) while the owner path
    runs on the event loop, and they never touch the same file.
    """

    def __init__(self, path, history_turns):
        """`path` may be a string OR a zero-arg callable returning one.

        The callable form exists because the modules that own a store keep a
        MEMORY_FILE constant, and both the tests and any future caller reasonably
        expect rebinding that constant to redirect the store. Snapshotting the
        string at construction made rebinding a SILENT NO-OP - the store kept
        writing to the old path while the caller believed it had moved. Two tests
        caught it immediately, but a silent no-op on a path is precisely the shape
        of failure this codebase has been chasing all week, so the seam takes a
        callable rather than asking everyone to remember.
        """
        self._path = path
        self.history_turns = int(history_turns)
        self._lock = threading.Lock()

    @property
    def path(self):
        return self._path() if callable(self._path) else self._path

    @path.setter
    def path(self, value):
        self._path = value

    def history(self, key):
        """Stored turns for one conversation, oldest first. [] if none."""
        return self._read().get(key, [])

    def remember(self, key, user_text, assistant_text):
        """Append one completed exchange.

        Only completed TEXT PAIRS are stored - never the tool_use/tool_result
        rounds - for two reasons that both still hold:

        Correctness. The API requires alternating roles and rejects a tool_result
        whose tool_use is missing. A loop that ends on a tool round (max rounds
        hit, an exception, a restart mid-call) would leave history ending on a
        user turn, and the NEXT message would send two user turns back to back and
        400. Storing pairs makes that structurally impossible rather than relying
        on every exit path to clean up after itself.

        Cost. Tool results include whole channel reads. Re-sending those verbatim
        on every subsequent turn of a long phone conversation is a bill that
        compounds for context the model rarely needs twice.
        """
        if not (user_text and assistant_text):
            return
        with self._lock:
            mem = self._read()
            turns = list(mem.get(key, []))
            turns.append({"role": "user", "content": user_text})
            turns.append({"role": "assistant", "content": assistant_text})
            mem[key] = turns[-self.history_turns * 2:]
            jsonio.write_json(self.path, mem)

    def forget(self, key=None):
        """Drop one conversation, or all of them."""
        with self._lock:
            if key is None:
                jsonio.write_json(self.path, {})
                return
            mem = self._read()
            mem.pop(key, None)
            jsonio.write_json(self.path, mem)

    def all_keys(self):
        return sorted(self._read())

    def _read(self):
        data = jsonio.read_json(self.path, default={})
        return data if isinstance(data, dict) else {}
