#!/bin/bash
# =============================================================================
# One-shot setup for running the Chrome Hearts drop monitor 24/7 on a Mac.
#
# Run on the SPARE Mac (Terminal):
#   curl -fsSL https://raw.githubusercontent.com/yjlee08/ChromeDrop/main/deploy/mac-setup.sh | bash
#
# No Xcode, Homebrew, or admin password needed. It:
#   - downloads the code (tarball, no git),
#   - installs `uv` and a modern Python just for this app,
#   - asks for your Telegram BOT_TOKEN / CHAT_ID and writes .env,
#   - installs a launchd agent that runs the bot under `caffeinate` (keeps the
#     Mac awake) and restarts it on crash/login.
#
# Re-running is safe: your .env and seen.json are preserved.
# =============================================================================
set -euo pipefail

APP_DIR="$HOME/ChromeDrop"
TARBALL="https://github.com/yjlee08/ChromeDrop/archive/refs/heads/main.tar.gz"
LABEL="com.chromedrop.monitor"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

echo "==> Downloading code to $APP_DIR"
TMP="$(mktemp -d)"
curl -fsSL "$TARBALL" | tar -xz -C "$TMP"
mkdir -p "$APP_DIR"
cp -R "$TMP"/ChromeDrop-*/. "$APP_DIR"/
rm -rf "$TMP"

echo "==> Installing uv (Python manager) if needed"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

echo "==> Setting up Python 3.12 + dependencies"
cd "$APP_DIR"
uv python install 3.12
uv venv --python 3.12 .venv
uv pip install --python "$APP_DIR/.venv/bin/python" -r requirements.txt

echo "==> Installing headless browser for the fallback path (optional)"
"$APP_DIR/.venv/bin/python" -m playwright install chromium \
  || echo "   (Playwright browser skipped — requests path still works.)"

if [ ! -f "$APP_DIR/.env" ]; then
  echo "==> Telegram credentials"
  printf "    BOT_TOKEN: "; read -r BT < /dev/tty
  printf "    CHAT_ID [2140967909]: "; read -r CI < /dev/tty
  CI="${CI:-2140967909}"
  cat > "$APP_DIR/.env" <<EOF
BOT_TOKEN=$BT
CHAT_ID=$CI
FETCH_STRATEGY=auto
DISCOVER_CATEGORIES=true
CHECK_INTERVAL=120
JITTER=30
PER_URL_DELAY=3
STATE_FILE=$APP_DIR/seen.json
LOG_FILE=$APP_DIR/ch_drop_bot.log
EOF
  chmod 600 "$APP_DIR/.env"
  echo "    Wrote $APP_DIR/.env"
else
  echo "==> Keeping existing .env"
fi

echo "==> Sending a Telegram test message"
"$APP_DIR/.venv/bin/python" "$APP_DIR/tg_setup.py" || true

echo "==> Installing launchd agent (caffeinate + auto-restart)"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/caffeinate</string>
        <string>-i</string>
        <string>-s</string>
        <string>$APP_DIR/.venv/bin/python</string>
        <string>$APP_DIR/ch_drop_bot.py</string>
    </array>
    <key>WorkingDirectory</key><string>$APP_DIR</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>30</integer>
    <key>StandardOutPath</key><string>$APP_DIR/launchd.out.log</string>
    <key>StandardErrorPath</key><string>$APP_DIR/launchd.err.log</string>
    <key>ProcessType</key><string>Background</string>
</dict>
</plist>
EOF

U="$(id -u)"
launchctl bootout "gui/$U/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$U" "$PLIST"
launchctl enable "gui/$U/$LABEL"

echo ""
echo "============================================================"
echo " Done. The monitor is running and will keep running 24/7."
echo " Watch it:   tail -f $APP_DIR/ch_drop_bot.log"
echo " Stop it:    launchctl bootout gui/$U/$LABEL"
echo " Reminder:   keep this Mac's lid OPEN and plugged in."
echo "============================================================"
