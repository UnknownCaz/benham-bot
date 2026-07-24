"""
webhook.py — post a message to a Discord channel via a saved webhook.

Webhooks are configured in webhooks.json (gitignored — holds secret URLs):
    {
      "default": "test",                     # which webhook to use when --target is omitted
      "webhooks": {
        "test": {
          "url": "https://discord.com/api/webhooks/<id>/<token>",
          "server": "Testing Server",        # human label: which server it posts to
          "channel": "#asd",                 # human label: which channel
          "note": "..."                      # freeform reminder
        }
      }
    }
To add a webhook, add another entry under "webhooks" with its own short name.

Usage:
    python webhook.py "hello world"                 # posts to the default webhook
    python webhook.py -t test "hello"               # posts to the named webhook "test"
    python webhook.py --url <url> "hello"           # posts to a raw URL (bypasses config)
    python webhook.py "hello" --username "AlertBot"  # override the displayed name
    python webhook.py --title "Deploy done" "v1.2 is live" --color 3066993   # rich embed
    python webhook.py --list                         # list configured webhooks (no URLs)

Exit code 0 on success (Discord returns HTTP 204).
"""

import os
import sys
import json
import argparse
import urllib.request
import urllib.error

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(BASE_DIR, "webhooks.json")


def load_config():
    if not os.path.exists(CONFIG):
        return {"default": None, "webhooks": {}}
    with open(CONFIG, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_url(cfg, target, raw_url):
    if raw_url:
        return raw_url, "(raw url)"
    hooks = cfg.get("webhooks", {})
    name = target or cfg.get("default")
    if not name or name not in hooks:
        avail = ", ".join(hooks) or "(none configured)"
        raise SystemExit(f"Unknown webhook {name!r}. Available: {avail}")
    entry = hooks[name]
    label = f"{name} -> {entry.get('server', '?')} {entry.get('channel', '')}".strip()
    return entry["url"], label


def build_payload(args):
    payload = {}
    if args.username:
        payload["username"] = args.username
    if args.title is not None:
        embed = {"title": args.title, "description": args.message or ""}
        if args.color is not None:
            embed["color"] = int(args.color)
        payload["embeds"] = [embed]
    else:
        payload["content"] = args.message
    return payload


def post(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            # Discord/Cloudflare rejects urllib's default UA (403 code 1010); send a real one.
            "User-Agent": "benham-bot-webhook/1.0 (+https://github.com/UnknownCaz/benham-bot)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def main(argv):
    p = argparse.ArgumentParser(description="Post to a Discord webhook.")
    p.add_argument("message", nargs="?", default="", help="message text (or embed description)")
    p.add_argument("-t", "--target", help="named webhook from webhooks.json (default: config default)")
    p.add_argument("--url", help="raw webhook URL, bypassing the config")
    p.add_argument("--username", help="override the displayed sender name")
    p.add_argument("--title", help="send as a rich embed with this title (message = description)")
    p.add_argument("--color", help="embed color as a decimal int (e.g. 3066993 = green)")
    p.add_argument("--list", action="store_true", help="list configured webhooks and exit")
    args = p.parse_args(argv[1:])

    cfg = load_config()

    if args.list:
        hooks = cfg.get("webhooks", {})
        print(f"default: {cfg.get('default')}")
        for name, e in hooks.items():
            print(f"  {name}: {e.get('server', '?')} {e.get('channel', '')} — {e.get('note', '')}")
        return 0

    if not args.message and args.title is None:
        print("Nothing to send. Provide a message (or --title for an embed).", file=sys.stderr)
        return 2

    url, label = resolve_url(cfg, args.target, args.url)
    status, body = post(url, build_payload(args))
    if status == 204:
        print(f"Delivered to {label} (HTTP 204)")
        return 0
    print(f"FAILED ({label}): HTTP {status} {body}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
