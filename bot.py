"""
benham-bot — persistent Discord bot repurposed as a Claude-controlled send tool.

What it does:
  * Connects with the bot token (BOT_KEY in environ.env) and stays online.
  * On login, writes channels.json listing every guild + text channel it can see,
    so we can look up channel IDs to send to.
  * Every ~2s, polls ./outbox for *.json request files of the form
        { "channel_id": <int>, "content": "<text>" }
    sends each one to that channel, then moves the file to outbox/sent/
    (on success) or outbox/failed/ (on error), recording the result.

Reading:
  * Every message the bot can see is appended to inbox.jsonl (one JSON object per line):
        {ts, guild, channel, channel_id, author, author_id, is_self, content, message_id}
    Reading message text requires the privileged Message Content intent (enable it in the
    Discord Developer Portal -> Bot -> Message Content Intent).
  * On-demand backlog: drop a request with "action": "history" (see fetch.py) to pull the
    last N messages of a channel into the result file.

Send a message by dropping a request into ./outbox — use send.py for that.
Read recent messages by tailing inbox.jsonl, or pull backlog with fetch.py.
"""

import os
import json
import shutil
import traceback
from datetime import datetime, timezone

import discord
from discord.ext import tasks
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTBOX = os.path.join(BASE_DIR, "outbox")
SENT = os.path.join(OUTBOX, "sent")
FAILED = os.path.join(OUTBOX, "failed")
CHANNELS_FILE = os.path.join(BASE_DIR, "channels.json")
INBOX_FILE = os.path.join(BASE_DIR, "inbox.jsonl")

load_dotenv(os.path.join(BASE_DIR, "environ.env"))

for d in (OUTBOX, SENT, FAILED):
    os.makedirs(d, exist_ok=True)

# Read + write proxy: message_content is privileged — enable it in the Dev Portal
# (Bot -> Privileged Gateway Intents -> Message Content Intent) or on_message text is blank.
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


def log(msg):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    print(f"[{stamp}] {msg}", flush=True)


def dump_channels():
    """Write channels.json listing all guilds + text channels the bot can see."""
    data = []
    for guild in client.guilds:
        channels = []
        for ch in guild.text_channels:
            channels.append(
                {
                    "name": ch.name,
                    "id": ch.id,
                    "can_send": ch.permissions_for(guild.me).send_messages,
                }
            )
        data.append({"guild": guild.name, "guild_id": guild.id, "text_channels": channels})
    with open(CHANNELS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    log(f"Wrote {CHANNELS_FILE} ({len(data)} guild(s))")
    for g in data:
        log(f"  guild: {g['guild']} ({g['guild_id']}) — {len(g['text_channels'])} text channel(s)")


def record_message(message):
    """Append one message to inbox.jsonl."""
    rec = {
        "ts": message.created_at.isoformat(),
        "guild": message.guild.name if message.guild else None,
        "guild_id": message.guild.id if message.guild else None,
        "channel": getattr(message.channel, "name", str(message.channel)),
        "channel_id": message.channel.id,
        "author": str(message.author),
        "author_id": message.author.id,
        "is_self": message.author.id == (client.user.id if client.user else None),
        "content": message.content,
        "message_id": message.id,
    }
    with open(INBOX_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


@client.event
async def on_ready():
    log(f"Logged in as {client.user} (id {client.user.id})")
    dump_channels()
    if not poll_outbox.is_running():
        poll_outbox.start()


@client.event
async def on_message(message):
    rec = record_message(message)
    if not rec["is_self"]:
        log(f"inbox #{rec['channel']} <{rec['author']}>: {rec['content']!r}")


@tasks.loop(seconds=2)
async def poll_outbox():
    try:
        files = sorted(f for f in os.listdir(OUTBOX) if f.endswith(".json"))
    except FileNotFoundError:
        return
    for fname in files:
        path = os.path.join(OUTBOX, fname)
        result = {"processed_at": datetime.now(timezone.utc).isoformat()}
        try:
            with open(path, "r", encoding="utf-8") as f:
                req = json.load(f)
            action = req.get("action", "send")
            channel_id = int(req["channel_id"])
            channel = client.get_channel(channel_id)
            if channel is None:
                channel = await client.fetch_channel(channel_id)

            if action == "history":
                limit = int(req.get("limit", 20))
                msgs = []
                async for m in channel.history(limit=limit):
                    msgs.append(
                        {
                            "ts": m.created_at.isoformat(),
                            "author": str(m.author),
                            "author_id": m.author.id,
                            "content": m.content,
                            "message_id": m.id,
                        }
                    )
                msgs.reverse()  # oldest -> newest
                result.update(
                    {"status": "fetched", "request": req, "channel": str(channel), "messages": msgs}
                )
                _finish(path, fname, SENT, result)
                log(f"Fetched {len(msgs)} msg(s) from #{getattr(channel, 'name', channel_id)}")
            else:
                content = str(req["content"])
                sent = await channel.send(content)
                result.update(
                    {
                        "status": "sent",
                        "request": req,
                        "message_id": sent.id,
                        "channel": str(channel),
                    }
                )
                _finish(path, fname, SENT, result)
                log(f"Sent to #{getattr(channel, 'name', channel_id)}: {content!r}")
        except Exception as e:  # noqa: BLE001 — record everything, keep the loop alive
            result.update({"status": "failed", "error": f"{type(e).__name__}: {e}"})
            log(f"FAILED {fname}: {result['error']}")
            log(traceback.format_exc())
            _finish(path, fname, FAILED, result)


def _finish(path, fname, dest_dir, result):
    """Move the request file to dest_dir and write a sibling _result.json."""
    try:
        base = os.path.splitext(fname)[0]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(dest_dir, f"{base}_{stamp}.json")
        shutil.move(path, dest)
        with open(os.path.splitext(dest)[0] + "_result.json", "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
    except Exception:  # noqa: BLE001
        log("Could not archive request file:\n" + traceback.format_exc())


@poll_outbox.before_loop
async def before_poll():
    await client.wait_until_ready()


def main():
    token = os.environ.get("BOT_KEY")
    if not token:
        raise SystemExit("BOT_KEY not set — put it in environ.env as BOT_KEY=<token>")
    client.run(token)


if __name__ == "__main__":
    main()
