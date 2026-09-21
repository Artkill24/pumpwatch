#!/usr/bin/env bash
# pumpwatch — VPS setup (Ubuntu 22.04/24.04).
#
# Run as root on a fresh server:
#   sudo bash setup_vps.sh
#
# Works on Oracle Cloud (Ampere ARM or AMD micro), Hetzner,
# DigitalOcean, Aruba, OVH: anything running Ubuntu.
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
# The folder belongs to the service user, git runs as root: without this
# git refuses to update it ("dubious ownership") on every rerun.
git config --global --get-all safe.directory 2>/dev/null | grep -qx "$APP" || \
  git config --global --add safe.directory "$APP"
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

# collector_pp.py streams from PumpPortal and replaces both main.py
# and tracker.py, with no RPC calls.
unit collector "$APP"       collector_pp.py "collector (PumpPortal stream)"
unit amm       "$APP/amm"   amm_tracker.py  "AMM tracker"
unit web       "$APP/score" api.py          "web page and API"

# the old curve tracker is no longer needed
if [ -f /etc/systemd/system/pumpwatch-tracker.service ]; then
  systemctl disable --now -q pumpwatch-tracker || true
  rm -f /etc/systemd/system/pumpwatch-tracker.service
fi

systemctl daemon-reload
systemctl enable -q pumpwatch-collector pumpwatch-amm pumpwatch-web

echo "== firewall"
if systemctl is-enabled -q netfilter-persistent 2>/dev/null; then
  # Oracle Cloud images ship their own iptables rules (SSH only).
  # Enabling ufw on top would conflict with them.
  echo "   provider firewall detected (netfilter-persistent): leaving it as is"
else
  ufw allow OpenSSH >/dev/null
  ufw --force enable >/dev/null
  echo "   ufw enabled: SSH only"
fi

echo "== nightly database backup (keeps 7 days)"
mkdir -p "$APP/backups"
chown "$USER_NAME:$USER_NAME" "$APP/backups"
cat > /etc/cron.d/pumpwatch-backup <<EOF
# sqlite .backup is safe while the collector is writing
30 3 * * * $USER_NAME sqlite3 $APP/pumpwatch.db ".backup $APP/backups/pumpwatch-\$(date +\%F).db" && find $APP/backups -name 'pumpwatch-*.db' -mtime +7 -delete
EOF
chmod 644 /etc/cron.d/pumpwatch-backup

echo
if [ ! -f "$APP/.env" ]; then
  echo "Missing: $APP/.env"
  echo "Copy it from your PC, then run:  systemctl start pumpwatch-{collector,amm,web}"
elif [ ! -f "$APP/pumpwatch.db" ]; then
  echo "No database yet: a new one will be created on start."
  echo "To keep your existing data, copy pumpwatch.db first."
  echo "Then run:  systemctl start pumpwatch-{collector,amm,web}"
else
  systemctl restart pumpwatch-collector pumpwatch-amm pumpwatch-web
  echo "All services started."
fi
echo
echo "Status:  systemctl status 'pumpwatch-*'"
echo "Logs:    journalctl -u pumpwatch-collector -f"
