#!/usr/bin/env bash
# pumpwatch — VPS setup (Ubuntu 22.04/24.04).
#
# Run as root on a fresh server:
#   bash setup_vps.sh
#
# Safe to run again: it updates the code and rewrites the services
# without touching the database or the .env file.

set -euo pipefail

APP=/opt/pumpwatch
REPO=https://github.com/Artkill24/pumpwatch.git
USER_NAME=pumpwatch

echo "== packages"
apt-get update -qq
apt-get install -y -qq python3 python3-venv git sqlite3 ufw >/dev/null

echo "== user"
id "$USER_NAME" >/dev/null 2>&1 || \
  useradd --system --home "$APP" --shell /usr/sbin/nologin "$USER_NAME"

echo "== code"
if [ -d "$APP/.git" ]; then
  git -C "$APP" pull --ff-only
else
  git clone -q "$REPO" "$APP"
fi

echo "== python"
[ -d "$APP/venv" ] || python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q --upgrade pip
"$APP/venv/bin/pip" install -q -r "$APP/requirements.txt"

chown -R "$USER_NAME:$USER_NAME" "$APP"

echo "== services"
unit() {
  # $1 name, $2 working dir, $3 script, $4 description
  cat > "/etc/systemd/system/pumpwatch-$1.service" <<EOF
[Unit]
Description=pumpwatch $4
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
WorkingDirectory=$2
EnvironmentFile=$APP/.env
Environment=PUMPWATCH_DB=$APP/pumpwatch.db
Environment=PYTHONUNBUFFERED=1
ExecStart=$APP/venv/bin/python $3
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
}

unit collector "$APP"       main.py        "collector"
unit tracker   "$APP"       tracker.py     "curve tracker"
unit amm       "$APP/amm"   amm_tracker.py "AMM tracker"
unit web       "$APP/score" api.py         "web page and API"

systemctl daemon-reload
systemctl enable -q pumpwatch-collector pumpwatch-tracker pumpwatch-amm pumpwatch-web

echo "== firewall (SSH only)"
ufw allow OpenSSH >/dev/null
ufw --force enable >/dev/null

echo
if [ ! -f "$APP/.env" ]; then
  echo "Missing: $APP/.env"
  echo "Copy it from your PC, then run:  systemctl start pumpwatch-{collector,tracker,amm,web}"
elif [ ! -f "$APP/pumpwatch.db" ]; then
  echo "No database yet: a new one will be created on start."
  echo "To keep your existing data, copy pumpwatch.db first."
  echo "Then run:  systemctl start pumpwatch-{collector,tracker,amm,web}"
else
  systemctl restart pumpwatch-collector pumpwatch-tracker pumpwatch-amm pumpwatch-web
  echo "All services started."
fi
echo
echo "Status:  systemctl status 'pumpwatch-*'"
echo "Logs:    journalctl -u pumpwatch-collector -f"
