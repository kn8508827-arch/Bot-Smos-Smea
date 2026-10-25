"""📰 TPNews alerts (/tpnews). Posts now come ONLY from the TPNews Facebook Page via fb_pages.py;
the Telegram-channel reader below is kept but no longer started.

Old notes: every new post on the TPNews Telegram channel pops up in Cooler Bot and the Hub.

Source: the channel's public page https://t.me/s/<TPNEWS_CHANNEL> (default TPNEWSREPORT), checked every
TPNEWS_POLL_MIN (5) minutes — no admin rights needed. If the bot is added as an admin of the channel,
posts also arrive instantly as channel_post updates.

People choose to receive it: /tpnews (or 🔔 in the Hub's TPNews card) toggles the alert for that chat.
The Hub shows the latest headlines (hub.personal_data → "tp").
"""
import html
import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CoolerBot/1.0; +https://t.me/TeamSmosSneabot)"}
MAX_PER_ROUND = 3


def channel():
    return (os.getenv("TPNEWS_CHANNEL", "") or "TPNEWSREPORT").lstrip("@").strip()


def post_link(pid):
    return f"https://t.me/{channel()}/{pid}"


@contextmanager
def _db(config):
    conn = sqlite3.connect(getattr(config, "BOT_DB_PATH", "bot_database.db"), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_schema(config):
    with _db(config) as c:
        c.execute("CREATE TABLE IF NOT EXISTS tp_posts (id INTEGER PRIMARY KEY, text TEXT, photo TEXT, ts INTEGER)")
        c.execute("CREATE TABLE IF NOT EXISTS tp_subs (chat_id INTEGER PRIMARY KEY, since INTEGER)")


def _clean(fragment):
    t = re.sub(r"<br\s*/?>", "\n", fragment or "")
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(t)).strip()


def parse_channel_page(page, name=None):
    """Posts on a t.me/s/<channel> page → [{id, text, photo, ts}] oldest first."""
    name = (name or channel()).lower()
    posts = []
    blocks = re.split(r'(?=<div class="tgme_widget_message [^"]*"[^>]*data-post=")', page or "")
    for b in blocks:
        m = re.search(r'data-post="([^"/]+)/(\d+)"', b)
        if not m or m.group(1).lower() != name:
            continue
        text = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', b, re.S)
        photo = re.search(r"tgme_widget_message_photo_wrap[^>]*background-image:url\('([^']+)'\)", b)
        when = re.search(r'<time datetime="([^"]+)"', b)
        ts = 0
        if when:
            try:
                ts = int(datetime.fromisoformat(when.group(1).replace("Z", "+00:00")).timestamp())
            except ValueError:
                ts = 0
        posts.append({"id": int(m.group(2)), "text": _clean(text.group(1)) if text else "",
                      "photo": photo.group(1) if photo else "", "ts": ts or int(time.time())})
    return sorted(posts, key=lambda p: p["id"])


def fetch_posts(client=None):
    c = client or httpx.Client(timeout=12, headers=HEADERS, follow_redirects=True)
    try:
        r = c.get(f"https://t.me/s/{channel()}")
        r.raise_for_status()
        return parse_channel_page(r.text)
    finally:
        if client is None:
            c.close()


def latest(config, n=3):
    """For the Hub: [[post id, short title, unix ts], ...] newest first."""
    try:
        with _db(config) as c:
            rows = c.execute("SELECT id, text, ts FROM tp_posts ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [[r["id"], (r["text"] or "📷").split("\n")[0][:70], r["ts"]] for r in rows]


def register(bot, config, fetch=None):
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    init_schema(config)
    fetch = fetch or fetch_posts

    def subscribed(chat_id):
        with _db(config) as c:
            return c.execute("SELECT 1 FROM tp_subs WHERE chat_id=?", (chat_id,)).fetchone() is not None

    def can_manage(message):
        chat = message.chat
        if getattr(chat, "type", None) == "private":
            return True
        try:
            return bot.get_chat_member(chat.id, message.from_user.id).status in ("administrator", "creator")
        except Exception:
            return False

    def post_markup(pid):
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("📖 អានពេញ", url=pid if str(pid).startswith("http") else post_link(pid)),
              InlineKeyboardButton("🔕 ឈប់ទទួល", callback_data="tpn:off"))
        return m

    @bot.message_handler(commands=["tpnews"])
    def tpnews_cmd(message):
        if not can_manage(message):
            bot.reply_to(message, "⛔ សម្រាប់ Admin Group ប៉ុណ្ណោះ")
            return
        cid = message.chat.id
        with _db(config) as c:
            if subscribed(cid):
                c.execute("DELETE FROM tp_subs WHERE chat_id=?", (cid,))
                bot.reply_to(message, "🔕 ឈប់ទទួលព័ត៌មាន TPNews ហើយ — វាយ /tpnews ម្ដងទៀតដើម្បីបើកវិញ")
                return
            c.execute("INSERT OR REPLACE INTO tp_subs(chat_id, since) VALUES (?, ?)", (cid, int(time.time())))
        bot.reply_to(message, "🔔 <b>បើកដំណឹង TPNews ហើយ!</b>\nរាល់ពេល TPNews ផុសព័ត៌មានថ្មី Bot នឹងផ្ញើមកភ្លាម 📰\n"
                              "🔕 /tpnews ម្ដងទៀត ដើម្បីឈប់", parse_mode="HTML")

    @bot.callback_query_handler(func=lambda c: (c.data or "") == "tpn:off")
    def tpn_off(call):
        with _db(config) as c:
            c.execute("DELETE FROM tp_subs WHERE chat_id=?", (call.message.chat.id,))
        bot.answer_callback_query(call.id, "🔕 ឈប់ទទួល TPNews ហើយ")

    def send_post(chat_id, p):
        text = p["text"] or ""
        head = "📰 <b>TPNews · ព័ត៌មានថ្មី</b>\n\n"
        body = html.escape(text[:700] + ("…" if len(text) > 700 else ""))
        if p.get("photo"):
            try:
                return bot.send_photo(chat_id, p["photo"], caption=(head + body)[:1024], parse_mode="HTML",
                                      reply_markup=post_markup(p["id"]))
            except Exception:
                pass
        return bot.send_message(chat_id, head + body, parse_mode="HTML", reply_markup=post_markup(p["id"]),
                                disable_web_page_preview=True)

    lock = threading.Lock()

    def store_new(posts):
        """Save posts, return the ones not seen before (oldest first)."""
        new = []
        with _db(config) as c:
            for p in posts:
                cur = c.execute("INSERT OR IGNORE INTO tp_posts(id, text, photo, ts) VALUES (?, ?, ?, ?)",
                                (p["id"], p["text"], p["photo"], p["ts"]))
                if cur.rowcount:
                    new.append(p)
        return new

    def announce(new):
        if not new:
            return 0
        with _db(config) as c:
            subs = [r["chat_id"] for r in c.execute("SELECT chat_id FROM tp_subs")]
        show = new[-MAX_PER_ROUND:]
        sent = 0
        for cid in subs:
            for p in show:
                try:
                    send_post(cid, p)
                    sent += 1
                except Exception as exc:
                    msg = str(exc).lower()
                    if any(k in msg for k in ("blocked", "kicked", "not found", "deactivated")):
                        with _db(config) as c:
                            c.execute("DELETE FROM tp_subs WHERE chat_id=?", (cid,))
                        break
                time.sleep(0.05)
        return sent

    def tick():
        with lock:
            try:
                posts = fetch()
            except Exception as exc:
                logger.info("TPNews fetch failed (%s)", type(exc).__name__)
                return 0
            with _db(config) as c:
                first_run = c.execute("SELECT COUNT(*) FROM tp_posts").fetchone()[0] == 0
            new = store_new(posts)
            return 0 if first_run else announce(new)    # first run: remember, don't flood

    def start_loop():
        def run():
            time.sleep(30)
            while True:
                tick()
                try:
                    minutes = max(2, int(os.getenv("TPNEWS_POLL_MIN", "5")))
                except ValueError:
                    minutes = 5
                time.sleep(minutes * 60)
        threading.Thread(target=run, name="tpnews-loop", daemon=True).start()

    def announce_fb(post):
        """A new post on the TPNews Facebook Page (from fb_pages) → /tpnews subscribers."""
        return announce([{"id": post["link"], "text": post["text"], "photo": post.get("picture", ""), "ts": post["ts"]}])

    return {"tick": tick, "start_loop": start_loop, "announce": announce, "store_new": store_new,
            "announce_fb": announce_fb}
