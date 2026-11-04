"""
jobclose.py - the words a job's close is made of (INTENT decision 61, Stage 9 C).

Caz, 2026-11-03: "they use it, and then I get a response from Benham on what was
done and found with the tool, in the form of a friendly heads up, then I get
prompted to have the tool removed, or kept." And on the guest's side (his call 2
on the plan's forms): "You only; guest gets the normal closing line."

So three texts, all built here and nowhere else, all pure (no I/O, no model):

  headsup()   the DM to Caz on every close - done, quiet or by hand. Friendly,
              not a report dump: his goal in his words, who it was with, how it
              ended, what Benham did (messages, searches), what it found (a
              summary the caller already made - guest/jobdone.summarize), which
              tools it used. Ring 1: only Caz reads it.
  nudge()     the one check-in after a quiet day, to the person. Written so it
              fits a job that never searched. Never names the owner (it goes to a
              guest and passes the privacy screen like every guest send).
  CLOSE_LINE  Benham's normal friendly close to the person, sent only when the
              guest brain's own reply did not already wrap up. Nothing about the
              report - that is Caz's.

Plain hyphens, no em dashes: these are read as Benham, in Caz's DM and theirs.
"""

CLOSE_LINE = "Glad that's sorted - shout any time you need a hand with something else."

# Discord's hard cap is 2000; the heads-up stays well inside it so the cards that
# follow it are never pushed into a split message.
_GOAL_MAX = 300
_OUTCOME_MAX = 300
_FOUND_LINE_MAX = 300


def _cut(text, n):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 3].rstrip() + "..."


def nudge(name, job):
    """The one check-in after a quiet day. A job may carry its own (`nudge`,
    set by open_job); else this, which fits a job with or without searching."""
    own = str(job.get("nudge") or "").strip()
    if own:
        return own
    first = str(name or "").split(" (")[0].strip()
    hi = f"Hey {first}" if first else "Hey"
    return f"{hi} - checking back in on this one. Did it get sorted, or want me to keep going?"


def ended_words(job):
    """How it ended, in a few words, from the stored close."""
    how = job.get("ended")
    outcome = _cut(job.get("outcome"), _OUTCOME_MAX)
    if how == "them":
        return f"they said they're sorted - \"{outcome}\""
    if how == "quiet":
        if job.get("nudged"):
            return "it went quiet - I checked in once and heard nothing back for another day"
        return "it went quiet"
    by = job.get("closed_by") or "?"
    return f"closed by hand ({by}) - {outcome}" if outcome else f"closed by hand ({by})"


def tools_used(job):
    """The tools the job actually used beyond chat. Today: web search, when a
    search ran for it."""
    return ["web_search"] if int(job.get("searches") or 0) > 0 else []


def headsup(job, label, found):
    """The heads-up DM to Caz. `label` is who it was with (capabilities'
    _job_label); `found` is a list of up to three summary lines, or a string."""
    who = "you" if job.get("with") == "caz" else f"**{label}**"
    turns = int(job.get("turns") or 0)
    searches = int(job.get("searches") or 0)
    did = []
    if job.get("with") != "caz":
        did.append(f"{turns} message{'s' if turns != 1 else ''} from them")
    did.append(f"{searches} web search{'es' if searches != 1 else ''}" if searches
               else "no searches")
    if isinstance(found, str):
        found = [found]
    found = [_cut(f, _FOUND_LINE_MAX) for f in (found or []) if str(f).strip()][:3]
    used = tools_used(job)
    lines = [f"Heads-up - I wrapped up **{job['id']}** with {who}.",
             f"You asked: \"{_cut(job.get('goal'), _GOAL_MAX)}\"",
             f"How it ended: {ended_words(job)}.",
             f"What I did: {', '.join(did)}."]
    if found:
        lines.append("What I found:")
        lines.extend(f"> {f}" for f in found)
    else:
        lines.append("What I found: nothing searched.")
    lines.append(f"Tools it used: {'web search' if used else 'none beyond chat'}.")
    return "\n".join(lines)
