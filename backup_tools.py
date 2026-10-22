"""Database backup to the owner's Telegram chat (members, payments, points live in SQLite).

  /backup     owner/admin: send a consistent copy of the database now
  daily       at BACKUP_HOUR (Cambodia time, default 3) the copy goes to OWNER_ID
              (BACKUP_DAILY=false turns the daily copy off)

To restore: download the .db file from Telegram and replace BOT_DB_PATH on the server.
"""
import logging
import os
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
TZ_KH = timezone(timedelta(hours=7))


def make_copy(db_path):
    """Online-safe copy with sqlite's backup API. Returns the temp file path."""
    fd, path = tempfile.mkstemp(prefix="coolerbot-", suffix=".db")
    os.close(fd)
    src = sqlite3.connect(db_path, timeout=30)
    dst = sqlite3.connect(path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return path


def summary(path):
    con = sqlite3.connect(path)
    try:
        def count(sql):
            try:
                return con.execute(sql).fetchone()[0]
            except sqlite3.Error:
                return 0
        now = int(time.time())
        return (f"👤 users {count('SELECT COUNT(*) FROM users')} · "
                f"💳 active members {count(f'SELECT COUNT(*) FROM subscriptions WHERE kind=\"u\" AND paid_until>{now}')} · "
                f"👥 paid groups {count(f'SELECT COUNT(*) FROM subscriptions WHERE kind=\"g\" AND paid_until>{now}')} · "
                f"🧾 payments {count('SELECT COUNT(*) FROM sub_payments')}")
    finally:
        con.close()


def register(bot, config):
    def privileged(uid):
        return uid is not None and (uid == getattr(config, "OWNER_ID", 0) or uid in getattr(config, "ADMIN_IDS", []))

    def send_backup(chat_id, reason="manual"):
        db_path = getattr(config, "BOT_DB_PATH", "bot_database.db")
        path = make_copy(db_path)
        try:
            stamp = datetime.now(TZ_KH).strftime("%Y-%m-%d_%H%M")
            with open(path, "rb") as fh:
                bot.send_document(chat_id, fh, visible_file_name=f"coolerbot_{stamp}.db",
                                  caption=f"🗄️ Backup ({reason}) {stamp}\n{summary(path)}\n"
                                          "🔒 កុំ Share ឯកសារនេះ — មានទិន្នន័យសមាជិក")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    @bot.message_handler(commands=["backup"], func=lambda m: privileged(getattr(m.from_user, "id", None)))
    def backup_cmd(message):
        if getattr(message.chat, "type", None) != "private":
            bot.reply_to(message, "សូមប្រើ /backup ក្នុង Private Chat ប៉ុណ្ណោះ 🔒")
            return
        try:
            send_backup(message.chat.id)
        except Exception as exc:
            logger.warning("backup failed (%s)", type(exc).__name__)
            bot.reply_to(message, f"⚠️ Backup មិនបាន ({type(exc).__name__})")

    def start_daily():
        if os.getenv("BACKUP_DAILY", "true").strip().lower() in ("0", "false", "no", "off"):
            return None
        hour = int(os.getenv("BACKUP_HOUR", "3") or 3)

        def run():
            last_day = None
            while True:
                now = datetime.now(TZ_KH)
                owner = getattr(config, "OWNER_ID", 0)
                if owner and now.hour == hour and last_day != now.date():
                    last_day = now.date()
                    try:
                        send_backup(owner, "daily")
                    except Exception as exc:
                        logger.warning("daily backup failed (%s)", type(exc).__name__)
                time.sleep(300)
        t = threading.Thread(target=run, name="daily-backup", daemon=True)
        t.start()
        return t

    return start_daily
