"""🎨 Colourful buttons + ✨ animated (custom) emoji for every Cooler Bot message.

Colours (Telegram Bot API `style`, works for everyone, no Premium needed) are picked from the button itself,
so the 400+ buttons in the bot need no edits:
  🟢 success (green) — go / pay / confirm / open the app:  ✅ 🚀 💳 🟢 ➕ ▶️, "បង់", "Start", web-app buttons
  🔴 danger  (red)   — cancel / delete / stop:            ❌ 🗑️ 🔴 ⛔, "បោះបង់", "លុប"
  🔵 primary (blue)  — links that leave the chat (url) and the main menu / Hub buttons
  (others keep Telegram's normal look). BUTTON_COLORS=off turns this off.

✨ Animated emoji (custom emoji) need the bot owner to have Telegram Premium (or a Fragment username).
The owner teaches them once: /emojikit → send a message with Premium animated emoji (e.g. 🔥 🎤 🚀) → the bot
remembers which animated version belongs to each emoji, then:
  • buttons that start with that emoji show the animated icon (icon_custom_emoji_id)
  • HTML messages show it animated (<tg-emoji>)
If Telegram refuses (no Premium), the bot turns animated emoji off by itself and resends the plain message,
so nothing ever breaks. /emojikit off|on|list|clear.
"""
import logging
import os
import re
import sqlite3
import threading

logger = logging.getLogger(__name__)

GREEN_START = ("✅", "🚀", "💳", "🟢", "➕", "▶️", "🎟️", "📲", "🔓", "💾", "✨ បង្កើត", "🎉")
RED_START = ("❌", "🗑️", "🔴", "⛔", "🔕", "🚫")
GREEN_WORDS = ("បង់", "ទិញ", "ចូលរួម", "start", "join", "pay", "បើក hub", "ចាប់ផ្ដើម")
RED_WORDS = ("បោះបង់", "លុប", "cancel", "delete", "ផ្ដាច់")
BLUE_CALLBACKS = ("nav:main", "nav:cat:", "hub:open", "menu")

_state = {"colors": None, "emoji": None, "map": {}, "loaded": False, "db": None}
_lock = threading.Lock()


def colors_on():
    if _state["colors"] is None:
        _state["colors"] = os.getenv("BUTTON_COLORS", "on").strip().lower() not in ("0", "off", "false", "no")
    return _state["colors"]


def auto_style(text, callback_data=None, url=None, web_app=False):
    t = (text or "").strip()
    low = t.casefold()
    if t.startswith(RED_START) or any(w in low for w in RED_WORDS):
        return "danger"
    if web_app or t.startswith(GREEN_START) or any(w in low for w in GREEN_WORDS):
        return "success"
    if url or (callback_data and any(str(callback_data).startswith(c) for c in BLUE_CALLBACKS)):
        return "primary"
    return None


# ---------------- custom (animated) emoji ----------------

def _db():
    path = _state["db"]
    if not path:
        return None
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def load(config):
    _state["db"] = getattr(config, "BOT_DB_PATH", "bot_database.db")
    conn = _db()
    try:
        with conn:
            conn.execute("CREATE TABLE IF NOT EXISTS custom_emoji (ch TEXT PRIMARY KEY, id TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS custom_emoji_cfg (k TEXT PRIMARY KEY, v TEXT)")
            _state["map"] = {r["ch"]: r["id"] for r in conn.execute("SELECT ch, id FROM custom_emoji")}
            row = conn.execute("SELECT v FROM custom_emoji_cfg WHERE k='on'").fetchone()
            _state["emoji"] = (row["v"] == "1") if row else bool(_state["map"])
    finally:
        conn.close()
    _state["loaded"] = True


def emoji_on():
    return bool(_state["emoji"]) and bool(_state["map"]) and \
        os.getenv("CUSTOM_EMOJI", "auto").strip().lower() not in ("0", "off", "false", "no")


def set_emoji(on):
    _state["emoji"] = bool(on)
    conn = _db()
    if conn is None:
        return
    try:
        with conn:
            conn.execute("INSERT OR REPLACE INTO custom_emoji_cfg(k, v) VALUES ('on', ?)", ("1" if on else "0",))
    finally:
        conn.close()


def learn(text, entities):
    """Save (emoji char → custom_emoji_id) from a message the owner sent. Returns the learned pairs."""
    if not text or not entities:
        return []
    u16 = text.encode("utf-16-le")
    pairs = []
    for e in entities:
        if getattr(e, "type", "") != "custom_emoji" or not getattr(e, "custom_emoji_id", None):
            continue
        ch = u16[e.offset * 2:(e.offset + e.length) * 2].decode("utf-16-le", "ignore").strip()
        if ch:
            pairs.append((ch, str(e.custom_emoji_id)))
    if pairs:
        conn = _db()
        try:
            with conn:
                conn.executemany("INSERT OR REPLACE INTO custom_emoji(ch, id) VALUES (?, ?)", pairs)
        finally:
            conn.close()
        _state["map"].update(dict(pairs))
    return pairs


def clear():
    conn = _db()
    try:
        with conn:
            conn.execute("DELETE FROM custom_emoji")
    finally:
        conn.close()
    _state["map"] = {}


def _emoji_pattern():
    keys = sorted(_state["map"], key=len, reverse=True)
    return re.compile("|".join(re.escape(k) for k in keys)) if keys else None


def animate_html(text):
    """Wrap known emoji in <tg-emoji> (outside HTML tags and <code>/<pre>/<a> text)."""
    if not text or not emoji_on():
        return text
    pat = _emoji_pattern()
    if pat is None:
        return text
    out, depth = [], 0
    for part in re.split(r"(<[^>]+>)", text):
        if part.startswith("<"):
            tag = part[1:].split()[0].lower().rstrip(">") if len(part) > 2 else ""
            if tag in ("code", "pre", "a", "tg-emoji"):
                depth += 1
            elif tag in ("/code", "/pre", "/a", "/tg-emoji"):
                depth = max(0, depth - 1)
            out.append(part)
        elif depth:
            out.append(part)
        else:
            out.append(pat.sub(lambda m: f'<tg-emoji emoji-id="{_state["map"][m.group(0)]}">{m.group(0)}</tg-emoji>', part))
    return "".join(out)


def button_icon(text):
    """(new text, custom emoji id) when the button starts with a learned emoji."""
    if not emoji_on() or not text:
        return text, None
    for ch in sorted(_state["map"], key=len, reverse=True):
        if text.startswith(ch):
            return text[len(ch):].lstrip() or text, _state["map"][ch]
    return text, None


def _refused(exc):
    d = str(getattr(exc, "description", "") or exc).lower()
    return "emoji" in d or "icon" in d or "premium" in d or "style" in d


# ---------------- wiring ----------------

def install(bot, config):
    """Patch button serialisation and message sending once (call right after creating the bot)."""
    from telebot import apihelper
    from telebot.types import InlineKeyboardButton, KeyboardButton

    try:
        load(config)
    except Exception as exc:
        logger.warning("custom emoji table unavailable (%s)", type(exc).__name__)

    if not getattr(InlineKeyboardButton, "_cooler_styled", False):
        original = InlineKeyboardButton.to_dict

        def to_dict(self):
            d = original(self)
            if colors_on() and not d.get("style"):
                s = auto_style(self.text, getattr(self, "callback_data", None), getattr(self, "url", None),
                               bool(getattr(self, "web_app", None)))
                if s:
                    d["style"] = s
            if not d.get("icon_custom_emoji_id"):
                text, icon = button_icon(d.get("text", ""))
                if icon:
                    d["text"], d["icon_custom_emoji_id"] = text, icon
            return d
        InlineKeyboardButton.to_dict = to_dict
        InlineKeyboardButton._cooler_styled = True

    if not getattr(KeyboardButton, "_cooler_styled", False):
        original_kb = KeyboardButton.to_dict

        def kb_to_dict(self):
            d = original_kb(self)
            if colors_on() and not d.get("style") and not d.get("request_location"):
                s = auto_style(self.text, web_app=bool(getattr(self, "web_app", None)))
                if s:
                    d["style"] = s
            return d
        KeyboardButton.to_dict = kb_to_dict
        KeyboardButton._cooler_styled = True

    # Messages: animate emoji in HTML text; if Telegram refuses (no Premium / old server), turn it off and retry.
    def wrap(name, text_arg):
        fn = getattr(apihelper, name, None)
        if fn is None or getattr(fn, "_cooler_wrapped", False):
            return

        def call(*args, **kwargs):
            args = list(args)
            plain_args, plain_kwargs = list(args), dict(kwargs)
            pm = kwargs.get("parse_mode")          # TeleBot always passes parse_mode by keyword
            animated = False
            if emoji_on() and (pm or "").upper() == "HTML":
                if "text" in kwargs:
                    kwargs["text"], animated = animate_html(kwargs["text"]), True
                elif len(args) > text_arg:
                    args[text_arg], animated = animate_html(args[text_arg]), True
            try:
                return fn(*args, **kwargs)
            except apihelper.ApiTelegramException as exc:
                if (animated or emoji_on() or colors_on()) and _refused(exc):
                    logger.warning("Telegram refused styled/animated content (%s) — sending plain",
                                   str(getattr(exc, "description", ""))[:120])
                    if "emoji" in str(exc).lower() or "icon" in str(exc).lower() or "premium" in str(exc).lower():
                        set_emoji(False)
                    else:
                        _state["colors"] = False
                    return fn(*plain_args, **plain_kwargs)
                raise
        call._cooler_wrapped = True
        setattr(apihelper, name, call)

    wrap("send_message", 2)            # send_message(token, chat_id, text, ...)
    wrap("edit_message_text", 1)       # edit_message_text(token, text, chat_id, ...)

    @bot.message_handler(commands=["emojikit"], func=lambda m: _is_owner(config, m))
    def emojikit_cmd(message):
        arg = ((message.text or "").split(maxsplit=1) + [""])[1].strip().lower()
        if arg in ("off", "on"):
            set_emoji(arg == "on")
            bot.reply_to(message, "✨ Emoji មានចលនា៖ " + ("បើក ✅" if arg == "on" else "បិទ 🔕"))
            return
        if arg == "clear":
            clear()
            bot.reply_to(message, "🧹 លុប Emoji ដែលបានរៀនទាំងអស់")
            return
        if arg == "list":
            items = " ".join(_state["map"]) or "(មិនទាន់មាន)"
            bot.reply_to(message, f"✨ Emoji ដែលស្គាល់ ({len(_state['map'])})៖ {items}\nស្ថានភាព៖ {'បើក' if emoji_on() else 'បិទ'}")
            return
        _waiting.add(message.from_user.id)
        bot.reply_to(message, "✨ <b>បង្រៀន Emoji មានចលនា</b>\n\n"
                              "ផ្ញើសារមួយមកឥឡូវ ដែលមាន <b>Premium Emoji មានចលនា</b> (ជ្រើសពីផ្ទាំង Emoji → Premium) "
                              "ឧ. 🔥 🎤 🚀 ✅ ❌ 🎨 🎧 💳 📅 🙏\n\n"
                              "Bot នឹងចាំថា emoji នីមួយៗ មានចលនាបែបណា ហើយប្រើវាក្នុងសារ និងប៊ូតុងទាំងអស់។\n"
                              "⚠️ ត្រូវការ Telegram Premium លើគណនីម្ចាស់ Bot\n"
                              "/emojikit list · on · off · clear", parse_mode="HTML")

    _waiting = set()

    @bot.message_handler(func=lambda m: getattr(getattr(m, "from_user", None), "id", None) in _waiting,
                         content_types=["text"])
    def emojikit_learn(message):
        _waiting.discard(message.from_user.id)
        pairs = learn(message.text, getattr(message, "entities", None))
        if not pairs:
            bot.reply_to(message, "🤔 រកមិនឃើញ Premium Emoji ទេ — ជ្រើស Emoji ពីផ្ទាំង Premium (មានចលនា) ហើយផ្ញើម្ដងទៀត /emojikit")
            return
        set_emoji(True)
        bot.reply_to(message, f"✅ រៀនបាន {len(pairs)} Emoji៖ {' '.join(p[0] for p in pairs)}\n"
                              "សាកមើល៖ /menu · បើ Telegram មិនអនុញ្ញាត Bot បិទខ្លួនឯង ហើយផ្ញើជាធម្មតា")


def _is_owner(config, message):
    uid = getattr(getattr(message, "from_user", None), "id", None)
    return uid is not None and (uid == getattr(config, "OWNER_ID", 0) or uid in (getattr(config, "ADMIN_IDS", []) or []))
