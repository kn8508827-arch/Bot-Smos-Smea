"""🙏 ថ្ងៃសីល reminders and 🕯️ personal anniversaries (solar or Khmer lunar dates).

  /sila → turn the sila-day reminder on/off (a message the evening before at 18:00 and on the
          morning of every ថ្ងៃសីល at SILA_HOUR, default 6:00, Cambodia time)
  /anniv 15/03 ខួបកំណើតម៉ាក់           → yearly reminder on 15 March
  /anniv ១៥កើត មាឃ ខួបលោកតា            → yearly reminder on that lunar day (Khmer or Arabic digits)
  /anniv                                 → list with 🗑️ delete buttons
  Anniversaries are reminded the evening before (18:00) and on the morning of the day.
  🔊 Sila reminders also come as a short Khmer voice message (Gemini TTS, warm voice SILA_VOICE, default
     Sulafat). Made once per reminder and re-sent by file_id, so 1,000 members cost one TTS call.
     SILA_VOICE=off → text only.
"""
import html
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta, timezone

import khmer_calendar as kc

logger = logging.getLogger(__name__)
TZ_KH = timezone(timedelta(hours=7))
EVE_HOUR = 18
MAX_ANNIV = 20
KH_DIGITS = str.maketrans("០១២៣៤៥៦៧៨៩", "0123456789")
PRECEPTS = "សីល ៥៖ មិនសម្លាប់សត្វ · មិនលួច · មិនប្រព្រឹត្តខុសក្នុងកាម · មិនកុហក · មិនផឹកស្រា"


def now_kh():
    return datetime.now(TZ_KH)


def _db(config):
    conn = sqlite3.connect(getattr(config, "BOT_DB_PATH", "bot_database.db"), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(config):
    conn = _db(config)
    try:
        with conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS sila_subs (
                chat_id INTEGER PRIMARY KEY, last_morning TEXT, last_eve TEXT, created_at INTEGER)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS anniversaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, lunar INTEGER NOT NULL DEFAULT 0,
                month INTEGER NOT NULL, day INTEGER NOT NULL, waning INTEGER NOT NULL DEFAULT 0,
                title TEXT NOT NULL, last_morning TEXT, last_eve TEXT, created_at INTEGER)""")
            conn.execute("CREATE INDEX IF NOT EXISTS anniv_chat ON anniversaries(chat_id)")
    finally:
        conn.close()


# ---------------- parsing & matching ----------------

def parse_anniv(text):
    """'15/03 title' or '១៥កើត មាឃ title' → dict or None."""
    t = (text or "").strip().translate(KH_DIGITS)
    m = re.match(r"^(\d{1,2})\s*[/\-.]\s*(\d{1,2})\s+(.+)$", t)
    if m:
        d, mo, title = int(m.group(1)), int(m.group(2)), m.group(3).strip()
        try:
            date(2024, mo, d)            # leap year: allows 29/02
        except ValueError:
            return None
        return {"lunar": 0, "month": mo, "day": d, "waning": 0, "title": title[:80]}
    m = re.match(r"^(\d{1,2})\s*(កើត|រោច)\s*(?:ខែ)?\s*(\S+)\s+(.+)$", t)
    if m:
        d, phase, mname, title = int(m.group(1)), m.group(2), m.group(3), m.group(4).strip()
        if not 1 <= d <= 15:
            return None
        names = list(kc.LUNAR_MONTHS)
        idx = next((i for i, n in enumerate(names) if mname == n or mname.startswith(n) or n.startswith(mname)), None)
        if idx is None:
            return None
        return {"lunar": 1, "month": idx, "day": d, "waning": int(phase == "រោច"), "title": title[:80]}
    return None


def matches(row, d):
    """Does the anniversary fall on date d?"""
    if not row["lunar"]:
        if row["month"] == 2 and row["day"] == 29 and not (d.month == 2 and d.day == 29):
            return d.month == 2 and d.day == 28 and (d + timedelta(days=1)).month == 3   # non-leap years: 28 Feb
        return d.month == row["month"] and d.day == row["day"]
    k = kc.gregorian_to_khmer(d)
    if k.month != row["month"] or bool(k.waning) != bool(row["waning"]):
        return False
    if k.day == row["day"]:
        return True
    # 15រោច in a 29-day month doesn't exist: remind on the month's last day (14រោច)
    return bool(row["waning"]) and row["day"] == 15 and k.day == 14 and k.month_days == 29


def describe(row):
    if row["lunar"]:
        return f"{kc.kh_num(row['day'])}{'រោច' if row['waning'] else 'កើត'} ខែ{kc.LUNAR_MONTHS[row['month']]}"
    return f"{kc.kh_num(row['day'])} {kc.SOLAR_MONTHS[row['month'] - 1]}"


def next_date(row, start, horizon=400):
    for i in range(horizon):
        d = start + timedelta(days=i)
        if matches(row, d):
            return d
    return None


# ---------------- bot wiring ----------------

def voice_on():
    return os.getenv("SILA_VOICE", "Sulafat").strip().lower() not in ("0", "off", "false", "no", "")


def spoken_eve(d):
    return (f"សួស្ដីបងប្អូន! ខ្ញុំ Cooler Bot សូមរំលឹកថា ស្អែកនេះ {kc.format_khmer(d).split(' ឆ្នាំ')[0]} "
            "ជាថ្ងៃសីល។ សូមត្រៀមខ្លួន ទៅវត្ត ឬរក្សាសីលប្រាំនៅផ្ទះ។ សូមឲ្យបងប្អូនមានសេចក្ដីសុខ ចម្រើន។")


def spoken_morning(d):
    return (f"អរុណសួស្ដីបងប្អូន! ថ្ងៃនេះ {kc.format_khmer(d).split(' ឆ្នាំ')[0]} ជាថ្ងៃសីល។ "
            "សីលប្រាំ៖ មិនសម្លាប់សត្វ មិនលួច មិនប្រព្រឹត្តខុសក្នុងកាម មិនកុហក និងមិនផឹកស្រា។ "
            "សូមឲ្យថ្ងៃនេះ ពោរពេញដោយសេចក្ដីសុខ និងបុណ្យកុសល។")


def register(bot, config, speak=None):
    """speak(text) -> OGG/Opus bytes for a voice message (None = text only). Injectable for tests."""
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup
    init_schema(config)
    if speak is None:
        def speak(text):
            import voice_studio
            wav = voice_studio.synthesize(getattr(config, "GEMINI_API_KEY", ""), text,
                                          os.getenv("SILA_VOICE", "Sulafat").strip() or "Sulafat",
                                          style="calm, warm and respectful, gentle pace, like a kind temple announcer")
            return voice_studio.wav_to_ogg(wav) or wav
    voice_cache = {}      # (kind, date) -> bytes or Telegram file_id once uploaded

    def _voice(chat_id, kind, d, script):
        """Send the spoken reminder; first chat uploads it, the rest reuse Telegram's file_id."""
        if not voice_on():
            return
        key = (kind, d.isoformat())
        if key not in voice_cache:
            try:
                voice_cache[key] = speak(script)
            except Exception as exc:
                logger.warning("sila voice failed (%s)", type(exc).__name__)
                voice_cache[key] = None
            for k in [k for k in voice_cache if k[1] < (d - timedelta(days=2)).isoformat()]:
                voice_cache.pop(k, None)
        audio = voice_cache.get(key)
        if not audio:
            return
        try:
            msg = bot.send_voice(chat_id, audio, caption="🔊 រំលឹកថ្ងៃសីល")
            fid = getattr(getattr(msg, "voice", None), "file_id", None) or getattr(getattr(msg, "audio", None), "file_id", None)
            if fid and isinstance(audio, (bytes, bytearray)):
                voice_cache[key] = fid
        except Exception as exc:
            logger.info("sila voice send failed (%s)", type(exc).__name__)

    def sila_on(chat_id):
        conn = _db(config)
        try:
            return conn.execute("SELECT 1 FROM sila_subs WHERE chat_id=?", (chat_id,)).fetchone() is not None
        finally:
            conn.close()

    def sila_text(chat_id):
        today = now_kh().date()
        nxt = None
        for i in range(16):
            if kc.is_sila_day(today + timedelta(days=i)):
                nxt = (i, today + timedelta(days=i))
                break
        hour = int(os.getenv("SILA_HOUR", "6") or 6)
        lines = ["🙏 <b>រំលឹកថ្ងៃសីល</b>", ""]
        if nxt:
            i, d = nxt
            when = "ថ្ងៃនេះ" if i == 0 else "ស្អែក" if i == 1 else f"{kc.kh_num(i)} ថ្ងៃទៀត"
            lines.append(f"ថ្ងៃសីលបន្ទាប់៖ <b>{kc.format_khmer(d).split(' ឆ្នាំ')[0]}</b> ({when})")
        lines.append(f"\nបើកការរំលឹក → Bot ផ្ញើសារ ម៉ោង {kc.kh_num(EVE_HOUR)}:០០ ល្ងាចមុនថ្ងៃសីល "
                     f"និងម៉ោង {kc.kh_num(hour)}:០០ ព្រឹកថ្ងៃសីល" + (" — ជាអក្សរ និង 🔊 សំឡេង" if voice_on() else ""))
        lines.append("\nស្ថានភាព៖ " + ("✅ បើក" if sila_on(chat_id) else "⛔ បិទ"))
        return "\n".join(lines)

    def sila_markup(chat_id):
        m = InlineKeyboardMarkup()
        if sila_on(chat_id):
            m.row(InlineKeyboardButton("🔕 បិទការរំលឹក", callback_data="sila:off"))
        else:
            m.row(InlineKeyboardButton("🔔 បើកការរំលឹកថ្ងៃសីល", callback_data="sila:on"))
        m.row(InlineKeyboardButton("🕯️ ខួប & បុណ្យផ្ទាល់ខ្លួន", callback_data="anniv:list"))
        return m

    @bot.message_handler(commands=["sila", "silaremind"])
    def sila_cmd(message):
        bot.reply_to(message, sila_text(message.chat.id), reply_markup=sila_markup(message.chat.id), parse_mode="HTML")

    @bot.callback_query_handler(func=lambda c: (c.data or "").startswith("sila:"))
    def sila_cb(call):
        chat_id = call.message.chat.id
        if getattr(call.message.chat, "type", "") != "private":
            try:
                member = bot.get_chat_member(chat_id, call.from_user.id)
                if member.status not in ("creator", "administrator"):
                    bot.answer_callback_query(call.id, "សម្រាប់ Admin Group ប៉ុណ្ណោះ")
                    return
            except Exception:
                pass
        conn = _db(config)
        try:
            with conn:
                if call.data == "sila:on":
                    conn.execute("INSERT OR IGNORE INTO sila_subs(chat_id, created_at) VALUES (?, ?)", (chat_id, int(time.time())))
                else:
                    conn.execute("DELETE FROM sila_subs WHERE chat_id=?", (chat_id,))
        finally:
            conn.close()
        bot.answer_callback_query(call.id, "✅ បើករួច 🙏" if call.data == "sila:on" else "🔕 បិទរួច")
        try:
            bot.edit_message_text(sila_text(chat_id), chat_id, call.message.message_id,
                                  reply_markup=sila_markup(chat_id), parse_mode="HTML")
        except Exception:
            pass

    # ----- anniversaries -----
    def anniv_rows(chat_id):
        conn = _db(config)
        try:
            return conn.execute("SELECT * FROM anniversaries WHERE chat_id=? ORDER BY id", (chat_id,)).fetchall()
        finally:
            conn.close()

    def anniv_view(chat_id):
        rows = anniv_rows(chat_id)
        today = now_kh().date()
        lines = ["🕯️ <b>ខួប & បុណ្យផ្ទាល់ខ្លួន</b>", ""]
        m = InlineKeyboardMarkup()
        if not rows:
            lines.append("មិនទាន់មានទេ។")
        for r in rows:
            nd = next_date(r, today)
            left = (nd - today).days if nd else None
            when = "" if nd is None else (" · ថ្ងៃនេះ" if left == 0 else " · ស្អែក" if left == 1 else f" · {kc.kh_num(left)} ថ្ងៃទៀត")
            lines.append(f"• <b>{html.escape(r['title'])}</b> — {describe(r)}{when}")
            m.row(InlineKeyboardButton(f"🗑️ {r['title'][:24]}", callback_data=f"anniv:del:{r['id']}"))
        lines += ["", "➕ បន្ថែម៖", "<code>/anniv 15/03 ខួបកំណើតម៉ាក់</code>", "<code>/anniv ១៥កើត មាឃ ខួបលោកតា</code>",
                  "", f"⏰ Bot រំលឹកម៉ោង {kc.kh_num(EVE_HOUR)}:០០ ល្ងាចមុនថ្ងៃ និងព្រឹកថ្ងៃខួប"]
        return "\n".join(lines), m

    @bot.message_handler(commands=["anniv", "khuob", "birthday"])
    def anniv_cmd(message):
        arg = (message.text or "").split(maxsplit=1)
        if len(arg) == 2:
            parsed = parse_anniv(arg[1])
            if not parsed:
                bot.reply_to(message, "🤔 មិនយល់ទម្រង់ថ្ងៃ។ ឧទាហរណ៍៖\n<code>/anniv 15/03 ខួបកំណើតម៉ាក់</code>\n"
                                      "<code>/anniv ៨រោច ពិសាខ ខួបលោកយាយ</code>", parse_mode="HTML")
                return
            if len(anniv_rows(message.chat.id)) >= MAX_ANNIV:
                bot.reply_to(message, f"⚠️ មានច្រើនពេកហើយ (អតិបរមា {MAX_ANNIV}) — លុបខ្លះសិន /anniv")
                return
            conn = _db(config)
            try:
                with conn:
                    conn.execute("INSERT INTO anniversaries(chat_id, lunar, month, day, waning, title, created_at) "
                                 "VALUES (?, ?, ?, ?, ?, ?, ?)", (message.chat.id, parsed["lunar"], parsed["month"],
                                                                  parsed["day"], parsed["waning"], parsed["title"], int(time.time())))
            finally:
                conn.close()
            nd = next_date(parsed, now_kh().date())
            bot.reply_to(message, f"✅ បានរក្សាទុក៖ <b>{html.escape(parsed['title'])}</b> — {describe(parsed)}"
                                  + (f"\n📅 លើកក្រោយ៖ {kc.format_solar(nd)}" if nd else ""), parse_mode="HTML")
            return
        text, markup = anniv_view(message.chat.id)
        bot.reply_to(message, text, reply_markup=markup, parse_mode="HTML")

    @bot.callback_query_handler(func=lambda c: (c.data or "").startswith("anniv:"))
    def anniv_cb(call):
        chat_id = call.message.chat.id
        parts = call.data.split(":")
        if parts[1] == "del" and len(parts) == 3 and parts[2].isdigit():
            conn = _db(config)
            try:
                with conn:
                    conn.execute("DELETE FROM anniversaries WHERE id=? AND chat_id=?", (int(parts[2]), chat_id))
            finally:
                conn.close()
            bot.answer_callback_query(call.id, "🗑️ លុបរួច")
            text, markup = anniv_view(chat_id)
            try:
                bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=markup, parse_mode="HTML")
            except Exception:
                pass
            return
        bot.answer_callback_query(call.id)
        text, markup = anniv_view(chat_id)
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")

    # ----- scheduler -----
    def _send(chat_id, text):
        try:
            bot.send_message(chat_id, text, parse_mode="HTML")
            return True
        except Exception as exc:
            msg = str(exc).lower()
            if any(w in msg for w in ("blocked", "kicked", "not found", "deactivated")):
                conn = _db(config)
                try:
                    with conn:
                        conn.execute("DELETE FROM sila_subs WHERE chat_id=?", (chat_id,))
                finally:
                    conn.close()
            return False

    def _claim(table, key_col, key, col, today):
        conn = _db(config)
        try:
            with conn:
                return conn.execute(f"UPDATE {table} SET {col}=? WHERE {key_col}=? AND ({col} IS NULL OR {col} != ?)",
                                    (today, key, today)).rowcount == 1
        finally:
            conn.close()

    def tick(now=None):
        now = now or now_kh()
        today, tomorrow = now.date(), now.date() + timedelta(days=1)
        stamp = today.isoformat()
        morning_hour = int(os.getenv("SILA_HOUR", "6") or 6)
        sent = 0
        conn = _db(config)
        try:
            subs = [r["chat_id"] for r in conn.execute("SELECT chat_id FROM sila_subs")]
            annivs = conn.execute("SELECT * FROM anniversaries").fetchall()
        finally:
            conn.close()
        if now.hour >= morning_hour and kc.is_sila_day(today):
            text = (f"🙏 <b>ថ្ងៃនេះជាថ្ងៃសីល</b>\n{kc.format_khmer(today)}\n\n{PRECEPTS}\n\n"
                    "សូមឲ្យមានសេចក្ដីសុខ ចម្រើន 🙏 · /calendar")
            for cid in subs:
                if _claim("sila_subs", "chat_id", cid, "last_morning", stamp) and _send(cid, text):
                    _voice(cid, "morning", today, spoken_morning(today))
                    sent += 1
                    time.sleep(0.05)
        if now.hour >= EVE_HOUR and kc.is_sila_day(tomorrow):
            text = f"🌙 <b>ស្អែកជាថ្ងៃសីល</b> 🙏\n{kc.format_khmer(tomorrow)}"
            for cid in subs:
                if _claim("sila_subs", "chat_id", cid, "last_eve", stamp) and _send(cid, text):
                    _voice(cid, "eve", tomorrow, spoken_eve(tomorrow))
                    sent += 1
                    time.sleep(0.05)
        for r in annivs:
            if now.hour >= morning_hour + 1 and matches(r, today):
                if _claim("anniversaries", "id", r["id"], "last_morning", stamp):
                    _send(r["chat_id"], f"🕯️ <b>ថ្ងៃនេះ៖ {html.escape(r['title'])}</b>\n{kc.format_khmer(today)}\n\n"
                                        "សូមរំលឹក និងបួងសួងសេចក្ដីល្អ 🙏")
                    sent += 1
            if now.hour >= EVE_HOUR and matches(r, tomorrow):
                if _claim("anniversaries", "id", r["id"], "last_eve", stamp):
                    _send(r["chat_id"], f"⏰ <b>ស្អែក៖ {html.escape(r['title'])}</b>\n{kc.format_khmer(tomorrow)}")
                    sent += 1
        return sent

    def start_loop(interval=60):
        def run():
            time.sleep(45)
            while True:
                try:
                    tick()
                except Exception as exc:
                    logger.warning("sila/anniv tick failed (%s)", type(exc).__name__)
                time.sleep(interval)
        threading.Thread(target=run, name="sila-anniv", daemon=True).start()

    class Api:
        pass
    api = Api()
    api.tick = tick
    api.start_loop = start_loop
    return api
