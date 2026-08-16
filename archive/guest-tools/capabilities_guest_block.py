# The seven guest capabilities, lifted verbatim from benham/core/capabilities.py
# when the guest tool loop was archived (2026-08-16). A record, not a module.

# ==========================================================================
# GUEST WORKSPACE (guest-refactor Stage 4). The only guest=True capabilities in
# the registry, and the registration invariant holds them to it: guest DM origin
# only, nothing outward, nothing posted, nothing confirmable, nothing Tyler's.
# File logic lives in guest_workspace.py; these handlers translate between the
# capability surface and that module, and re-raise its refusals in ActionError.
# Granting any of them is a control.json edit (guest.capabilities) + restart.
# ==========================================================================

def _ws(call, *args):
    """Run one guest_workspace function, translating its refusals."""
    from benham.guest import guest_workspace
    try:
        return getattr(guest_workspace, call)(*args)
    except guest_workspace.WorkspaceError as e:
        raise ActionError(str(e))


@action("ws_list", identity.READ,
        "List the files in your workspace and in the shared commons folder, "
        "with sizes and your remaining quota.",
        {}, origins={policy.Origin.GUEST_DM}, taints=True, guest=True)
async def _ws_list(ctx, p):
    return _ws("list_files", ctx.actor_id)


@action("ws_read", identity.READ,
        "Read one file from your workspace or the commons. Text comes back "
        "inline (truncated if huge); binary files report metadata - use "
        "ws_attach to receive one.",
        {"name": {"type": "str", "required": True, "desc": "Plain filename"},
         "area": {"type": "str", "desc": "'mine' (default) or 'commons'"}},
        origins={policy.Origin.GUEST_DM}, taints=True, guest=True)
async def _ws_read(ctx, p):
    return _ws("read_file", ctx.actor_id, p["name"], p.get("area") or "mine")


@action("ws_write", identity.MANAGE,
        "Create or overwrite one text file in your own workspace folder.",
        {"name": {"type": "str", "required": True,
                  "desc": "Plain filename - no folders, no runnable extensions"},
         "text": {"type": "str", "required": True}},
        origins={policy.Origin.GUEST_DM}, taints=True, guest=True)
async def _ws_write(ctx, p):
    return _ws("write_file", ctx.actor_id, p["name"], p["text"])


@action("ws_delete", identity.MANAGE,
        "Delete one file from your own workspace folder.",
        {"name": {"type": "str", "required": True}},
        origins={policy.Origin.GUEST_DM}, taints=True, guest=True)
async def _ws_delete(ctx, p):
    return _ws("delete_file", ctx.actor_id, p["name"])


@action("ws_import", identity.MANAGE,
        "Save the files attached to a message YOU sent in this DM into your "
        "workspace. Defaults to the message that started this turn.",
        {"message_id": {"type": "int",
                        "desc": "One of your earlier messages in this DM "
                                "(default: the current one)"},
         "index": {"type": "int", "desc": "Only this attachment, 0-based (default all)"}},
        origins={policy.Origin.GUEST_DM}, taints=True, guest=True)
async def _ws_import(ctx, p):
    # The read_attachments shape, plus one pinning rule of its own: no URL
    # parameter exists, the channel is the DM this turn arrived in (from the
    # CallContext, never a parameter - note the schema above has no channel_id),
    # and the message must be the guest's OWN. Worst case of a doctored
    # message_id is importing an older attachment from their own DM, which is a
    # feature: that channel contains only this guest and Benham.
    mid = p.get("message_id") or ctx.source_message_id
    if not mid:
        raise ActionError("ws_import has no message to import from on this surface")
    if ctx.source_channel_id is None:
        raise ActionError("ws_import needs the DM it was called from")
    m = await ctx.message(ctx.source_channel_id, mid)
    if int(m.author.id) != int(ctx.actor_id):
        raise ActionError("ws_import only takes attachments from YOUR OWN messages")

    atts = list(m.attachments)
    base = 0
    if p.get("index") is not None:
        i = int(p["index"])
        if not 0 <= i < len(atts):
            raise ActionError(f"index {i} is out of range - that message has "
                              f"{len(atts)} attachment(s)")
        atts, base = [atts[i]], i
    if not atts:
        return {"count": 0, "note": "that message has no attachments"}

    from benham.guest import guest_workspace
    out = []
    for offset, a in enumerate(atts):
        rec = {"index": base + offset, "filename": a.filename, "bytes": a.size}
        # Size from metadata, refused BEFORE the bandwidth is spent - the
        # read_attachments rule, and here it is also the quota rule.
        if a.size > guest_workspace.PER_FILE_BYTES:
            rec["skipped"] = (f"{a.size / 1048576:.1f}MB is over the per-file cap")
            out.append(rec)
            continue
        try:
            data = await a.read()
        except (discord.HTTPException, discord.NotFound) as e:
            rec["skipped"] = f"download failed: {getattr(e, 'text', None) or e}"
            out.append(rec)
            continue
        try:
            rec.update(_ws("import_bytes", ctx.actor_id, a.filename, data))
        except ActionError as e:
            rec["skipped"] = str(e)
        out.append(rec)
    return {"count": len(out), "imported": out}


@action("read_shared_channel", identity.READ,
        "Read recent messages from a channel the owner has shared with guests. "
        "Call with no channel_id to see which channels are shared.",
        {"channel": {"type": "str",
                     "desc": "A shared channel's NAME (e.g. 'benham-beta') or its id. "
                             "Omit to list what is shared."},
         "channel_id": {"type": "int", "desc": "The id, if you have it"},
         "limit": {"type": "int", "desc": "How many messages (default 20, max 50)"}},
        origins={policy.Origin.GUEST_DM}, taints=True, guest=True)
async def _read_shared_channel(ctx, p):
    # A SEPARATE capability, deliberately not a grant of read_channel. That one's
    # parameter space is every channel Benham can see, and scoping it per-caller
    # would put a guest branch inside the owner's path - the shape this codebase
    # exists to avoid. The serialisers are shared; the capability is not.
    shared = identity.guest_read_channels()
    if not shared:
        raise ActionError("no channels are shared with guests")

    def _named():
        out = []
        for c in sorted(shared):
            ch = ctx.client.get_channel(int(c))
            out.append({"channel_id": int(c),
                        "name": getattr(ch, "name", None) or "(unavailable)"})
        return out

    asked = p.get("channel_id") or p.get("channel")
    if not asked:
        # Discovery. Names only for channels already on the allowlist, so this
        # cannot be used to enumerate anything else Benham can see.
        out = _named()
        return {"count": len(out), "shared_channels": out}

    # A NAME is accepted as well as an id, and resolved only against the
    # allowlist - never against every channel Benham can see, which would turn
    # this into the enumeration tool the whole capability exists not to be.
    # Names matter because guest memory keeps text pairs and drops tool results
    # (guest._remember, by design), so an id learned from a discovery call is
    # gone by the next turn: Doom asked for a channel by name, the model no
    # longer had the id, guessed, was refused, and then told him the channel
    # was not shared at all. A name survives a turn boundary; an id does not.
    cid = None
    try:
        cid = int(str(asked).strip())
    except (TypeError, ValueError):
        want = str(asked).strip().lstrip("#").lower()
        for entry in _named():
            if entry["name"].lower() == want:
                cid = entry["channel_id"]
                break
        if cid is None:
            raise ActionError(
                f"no shared channel called {asked!r}. Shared right now: "
                + (", ".join(e["name"] for e in _named()) or "(none)"))
    # Checked BEFORE the channel is resolved, and the refusal names no channel
    # but the one asked for. Same rule as rule_destructive_guild: reporting what
    # is inside a channel you may not read is itself the leak.
    if cid not in shared:
        raise ActionError(f"channel {cid} is not shared with guests")

    ch = await ctx.channel(cid)
    limit = min(max(int(p.get("limit") or 20), 1), 50)
    msgs = [msg_dict(m) async for m in ch.history(limit=limit)]
    msgs.reverse()
    return {"channel": str(ch), "channel_id": ch.id, "count": len(msgs),
            "messages": msgs}


@action("ws_attach", identity.MANAGE,
        "Attach one of your workspace files to my reply, so you receive it as "
        "a Discord upload.",
        {"name": {"type": "str", "required": True}},
        origins={policy.Origin.GUEST_DM}, taints=True, guest=True)
async def _ws_attach(ctx, p):
    path = _ws("attach_path", ctx.actor_id, p["name"])
    if ctx.on_attach is None:
        raise ActionError("attachments cannot ride the reply on this surface")
    size = os.path.getsize(path)
    if size > MAX_UPLOAD_BYTES:
        raise ActionError(f"{p['name']!r} is {size / 1048576:.1f}MB; Discord's "
                          "limit is 25MB per message")
    ctx.on_attach(path)
    return {"status": "attached", "name": str(p["name"]), "bytes": size,
            "note": "it will arrive with this reply"}


