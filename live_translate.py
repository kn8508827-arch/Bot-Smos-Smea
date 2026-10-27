"""🎙️ Live translate (walkie-talkie style): talk in Khmer, the bot speaks the other language — and back.

/live → pick a language → for LIVE_MINUTES (20) every voice note (or typed message) in the private chat is:
  🎤 voice in → translated VOICE out (text as caption) · ✍️ text in → translated TEXT out (no voice).
  The AI works out which side spoke (Khmer or the other language) and translates into the other one. Hold the mic, speak, hand the phone over, the other person replies.

It is near-live (a few seconds per turn): Telegram bots receive voice notes, not a live audio stream.
LIVE_DAILY_LIMIT (default 80) voice turns per person per day; owner/admins unlimited.
"""
import base64
import html
import io
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)
TZ_KH = timezone(timedelta(hours=7))
LANGS = {   # code: (button label, language name for the AI)
    "en": ("🇬🇧 English", "English"), "zh": ("🇨🇳 中文", "Chinese (Simplified)"), "th": ("🇹🇭 ไทย", "Thai"),
    "vi": ("🇻🇳 Tiếng Việt", "Vietnamese"), "ko": ("🇰🇷 한국어", "Korean"), "ja": ("🇯🇵 日本語", "Japanese"),
    "fr": ("🇫🇷 Français", "French"), "lo": ("🇱🇦 ລາວ", "Lao"),
}
PROMPT = """You are a live interpreter between Khmer and {other}.
The input is one turn of a conversation ({kind}). Detect whether it is Khmer or {other}.
Translate it into the OTHER language, naturally and politely, as a real interpreter would say it (short, spoken style).
Return ONLY JSON: {{"heard": "what was said, in its original language and script", "from": "km" or "{code}",
"translation": "the translation"}}. If nothing understandable was said return {{"heard": "", "from": "", "translation": ""}}."""


def minutes():
    try:
        return max(1, int(os.getenv("LIVE_MINUTES", "20")))
    except ValueError:
        return 20


def daily_limit():
    try:
        return int(os.getenv("LIVE_DAILY_LIMIT", "80"))
    except ValueError:
        return 80


def parse_reply(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("translation") else None


def register(bot, config, ai=None, tts=None):
    """ai(parts, prompt) -> text; tts(text) -> WAV bytes. Both injectable for tests.
    Register before the generic voice / AI-chat handlers so an active session wins."""
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    if ai is None:
        def ai(parts, prompt):
            import knowledge_base
            return knowledge_base.gemini(getattr(config, "GEMINI_API_KEY", ""), list(parts) + [{"text": prompt}],
                                         max_tokens=1200, temperature=0.2)
    if tts is None:
        def tts(text):
            import voice_studio
            return voice_studio.synthesize(getattr(config, "GEMINI_API_KEY", ""), text, voice_studio.voice_name())
    sessions = {}     # uid -> (lang code, expires monotonic)
    used = {}         # (uid, day) -> turns

    def privileged(uid):
        return uid == getattr(config, "OWNER_ID", 0) or uid in getattr(config, "ADMIN_IDS", [])

    def active(message):
        if getattr(message.chat, "type", None) != "private":
            return False
        s = sessions.get(message.from_user.id)
        if not s:
            return False
        if s[1] < time.monotonic():
            sessions.pop(message.from_user.id, None)
            return False
        return True

    def lang_markup():
        m = InlineKeyboardMarkup()
        btns = [InlineKeyboardButton(label, callback_data=f"ltr:set:{code}") for code, (label, _n) in LANGS.items()]
        for i in range(0, len(btns), 2):
            m.row(*btns[i:i + 2])
        return m

    def stop_markup():
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("🔁 ប្ដូរភាសា", callback_data="ltr:menu"),
              InlineKeyboardButton("⏹ បញ្ចប់", callback_data="ltr:stop"))
        return m

    @bot.message_handler(commands=["live", "livetr", "interpreter"])
    def live_cmd(message):
        if getattr(message.chat, "type", None) != "private":
            bot.reply_to(message, "🎙️ សូមប្រើ /live ក្នុង Private Chat ជាមួយ Bot")
            return
        bot.reply_to(message, "🎙️ <b>បកប្រែ Live — Cooler Voice</b>\n"
                              "ជ្រើសភាសារបស់ភ្ញៀវ 👇 រួចសង្កត់ 🎤 ហើយនិយាយ — Bot បកប្រែ និងនិយាយចេញជាសំឡេងភ្លាម",
                     reply_markup=lang_markup(), parse_mode="HTML")

    @bot.callback_query_handler(func=lambda c: (c.data or "").startswith("ltr:"))
    def ltr_cb(call):
        parts = (call.data or "").split(":")
        uid = call.from_user.id
        bot.answer_callback_query(call.id)
        if parts[1] == "set" and len(parts) > 2 and parts[2] in LANGS:
            sessions[uid] = (parts[2], time.monotonic() + minutes() * 60)
            label = LANGS[parts[2]][0]
            bot.send_message(call.message.chat.id,
                             f"✅ <b>ខ្មែរ ⇄ {html.escape(label)}</b> — បើក {minutes()} នាទី\n\n"
                             "1️⃣ សង្កត់ 🎤 ហើយនិយាយ (ខ្មែរ ឬភាសាភ្ញៀវ)\n"
                             "2️⃣ Bot សរសេរ + និយាយបកប្រែចេញជាសំឡេង 🔊\n"
                             "3️⃣ ឲ្យភ្ញៀវសង្កត់ 🎤 ឆ្លើយវិញ — Bot បកជាខ្មែរ\n"
                             "✍️ វាយអក្សរ → ឆ្លើយជាអក្សរ", reply_markup=stop_markup(), parse_mode="HTML")
        elif parts[1] == "menu":
            bot.send_message(call.message.chat.id, "🌐 ជ្រើសភាសា 👇", reply_markup=lang_markup())
        elif parts[1] == "stop":
            sessions.pop(uid, None)
            bot.send_message(call.message.chat.id, "⏹ បានបញ្ចប់ការបកប្រែ Live")

    def take_turn(uid):
        if privileged(uid):
            return True
        key = (uid, datetime.now(TZ_KH).strftime("%Y-%m-%d"))
        import vip
        if used.get(key, 0) >= vip.cap(uid, daily_limit()):
            return False
        used[key] = used.get(key, 0) + 1
        if len(used) > 5000:
            used.clear()
        return True

    @bot.message_handler(func=lambda m: active(m) and not (m.text or "").startswith("/"),
                         content_types=["voice", "audio", "text"])
    def turn(message):
        uid = message.from_user.id
        code = sessions[uid][0]
        sessions[uid] = (code, time.monotonic() + minutes() * 60)      # keep it open while talking
        if not take_turn(uid):
            import vip
            bot.reply_to(message, f"⏳ ថ្ងៃនេះបកប្រែ Live អស់ {vip.cap(uid, daily_limit())} ដងហើយ — ស្អែកមកម្ដងទៀត 🙏")
            return
        other = LANGS[code][1]
        try:
            bot.send_chat_action(message.chat.id, "typing")
            if message.content_type == "text":
                parts, kind = [], "typed text: " + message.text[:1500]
            else:
                f = message.voice or message.audio
                data = bot.download_file(bot.get_file(f.file_id).file_path)
                mime = getattr(f, "mime_type", None) or "audio/ogg"
                parts, kind = [{"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}}], "a voice message"
            res = parse_reply(ai(parts, PROMPT.format(other=other, code=code, kind=kind)))
        except Exception as exc:
            logger.warning("live translate failed (%s)", type(exc).__name__)
            res = None
        if not res:
            bot.reply_to(message, "😔 ស្ដាប់មិនច្បាស់ — សូមនិយាយម្ដងទៀតឲ្យជិតទូរស័ព្ទ", reply_markup=stop_markup())
            return
        to_khmer = res.get("from") != "km"
        flag = "🇰🇭" if to_khmer else LANGS[code][0].split()[0]
        text = f"{flag} <b>{html.escape(res['translation'])}</b>"
        if message.content_type == "text":          # ✍️ typed → translated text only, right away
            bot.reply_to(message, text, reply_markup=stop_markup(), parse_mode="HTML")
            return
        try:                                         # 🎤 spoken → translated voice (the text rides along as caption)
            bot.send_chat_action(message.chat.id, "record_voice")
            wav = tts(res["translation"][:600])
            import voice_studio
            ogg = voice_studio.wav_to_ogg(wav)
            if ogg:
                bot.send_voice(message.chat.id, ogg, caption=text, parse_mode="HTML", reply_markup=stop_markup(),
                               duration=voice_studio.wav_seconds(wav), reply_to_message_id=message.message_id)
            else:
                bot.send_audio(message.chat.id, io.BytesIO(wav), caption=text, parse_mode="HTML",
                               reply_markup=stop_markup(), title="Cooler Voice")
        except Exception as exc:
            logger.info("live translate voice failed (%s) — text only", type(exc).__name__)
            bot.reply_to(message, text, reply_markup=stop_markup(), parse_mode="HTML")

    return {"sessions": sessions}
