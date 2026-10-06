"""
answers.py - did a guest's message answer a question Benham asked them?

THE BUG THIS ENDS (the board's "bug that matters", measured 2026-09-27). Since
Phase B a guest's answer to an outreach question had no code path to the
question. Tyler's own answers bind in on_message, but a guest never reaches that
block, and the guest brain holds no tool to call answer_conversation with. The
binding used to happen in a PC session tailing inbox.jsonl, and that file moved
to the Mac with the bot. c40 (Draco, 2026-09-22) is the case: they answered
"yes", the brain sat out as it had been told to, and the answer was found by
hand five minutes later.

The fix is Tyler's own rule, applied to guests (INTENT 3.3, item 10 - "both,
reply binds and the model judges and tells me", reaffirmed 2026-10-05): a
Discord reply or a slot number binds in code (bot.bind_certain, the same block
his path runs), and anything else is JUDGED here and announced - a check mark
on their message and a quiet line in Tyler's DMs naming the question it was
taken for. Code never binds a plain message by itself; this file is the model.

WHY A SEPARATE CALL AND NOT THE GUEST BRAIN. guest.py's security story is that
its call passes no client tool, and the brain is also the thing a quiet mutes -
and the quiet is on precisely when outreach answers arrive. This call takes the
questions that were in front of the guest and their words, and returns ids.
Code filters those to the candidates it passed in. So the worst a guest can
talk it into is filing their own words as their answer to a question that was
asked of them, which is what binding is for. Nothing here runs a capability.

COST. One small call per guest message, and only while a question asked of that
guest can still take an answer - from delivery until the bank grace runs out,
under an hour per ask. A guest with nothing waiting on them never reaches it.
"""

import json
import re

from benham.core import msgparts
from benham.guest import guest

# The judge is a classification, not a conversation - the cheapest model is
# right even when guests chat on a bigger one. Overridable in control.json's
# guest block, next to `model`.
MODEL = guest._CFG.get("judge_model") or "claude-haiku-4-5"
MAX_TOKENS = 100
MAX_CHARS = 2000        # of their message; deciding needs the gist, not a novel
TIMEOUT = 30            # seconds; a slow judge must not hold the DM for minutes

_SYSTEM = (
    "You sort messages. You are not talking to anyone, and nothing you write is "
    "shown to a person. Benham, a Discord bot, asked someone one or more questions "
    "and is waiting for their answers. Read their new message and decide which of "
    "the questions it answers. Reply with JSON only."
)


def _prompt(text, questions):
    lines = [f"Benham asked them {len(questions)} question(s) and is waiting:", ""]
    for c in questions:
        lines.append(f"[{c['id']}] {str(c.get('question') or '')[:1200]}")
        lines.append("")
    lines.append("Their new message. They typed it rather than using Discord's "
                 "reply on the question, so nothing has matched it yet:")
    lines.append("")
    lines.append(msgparts.fence("their message", [str(text)[:MAX_CHARS]]))
    lines.append("")
    lines.append(
        "Which questions does the message answer? A short reply - \"yes\", "
        "\"sure\", \"no thanks\", \"done\", \"it works\" - answers a question it "
        "plainly responds to. A greeting, a new topic, or a question back about "
        "it does not. When unsure, it does not.")
    lines.append("")
    lines.append('Reply with JSON only: {"answers": ["c40"]} listing the ids it '
                 'answers, or {"answers": []}.')
    return "\n".join(lines)


def parse(raw, questions):
    """The ids out of the judge's reply, kept only if they were candidates.

    Fails toward [] on anything unexpected. An answer left unbound is still in
    inbox.jsonl and still on Tyler's screen; one bound to the wrong question is
    a quiet misfiling, which is the failure binding exists to prevent.
    """
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return []
    ids = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(ids, list):
        return []
    allowed = [str(c["id"]) for c in questions]
    out = []
    for i in ids:
        s = str(i).strip()
        if s in allowed and s not in out:
            out.append(s)
    return out


def judge(user_id, text, questions, log=None):
    """Which of `questions` does `text` answer? Returns conversation ids, maybe [].

    `questions` are the conversation dicts in front of this guest - delivered
    and still answerable. The caller binds what comes back with
    bound_by="judged" and announces it; this function only reads.
    """
    def _log(msg):
        if log:
            log(msg)

    if not questions or not str(text or "").strip():
        return []
    try:
        resp = guest._get_client().messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=_SYSTEM,
            messages=[{"role": "user", "content": _prompt(text, questions)}],
            timeout=TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001 - an unjudged answer stays unbound, never lost
        _log(f"guest answer judge failed for {user_id} ({type(e).__name__}: {e}) "
             "- left unbound")
        return []
    raw = "".join(getattr(b, "text", "") for b in resp.content
                  if getattr(b, "type", "") == "text")
    u = getattr(resp, "usage", None)
    if u is not None:
        # The shape usage.py already parses, so the judge shows up in its totals.
        _log(f"agent usage [guest-judge:{user_id}] "
             f"in={getattr(u, 'input_tokens', 0)} out={getattr(u, 'output_tokens', 0)} "
             f"model={MODEL}")
    ids = parse(raw, questions)
    _log(f"guest answer judge [{user_id}]: {ids or 'not an answer'} "
         f"(of {', '.join(str(c['id']) for c in questions)})")
    return ids
