"""More health tools (Khmer): blood-pressure log, water, sleep, AI health Q&A.

General information only — never a diagnosis or a medicine dose.

  /bp         blood-pressure log: /bp 130/85  (last 7 readings + what the numbers mean)
  /water      drinking-water tips for hot days
  /sleep      sleep tips
  /askhealth  ask the AI a health question in simple Khmer (safety rules + 119 for red flags)
"""
import html
import logging
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Phnom_Penh")
MAX_BP_ROWS = 60
DISCLAIMER = "\n\n⚕️ <i>ព័ត៌មានទូទៅប៉ុណ្ណោះ មិនមែនជំនួសគ្រូពេទ្យទេ។</i>"

WATER_TEXT = (
    "💧 <b>ផឹកទឹកឲ្យបានគ្រប់ — ជាពិសេសថ្ងៃក្ដៅ</b>\n\n"
    "• មនុស្សចាស់ច្រើន <b>មិនសូវស្រេក</b> ទោះខ្លួនខ្វះទឹក — កុំរង់ចាំស្រេកទើបផឹក\n"
    "• ផឹកបន្តិចៗ ញឹកៗ ពេញមួយថ្ងៃ (ដាក់ដបទឹកនៅក្បែរខ្លួនជានិច្ច 🍶)\n"
    "• ពណ៌ទឹកនោម <b>លឿងស្រាល</b> = ល្អ · <b>លឿងចាស់</b> = ត្រូវផឹកបន្ថែម\n"
    "• ថ្ងៃក្ដៅ៖ នៅម្លប់ ម៉ោង ១១-៣ រសៀល, ពាក់មួក, ស្លៀកពាក់ស្រាលៗ\n"
    "• ផ្លែឈើមានទឹកច្រើន៖ ឪឡឹក ទឹកដូង ក្រូច ក៏ជួយបាន\n"
    "• ⚠️ កាត់បន្ថយកាហ្វេ តែខ្លាំង ស្រា និងភេសជ្ជៈផ្អែម\n\n"
    "🩺 អ្នកមានជំងឺបេះដូង ឬតម្រងនោម ដែលពេទ្យកំណត់ទឹក — <b>ធ្វើតាមពេទ្យ</b>។\n"
    "🌤️ មើលអាកាសធាតុថ្ងៃនេះ៖ /weather · 🚨 ពុលកម្ដៅ៖ /warning"
)

SLEEP_TEXT = (
    "😴 <b>គេងឲ្យលក់ស្រួល</b>\n\n"
    "• 🕘 ចូលគេង និងក្រោក <b>ម៉ោងដដែល</b> រៀងរាល់ថ្ងៃ\n"
    "• ☀️ ត្រូវពន្លឺថ្ងៃពេលព្រឹក និងដើរលេងពេលថ្ងៃ — ជួយឲ្យយប់ងងុយ\n"
    "• 😪 គេងថ្ងៃខ្លីៗ (មិនលើស ៣០ នាទី) និងមុនម៉ោង ៣ រសៀល\n"
    "• ☕ ជៀសកាហ្វេ តែខ្លាំង ក្រោយម៉ោង ២ រសៀល\n"
    "• 🍚 កុំញ៉ាំបាយច្រើនពេក ឬផឹកទឹកច្រើនមុនគេងភ្លាម\n"
    "• 📱 បិទទូរស័ព្ទ/ទូរទស្សន៍ ៣០ នាទីមុនគេង\n"
    "• 🛏️ បន្ទប់ងងឹត ស្ងាត់ ត្រជាក់ល្មម\n"
    "• 🚽 ក្រោកយប់ញឹក → ទុកភ្លើងតូចបំភ្លឺផ្លូវទៅបន្ទប់ទឹក ការពារការដួល\n\n"
    "👨‍⚕️ ជួបពេទ្យ បើស្រមុកខ្លាំង ហើយដង្ហើមដាច់ៗពេលគេង ឬគេងមិនលក់យូរជាង ១ ខែ។"
)

# Words that mean "go to hospital now" — checked before asking the AI.
RED_FLAGS = (
    "ឈឺទ្រូង", "តឹងទ្រូង", "ដកដង្ហើមមិនរួច", "ថប់ដង្ហើម", "សន្លប់", "មុខវៀច", "និយាយមិនច្បាស់",
    "ស្ពឹកម្ខាង", "ខ្សោយម្ខាង", "ឈាមហូរ", "ក្អួតឈាម", "បាក់ឆ្អឹង", "ប្រកាច់", "ពុលថ្នាំ", "លេបថ្នាំច្រើន",
    "chest pain", "can't breathe", "cannot breathe", "unconscious", "stroke", "seizure", "overdose",
)
EMERGENCY_REPLY = (
    "🚨 <b>នេះអាចជាស្ថានភាពបន្ទាន់!</b>\n\n"
    "🚑 <b>ទូរស័ព្ទ 119 ភ្លាម</b> ឬនាំទៅមន្ទីរពេទ្យជិតបំផុតឥឡូវនេះ — កុំរង់ចាំ។\n"
    "• និយាយទីតាំងឲ្យច្បាស់ ហើយកុំបិទទូរស័ព្ទ\n"
    "• កុំឲ្យអ្នកជំងឺញ៉ាំ ឬផឹកអ្វី បើគាត់មិនដឹងខ្លួនច្បាស់\n\n"
    "🆘 /sos · 🚨 /warning"
)

HEALTH_PROMPT = (
    "You are Cooler Bot's health helper for Cambodian families, especially older people. "
    "Answer this question in simple, warm, natural Khmer, in at most 8 short lines: \"{q}\".\n"
    "Rules: give general, widely accepted health information only. Do NOT diagnose, do NOT name a disease "
    "the person 'has', do NOT give any medicine names with doses, and never tell anyone to stop or change "
    "a prescribed medicine. Suggest simple safe steps (rest, water, food, gentle movement) when suitable, "
    "and say clearly when to see a doctor or health centre. If anything sounds urgent (chest pain, trouble "
    "breathing, stroke signs, heavy bleeding, fainting, seizures, poisoning), tell them to call 119 now. "
    "If the question is not about health, politely say you only answer health questions here. "
    "Plain text, no markdown."
)


@contextmanager
def _connect(config):
    conn = sqlite3.connect(getattr(config, "BOT_DB_PATH", "bot_database.db"), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_schema(config):
    with _connect(config) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS health_bp_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            systolic INTEGER NOT NULL,
            diastolic INTEGER NOT NULL,
            pulse INTEGER,
            taken_at TEXT NOT NULL
        )""")


def parse_bp(text):
    """'130/85' or '130/85 72' -> (sys, dia, pulse|None) or None."""
    m = re.fullmatch(r"\s*(\d{2,3})\s*/\s*(\d{2,3})(?:\s+(\d{2,3}))?\s*", text or "")
    if not m:
        return None
    s, d = int(m.group(1)), int(m.group(2))
    p = int(m.group(3)) if m.group(3) else None
    if not (60 <= s <= 260 and 30 <= d <= 160 and s > d) or (p is not None and not 30 <= p <= 220):
        return None
    return s, d, p


def bp_level(s, d):
    """(emoji, Khmer label, advice) — common adult reference ranges, not a diagnosis."""
    if s >= 180 or d >= 120:
        return ("🔴", "ខ្ពស់ខ្លាំងណាស់",
                "វាស់ម្ដងទៀតក្រោយសម្រាក ៥ នាទី។ បើនៅខ្ពស់ ឬមានឈឺក្បាលខ្លាំង ឈឺទ្រូង ស្ពឹក និយាយមិនច្បាស់ "
                "→ <b>ទូរស័ព្ទ 119 ភ្លាម</b>។")
    if s >= 140 or d >= 90:
        return ("🟠", "ខ្ពស់", "បើវាស់ច្រើនថ្ងៃហើយនៅខ្ពស់ ត្រូវជួបពេទ្យ។ កាត់បន្ថយអំបិល ហើយលេបថ្នាំតាមពេទ្យ។")
    if s >= 120 or d >= 80:
        return ("🟡", "ខ្ពស់បន្តិច", "តាមដានជាប្រចាំ កាត់បន្ថយអំបិល ដើរលេងរាល់ថ្ងៃ។")
    if s < 90 or d < 60:
        return ("🔵", "ទាប", "បើវិលមុខ ឬចង់សន្លប់ ពេលក្រោកឈរ — ក្រោកយឺតៗ ផឹកទឹក ហើយប្រាប់ពេទ្យ។")
    return ("🟢", "ធម្មតា", "ល្អណាស់! បន្តរក្សាទម្លាប់ល្អ 👍")


def has_red_flag(text):
    low = (text or "").casefold()
    return any(flag in low for flag in RED_FLAGS)


def register(bot, config, ask_gemini=None):
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    init_schema(config)
    waiting = {}  # user_id -> expires_at for /askhealth
    ai_used = {}  # (user_id, date) -> count
    ai_lock = threading.Lock()
    ai_limit = 5

    def back():
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("🔙 សុខភាព", callback_data="health:menu"),
              InlineKeyboardButton("🏠 មឺនុយមេ", callback_data="nav:main"))
        return m

    def reply(message, text, markup=None):
        bot.reply_to(message, text, reply_markup=markup or back(), parse_mode="HTML")

    # ---------- 🩸 blood pressure log ----------
    def bp_view(user_id):
        with _connect(config) as conn:
            rows = conn.execute("SELECT systolic, diastolic, pulse, taken_at FROM health_bp_log WHERE user_id=? "
                                "ORDER BY id DESC LIMIT 7", (user_id,)).fetchall()
        lines = ["🩸 <b>កំណត់ត្រាសម្ពាធឈាម</b>", ""]
        if rows:
            for r in rows:
                emoji, label, _ = bp_level(r["systolic"], r["diastolic"])
                pulse = f" · ❤️ {r['pulse']}" if r["pulse"] else ""
                lines.append(f"{emoji} {r['systolic']}/{r['diastolic']}{pulse} — {label} · {r['taken_at'][5:16]}")
        else:
            lines.append("មិនទាន់មានកំណត់ត្រាទេ។")
        lines += ["", "➕ <b>កត់ត្រា</b>៖ <code>/bp 130/85</code> (ឬ <code>/bp 130/85 72</code> បើមានជីពចរ)",
                  "💡 វាស់ពេលអង្គុយសម្រាក ៥ នាទី ពេលព្រឹក និងល្ងាច ហើយយកកំណត់ត្រានេះទៅបង្ហាញពេទ្យ។"]
        return "\n".join(lines) + DISCLAIMER

    @bot.message_handler(commands=["bp", "pressure"])
    def bp(message):
        user_id = message.from_user.id
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) > 1:
            reading = parse_bp(parts[1])
            if not reading:
                reply(message, "❓ សរសេរបែបនេះ៖ <code>/bp 130/85</code> (លេខខាងលើ/លេខខាងក្រោម)")
                return
            s, d, p = reading
            now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
            with _connect(config) as conn:
                conn.execute("INSERT INTO health_bp_log(user_id, systolic, diastolic, pulse, taken_at) VALUES (?, ?, ?, ?, ?)",
                             (user_id, s, d, p, now))
                conn.execute("DELETE FROM health_bp_log WHERE user_id=? AND id NOT IN (SELECT id FROM health_bp_log "
                             "WHERE user_id=? ORDER BY id DESC LIMIT ?)", (user_id, user_id, MAX_BP_ROWS))
            emoji, label, advice = bp_level(s, d)
            reply(message, f"{emoji} <b>{s}/{d}</b> — {label}\n{advice}\n\n" + bp_view(user_id))
            return
        reply(message, bp_view(user_id))

    # ---------- 💧 water / 😴 sleep ----------
    @bot.message_handler(commands=["water"])
    def water(message):
        reply(message, WATER_TEXT + DISCLAIMER)

    @bot.message_handler(commands=["sleep"])
    def sleep(message):
        reply(message, SLEEP_TEXT + DISCLAIMER)

    # ---------- 🤖 AI health questions ----------
    def answer(message, question):
        question = " ".join((question or "").split())[:300]
        if not question:
            return
        if has_red_flag(question):
            reply(message, EMERGENCY_REPLY)
            return
        if ask_gemini is None or not getattr(config, "GEMINI_API_KEY", ""):
            reply(message, "AI មិនទាន់បើកដំណើរការនៅឡើយទេ។ មើល /health សិន។")
            return
        user_id = message.from_user.id
        key = (user_id, datetime.now(TZ).strftime("%Y-%m-%d"))
        unlimited = user_id == getattr(config, "OWNER_ID", 0) or user_id in getattr(config, "ADMIN_IDS", [])
        with ai_lock:
            import vip
            lim = vip.cap(user_id, ai_limit, config)
            if not unlimited and ai_used.get(key, 0) >= lim:
                reply(message, f"អ្នកសួរអស់ {lim} ដងសម្រាប់ថ្ងៃនេះហើយ។ មើល /health ឬត្រឡប់មកថ្ងៃស្អែក 🙏")
                return
            ai_used[key] = ai_used.get(key, 0) + 1
        try:
            bot.send_chat_action(message.chat.id, "typing")
        except Exception:
            pass
        try:
            text = (ask_gemini(HEALTH_PROMPT.format(q=question)) or "").strip()
        except Exception as exc:
            logger.warning("askhealth failed (%s)", type(exc).__name__)
            text = ""
        if not text:
            reply(message, "សុំទោស AI មិនអាចឆ្លើយបានទេឥឡូវនេះ។ សូមសាកម្ដងទៀត។")
            return
        reply(message, "🩺 <b>ចម្លើយ</b>\n\n" + html.escape(text[:3000]) + DISCLAIMER)

    @bot.message_handler(commands=["askhealth", "doctor"])
    def askhealth(message):
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) > 1:
            answer(message, parts[1])
            return
        waiting[message.from_user.id] = time.monotonic() + 300
        reply(message, "🩺 <b>សួរអំពីសុខភាព</b>\nសរសេរសំណួររបស់អ្នកមក (ឧ. <i>ឈឺជង្គង់ពេលឡើងជណ្ដើរ គួរធ្វើយ៉ាងម៉េច?</i>)\n\n"
                       "🚨 បើបន្ទាន់ ទូរស័ព្ទ <b>119</b> ភ្លាម")

    @bot.callback_query_handler(func=lambda call: (call.data or "").startswith("hmore:"))
    def more_cb(call):
        key = (call.data or "").split(":", 1)[1]
        bot.answer_callback_query(call.id)
        user_id = call.from_user.id
        chat = call.message.chat
        markup = back()
        if key == "water":
            text = WATER_TEXT + DISCLAIMER
        elif key == "sleep":
            text = SLEEP_TEXT + DISCLAIMER
        elif key == "bp":
            text = bp_view(user_id)
        elif key == "ask":
            waiting[user_id] = time.monotonic() + 300
            text = ("🩺 <b>សួរអំពីសុខភាព</b>\nសរសេរសំណួររបស់អ្នកមក (ឧ. <i>ឈឺជង្គង់ពេលឡើងជណ្ដើរ គួរធ្វើយ៉ាងម៉េច?</i>)\n\n"
                    "🚨 បើបន្ទាន់ ទូរស័ព្ទ <b>119</b> ភ្លាម")
        else:
            return
        try:
            bot.edit_message_text(text, chat.id, call.message.message_id, reply_markup=markup, parse_mode="HTML")
        except Exception as exc:
            if "message is not modified" not in str(exc):
                bot.send_message(chat.id, text, reply_markup=markup, parse_mode="HTML")

    def is_waiting(message):
        user = getattr(message, "from_user", None)
        if user is None or not getattr(message, "text", None) or message.text.startswith("/"):
            return False
        exp = waiting.get(user.id)
        return bool(exp and exp > time.monotonic())

    @bot.message_handler(func=is_waiting, content_types=["text"])
    def askhealth_follow_up(message):
        waiting.pop(message.from_user.id, None)
        answer(message, message.text)
