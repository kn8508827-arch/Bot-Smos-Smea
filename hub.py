"""🚀 Cooler Hub — a modern Mini App home screen for the bot.

The page lives on GitHub Pages (HUB_URL, default the Blue-Gold-Rush repo's /hub/). The bot opens it
with a small personal data blob (?d=base64url JSON): name, membership, Khmer date, next holiday,
this month's cash summary and — for the owner only — revenue numbers. Tapping a tile in the hub
opens t.me/<bot>?start=go_<key>; this module turns that into the matching command (e.g. /song),
so every feature (and the paywall) works exactly as when the command is typed.

  /hub (/app) → button that opens the hub (web_app in private chats, normal link in groups)
  Chat menu button (private chats) → the hub, unless HUB_MENU_BUTTON=false
"""
import base64
import copy
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)
TZ_KH = timezone(timedelta(hours=7))
DEFAULT_URL = "https://kn8508827-arch.github.io/Blue-Gold-Rush/hub/"

GO = {   # hub tile key -> command it runs
    "imagine": "/imagine", "song": "/song", "beat": "/beat", "say": "/say", "prompt": "/prompt",
    "studio": "/studio", "rap": "/rap", "fortune": "/fortune", "games": "/games", "play": "/play",
    "quiz": "/quiz", "card": "/card", "checkin": "/checkin", "calendar": "/calendar", "morning": "/morning",
    "rate": "/rate", "gold": "/gold", "news": "/news", "weather": "/weather", "trip": "/trip",
    "health": "/health", "parent": "/parent", "pet": "/pet", "scam": "/scam", "totext": "/totext",
    "translate": "/translate", "homework": "/homework", "remind": "/remind", "reminders": "/reminders",
    "cash": "/cash", "report": "/report", "kb": "/kb", "memory": "/memory", "pay": "/pay",
    "myplan": "/myplan", "follow": "/follow", "contact": "/contact", "sos": "/sos", "menu": "/menu",
    "help": "/help", "stats": "/stats", "subgroups": "/subgroups", "status": "/status", "goodday": "/goodday",
    "summary": "/summary", "guard": "/guard", "invite": "/invite", "nearby": "/nearby", "icom": "/icom",
    "live": "/live", "tpnews": "/tpnews",
    "addgroup": "/addgroup", "work": "/work", "sleep": "/sleep", "workin": "/workin", "workout": "/workout", "staff": "/staff",
}


ICOM_URL_DEFAULT = "https://team-smos-snea.lovable.app/icom"


def icom_url():
    return os.getenv("ICOM_URL", "").strip() or ICOM_URL_DEFAULT


def icom_in_hub():
    """True when ICOM runs on the bot's own server (Railway ICOM_URL = https://<domain>/icom).
    LiveKit keys are not required here: if they are missing the page itself says so (no silent Lovable)."""
    url = os.getenv("ICOM_URL", "").strip()
    return url.startswith("https://") and "lovable" not in url


def icom_diagnose():
    """Owner-only checklist shown under /icom, so a missing Railway variable is obvious."""
    import icom_live
    s = icom_live.settings()
    url = os.getenv("ICOM_URL", "").strip()
    ok = lambda v: "✅" if v else "❌"
    lines = [
        f"{ok(icom_in_hub())} ICOM_URL = {url or '(មិនទាន់ដាក់)'}",
        f"{ok(s['url'].startswith('wss://'))} LIVEKIT_URL",
        f"{ok(s['key'])} LIVEKIT_API_KEY",
        f"{ok(s['secret'])} LIVEKIT_API_SECRET",
        f"{ok(os.getenv('PORT', '').strip())} PORT (Railway → Settings → Networking → Generate Domain)",
    ]
    good, why = icom_live.check_livekit()
    lines.append(f"{ok(good)} {why}")
    return "\n".join(lines)


def hub_url():
    return (os.getenv("HUB_URL", "").strip() or DEFAULT_URL)


def encode(data):
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode(text):
    pad = "=" * (-len(text) % 4)
    return json.loads(base64.urlsafe_b64decode(text + pad).decode("utf-8"))


def personal_data(config, user, bot_username, now=None):
    """Small JSON for the hub page (kept short: Telegram URLs should stay small)."""
    import subscription as sub
    now = now or time.time()
    uid = user.id
    owner = uid == getattr(config, "OWNER_ID", 0) or uid in getattr(config, "ADMIN_IDS", [])
    name = " ".join(x for x in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if x)[:30]
    until = sub.paid_until(config, "u", uid)
    data = {"b": bot_username or "TeamSmosSneabot", "n": name or "Cooler Fan",
            "i": icom_url() if icom_in_hub() else "",
            "m": {"a": bool(owner or until > now), "o": owner,
                  "u": "" if owner or until <= now else sub.fmt_date(until)[:10],
                  "l": 0 if owner or until <= now else int((until - now) // 86400) + 1, "t": sub.settings().days}}
    try:
        import khmer_calendar as kc
        import calendar_brief as cb
        today = datetime.fromtimestamp(now, TZ_KH).date()
        data["k"] = kc.format_khmer(today, with_weekday=False).split(" ព.ស.")[0]
        nxt = cb.upcoming(today, 60)
        if nxt:
            day, hname = nxt[0]
            data["h"] = [hname[:40], (day - today).days]
        data["s"] = 1 if kc.is_sila_day(today) else 0
    except Exception:
        pass
    try:
        import daily_tools as dt
        import sqlite3
        conn = sqlite3.connect(getattr(config, "BOT_DB_PATH", "bot_database.db"), timeout=10)
        conn.row_factory = sqlite3.Row
        start = int(datetime.fromtimestamp(now, TZ_KH).replace(day=1, hour=0, minute=0, second=0).timestamp())
        try:
            rows = conn.execute("SELECT kind, amount, currency, category FROM cashbook WHERE chat_id=? AND at>=?",
                                (uid, start)).fetchall()
        finally:
            conn.close()
        if rows:
            usd = lambda r: r["amount"] if r["currency"] == "USD" else r["amount"] / dt.KHR_PER_USD_FALLBACK
            cats = {}
            for r in rows:
                if r["kind"] == "out":
                    cats[r["category"]] = cats.get(r["category"], 0) + usd(r)
            data["c"] = {"i": round(sum(usd(r) for r in rows if r["kind"] == "in"), 2),
                         "o": round(sum(usd(r) for r in rows if r["kind"] == "out"), 2),
                         "g": [[k, round(v, 2)] for k, v in sorted(cats.items(), key=lambda kv: -kv[1])[:5]]}
    except Exception:
        pass
    try:   # ⭐ points · level · check-in streak for the flip card
        import sqlite3
        import community_features as cf
        conn = sqlite3.connect(getattr(config, "BOT_DB_PATH", "bot_database.db"), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            pts, vip = cf._points(conn, uid)
            days = {r[0] for r in conn.execute(
                "SELECT local_date FROM community_checkins WHERE user_id=? ORDER BY local_date DESC LIMIT 60", (uid,))}
        finally:
            conn.close()
        streak, day = 0, datetime.fromtimestamp(now, TZ_KH).date()
        if day.isoformat() not in days:
            day -= timedelta(days=1)          # today's check-in not done yet: count up to yesterday
        while day.isoformat() in days:
            streak += 1
            day -= timedelta(days=1)
        if pts or streak or vip:
            data["p"] = [pts, cf._level(pts, vip), streak]
    except Exception:
        pass
    try:   # 📰 TPNews latest headlines for the live strip (kept short: the URL must stay small)
        import tpnews_feed
        tp = tpnews_feed.latest(config, 2)
        if tp:
            data["tp"] = [[pid, title[:48], ts] for pid, title, ts in tp]
            data["tc"] = tpnews_feed.channel()
    except Exception:
        pass
    if owner:
        try:
            st = sub.stats(config, now)
            data["x"] = {"r": st.rev_month, "rl": st.rev_last, "rt": st.rev_today, "u": st.active_u, "g": st.active_g,
                         "p": st.pending, "e": len(st.expiring)}
        except Exception:
            pass
    return data


def register(bot, config):
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, WebAppInfo

    me = {}

    def username():
        if "u" not in me:
            try:
                me["u"] = bot.get_me().username
            except Exception:
                return "TeamSmosSneabot"
        return me["u"]

    def personal_url(user):
        try:
            return hub_url() + "?d=" + encode(personal_data(config, user, username()))
        except Exception as exc:
            logger.info("hub data skipped (%s)", type(exc).__name__)
            return hub_url()

    def hub_markup(message_or_user, private=True):
        user = message_or_user
        m = InlineKeyboardMarkup()
        if private:
            m.row(InlineKeyboardButton("🚀 បើក Cooler Hub", web_app=WebAppInfo(url=personal_url(user))))
        else:
            m.row(InlineKeyboardButton("🚀 បើក Cooler Hub", url=f"https://t.me/{username()}?start=hub"))
        return m

    def hub_text():
        return ("🚀 <b>Cooler Hub</b> — គ្រប់មុខងារក្នុងផ្ទាំងតែមួយ ✨\n"
                "AI · គូររូប · ច្រៀង · ហ្គេម · ប្រតិទិន · លុយ · ឧបករណ៍ — ចុចដើម្បីបើក 👇")

    def icom_markup(private=True):
        m = InlineKeyboardMarkup()
        if icom_in_hub() and private:
            # ICOM on our own server (icom_live.py): opens inside Telegram, no Sign-in — Telegram tells us who it is.
            m.row(InlineKeyboardButton("🎙️ ចូល ICOM ឥឡូវ", web_app=WebAppInfo(url=icom_url())))
        elif icom_in_hub():
            m.row(InlineKeyboardButton("🎙️ ចូល ICOM ឥឡូវ", url=f"https://t.me/{username()}?start=go_icom"))
        else:
            # Old Lovable ICOM: a plain URL button opens the phone's browser, where its login works.
            m.row(InlineKeyboardButton("🎙️ ចូល ICOM ឥឡូវ", url=icom_url()))
        m.row(InlineKeyboardButton("🚀 Cooler Hub", callback_data="hub:open"))
        return m

    @bot.message_handler(commands=["icom", "talk"])
    def icom_cmd(message):
        private = getattr(message.chat, "type", None) == "private"
        if icom_in_hub():
            text = ("🎙️ <b>ICOM — Team ស្មោះស្នេហ៍</b>\n"
                    "ចុចប៊ូតុងខាងក្រោម → ចូលនិយាយជាមួយក្រុមភ្លាម 🔊 មិនបាច់ Sign in ទេ!\n"
                    "💡 អនុញ្ញាត Microphone ពេល Telegram សួរ (ម្ដងគត់)")
        else:
            text = ("🎙️ <b>ICOM — Team ស្មោះស្នេហ៍</b>\n"
                    "ចុចប៊ូតុងខាងក្រោម ហើយ Login (ម្ដងគត់) → ចូលនិយាយជាមួយក្រុមភ្លាម 🔊\n"
                    "💡 អនុញ្ញាត Microphone ពេល Browser សួរ")
        uid = getattr(message.from_user, "id", 0)
        if private and (uid == getattr(config, "OWNER_ID", 0) or uid in getattr(config, "ADMIN_IDS", [])):
            text += "\n\n🛠 <b>Papa · ពិនិត្យ ICOM</b>\n" + icom_diagnose()
        if private:
            personal_menu_button(message.from_user, message.chat.id)   # refresh the 🚀 Hub button with the new ICOM link
        bot.reply_to(message, text, reply_markup=icom_markup(private), parse_mode="HTML")

    def personal_menu_button(user, chat_id):
        """The chat's menu button opens the hub *with* this person's card data (refreshed on /hub)."""
        if os.getenv("HUB_MENU_BUTTON", "true").strip().lower() in ("0", "false", "no", "off"):
            return
        try:
            bot.set_chat_menu_button(chat_id=chat_id, menu_button=MenuButtonWebApp(
                type="web_app", text="🚀 Hub", web_app=WebAppInfo(url=personal_url(user))))
        except Exception as exc:
            logger.info("personal menu button skipped (%s)", type(exc).__name__)

    @bot.message_handler(commands=["hub", "app"])
    def hub_cmd(message):
        private = getattr(message.chat, "type", "") == "private"
        if private:
            personal_menu_button(message.from_user, message.chat.id)
        bot.reply_to(message, hub_text(), reply_markup=hub_markup(message.from_user, private), parse_mode="HTML")

    def is_go(message):
        parts = (getattr(message, "text", None) or "").split()
        return len(parts) == 2 and parts[0].split("@")[0] == "/start" and (parts[1].startswith("go_") or parts[1] == "hub")

    @bot.message_handler(commands=["start"], func=is_go)
    def start_go(message):
        key = message.text.split()[1]
        if key == "hub":
            personal_menu_button(message.from_user, message.chat.id)
            bot.reply_to(message, hub_text(), reply_markup=hub_markup(message.from_user, True), parse_mode="HTML")
            return
        cmd = GO.get(key[3:])
        if not cmd:
            bot.reply_to(message, "🤔 មិនស្គាល់មុខងារនេះ — /menu")
            return
        fake = copy.copy(message)
        fake.text = cmd
        fake.entities = None
        try:
            bot.process_new_messages([fake])   # runs through the paywall and the normal handler
        except Exception as exc:
            logger.warning("hub go failed (%s)", type(exc).__name__)

    @bot.callback_query_handler(func=lambda c: (c.data or "") == "hub:open")
    def hub_cb(call):
        bot.answer_callback_query(call.id)
        private = getattr(call.message.chat, "type", "") == "private"
        bot.send_message(call.message.chat.id, hub_text(), reply_markup=hub_markup(call.from_user, private),
                         parse_mode="HTML")

    def setup_menu_button():
        if os.getenv("HUB_MENU_BUTTON", "true").strip().lower() in ("0", "false", "no", "off"):
            return
        try:
            bot.set_chat_menu_button(menu_button=MenuButtonWebApp(type="web_app", text="🚀 Hub",
                                                                  web_app=WebAppInfo(url=hub_url())))
        except Exception as exc:
            logger.info("menu button not set (%s)", type(exc).__name__)

    class Api:
        pass
    api = Api()
    api.hub_markup = hub_markup
    api.personal_url = personal_url
    api.setup_menu_button = setup_menu_button
    api.personal_menu_button = personal_menu_button
    return api


def member_response(config, body, now=None):
    """POST /hub/me {initData} → {"member": bool, "owner": bool}. The Hub locks games/pages for non-members.
    Telegram's signed initData proves who is asking (same check as ICOM)."""
    import json as _json
    import icom_live
    try:
        data = _json.loads(body.decode("utf-8") or "{}")
    except (ValueError, AttributeError):
        return 400, {"error": "bad_request"}
    user = icom_live.verify_init_data((data or {}).get("initData", ""), getattr(config, "BOT_TOKEN", ""), now)
    if not user:
        return 200, {"member": False, "owner": False}
    uid = int(user["id"])
    owner = uid in icom_live._staff(config)
    return 200, {"member": bool(owner or icom_live.is_allowed(config, uid, now)), "owner": owner}
