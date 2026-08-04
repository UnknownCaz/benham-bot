"""read_history.py — one-shot, invisible, READ-ONLY message reader.

Logs in as Benham with an INVISIBLE presence (no green dot), lists the guilds it
is in, then reads the last N messages from every readable text channel in guilds
OTHER than the Testing Server, prints them, and exits. It sends NOTHING — there is
no send/edit/react/voice/on_message code in this process, so it cannot possibly
post or trigger an auto-reply. Nobody in the server sees Benham come online.

Usage:
    python -u read_history.py [limit]
Default: limit=100 per channel. Testing Server is always excluded.
"""
import os
import sys

import discord
from dotenv import load_dotenv

from benham import paths
load_dotenv(os.path.join(paths.CONFIG_DIR, "environ.env"))

TESTING_SERVER_ID = 736988645562646619  # excluded — we want the friend's server only
# limit arg: a number, or "all"/"0"/"full" for the entire channel history
_arg = sys.argv[1].lower() if len(sys.argv) > 1 else "100"
LIMIT = None if _arg in ("all", "0", "full", "none") else int(_arg)
TOKEN = os.environ["BOT_KEY"]

intents = discord.Intents.default()
intents.message_content = True  # privileged intent, already enabled app-side; needed to read .content
# status=invisible => Benham never shows online to server members
client = discord.Client(intents=intents, status=discord.Status.invisible)


@client.event
async def on_ready():
    try:
        print(f"=== Logged in as {client.user} (INVISIBLE) ===")
        guilds = list(client.guilds)
        print(f"Benham is in {len(guilds)} guild(s):")
        for g in guilds:
            tag = "  [TESTING — excluded]" if g.id == TESTING_SERVER_ID else ""
            print(f"  - {g.name} (id {g.id}){tag}")

        targets = [g for g in guilds if g.id != TESTING_SERVER_ID]
        if not targets:
            print("\nNO non-Testing guild found — the friend's server is NOT added yet. "
                  "Nothing to read.")
            return

        me = None
        for g in targets:
            me = g.me
            print(f"\n########## GUILD: {g.name} (id {g.id}) ##########")
            for ch in g.text_channels:
                perms = ch.permissions_for(g.me)
                if not (perms.view_channel and perms.read_message_history):
                    print(f"\n--- #{ch.name}: SKIPPED (no read access) ---")
                    continue
                print(f"\n--- #{ch.name} (last {LIMIT}, oldest→newest) ---")
                try:
                    msgs = [m async for m in ch.history(limit=LIMIT)]
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
                    print("  SKIPPED (Forbidden)")
                except Exception as e:  # noqa: BLE001
                    print(f"  ERROR: {e}")
        print("\n=== DONE (read-only; nothing was posted) ===")
    finally:
        await client.close()


if __name__ == "__main__":
    client.run(TOKEN, log_handler=None)
