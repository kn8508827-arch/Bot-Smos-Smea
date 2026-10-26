"""💬 Cooler Bot in Facebook Messenger: the same Khmer AI chat, answering on the owner's Pages.

Setup (owner, once):
  Railway variables: FB_APP_ID, FB_APP_SECRET, MESSENGER_VERIFY_TOKEN (any secret word) — and a public
  domain for the service (Settings → Networking → Generate Domain; Railway then sets PORT).
  Meta app → Messenger → Webhook: callback URL https://<domain>/messenger/webhook + the verify token,
  fields: messages, messaging_postbacks.
  In the bot: /msgconnect <user token with pages_show_list, pages_messaging, pages_manage_metadata>
  → keeps a Page token for every Page and subscribes the app to each Page's messages.

A small web server runs next to the Telegram polling (thread). Requests are checked with the
X-Hub-Signature-256 header (FB_APP_SECRET). Replies use ai_assistant.ask_ai, so the daily AI limit applies.
"""
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import httpx

logger = logging.getLogger(__name__)
MAX_LEN = 1900
WELCOME = ("សួស្ដី! 👋 ខ្ញុំ Cooler Bot កូនប្រុសរបស់ Vid Cooler 😎\n"
           "សួរខ្ញុំបានគ្រប់រឿងជាភាសាខ្មែរ — ឬចូល Telegram ដើម្បីប្រើ AI គូររូប ហ្គេម ប្រតិទិន និងមុខងារជាច្រើនទៀត 🚀")
def ai_user_id(psid):
    """Messenger people get a NEGATIVE id (fits SQLite int64, never clashes with Telegram user ids)."""
    return -(10 ** 17 + int(psid) % 10 ** 17)


def graph(path):
    return f"https://graph.facebook.com/{os.getenv('FB_GRAPH_VERSION', 'v23.0')}/{path.lstrip('/')}"


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
        c.execute("CREATE TABLE IF NOT EXISTS msg_pages (page_id TEXT PRIMARY KEY, name TEXT, token TEXT, updated INTEGER)")
        c.execute("CREATE TABLE IF NOT EXISTS msg_seen (psid TEXT PRIMARY KEY, first INTEGER)")


def valid_signature(body, header, secret):
    if not secret or not header or not header.startswith("sha256="):
        return False
    want = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, header[7:])


def split_text(text, size=MAX_LEN):
    parts, text = [], (text or "").strip()
    while text:
        cut = text.rfind("\n", 0, size) if len(text) > size else len(text)
        cut = cut if cut > size // 2 else min(size, len(text))
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    return parts or ["🙂"]


def connect(config, short_token, client=None):
    """Short user token → long-lived → Page tokens; subscribe the app to each Page's messages."""
    app_id, secret = os.getenv("FB_APP_ID", "").strip(), os.getenv("FB_APP_SECRET", "").strip()
    if not app_id or not secret:
        raise RuntimeError("FB_APP_ID / FB_APP_SECRET មិនទាន់ដាក់នៅ Railway")
    c = client or httpx.Client(timeout=15)
    try:
        def get(path, params):
            r = c.get(graph(path), params=params)
            d = r.json()
            if r.status_code >= 400 or "error" in d:
                raise RuntimeError((d.get("error") or {}).get("message", f"HTTP {r.status_code}"))
            return d
        long = get("oauth/access_token", {"grant_type": "fb_exchange_token", "client_id": app_id,
                                          "client_secret": secret, "fb_exchange_token": short_token})["access_token"]
        pages = get("me/accounts", {"fields": "id,name,access_token", "limit": 100, "access_token": long}).get("data", [])
        done = []
        for p in pages:
            r = c.post(graph(f"{p['id']}/subscribed_apps"),
                       params={"subscribed_fields": "messages,messaging_postbacks", "access_token": p["access_token"]})
            ok = r.status_code < 400 and r.json().get("success", False)
            with _db(config) as db:
                db.execute("INSERT OR REPLACE INTO msg_pages(page_id, name, token, updated) VALUES (?, ?, ?, ?)",
                           (p["id"], p.get("name", ""), p["access_token"], int(time.time())))
            try:
                import hub as _hub
                set_profile(p["access_token"], _hub.hub_url(), "https://t.me/" + os.getenv("BOT_USERNAME", "TeamSmosSneabot"), c)
            except Exception as exc:
                logger.info("messenger menu skipped (%s)", type(exc).__name__)
            done.append((p.get("name", p["id"]), ok))
        return done
    finally:
        if client is None:
            c.close()


def messenger_note(username="TeamSmosSneabot"):
    """Tells the AI it is answering on a Facebook Page in Messenger: no Telegram /commands, real links instead."""
    try:
        import social_links
        links = "\n".join(f"- {label}: {url}" for _k, label, url in social_links.links())
    except Exception:
        links = ""
    return ("CHANNEL: You are answering inside Facebook Messenger on Vid Cooler's Facebook Page, not in Telegram. "
            "Telegram /commands (like /contact, /follow, /hub, /pay) do NOT work here — never tell people to type them. "
            "Messenger here supports text chat only. For images, games, the Khmer calendar, membership and all other "
            f"features, invite them to open Cooler Bot on Telegram: https://t.me/{username} . "
            "To contact or follow Vid Cooler, give these links directly:\n" + (links or f"- Telegram: https://t.me/{username}"))


CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "yescooler34@gmail.com")
LEGAL = {
    "privacy": ("Privacy Policy · គោលការណ៍ឯកជនភាព", """
<p>Cooler Bot (Telegram bot and the Facebook Page "Cooler Bot") is run by Vid Cooler, Cambodia.</p>
<h2>What we receive</h2><ul>
<li>Your name and the ID Telegram or Messenger gives the bot for you.</li>
<li>The messages, voice notes and photos you send the bot.</li>
<li>For paying members: membership dates and payment receipts you send.</li></ul>
<h2>How we use it</h2><ul>
<li>To answer you. Your message is sent to Google Gemini (AI) to write the reply.</li>
<li>To run membership, daily limits and the features you use. We never sell your data or use it for ads.</li></ul>
<h2>How long</h2><p>Chat history is kept only as long as needed to keep the conversation going; you can ask us to delete everything at any time (see Data deletion).</p>
<h2>ខ្មែរ</h2><p>Cooler Bot ទទួលបានតែឈ្មោះ លេខសម្គាល់ និងសារដែលអ្នកផ្ញើមក ដើម្បីឆ្លើយតបអ្នកប៉ុណ្ណោះ (AI របស់ Google Gemini ជួយសរសេរចម្លើយ)។ យើងមិនលក់ ឬចែកទិន្នន័យរបស់អ្នកដល់អ្នកផ្សេងទេ។ អ្នកអាចសុំលុបទិន្នន័យបានគ្រប់ពេល។</p>
"""),
    "data-deletion": ("Data deletion · ការលុបទិន្នន័យ", """
<p>To delete everything Cooler Bot keeps about you, email <a href="mailto:{email}">{email}</a>
with the subject <b>delete my data</b> and your Facebook or Telegram name.</p>
<p>We delete your data within 7 days and reply to confirm.</p>
<p>ដើម្បីលុបទិន្នន័យរបស់អ្នក សូម email ដាក់ចំណងជើង <b>delete my data</b> និងឈ្មោះ Facebook ឬ Telegram របស់អ្នក មក <a href="mailto:{email}">{email}</a>។ យើងនឹងលុបក្នុងរយៈពេល ៧ ថ្ងៃ។</p>
"""),
    "terms": ("Terms of Service · លក្ខខណ្ឌប្រើប្រាស់", """
<ul><li>Cooler Bot is an AI assistant; answers can be wrong — check important information yourself.</li>
<li>Do not use the bot for anything illegal, hateful or harmful. We may block accounts that do.</li>
<li>Paid membership lasts 1 month from payment and is approved by the owner.</li></ul>
<p>Cooler Bot ជាជំនួយការ AI ចម្លើយអាចខុសបាន។ ហាមប្រើសម្រាប់រឿងខុសច្បាប់។ សមាជិកភាពមានសុពលភាព ១ ខែ គិតពីថ្ងៃបង់។</p>
"""),
}


def legal_page(name):
    title, body = LEGAL[name]
    body = body.replace("{email}", CONTACT_EMAIL)
    return (f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Cooler Bot — {title}</title>
<style>body{{font-family:system-ui,'Kantumruy Pro',sans-serif;max-width:720px;margin:0 auto;padding:24px 16px;line-height:1.6;
color:#1b2340;background:#f6f8ff}}h1{{font-size:24px}}h2{{font-size:18px;margin-top:22px}}a{{color:#2563eb}}</style></head>
<body><h1>😎 Cooler Bot — {title}</h1>{body}<p style="color:#667;font-size:13px;margin-top:30px">
Contact: <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a></p></body></html>""").encode("utf-8")


# ---------- Messenger "app" look: Get Started, greeting, ice breakers, menu ----------
ICE = {   # payload: (button text, what the bot is asked)
    "ICE_HELLO": ("👋 Cooler Bot ធ្វើអ្វីបានខ្លះ?", "សួស្ដី Cooler Bot! ប្រាប់ខ្ញុំខ្លីៗថាអ្នកជួយអ្វីបានខ្លះ"),
    "ICE_RAP": ("🎤 សរសេរ Rap ឱ្យខ្ញុំ", "សរសេរ Rap ខ្មែរខ្លីមួយ ៨ ឃ្លា ប្រធានបទ ជីវិត និងការតស៊ូ"),
    "ICE_IDEA": ("💡 គំនិតរកលុយអនឡាញ", "ផ្ដល់គំនិតរកលុយអនឡាញ ៥ យ៉ាង ដែលធ្វើបាននៅកម្ពុជា"),
    "ICE_JOKE": ("😂 និយាយលេងសើចមួយ", "និយាយរឿងកំប្លែងខ្មែរខ្លីមួយ ដែលសមរម្យសម្រាប់គ្រប់វ័យ"),
}
GREETING = "សួស្ដី {{user_first_name}}! 👋 ខ្ញុំ Cooler Bot 😎 AI និយាយខ្មែរ ២៤ ម៉ោង · Rap · ហ្គេម · Cooler Hub"


def profile_body(hub, telegram):
    """Body for POST /me/messenger_profile — makes the Page chat feel like an app."""
    from urllib.parse import urlparse as _u
    hub_origin = "{0.scheme}://{0.netloc}".format(_u(hub))
    return {
        "get_started": {"payload": "GET_STARTED"},
        "greeting": [{"locale": "default", "text": GREETING[:160]}],
        "ice_breakers": [{"locale": "default", "call_to_actions": [
            {"question": q, "payload": k} for k, (q, _) in ICE.items()]}],
        "persistent_menu": [{"locale": "default", "composer_input_disabled": False, "call_to_actions": [
            {"type": "web_url", "title": "🚀 បើក Cooler Hub", "url": hub, "webview_height_ratio": "full"},
            {"type": "postback", "title": "🎤 សរសេរ Rap", "payload": "ICE_RAP"},
            {"type": "web_url", "title": "📲 Cooler Bot ក្នុង Telegram", "url": telegram},
        ]}],
        "whitelisted_domains": [hub_origin],
    }


def hub_card(hub, telegram):
    return {"attachment": {"type": "template", "payload": {"template_type": "generic", "elements": [{
        "title": "🚀 Cooler Hub — គ្រប់យ៉ាងក្នុងមួយ",
        "subtitle": "ហ្គេម · ព័ត៌មាន · ដៃគូ · AI Studio — ចុចបើកបានភ្លាម",
        "default_action": {"type": "web_url", "url": hub, "webview_height_ratio": "full"},
        "buttons": [{"type": "web_url", "url": hub, "title": "🚀 បើក Hub", "webview_height_ratio": "full"},
                    {"type": "web_url", "url": telegram, "title": "📲 មុខងារពេញ (Telegram)"}]}]}}}


def set_profile(page_token, hub, telegram, client=None):
    c = client or httpx.Client(timeout=15)
    try:
        r = c.post(graph("me/messenger_profile"), params={"access_token": page_token}, json=profile_body(hub, telegram))
        if r.status_code >= 400:
            logger.warning("messenger_profile failed: %s", r.text[:300])
        return r.status_code < 400
    finally:
        if client is None:
            c.close()


def register(bot, config, ask=None, client=None):
    """ask(user_id:int, text, name) -> reply text (defaults to ai_assistant.ask_ai)."""
    init_schema(config)
    if ask is None:
        def ask(uid, text, name):
            import ai_assistant
            import async_runner
            return async_runner.run_sync(ai_assistant.ask_ai(user_id=uid, user_message=text, user_name=name,
                                                             extra_instruction=messenger_note()), timeout=90)

    def owner(uid):
        return uid == getattr(config, "OWNER_ID", 0) or uid in getattr(config, "ADMIN_IDS", [])

    def page_token(page_id):
        with _db(config) as c:
            r = c.execute("SELECT token FROM msg_pages WHERE page_id=?", (page_id,)).fetchone()
        return r["token"] if r else None

    def send(page_id, psid, message, action=None):
        token = page_token(page_id)
        if not token:
            logger.warning("messenger: no token for page %s — run /msgconnect again", page_id)
            return False
        body = {"recipient": {"id": psid}}
        if action:
            body["sender_action"] = action
        else:
            body["messaging_type"] = "RESPONSE"
            body["message"] = message
        c = client or httpx.Client(timeout=15)
        try:
            r = c.post(graph("me/messages"), params={"access_token": token}, json=body)
            if r.status_code >= 400:
                logger.warning("messenger send failed: %s", r.text[:300])
            if r.status_code < 400:
                logger.info("messenger sent to page %s", page_id)
            return r.status_code < 400
        finally:
            if client is None:
                c.close()

    def telegram_button(text):
        username = "TeamSmosSneabot"
        try:
            username = bot.get_me().username or username
        except Exception:
            pass
        return {"attachment": {"type": "template", "payload": {"template_type": "button", "text": text[:640],
                "buttons": [{"type": "web_url", "url": f"https://t.me/{username}", "title": "🚀 Cooler Bot (Telegram)"}]}}}

    def tg_link():
        username = "TeamSmosSneabot"
        try:
            username = bot.get_me().username or username
        except Exception:
            pass
        return f"https://t.me/{username}"

    def hub_link():
        try:
            import hub as _hub
            return _hub.hub_url()
        except Exception:
            return "https://kn8508827-arch.github.io/Blue-Gold-Rush/hub/"

    def first_time(psid):
        with _db(config) as c:
            return c.execute("INSERT OR IGNORE INTO msg_seen(psid, first) VALUES (?, ?)",
                             (psid, int(time.time()))).rowcount == 1

    def handle_event(page_id, ev):
        psid = (ev.get("sender") or {}).get("id")
        logger.info("messenger event: page=%s keys=%s", page_id, ",".join(sorted(ev.keys())))
        if not psid or psid == page_id:
            return
        msg = ev.get("message") or {}
        if msg.get("is_echo"):
            return
        text = (msg.get("text") or "").strip()
        payload = (ev.get("postback") or {}).get("payload") or (msg.get("quick_reply") or {}).get("payload") or ""
        new = first_time(psid)
        if new or payload == "GET_STARTED":
            send(page_id, psid, telegram_button(WELCOME))
            send(page_id, psid, hub_card(hub_link(), tg_link()))
        if payload in ICE:
            text = ICE[payload][1]
        elif payload == "OPEN_HUB":
            send(page_id, psid, hub_card(hub_link(), tg_link()))
            return
        elif payload:
            return
        if text.lower() in ("hub", "/hub", "ហាប់", "menu", "ម៉ឺនុយ"):
            send(page_id, psid, hub_card(hub_link(), tg_link()))
            return
        if not text:
            if not new and msg.get("attachments"):
                send(page_id, psid, {"text": "🙂 ពេលនេះខ្ញុំអានបានតែអក្សរនៅ Messenger — សូមវាយសំណួរមក ✍️"})
            return
        send(page_id, psid, None, action="typing_on")
        try:
            reply = ask(ai_user_id(psid), text[:2000], None)
        except Exception as exc:
            logger.warning("messenger AI failed (%s)", type(exc).__name__)
            reply = "😔 ខ្ញុំរវល់បន្តិច — សូមសាកម្ដងទៀតបន្តិចទៀត 🙏"
        for part in split_text(reply):
            send(page_id, psid, {"text": part})

    def handle_body(body):
        data = json.loads(body.decode("utf-8") or "{}")
        logger.info("messenger webhook: object=%s entries=%d", data.get("object"), len(data.get("entry", [])))
        if data.get("object") != "page":
            return
        for entry in data.get("entry", []):
            for ev in entry.get("messaging", []):
                threading.Thread(target=handle_event, args=(entry.get("id"), ev), daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, text="ok"):
            out = text.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def _send_bytes(self, code, out, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(out)))
            self.send_header("Cache-Control", "no-store")
            # ICOM page also lives on the Hub's site (GitHub Pages) and calls /icom/* from there.
            # Safe to allow any origin: every call is authorised by Telegram's signed initData, no cookies.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(out)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path.rstrip("/") == "/icom":          # 🎙️ ICOM page for the Cooler Hub (icom_live.py)
                import icom_live
                return self._send_bytes(200, icom_live.page_html(), "text/html; charset=utf-8")
            if u.path.rstrip("/") in ("/privacy", "/data-deletion", "/terms"):   # pages Meta asks for (App settings → Basic)
                return self._send_bytes(200, legal_page(u.path.rstrip("/")[1:]), "text/html; charset=utf-8")
            if u.path.rstrip("/") == "/messenger/webhook":
                q = parse_qs(u.query)
                want = os.getenv("MESSENGER_VERIFY_TOKEN", "").strip()
                if want and q.get("hub.mode", [""])[0] == "subscribe" and \
                        hmac.compare_digest(q.get("hub.verify_token", [""])[0], want):
                    return self._send(200, q.get("hub.challenge", [""])[0])
                return self._send(403, "forbidden")
            return self._send(200, "Cooler Bot is running")

        def do_OPTIONS(self):                      # CORS preflight for /icom/* from the Hub site
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "86400")
            self.end_headers()

        def do_POST(self):
            if urlparse(self.path).path.startswith("/icom/"):    # 🎙️ ICOM: token / members / ring
                import icom_live
                body = self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 20_000))
                try:
                    out = icom_live.handle_post(urlparse(self.path).path, config, body, bot)
                except Exception as exc:
                    logger.warning("icom error (%s)", type(exc).__name__)
                    out = (500, {"error": "server"})
                code, data = out or (404, {"error": "not_found"})
                return self._send_bytes(code, json.dumps(data, ensure_ascii=False).encode(), "application/json")
            if urlparse(self.path).path.rstrip("/") != "/messenger/webhook":
                return self._send(404, "not found")
            body = self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 1_000_000))
            if not valid_signature(body, self.headers.get("X-Hub-Signature-256"), os.getenv("FB_APP_SECRET", "").strip()):
                logger.warning("messenger webhook: BAD SIGNATURE — FB_APP_SECRET on Railway does not match the Meta app secret")
                return self._send(403, "bad signature")
            self._send(200, "EVENT_RECEIVED")      # answer Meta fast, work in the background
            try:
                handle_body(body)
            except Exception as exc:
                logger.warning("messenger webhook error (%s)", type(exc).__name__)

    def start_server():
        port = os.getenv("PORT", "").strip()
        if not port:
            logger.info("messenger: no PORT (no public domain yet) — webhook server not started")
            return None
        srv = ThreadingHTTPServer(("0.0.0.0", int(port)), Handler)
        threading.Thread(target=srv.serve_forever, name="messenger-webhook", daemon=True).start()
        logger.info("messenger webhook listening on :%s", port)
        return srv

    @bot.message_handler(commands=["msgmenu"])
    def msgmenu_cmd(message):
        if getattr(message.chat, "type", None) != "private" or not owner(message.from_user.id):
            return
        with _db(config) as c:
            pages = c.execute("SELECT name, token FROM msg_pages").fetchall()
        lines = [f"{'✅' if set_profile(t, hub_link(), tg_link(), client) else '⚠️'} {n}" for n, t in pages] or ["(គ្មាន Page — /msgconnect សិន)"]
        bot.send_message(message.chat.id, "📱 Messenger Menu + Hub៖\n" + "\n".join(lines))

    @bot.message_handler(commands=["msgconnect"], func=lambda m: owner(getattr(m.from_user, "id", None)))
    def msgconnect_cmd(message):
        if getattr(message.chat, "type", None) != "private":
            return
        parts = (message.text or "").split(maxsplit=1)
        try:
            bot.delete_message(message.chat.id, message.message_id)   # the token never stays in the chat
        except Exception:
            pass
        if len(parts) < 2:
            bot.send_message(message.chat.id, "💬 <b>ភ្ជាប់ Messenger</b>\nGraph API Explorer → App <b>Cooler Hub</b> → "
                             "permissions៖ pages_show_list, pages_messaging, pages_manage_metadata → Generate Access Token\n"
                             "រួចផ្ញើ <code>/msgconnect TOKEN</code> (Bot លុបសារភ្លាម)", parse_mode="HTML")
            return
        try:
            done = connect(config, parts[1].strip(), client)
        except Exception as exc:
            bot.send_message(message.chat.id, f"❌ ភ្ជាប់មិនបាន៖ {str(exc)[:200]}")
            return
        lines = [f"{'✅' if ok else '⚠️'} {name}" for name, ok in done] or ["(គ្មាន Page)"]
        bot.send_message(message.chat.id, "💬 Messenger Pages៖\n" + "\n".join(lines) +
                         "\n\n✅ = Cooler Bot ឆ្លើយក្នុង Messenger របស់ Page នោះហើយ")

    return {"start_server": start_server, "handle_body": handle_body, "Handler": Handler}
