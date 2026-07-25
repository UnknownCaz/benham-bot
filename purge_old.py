"""purge_old.py — one-shot: delete messages older than N days from a guild.

Logs in as Benham (BOT_KEY), purges every text channel in the target guild of
messages older than --days (default 7), prints a per-channel count, and exits.
Requires the bot to have Manage Messages in the target channels.

Usage:
    python purge_old.py [guild_id] [days]
Defaults: guild_id=736988645562646619 (Testing Server), days=7
"""
import os
import sys
import asyncio
from datetime import datetime, timezone, timedelta

import discord
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, "environ.env"))

GUILD_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 736988645562646619
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 7
TOKEN = os.environ["BOT_KEY"]

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


@client.event
async def on_ready():
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS)
        guild = client.get_guild(GUILD_ID) or await client.fetch_guild(GUILD_ID)
        print(f"Logged in as {client.user} | guild={guild.name} | "
              f"deleting messages before {cutoff.isoformat()} (older than {DAYS}d)")
        total = 0
        # fetch_channels works even if the cache isn't populated on a fresh login
        channels = [c for c in await guild.fetch_channels()
                    if isinstance(c, discord.TextChannel)]
        for ch in channels:
            try:
                deleted = await ch.purge(limit=None, before=cutoff, bulk=True)
                print(f"  #{ch.name}: deleted {len(deleted)}")
                total += len(deleted)
            except discord.Forbidden:
                print(f"  #{ch.name}: SKIPPED (missing Manage Messages)")
            except Exception as e:  # noqa: BLE001
                print(f"  #{ch.name}: ERROR {e}")
        print(f"DONE. Total deleted: {total}")
    finally:
        await client.close()


if __name__ == "__main__":
    client.run(TOKEN, log_handler=None)
