"""
wire.py - the last line between Benham's words and Discord.

Issue 11 (2026-10-19): the owner's legal name reached the tracker because no
code looked at outward words. issues.file_issue now screens before gh; this
module does the same for Discord, at the one layer every Discord path shares:
the library's own send and edit methods. install() wraps them once at boot, so
a reply, a DM, a guest answer, a nudge, an embed, a poll and a webhook post all
pass policy.screen_value whatever code produced them - including a path that
does not exist yet. Policy decides; this only enforces and records.

Every send that goes (and every send that is refused) is written to
outwardlog with its full text. That is also issue 10's fix: what_i_did reads
the record (selfrecord), so a message that left by a route which never passed
capabilities.run - the approved reply to a bot's mention, the closing message,
an owner-mention reply - is no longer invisible to "did you do X?".

The exemption: a DM whose only reader is the owner is ring 1 and passes
unscreened (it is still recorded). A DM channel whose recipient is unknown is
NOT assumed to be his - unknown is screened.

Test fakes do not subclass discord's classes, so the existing suite drives its
own sends untouched; tests/test_privacy_screen.py drives the real wrapped
methods with the HTTP layer stubbed.
"""

import discord
from discord.utils import MISSING

from benham.core import identity, outwardlog, policy

# Re-exported for capabilities.run; it lives in outwardlog so issues.py can
# read it without importing discord.
current_action = outwardlog.current_action

_ORIG = {}
_log = print


def _set(v):
    return v is not None and v is not MISSING


def _owner_only(target):
    """True only when the sole reader is the owner (his own DM)."""
    try:
        if isinstance(target, (discord.User, discord.Member)):
            return identity.is_owner(target.id)
        if isinstance(target, discord.DMChannel):
            r = target.recipient
            return r is not None and identity.is_owner(r.id)
    except Exception:  # noqa: BLE001 - unknown is screened, never exempt
        return False
    return False


def _label(target):
    try:
        if isinstance(target, str):
            return target
        if isinstance(target, (discord.User, discord.Member)):
            return f"dm user {target.id}"
        if isinstance(target, discord.DMChannel):
            r = target.recipient
            return f"dm {getattr(r, 'id', '?')} (channel {target.id})"
        g = getattr(target, "guild", None)
        return (f"#{getattr(target, 'name', '?')} ({getattr(g, 'name', '?')}) "
                f"{getattr(target, 'id', '?')}")
    except Exception:  # noqa: BLE001
        return repr(target)[:120]


def _poll_texts(poll):
    out = [str(getattr(poll, "question", "") or "")]
    for a in getattr(poll, "answers", None) or ():
        out.append(str(getattr(a, "text", "") or ""))
    return out


def _view_texts(view):
    out = []
    for item in getattr(view, "children", None) or ():
        for attr in ("label", "placeholder"):
            v = getattr(item, attr, None)
            if v:
                out.append(str(v))
        for opt in getattr(item, "options", None) or ():
            out.append(str(getattr(opt, "label", "") or ""))
    return out


def _file_names(kw):
    names = []
    f = kw.get("file")
    if _set(f):
        names.append(str(getattr(f, "filename", "") or ""))
    for key in ("files", "attachments"):
        for f in (kw.get(key) if _set(kw.get(key)) else None) or ():
            names.append(str(getattr(f, "filename", "") or ""))
    return names


def screen_payload(content, kw, terms=None):
    """(content, kw, decision, text) after the screen.

    Rewrites content, embeds and a webhook's thread name. Poll text, button
    labels and file names cannot be rewritten safely, so a personal term in any
    of them refuses the send outright."""
    terms = identity.privacy_terms() if terms is None else tuple(terms)
    kw = dict(kw)
    if not terms:
        # screen_text refuses an empty list; a payload with no text at all (a
        # bare file) must not slip past an unconfigured screen either.
        return content, kw, policy.screen_text("", terms)[1], ""
    texts = []
    rewrote = 0

    def note(decision):
        nonlocal rewrote
        if decision.rule == "privacy_rewrote":
            rewrote += 1
        return decision

    if _set(content):
        content, d = policy.screen_text(str(content), terms)
        if note(d).denied:
            return content, kw, d, content
        texts.append(content)
    if _set(kw.get("embed")):
        raw, d = policy.screen_value(kw["embed"].to_dict(), terms)
        if note(d).denied:
            return content, kw, d, "\n".join(texts + [str(raw)])
        kw["embed"] = discord.Embed.from_dict(raw)
        texts.append(str(raw))
    if _set(kw.get("embeds")):
        new = []
        for e in kw["embeds"]:
            raw, d = policy.screen_value(e.to_dict(), terms)
            if note(d).denied:
                return content, kw, d, "\n".join(texts + [str(raw)])
            new.append(discord.Embed.from_dict(raw))
            texts.append(str(raw))
        kw["embeds"] = new
    if _set(kw.get("thread_name")):
        kw["thread_name"], d = policy.screen_text(str(kw["thread_name"]), terms)
        if note(d).denied:
            return content, kw, d, "\n".join(texts + [kw["thread_name"]])
        texts.append(kw["thread_name"])
    check = []
    if _set(kw.get("poll")):
        check += _poll_texts(kw["poll"])
    if _set(kw.get("view")):
        check += _view_texts(kw["view"])
    check += _file_names(kw)
    for t in check:
        _, d = policy.screen_text(t, terms)
        if d.denied or d.rule == "privacy_rewrote":
            if not d.denied:
                d = policy._deny("privacy_unrewritable",
                                 "a personal term is in a poll, a button or a file "
                                 "name, which the screen cannot rewrite - refusing "
                                 "to send")
            return content, kw, d, "\n".join(texts + check)
    texts += check
    verdict = (policy.Decision(policy.Decision.ALLOW, f"{rewrote} part(s) rewritten",
                               "privacy_rewrote") if rewrote else policy._ALLOW)
    return content, kw, verdict, "\n".join(t for t in texts if t)


def _plain_text(content, kw):
    bits = [str(content)] if _set(content) else []
    embeds = ([kw["embed"]] if _set(kw.get("embed")) else []) + \
             (list(kw["embeds"]) if _set(kw.get("embeds")) else [])
    bits += [str(e.to_dict()) for e in embeds]
    return "\n".join(bits)


async def _guarded(kind, target, owner_only, call, content, kw):
    label = _label(target)
    via = current_action.get()
    if owner_only:
        msg = await call(content, kw)
        outwardlog.record("discord", label, _plain_text(content, kw), verdict="sent",
                          rule="owner_dm", via=via,
                          extra={"kind": kind, "message_id": getattr(msg, "id", None)})
        return msg
    content, kw, decision, text = screen_payload(content, kw)
    if decision.denied:
        _log(f"BLOCKED {kind} to {label} [rule={decision.rule}]: {decision.reason}")
        outwardlog.record("discord", label, text, verdict="blocked",
                          rule=decision.rule, via=via, extra={"kind": kind})
        raise policy.OutwardBlocked(decision)
    if decision.rule == "privacy_rewrote":
        _log(f"privacy screen: {decision.reason} in a {kind} to {label}")
    msg = await call(content, kw)
    outwardlog.record("discord", label, text, verdict="sent", rule=decision.rule,
                      via=via, extra={"kind": kind, "message_id": getattr(msg, "id", None)})
    return msg


def installed():
    return sorted(_ORIG)


def install(log=print):
    """Wrap discord's send/edit methods. Idempotent; returns the wrapped names."""
    global _log
    _log = log
    if _ORIG:
        return installed()

    orig_send = discord.abc.Messageable.send

    async def send(self, content=None, **kw):
        return await _guarded("send", self, _owner_only(self),
                              lambda c, k: orig_send(self, c, **k), content, kw)

    orig_edit = discord.Message.edit

    async def edit(self, **kw):
        content = kw.pop("content", MISSING)
        ch = getattr(self, "channel", None)

        async def call(c, k):
            if c is not MISSING:
                k = dict(k, content=c)
            return await orig_edit(self, **k)
        return await _guarded("edit", ch, _owner_only(ch), call, content, kw)

    orig_hook = discord.Webhook.send

    async def hook_send(self, content=MISSING, **kw):
        return await _guarded("webhook", f"webhook {getattr(self, 'id', '?')}", False,
                              lambda c, k: orig_hook(self, c, **k), content, kw)

    orig_resp = discord.InteractionResponse.send_message

    async def resp_send(self, content=None, **kw):
        return await _guarded("interaction", "interaction response", False,
                              lambda c, k: orig_resp(self, c, **k), content, kw)

    _ORIG.update({"Messageable.send": orig_send, "Message.edit": orig_edit,
                  "Webhook.send": orig_hook,
                  "InteractionResponse.send_message": orig_resp})
    discord.abc.Messageable.send = send
    discord.Message.edit = edit
    discord.Webhook.send = hook_send
    discord.InteractionResponse.send_message = resp_send
    return installed()
