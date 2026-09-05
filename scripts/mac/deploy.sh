#!/bin/zsh
# deploy.sh - stand the Benham face up on cazzy-mac WITHOUT starting it (Phase B, brief step 2).
#
# Idempotent. Run as administrator on the Mac (scp it there; never paste it inline
# through PowerShell, which mangles quotes). It clones the repo at a pinned commit
# OUTSIDE the synced repo clone (~/benham-bot - the codex precedent: sync over a
# running service is the hazard), builds a venv on the system python 3.14, runs
# the suite there, vendors the exaroton skill module, creates the runtime dirs,
# and writes the plist to /Library/LaunchDaemons WITHOUT bootstrapping it.
#
# What it deliberately does NOT do - each is a separate, visible step:
#   - copy any secret: environ.env, control.json, exaroton .env, the token ride
#     scp from the PC (or are typed by Caz at step 6b);
#   - bootstrap the daemon (step 8, inside the down window);
#   - touch ~/codex-bot or com.caz.codex-bot.
#
#   ./deploy.sh <commit-sha>

set -eu
PIN="${1:?usage: deploy.sh <commit-sha>}"
DEST="$HOME/benham-bot"
REPO="git@github.com:UnknownCaz/benham-bot.git"
PY=/usr/local/bin/python3

echo "--- clone at $PIN -> $DEST"
if [ ! -d "$DEST/.git" ]; then
  git clone "$REPO" "$DEST"
fi
git -C "$DEST" fetch --quiet origin
git -C "$DEST" checkout --quiet "$PIN"
echo "pinned: $(git -C "$DEST" log --oneline -1)"

echo "--- venv on $($PY --version)"
if [ ! -x "$DEST/.venv/bin/python" ]; then
  "$PY" -m venv "$DEST/.venv"
fi
"$DEST/.venv/bin/pip" install --quiet --upgrade pip
"$DEST/.venv/bin/pip" install --quiet -r "$DEST/requirements.txt"
"$DEST/.venv/bin/python" -c 'import discord, anthropic, dotenv, requests; print("imports ok:", discord.__version__, anthropic.__version__)'

echo "--- runtime dirs"
mkdir -p "$DEST/logs" "$DEST/state" "$DEST/vendor/exaroton"
chmod 700 "$DEST/config" 2>/dev/null || true

echo "--- exaroton skill module (NOT its .env - that rides scp separately)"
# The codex clone already carries the vendored module at the same commit family;
# copy the code files from there so the two faces share one vendored copy shape.
for f in exaroton.py console.py SKILL.md .env.example; do
  if [ -f "$HOME/codex-bot/vendor/exaroton/$f" ]; then
    cp "$HOME/codex-bot/vendor/exaroton/$f" "$DEST/vendor/exaroton/$f"
  fi
done
ls -la "$DEST/vendor/exaroton" | grep -v "^total"

echo "--- suite (no secrets needed)"
cd "$DEST"
BENHAM_FACE= .venv/bin/python run_tests.py | tail -3

echo "--- plist (written, NOT bootstrapped)"
sudo cp "$DEST/scripts/mac/com.caz.benham-bot.plist" /Library/LaunchDaemons/com.caz.benham-bot.plist
sudo chown root:wheel /Library/LaunchDaemons/com.caz.benham-bot.plist
sudo chmod 644 /Library/LaunchDaemons/com.caz.benham-bot.plist
plutil -lint /Library/LaunchDaemons/com.caz.benham-bot.plist
sudo launchctl print system/com.caz.benham-bot >/dev/null 2>&1 && echo "WARNING: com.caz.benham-bot is already loaded" || echo "not loaded (as intended)"

echo "--- 8903 must be free"
if netstat -an | grep LISTEN | grep -q '\.8903 '; then echo "WARNING: something listens on 8903"; else echo "8903 free"; fi

echo "--- still to do by hand: scp environ.env (ANTHROPIC_API_KEY only for now), control.json, exaroton_watch.json, vendor/exaroton/.env (600); mint the token by starting the bot once at step 8; copy ~/.config/benham-bot.token to the PC."
