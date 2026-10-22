#!/usr/bin/env bash
# Install / reinstall the bot on a fresh Ubuntu 24.04 server (DigitalOcean Droplet etc.)
#
#   curl -fsSL https://raw.githubusercontent.com/kn8508827-arch/Bot-Smos-Smea/main/deploy/install.sh | sudo bash
#
# It asks for the bot token, Gemini key and owner ID (typed on the server, never sent anywhere
# else), runs the bot 24/7 as a systemd service that restarts on crash or reboot, and backs up
# the database every day. Running it again keeps your settings and data.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/kn8508827-arch/Bot-Smos-Smea.git}"
BRANCH="${BRANCH:-main}"
APP_USER="coolerbot"
APP_DIR="/opt/coolerbot"
DATA_DIR="/var/lib/coolerbot"
ENV_FILE="/etc/coolerbot.env"
SERVICE="coolerbot"

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✔ %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m✘ %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run as root: curl ... | sudo bash"
command -v apt-get >/dev/null || die "This script is for Ubuntu/Debian."

ask() { # ask VAR "Question" [secret]
  local __var=$1 __q=$2 __secret=${3:-} __val=""
  while [ -z "$__val" ]; do
    if [ -n "$__secret" ]; then read -r -s -p "$__q: " __val </dev/tty; echo
    else read -r -p "$__q: " __val </dev/tty; fi
    __val="$(printf '%s' "$__val" | tr -d '[:space:]')"
  done
  printf -v "$__var" '%s' "$__val"
}

say "1/7 Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip sqlite3 ca-certificates cron >/dev/null
ok "packages ready"
# the code folder belongs to the bot user; let root's git work in it too
git config --system --get-all safe.directory 2>/dev/null | grep -qx "$APP_DIR" || git config --system --add safe.directory "$APP_DIR"

say "2/7 Swap (helps small 1 GB servers)"
if ! swapon --show | grep -q .; then
  fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  ok "1 GB swap added"
else ok "swap already present"; fi

say "3/7 App user and folders"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$DATA_DIR/backups"
chown -R "$APP_USER:$APP_USER" "$DATA_DIR"
ok "user $APP_USER, data in $DATA_DIR"

say "4/7 Downloading the bot code ($BRANCH)"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch -q origin "$BRANCH" && git -C "$APP_DIR" reset -q --hard "origin/$BRANCH"
else
  rm -rf "$APP_DIR"; git clone -q --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
ok "code at $(git -C "$APP_DIR" log -1 --format='%h %s')"

say "5/7 Python packages"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -q --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
ok "python packages installed"

say "6/7 Settings ($ENV_FILE)"
if [ -f "$ENV_FILE" ]; then
  ok "keeping existing settings (edit with: sudo nano $ENV_FILE)"
else
  echo "Paste each value and press Enter. Secrets are hidden while you type."
  ask BOT_TOKEN "Telegram BOT_TOKEN (from @BotFather)" secret
  ask GEMINI_API_KEY "GEMINI_API_KEY (from aistudio.google.com)" secret
  ask OWNER_ID "OWNER_ID (your Telegram user ID, numbers only)"
  case "$BOT_TOKEN" in *:*) ;; *) die "BOT_TOKEN looks wrong (it should contain ':')";; esac
  case "$OWNER_ID" in *[!0-9]*) die "OWNER_ID must be numbers only";; esac
  umask 077
  cat > "$ENV_FILE" <<EOF
# Bot settings — edit with: sudo nano $ENV_FILE  then: sudo systemctl restart $SERVICE
BOT_TOKEN=$BOT_TOKEN
GEMINI_API_KEY=$GEMINI_API_KEY
OWNER_ID=$OWNER_ID
ADMIN_IDS=
BOT_DB_PATH=$DATA_DIR/bot_database.db

# Features (true/false)
STUDIO_ENABLED=true
STUDIO_DAILY_LIMIT=3
DAILYCARE_ENABLED=false

# Leave the rest as defaults unless you know you need them
# AI_MODEL=gemini-2.0-flash
# WEB_APP_URL=https://team-smos-snea.lovable.app
EOF
  chown root:"$APP_USER" "$ENV_FILE"; chmod 640 "$ENV_FILE"
  unset BOT_TOKEN GEMINI_API_KEY
  ok "settings saved (only root and the bot can read them)"
fi

say "7/7 Service, update command and daily backup"
cat > "/etc/systemd/system/$SERVICE.service" <<EOF
[Unit]
Description=Cooler Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/.venv/bin/python bot.py
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=full
ReadWritePaths=$DATA_DIR $APP_DIR

[Install]
WantedBy=multi-user.target
EOF

cat > /usr/local/bin/coolerbot-update <<EOF
#!/usr/bin/env bash
# Pull the latest code from GitHub and restart the bot.
set -euo pipefail
[ "\$(id -u)" -eq 0 ] || exec sudo "\$0" "\$@"
sudo -u $APP_USER git -C $APP_DIR fetch -q origin $BRANCH
sudo -u $APP_USER git -C $APP_DIR reset -q --hard origin/$BRANCH
sudo -u $APP_USER $APP_DIR/.venv/bin/pip install -q -r $APP_DIR/requirements.txt
/usr/local/bin/coolerbot-backup
systemctl restart $SERVICE
echo "Updated to: \$(git -C $APP_DIR log -1 --format='%h %s')"
sleep 3; systemctl --no-pager --lines=5 status $SERVICE || true
EOF

cat > /usr/local/bin/coolerbot-backup <<EOF
#!/usr/bin/env bash
# Safe copy of the SQLite database; keeps the last 14 daily backups.
set -euo pipefail
DB=$DATA_DIR/bot_database.db
[ -f "\$DB" ] || { echo "no database yet"; exit 0; }
OUT=$DATA_DIR/backups/bot_database-\$(date +%Y%m%d-%H%M).db
python3 -c 'import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()' "\$DB" "\$OUT"
chown $APP_USER:$APP_USER "\$OUT"
ls -1t $DATA_DIR/backups/bot_database-*.db | tail -n +15 | xargs -r rm -f
echo "backup: \$OUT"
EOF

cat > /usr/local/bin/coolerbot-logs <<EOF
#!/usr/bin/env bash
# Live bot logs (Ctrl+C to stop)
exec journalctl -u $SERVICE -f -n 50
EOF
chmod 755 /usr/local/bin/coolerbot-update /usr/local/bin/coolerbot-backup /usr/local/bin/coolerbot-logs
echo "30 3 * * * root /usr/local/bin/coolerbot-backup >/dev/null 2>&1" > /etc/cron.d/coolerbot-backup

systemctl daemon-reload
systemctl enable -q "$SERVICE"
systemctl restart "$SERVICE"
sleep 6
if systemctl is-active -q "$SERVICE"; then
  ok "bot is running 🎉"
else
  journalctl -u "$SERVICE" -n 30 --no-pager || true
  die "bot did not start — see the log above"
fi

cat <<EOF

────────────────────────────────────────────
 ✅ Done! Open Telegram and send /start to your bot.

 Useful commands:
   coolerbot-update   pull new code from GitHub + restart
   coolerbot-logs     watch live logs (Ctrl+C to exit)
   coolerbot-backup   back up the database now (also daily at 03:30)
   sudo nano $ENV_FILE   edit settings, then: sudo systemctl restart $SERVICE
────────────────────────────────────────────
EOF
