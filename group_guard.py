"""🛡️ Cooler Guard — keeps paid groups clean (Bot must be a group admin with "Delete" + "Ban" rights).

  /guard            → settings with on/off buttons (group admins)
  Filters (non-admins only): 🔗 links & t.me invites (allow-list), 📢 forwards from channels,
  🌊 flood (too many messages fast → muted 10 min), 🤬 bad-word list
  🙋 Join captcha: new members are muted until they tap "ខ្ញុំជាមនុស្ស" (kicked after 3 minutes)
  ⚠️ 3 warnings → muted 24 h
  Admin replies: /warn /unwarn /mute [minutes] /unmute /ban /unban <id> /setwelcome <text> /badword add|del|list <word>
"""
import html
import logging
import re
import sqlite3
import threading
import time

logger = logging.getLogger(__name__)

LINK_RE = re.compile(r"(https?://\S+|www\.\S+|t\.me/\S+|telegram\.(?:me|dog)/\S+|\b[\w-]+\.(?:com|net|org|xyz|top|"
                     r"vip|io|me|cc|info|biz|online|site|club|shop|live|app|link|kh)\b(?:/\S*)?)", re.I)
DEFAULT_ALLOWED = ("tiktok.com/@vidcooler", "youtube.com", "youtu.be", "kn8508827-arch.github.io",
                   "t.me/teamsmossneabot")
FLOOD_COUNT, FLOOD_SECONDS, FLOOD_MUTE = 6, 8, 600
CAPTCHA_SECONDS = 180
MAX_WARNS = 3
DEFAULTS = {"enabled": 1, "links": 1, "forwards": 0, "flood": 1, "captcha": 1, "badwords": 1}
LABELS = {"links": "🔗 បិទ Link", "forwards": "📢 បិទ Forward ពី Channel", "flood": "🌊 ការពារ Flood",
          "captcha": "🙋 ផ្ទៀងផ្ទាត់សមាជិកថ្មី", "badwords": "🤬 ពាក្យអាក្រក់"}


def _db(config):
    conn = sqlite3.connect(getattr(config, "BOT_DB_PATH", "bot_database.db"), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(config):
    conn = _db(config)
    try:
        with conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS guard_settings (chat_id INTEGER PRIMARY KEY, enabled INTEGER,
                links INTEGER, forwards INTEGER, flood INTEGER, captcha INTEGER, badwords INTEGER,
                welcome TEXT, words TEXT, allow TEXT)""")
            conn.execute("CREATE TABLE IF NOT EXISTS guard_warns (chat_id INTEGER, user_id INTEGER, n INTEGER NOT NULL, "
                         "PRIMARY KEY (chat_id, user_id))")
    finally:
        conn.close()


def get_settings(config, chat_id):
    conn = _db(config)
    try:
        row = conn.execute("SELECT * FROM guard_settings WHERE chat_id=?", (chat_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    d = dict(row)
    d["words"] = [w for w in (d.get("words") or "").split("\n") if w]
    d["allow"] = [w for w in (d.get("allow") or "").split("\n") if w]
    return d


def save_settings(config, chat_id, **changes):
    current = get_settings(config, chat_id) or dict(DEFAULTS, welcome=None, words=[], allow=[])
    current.update(changes)
    conn = _db(config)
    try:
        with conn:
            conn.execute("INSERT OR REPLACE INTO guard_settings(chat_id, enabled, links, forwards, flood, captcha, badwords, "
                         "welcome, words, allow) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (chat_id, current["enabled"], current["links"], current["forwards"], current["flood"],
                          current["captcha"], current["badwords"], current.get("welcome"),
                          "\n".join(current["words"]), "\n".join(current["allow"])))
    finally:
        conn.close()
    return current


def add_warn(config, chat_id, user_id, delta=1):
    conn = _db(config)
    try:
        with conn:
            conn.execute("INSERT INTO guard_warns(chat_id, user_id, n) VALUES (?, ?, MAX(0, ?)) ON CONFLICT(chat_id, user_id) "
                         "DO UPDATE SET n=MAX(0, n+?)", (chat_id, user_id, delta, delta))
            return conn.execute("SELECT n FROM guard_warns WHERE chat_id=? AND user_id=?", (chat_id, user_id)).fetchone()["n"]
    finally:
        conn.close()


def reset_warns(config, chat_id, user_id):
    conn = _db(config)
    try:
        with conn:
            conn.execute("DELETE FROM guard_warns WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    finally:
        conn.close()


def find_bad_link(text, allow=()):
    for m in LINK_RE.finditer(text or ""):
        link = m.group(0).lower()
        if not any(a.lower() in link for a in (*DEFAULT_ALLOWED, *allow)):
            return m.group(0)
    return None


def find_bad_word(text, words):
    low = (text or "").lower()
    return next((w for w in words if w and w.lower() in low), None)


def register(bot, config, group_allowed=None):
    """group_allowed(chat_id) -> bool: guard only works in paid (or grace-period) groups."""
    from telebot.types import ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup

    init_schema(config)
    admin_cache = {}
    flood = {}                # (chat, user) -> [timestamps]
    captcha = {}              # (chat, user) -> deadline
    notice_at = {}            # (chat, user) -> last notice time
    lock = threading.Lock()
    me = {}

    def bot_id():
        if "id" not in me:
            try:
                me["id"] = bot.get_me().id
            except Exception:
                return None
        return me["id"]

    def privileged(uid):
        return uid == getattr(config, "OWNER_ID", 0) or uid in getattr(config, "ADMIN_IDS", [])

    def is_group(chat):
        return getattr(chat, "type", None) in ("group", "supergroup")

    def is_admin(chat_id, uid):
        if privileged(uid):
            return True
        key = (chat_id, uid)
        hit = admin_cache.get(key)
        if hit and hit[1] > time.monotonic():
            return hit[0]
        try:
            ok = bot.get_chat_member(chat_id, uid).status in ("creator", "administrator")
        except Exception:
            ok = False
        admin_cache[key] = (ok, time.monotonic() + 600)
        return ok

    def active(chat_id):
        if group_allowed is not None:
            try:
                if not group_allowed(chat_id):
                    return None
            except Exception:
                return None
        s = get_settings(config, chat_id)
        return s if s and s["enabled"] else None

    def mute(chat_id, user_id, seconds):
        until = int(time.time()) + max(60, seconds)
        bot.restrict_chat_member(chat_id, user_id, until_date=until,
                                 permissions=ChatPermissions(can_send_messages=False))

    def unmute(chat_id, user_id):
        bot.restrict_chat_member(chat_id, user_id, permissions=ChatPermissions(
            can_send_messages=True, can_send_audios=True, can_send_documents=True, can_send_photos=True,
            can_send_videos=True, can_send_video_notes=True, can_send_voice_notes=True, can_send_polls=True,
            can_send_other_messages=True, can_add_web_page_previews=True, can_invite_users=True))

    def name_of(user):
        n = " ".join(x for x in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if x) or "សមាជិក"
        return f'<a href="tg://user?id={user.id}">{html.escape(n[:40])}</a>'

    def notice(chat_id, user, text):
        key = (chat_id, user.id)
        if key in notice_at and time.monotonic() - notice_at[key] < 60:
            return
        notice_at[key] = time.monotonic()
        try:
            msg = bot.send_message(chat_id, f"🛡️ {name_of(user)} {text}", parse_mode="HTML")
            t = threading.Timer(45, lambda: _safe(bot.delete_message, chat_id, msg.message_id))
            t.daemon = True
            t.start()
        except Exception:
            pass

    def _safe(fn, *a, **k):
        try:
            return fn(*a, **k)
        except Exception:
            return None

    def punish(message, reason):
        chat_id, user = message.chat.id, message.from_user
        _safe(bot.delete_message, chat_id, message.message_id)
        n = add_warn(config, chat_id, user.id)
        if n >= MAX_WARNS:
            reset_warns(config, chat_id, user.id)
            _safe(mute, chat_id, user.id, 86400)
            notice(chat_id, user, f"ត្រូវបានបិទមាត់ ២៤ ម៉ោង ({reason}) 🔇")
        else:
            notice(chat_id, user, f"សារត្រូវបានលុប — {reason} ⚠️ {n}/{MAX_WARNS}")

    # ---------- filter (returns True only when it acted, so other features still see normal messages) ----------

    def violation(message):
        cached = getattr(message, "_guard_kind", "unset")
        if cached != "unset":
            return cached
        kind = _violation(message)
        try:
            message._guard_kind = kind
        except Exception:
            pass
        return kind

    def _violation(message):
        chat = getattr(message, "chat", None)
        user = getattr(message, "from_user", None)
        if not is_group(chat) or user is None or getattr(user, "is_bot", False) and user.id == bot_id():
            return None
        s = active(chat.id)
        if not s:
            return None
        key = (chat.id, user.id)
        if key in captcha:
            return "captcha"
        text = (getattr(message, "text", None) or getattr(message, "caption", None) or "")
        if s["flood"]:
            now = time.monotonic()
            with lock:
                stamps = [t for t in flood.get(key, []) if now - t < FLOOD_SECONDS] + [now]
                flood[key] = stamps
            if len(stamps) > FLOOD_COUNT and not is_admin(chat.id, user.id):
                flood[key] = []
                return "flood"
        if not (s["links"] or s["forwards"] or s["badwords"]):
            return None
        bad = None
        if s["links"] and find_bad_link(text, s["allow"]):
            bad = "link"
        elif s["links"] and any(getattr(e, "type", "") in ("url", "text_link")
                                and find_bad_link(getattr(e, "url", None) or "", s["allow"])
                                for e in (getattr(message, "entities", None) or getattr(message, "caption_entities", None) or [])):
            bad = "link"
        elif s["forwards"] and (getattr(message, "forward_from_chat", None) or
                                getattr(getattr(message, "forward_origin", None), "type", "") == "channel"):
            bad = "forward"
        elif s["badwords"] and find_bad_word(text, s["words"]):
            bad = "word"
        if bad and not is_admin(chat.id, user.id):
            return bad
        return None

    REASONS = {"link": "មិនអនុញ្ញាតផ្ញើ Link", "forward": "មិនអនុញ្ញាត Forward ពី Channel",
               "word": "ពាក្យមិនសមរម្យ", "flood": "ផ្ញើលឿនពេក"}

    @bot.message_handler(func=lambda m: violation(m) is not None,
                         content_types=["text", "photo", "video", "document", "audio", "voice", "sticker", "animation",
                                        "video_note", "contact", "location"])
    def guard_filter(message):
        kind = violation(message) or "flood"
        if kind == "captcha":
            _safe(bot.delete_message, message.chat.id, message.message_id)
            return
        if kind == "flood":
            _safe(mute, message.chat.id, message.from_user.id, FLOOD_MUTE)
            notice(message.chat.id, message.from_user, "ផ្ញើលឿនពេក — បិទមាត់ ១០ នាទី 🔇")
            return
        punish(message, REASONS[kind])

    # ---------- join captcha + welcome ----------

    @bot.message_handler(content_types=["new_chat_members"])
    def on_join(message):
        chat = message.chat
        s = active(chat.id)
        if not s:
            return
        for user in message.new_chat_members or []:
            if getattr(user, "is_bot", False):
                continue
            welcome = (s.get("welcome") or "សូមស្វាគមន៍មកកាន់ Group! 🎉").replace("{name}", name_of(user))
            if s["captcha"] and not is_admin(chat.id, user.id):
                try:
                    bot.restrict_chat_member(chat.id, user.id, permissions=ChatPermissions(can_send_messages=False))
                except Exception as exc:
                    logger.info("captcha restrict failed (%s) — is the bot admin?", type(exc).__name__)
                    bot.send_message(chat.id, f"👋 {name_of(user)} {welcome}", parse_mode="HTML")
                    continue
                captcha[(chat.id, user.id)] = time.time() + CAPTCHA_SECONDS
                m = InlineKeyboardMarkup()
                m.row(InlineKeyboardButton("✅ ខ្ញុំជាមនុស្ស", callback_data=f"guard:ok:{user.id}"))
                msg = bot.send_message(chat.id, f"👋 {name_of(user)} {welcome}\n\n🙋 សូមចុចប៊ូតុងខាងក្រោម ក្នុង ៣ នាទី "
                                                "ដើម្បីបញ្ជាក់ថាអ្នកមិនមែនជា Bot", reply_markup=m, parse_mode="HTML")
                t = threading.Timer(CAPTCHA_SECONDS, kick_if_pending, args=(chat.id, user.id, msg.message_id))
                t.daemon = True
                t.start()
            else:
                bot.send_message(chat.id, f"👋 {name_of(user)} {welcome}", parse_mode="HTML")

    def kick_if_pending(chat_id, user_id, msg_id):
        if captcha.pop((chat_id, user_id), None) is None:
            return
        _safe(bot.ban_chat_member, chat_id, user_id, until_date=int(time.time()) + 60)
        _safe(bot.unban_chat_member, chat_id, user_id, only_if_banned=True)
        _safe(bot.delete_message, chat_id, msg_id)

    # ---------- settings menu ----------

    def menu(chat_id):
        s = get_settings(config, chat_id) or dict(DEFAULTS, enabled=0, words=[], allow=[])
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton(("🟢 Guard កំពុងបើក" if s["enabled"] else "🔴 Guard បិទ — ចុចដើម្បីបើក"),
                                   callback_data="guard:t:enabled"))
        for key, label in LABELS.items():
            m.row(InlineKeyboardButton(("✅ " if s[key] else "⬜ ") + label, callback_data=f"guard:t:{key}"))
        text = ("🛡️ <b>Cooler Guard — ការពារ Group</b>\n\n"
                "លុប Link បោក · Spam · Flood · ផ្ទៀងផ្ទាត់សមាជិកថ្មី · Warn ៣ ដង = បិទមាត់ ២៤ម៉ោង\n\n"
                "⚠️ ត្រូវដាក់ Bot ជា <b>Admin</b> (សិទ្ធិ Delete messages + Ban users)\n\n"
                "Admin៖ Reply សារ ហើយវាយ /warn · /mute ៣០ · /unmute · /ban\n"
                "/setwelcome អត្ថបទ ({name} = ឈ្មោះ) · /badword add ពាក្យ · /allowlink domain.com")
        if s["words"]:
            text += f"\n🤬 ពាក្យហាមឃាត់៖ {len(s['words'])}"
        return text, m

    def admin_only(message):
        return is_group(getattr(message, "chat", None)) and is_admin(message.chat.id, message.from_user.id)

    @bot.message_handler(commands=["guard", "antispam"])
    def guard_cmd(message):
        if not is_group(message.chat):
            bot.reply_to(message, "🛡️ /guard ប្រើក្នុង Group (Add Bot ជា Admin ក្នុង Group របស់អ្នក)")
            return
        if not admin_only(message):
            bot.reply_to(message, "⛔ សម្រាប់ Admin Group ប៉ុណ្ណោះ")
            return
        text, m = menu(message.chat.id)
        try:
            st = bot.get_chat_member(message.chat.id, bot_id()).status
            if st != "administrator":
                text += "\n\n❗ <b>Bot មិនទាន់ជា Admin ទេ</b> — Guard ធ្វើការមិនបាន"
        except Exception:
            pass
        bot.reply_to(message, text, reply_markup=m, parse_mode="HTML")

    @bot.callback_query_handler(func=lambda c: (c.data or "").startswith("guard:"))
    def guard_cb(call):
        parts = (call.data or "").split(":")
        chat_id, uid = call.message.chat.id, call.from_user.id
        if parts[1] == "ok" and len(parts) == 3:
            if str(uid) != parts[2]:
                bot.answer_callback_query(call.id, "ប៊ូតុងនេះសម្រាប់សមាជិកថ្មីប៉ុណ្ណោះ 🙂", show_alert=True)
                return
            captcha.pop((chat_id, uid), None)
            _safe(unmute, chat_id, uid)
            bot.answer_callback_query(call.id, "✅ អរគុណ! សូមស្វាគមន៍ 🎉")
            _safe(bot.edit_message_text, f"✅ {name_of(call.from_user)} បានចូលរួមជាផ្លូវការ — សូមស្វាគមន៍! 🎉",
                  chat_id, call.message.message_id, parse_mode="HTML")
            return
        if not is_admin(chat_id, uid):
            bot.answer_callback_query(call.id, "⛔ សម្រាប់ Admin ប៉ុណ្ណោះ", show_alert=True)
            return
        if parts[1] == "t" and len(parts) == 3 and (parts[2] in LABELS or parts[2] == "enabled"):
            s = get_settings(config, chat_id) or dict(DEFAULTS, enabled=0, words=[], allow=[])
            save_settings(config, chat_id, **{parts[2]: 0 if s[parts[2]] else 1})
            bot.answer_callback_query(call.id, "✅")
            text, m = menu(chat_id)
            _safe(bot.edit_message_text, text, chat_id, call.message.message_id, reply_markup=m, parse_mode="HTML")
            return
        bot.answer_callback_query(call.id)

    def target_of(message):
        src = getattr(message, "reply_to_message", None)
        return getattr(src, "from_user", None) if src else None

    @bot.message_handler(commands=["warn", "unwarn", "mute", "unmute", "ban", "kick"], func=admin_only)
    def admin_actions(message):
        cmd = (message.text or "").split()[0][1:].split("@")[0].lower()
        user = target_of(message)
        if user is None:
            bot.reply_to(message, f"↩️ Reply លើសាររបស់អ្នកនោះ ហើយវាយ /{cmd}")
            return
        if is_admin(message.chat.id, user.id) or user.id == bot_id():
            bot.reply_to(message, "🙅 មិនអាចធ្វើលើ Admin បានទេ")
            return
        chat_id = message.chat.id
        try:
            if cmd == "warn":
                n = add_warn(config, chat_id, user.id)
                if n >= MAX_WARNS:
                    reset_warns(config, chat_id, user.id)
                    mute(chat_id, user.id, 86400)
                    bot.reply_to(message, f"🔇 {name_of(user)} Warn ៣ ដង — បិទមាត់ ២៤ ម៉ោង", parse_mode="HTML")
                else:
                    bot.reply_to(message, f"⚠️ {name_of(user)} Warn {n}/{MAX_WARNS}", parse_mode="HTML")
            elif cmd == "unwarn":
                reset_warns(config, chat_id, user.id)
                bot.reply_to(message, f"✅ លុប Warn របស់ {name_of(user)}", parse_mode="HTML")
            elif cmd == "mute":
                parts = (message.text or "").split()
                minutes = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 60
                mute(chat_id, user.id, minutes * 60)
                bot.reply_to(message, f"🔇 {name_of(user)} បិទមាត់ {minutes} នាទី", parse_mode="HTML")
            elif cmd == "unmute":
                unmute(chat_id, user.id)
                bot.reply_to(message, f"🔊 {name_of(user)} អាចនិយាយវិញហើយ", parse_mode="HTML")
            elif cmd in ("ban", "kick"):
                bot.ban_chat_member(chat_id, user.id)
                if cmd == "kick":
                    bot.unban_chat_member(chat_id, user.id, only_if_banned=True)
                _safe(bot.delete_message, chat_id, message.reply_to_message.message_id)
                bot.reply_to(message, f"🚫 {name_of(user)} {'ត្រូវបានដកចេញ' if cmd == 'kick' else 'ត្រូវបាន Ban'}",
                             parse_mode="HTML")
        except Exception as exc:
            bot.reply_to(message, f"😔 ធ្វើមិនបាន — Bot ត្រូវជា Admin មានសិទ្ធិ Ban ({type(exc).__name__})")

    @bot.message_handler(commands=["unban"], func=admin_only)
    def unban_cmd(message):
        parts = (message.text or "").split()
        try:
            uid = int(parts[1])
            bot.unban_chat_member(message.chat.id, uid, only_if_banned=True)
            bot.reply_to(message, f"✅ Unban {uid}")
        except Exception:
            bot.reply_to(message, "របៀបប្រើ៖ /unban USER_ID")

    @bot.message_handler(commands=["setwelcome"], func=admin_only)
    def setwelcome_cmd(message):
        parts = (message.text or "").split(maxsplit=1)
        text = parts[1].strip()[:500] if len(parts) > 1 else None
        save_settings(config, message.chat.id, welcome=text)
        bot.reply_to(message, "✅ បានកំណត់សារស្វាគមន៍" if text else "✅ ប្រើសារស្វាគមន៍ដើមវិញ")

    @bot.message_handler(commands=["badword", "badwords", "allowlink"], func=admin_only)
    def words_cmd(message):
        parts = (message.text or "").split(maxsplit=2)
        cmd = parts[0][1:].split("@")[0].lower()
        s = get_settings(config, message.chat.id) or save_settings(config, message.chat.id)
        if cmd == "allowlink":
            if len(parts) < 2:
                bot.reply_to(message, "របៀបប្រើ៖ /allowlink facebook.com\nអនុញ្ញាតរួច៖ " + ", ".join(s["allow"] or ["—"]))
                return
            dom = parts[1].lower().strip()[:60]
            allow = sorted(set(s["allow"]) ^ {dom})
            save_settings(config, message.chat.id, allow=allow)
            bot.reply_to(message, ("✅ អនុញ្ញាត " if dom in allow else "🗑️ ដកចេញ ") + dom)
            return
        action = parts[1].lower() if len(parts) > 1 else "list"
        if action == "list" or len(parts) < 3:
            bot.reply_to(message, "🤬 ពាក្យហាមឃាត់៖ " + (", ".join(s["words"]) or "—") +
                         "\nបន្ថែម៖ /badword add ពាក្យ · លុប៖ /badword del ពាក្យ")
            return
        word = parts[2].strip().lower()[:40]
        words = set(s["words"])
        (words.add if action == "add" else words.discard)(word)
        save_settings(config, message.chat.id, words=sorted(words))
        _safe(bot.delete_message, message.chat.id, message.message_id)
        bot.send_message(message.chat.id, f"✅ ពាក្យហាមឃាត់៖ {len(words)}")

    return violation
