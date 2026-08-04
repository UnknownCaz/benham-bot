"""catchup.py — invisible, READ-ONLY "catch me up on one channel".

Logs in as Benham with an INVISIBLE presence (no green dot), reads the last N messages of a
SINGLE channel by id, prints them oldest->newest, and exits. It sends NOTHING — there is no
send/edit/react/voice/on_message code in this process, so it cannot post or trigger an
auto-reply, and nobody in the server sees Benham come online.

Use it to get a quick read on a friend-server channel (e.g. Chillbar #general) so Claude can
give Tyler a short catch-up summary. Keep the read LIGHT: recent activity and vibe, not deep
profiles or sensitive personal content.

Usage:
    python -u catchup.py <channel_id> [limit]
Default: limit=40.
"""
import os
import sys

import discord
from dotenv import load_dotenv

# Discord messages contain emoji; a Windows console (cp1252) crashes on them. Force UTF-8 stdout
# with replacement so printing never dies on an un-encodable char (e.g. 🟢 in a watchdog alert).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from benham import paths
load_dotenv(os.path.join(paths.CONFIG_DIR, "environ.env"))

if len(sys.argv) < 2:
    print("Usage: python -u catchup.py <channel_id> [limit]", file=sys.stderr)
    raise SystemExit(2)
try:
    CHANNEL_ID = int(sys.argv[1])
except ValueError:
    print(f"channel_id must be an integer, got {sys.argv[1]!r}", file=sys.stderr)
    raise SystemExit(2)
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 40
TOKEN = os.environ["BOT_KEY"]

intents = discord.Intents.default()
intents.message_content = True  # privileged intent (already enabled app-side); needed for .content
# status=invisible => Benham never shows online to server members
client = discord.Client(intents=intents, status=discord.Status.invisible)


@client.event
async def on_ready():
    try:
        print(f"=== Logged in as {client.user} (INVISIBLE) ===")
        channel = client.get_channel(CHANNEL_ID)
        if channel is None:
            try:
                channel = await client.fetch_channel(CHANNEL_ID)
            except Exception as e:  # noqa: BLE001
                print(f"Could not access channel {CHANNEL_ID}: {e}")
                return
        guild_name = getattr(getattr(channel, "guild", None), "name", "?")
        chan_name = getattr(channel, "name", str(CHANNEL_ID))
        perms = channel.permissions_for(channel.guild.me) if getattr(channel, "guild", None) else None
        if perms is not None and not (perms.view_channel and perms.read_message_history):
            print(f"No read access to #{chan_name} in {guild_name}.")
            return

        print(f"--- {guild_name} / #{chan_name} (last {LIMIT}, oldest->newest) ---")
        try:
            msgs = [m async for m in channel.history(limit=LIMIT)]
            msgs.reverse()
            if not msgs:
                print("  (no messages)")
            for m in msgs:
                ts = m.created_at.strftime("%Y-%m-%d %H:%M")
                content = m.content or ""
                if m.attachments:
                    content += " " + " ".join(f"[attach:{a.filename}]" for a in m.attachments)
                if m.embeds and not content.strip():
                    content = f"[embed x{len(m.embeds)}]"
                print(f"  [{ts}] {m.author}: {content}")
        except discord.Forbidden:
            print("  SKIPPED (Forbidden - no read access)")
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR reading history: {type(e).__name__}: {e}")
        print("=== DONE (read-only; nothing was posted) ===")
    except Exception as e:  # noqa: BLE001
        print(f"=== on_ready ERROR: {type(e).__name__}: {e} ===")
    finally:
        await client.close()


if __name__ == "__main__":
    client.run(TOKEN, log_handler=None)
