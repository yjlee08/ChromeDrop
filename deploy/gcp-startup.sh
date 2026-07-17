#!/bin/bash
# =============================================================================
# Google Cloud e2-micro startup script for the Chrome Hearts drop monitor.
#
# HOW TO USE:
#   1. Edit the two values in the CONFIG block below (your Telegram creds).
#   2. When creating the VM in the Google Cloud console, expand
#      "Advanced options" -> "Management" -> "Automation" and paste this whole
#      file into the "Startup script" box.
#   3. Create the VM. On first boot it installs everything and starts the bot
#      as a systemd service that survives reboots and crashes.
#
# It is safe to re-run (idempotent-ish): it will re-clone/update and restart.
# Runs as root on the VM.
# =============================================================================
set -euo pipefail

# ------------------------------- CONFIG --------------------------------------
BOT_TOKEN="__PASTE_YOUR_BOT_TOKEN__"
CHAT_ID="__PASTE_YOUR_CHAT_ID__"
# Fetch strategy on a datacenter IP: "auto" tries requests then Playwright.
# If you see repeated 403s in the logs, requests is being blocked from this IP.
FETCH_STRATEGY="auto"
# -----------------------------------------------------------------------------

APP_DIR=/opt/ChromeDrop
REPO=https://github.com/yjlee08/ChromeDrop.git

echo "[startup] adding swap (helps headless Chromium fit in 1GB RAM)"
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "[startup] installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git

echo "[startup] cloning/updating repo"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only || true
else
  git clone "$REPO" "$APP_DIR"
fi

echo "[startup] python deps"
cd "$APP_DIR"
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

echo "[startup] Playwright browser + OS deps (fallback path)"
./.venv/bin/python -m playwright install --with-deps chromium || \
  echo "[startup] WARNING: Playwright browser install failed; requests-only fallback."

echo "[startup] writing .env"
cat > "$APP_DIR/.env" <<EOF
BOT_TOKEN=${BOT_TOKEN}
CHAT_ID=${CHAT_ID}
FETCH_STRATEGY=${FETCH_STRATEGY}
DISCOVER_CATEGORIES=true
CHECK_INTERVAL=120
JITTER=30
PER_URL_DELAY=3
STATE_FILE=${APP_DIR}/seen.json
LOG_FILE=${APP_DIR}/ch_drop_bot.log
EOF
chmod 600 "$APP_DIR/.env"

echo "[startup] creating service user + systemd unit"
id chdrop >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin chdrop
# Playwright caches the browser under root's home during install; make it
# readable, and point the service at it.
PW_CACHE=/root/.cache/ms-playwright
chown -R chdrop:chdrop "$APP_DIR"

cat > /etc/systemd/system/ch-drop-bot.service <<EOF
[Unit]
Description=Chrome Hearts drop monitor -> Telegram alerts
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=chdrop
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
Environment=PLAYWRIGHT_BROWSERS_PATH=${PW_CACHE}
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/ch_drop_bot.py
Restart=always
RestartSec=15
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

# If Playwright installed the browser under root cache, let chdrop read it.
[ -d "$PW_CACHE" ] && chmod -R a+rX "$PW_CACHE" || true

systemctl daemon-reload
systemctl enable --now ch-drop-bot
echo "[startup] done. Check: journalctl -u ch-drop-bot -f"
