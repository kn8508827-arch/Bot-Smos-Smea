"""📘 Partner Facebook PAGES → Hub spotlight + alerts. Only Pages, never personal accounts.

How it connects (no web server needed):
  1. Railway variables FB_APP_ID and FB_APP_SECRET (Meta app "Cooler Hub").
  2. A partner gives the owner's Facebook account a small role on their Page (Page access → task access).
  3. Owner opens Graph API Explorer, picks the app, adds pages_show_list + pages_read_engagement,
     "Generate Access Token", and sends it to the bot: /fbconnect <token> (private chat, message is deleted).
     The bot swaps it for a long-lived token and keeps a Page token for every Page the owner can see.
  4. /fbpages: pick which partner each Page belongs to (TPNews, E1 Auto ...).
Every FB_POLL_MIN (5) minutes the bot reads the newest posts of each assigned Page. A new post:
  → goes into the Hub spotlight (hub.personal_data "sp"), newest partner on top, 48 h;
  → is sent to people who turned the partner alert on (/tpnews for TPNews).
"""
import html
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)
PARTNERS = {   # key: (name, Hub logo file)
    "tpnews": ("TPNews", "tpnews-logo.png"), "e1auto": ("E1 Auto", "e1auto-icon.png"),
    "xincheng": ("Xincheng", "xincheng-icon.png"), "vdaudio": ("VD Audio", "vdaudio-icon.png"),
    "betell": ("Digital", "betell-icon.png"),
}
SPOT_HOURS = 48


def graph_url(path):
    return f"https://graph.facebook.com/{os.getenv('FB_GRAPH_VERSION', 'v23.0')}/{path.lstrip('/')}"


def app_creds():
    return os.getenv("FB_APP_ID", "").strip(), os.getenv("FB_APP_SECRET", "").strip()


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
        c.execute("""CREATE TABLE IF NOT EXISTS fb_pages (page_id TEXT PRIMARY KEY, name TEXT, token TEXT,
            partner TEXT, updated INTEGER)""")
        c.execute("""CREATE TABLE IF NOT EXISTS fb_posts (post_id TEXT PRIMARY KEY, page_id TEXT, partner TEXT,
            text TEXT, link TEXT, picture TEXT, ts INTEGER)""")


def _get(path, params, client=None):
    c = client or httpx.Client(timeout=15)
    try:
        r = c.get(graph_url(path), params=params)
        data = r.json()
        if r.status_code >= 400 or "error" in data:
            raise RuntimeError((data.get("error") or {}).get("message", f"HTTP {r.status_code}"))
        return data
    finally:
        if client is None:
            c.close()


def connect(config, short_token, client=None):
    """Short user token → long-lived → Page tokens. Returns [(page_id, name)]."""
    app_id, secret = app_creds()
    if not app_id or not secret:
        raise RuntimeError("FB_APP_ID / FB_APP_SECRET មិនទាន់ដាក់នៅ Railway")
    long = _get("oauth/access_token", {"grant_type": "fb_exchange_token", "client_id": app_id,
                                       "client_secret": secret, "fb_exchange_token": short_token}, client)
    pages = _get("me/accounts", {"fields": "id,name,access_token", "limit": 100,
                                 "access_token": long["access_token"]}, client).get("data", [])
    now = int(time.time())
    with _db(config) as c:
        for p in pages:
            c.execute("""INSERT INTO fb_pages(page_id, name, token, partner, updated) VALUES (?, ?, ?, NULL, ?)
                ON CONFLICT(page_id) DO UPDATE SET name=excluded.name, token=excluded.token, updated=excluded.updated""",
                      (p["id"], p.get("name", ""), p["access_token"], now))
    return [(p["id"], p.get("name", "")) for p in pages]


def page_posts(page_id, token, client=None, limit=5):
    data = _get(f"{page_id}/posts", {"fields": "id,message,created_time,permalink_url,full_picture",
                                     "limit": limit, "access_token": token}, client).get("data", [])
    out = []
    for p in data:
        try:
            ts = int(datetime.strptime(p["created_time"], "%Y-%m-%dT%H:%M:%S%z").timestamp())
        except (KeyError, ValueError):
            ts = int(time.time())
        out.append({"id": p["id"], "text": (p.get("message") or "").strip(), "ts": ts,
                    "link": p.get("permalink_url") or f"https://www.facebook.com/{p['id']}",
                    "picture": p.get("full_picture") or ""})
    return out


def spotlight(config, now=None):
    """For the Hub: newest post per partner in the last 48 h, newest partner first.
    [[partner key, name, logo, short text, ts, link], ...]"""
    now = now or time.time()
    try:
        with _db(config) as c:
            rows = c.execute("""SELECT partner, text, ts, link FROM fb_posts p WHERE ts > ? AND ts = (
                SELECT MAX(ts) FROM fb_posts q WHERE q.partner = p.partner) ORDER BY ts DESC""",
                             (int(now - SPOT_HOURS * 3600),)).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for r in rows:
        name, logo = PARTNERS.get(r["partner"], (r["partner"], ""))
        out.append([r["partner"], name, logo, (r["text"] or "📷 រូបភាព / វីដេអូថ្មី").split("\n")[0][:60],
                    r["ts"], r["link"]])
    return out[:5]


def register(bot, config, on_new=None, client=None):
    """on_new(partner, post) is called for every new post (e.g. TPNews alerts)."""
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    init_schema(config)
    lock = threading.Lock()

    def owner(uid):
        return uid == getattr(config, "OWNER_ID", 0) or uid in getattr(config, "ADMIN_IDS", [])

    @bot.message_handler(commands=["fbconnect"], func=lambda m: owner(getattr(m.from_user, "id", None)))
    def fbconnect_cmd(message):
        if getattr(message.chat, "type", None) != "private":
            bot.reply_to(message, "🔒 សូមប្រើ /fbconnect ក្នុង Private Chat ប៉ុណ្ណោះ")
            return
        parts = (message.text or "").split(maxsplit=1)
        try:
            bot.delete_message(message.chat.id, message.message_id)      # the token never stays in the chat
        except Exception:
            pass
        if len(parts) < 2:
            bot.send_message(message.chat.id, "📘 <b>ភ្ជាប់ Facebook Page</b>\n"
                             "១. បើក developers.facebook.com/tools/explorer\n"
                             "២. Meta App = <b>Cooler Hub</b> · Permissions៖ pages_show_list, pages_read_engagement\n"
                             "៣. ចុច Generate Access Token → Copy\n"
                             "៤. ផ្ញើ <code>/fbconnect TOKEN</code> មកទីនេះ (Bot លុបសារភ្លាម)", parse_mode="HTML")
            return
        try:
            pages = connect(config, parts[1].strip(), client)
        except Exception as exc:
            bot.send_message(message.chat.id, f"❌ ភ្ជាប់មិនបាន៖ <code>{html.escape(str(exc)[:200])}</code>",
                             parse_mode="HTML")
            return
        names = "\n".join(f"• {html.escape(n)}" for _i, n in pages) or "(គ្មាន Page ទេ — ពិនិត្យសិទ្ធិលើ Page)"
        bot.send_message(message.chat.id, f"✅ ភ្ជាប់បាន {len(pages)} Page៖\n{names}\n\n👉 /fbpages ដើម្បីកំណត់ថា "
                                          "Page នីមួយៗជារបស់ដៃគូណា", parse_mode="HTML")

    def pages_view():
        with _db(config) as c:
            rows = [dict(r) for r in c.execute("SELECT page_id, name, partner FROM fb_pages ORDER BY name")]
        if not rows:
            return "📘 មិនទាន់មាន Page ទេ — /fbconnect", None
        lines, m = ["📘 <b>Facebook Pages</b> — ចុចដើម្បីកំណត់ដៃគូ", ""], InlineKeyboardMarkup()
        for r in rows:
            who = PARTNERS.get(r["partner"], ("—",))[0]
            lines.append(f"• {html.escape(r['name'])} → <b>{html.escape(who)}</b>")
            m.row(InlineKeyboardButton(f"⚙️ {r['name'][:30]}", callback_data=f"fbp:pick:{r['page_id']}"))
        return "\n".join(lines), m

    @bot.message_handler(commands=["fbpages"], func=lambda m: owner(getattr(m.from_user, "id", None)))
    def fbpages_cmd(message):
        text, m = pages_view()
        bot.reply_to(message, text, reply_markup=m, parse_mode="HTML")

    @bot.callback_query_handler(func=lambda c: (c.data or "").startswith("fbp:"))
    def fbp_cb(call):
        if not owner(call.from_user.id):
            bot.answer_callback_query(call.id, "⛔ សម្រាប់ Owner", show_alert=True)
            return
        parts = (call.data or "").split(":")
        if parts[1] == "pick" and len(parts) == 3:
            m = InlineKeyboardMarkup()
            for key, (name, _logo) in PARTNERS.items():
                m.row(InlineKeyboardButton(name, callback_data=f"fbp:set:{parts[2]}:{key}"))
            m.row(InlineKeyboardButton("🚫 មិនមែនដៃគូ", callback_data=f"fbp:set:{parts[2]}:-"))
            bot.answer_callback_query(call.id)
            bot.send_message(call.message.chat.id, "ជ្រើសដៃគូសម្រាប់ Page នេះ 👇", reply_markup=m)
            return
        if parts[1] == "set" and len(parts) == 4:
            with _db(config) as c:
                c.execute("UPDATE fb_pages SET partner=? WHERE page_id=?", (None if parts[3] == "-" else parts[3], parts[2]))
            bot.answer_callback_query(call.id, "✅ រួចរាល់")
            text, m = pages_view()
            bot.send_message(call.message.chat.id, text, reply_markup=m, parse_mode="HTML")
            threading.Thread(target=tick, daemon=True).start()

    def tick():
        with lock:
            with _db(config) as c:
                pages = [dict(r) for r in c.execute("SELECT * FROM fb_pages WHERE partner IS NOT NULL")]
            sent = 0
            for pg in pages:
                try:
                    posts = page_posts(pg["page_id"], pg["token"], client)
                except Exception as exc:
                    logger.warning("FB page %s failed (%s)", pg["name"], str(exc)[:150])
                    continue
                with _db(config) as c:
                    first = c.execute("SELECT COUNT(*) FROM fb_posts WHERE page_id=?", (pg["page_id"],)).fetchone()[0] == 0
                    new = []
                    for p in sorted(posts, key=lambda x: x["ts"]):
                        cur = c.execute("INSERT OR IGNORE INTO fb_posts VALUES (?, ?, ?, ?, ?, ?, ?)",
                                        (p["id"], pg["page_id"], pg["partner"], p["text"], p["link"], p["picture"], p["ts"]))
                        if cur.rowcount:
                            new.append(p)
                if first or on_new is None:
                    continue           # first read of a Page: remember, don't flood
                for p in new[-3:]:
                    try:
                        on_new(pg["partner"], p)
                        sent += 1
                    except Exception as exc:
                        logger.info("partner alert failed (%s)", type(exc).__name__)
            return sent

    def start_loop():
        def run():
            time.sleep(40)
            while True:
                try:
                    tick()
                except Exception as exc:
                    logger.warning("fb pages tick failed (%s)", type(exc).__name__)
                try:
                    minutes = max(2, int(os.getenv("FB_POLL_MIN", "5")))
                except ValueError:
                    minutes = 5
                time.sleep(minutes * 60)
        threading.Thread(target=run, name="fb-pages-loop", daemon=True).start()

    return {"tick": tick, "start_loop": start_loop}
