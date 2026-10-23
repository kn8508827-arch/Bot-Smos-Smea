"""Everyday helpers (Khmer UI).

  🚨 /scam  — reply to (or paste) a suspicious message/link/photo → AI says how risky it is and why
  🎤 /totext — reply to a voice message (or send /totext then a voice) → Khmer text to copy
  📷 /translate — reply to a photo or text → Khmer translation (or Khmer → English)
     /homework — reply to a photo of an exercise (or type it) → step-by-step explanation in Khmer
  ⏰ /remind <anything in Khmer/English> → AI understands the time; once / daily / weekly / monthly
     /reminders → list with 🗑️ buttons
  💵 /cash ចាយ 5000 រៀល កាហ្វេ · /cash លក់បាន $20 → saved; /report → this month by category
     (per chat: a private chat is your own book, a group is the shop's shared book)
"""
import base64
import html
import json
import logging
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)
TZ_KH = timezone(timedelta(hours=7))
KHR_PER_USD_FALLBACK = 4000

SCAM_PROMPT = """You are a Cambodian anti-scam assistant. Judge if the content below is a scam or phishing attempt.
Common Cambodian scams: fake ABA/ACLEDA/Wing/Bakong links, "you won a prize" messages, fake jobs with high pay,
asking for OTP/PIN/password, fake Facebook/Telegram login pages, investment/crypto doubling, fake delivery fees,
romance/loan scams, impersonating police/bank/family. Reply in Khmer, short:
Line 1: one of "🔴 គ្រោះថ្នាក់ខ្ពស់", "🟠 គួរប្រយ័ត្ន", "🟢 ហាក់ដូចជាមានសុវត្ថិភាព", plus a risk percent.
Then 2-4 bullet reasons (point at concrete signs: domain spelling, urgency, money/OTP requests).
Then 1-2 bullets "ធ្វើអ្វី" (e.g. don't click, call the bank's official number, block & report).
Never say something is 100% safe. Content:
"""
TRANSLATE_PROMPT = ("Translate the text (or all text in the image) into natural Khmer. If it is already Khmer, translate "
                    "into English. Keep names, numbers, prices. Output only the translation, keep line breaks.")
HOMEWORK_PROMPT = ("You are a patient Khmer teacher. Solve/explain this exercise step by step in simple Khmer for a "
                   "student; show the working, then the final answer clearly. If there are several questions, do each. "
                   "Encourage the student to try similar ones themselves.")
TOTEXT_PROMPT = ("Transcribe this voice message exactly as spoken, in the language spoken (Khmer in Khmer script). "
                 "Add normal punctuation. Output only the text.")
REMIND_PROMPT = """Now in Cambodia (UTC+7) it is {now} ({weekday}). Parse this reminder request: "{text}"
Return ONLY JSON: {{"when": "YYYY-MM-DD HH:MM" (Cambodia time, future), "repeat": "none|daily|weekly|monthly",
"what": "short reminder text in the user's language"}}. If no time is given use 08:00. If the date is unclear,
choose the nearest sensible future time. If it is not a reminder at all return {{"error": "no"}}."""

CATEGORIES = {
    "🍜 អាហារ": ("បាយ", "ញាំ", "ម្ហូប", "កាហ្វេ", "តែ", "ភេសជ្ជៈ", "ទឹក", "នំ", "food", "coffee", "lunch", "dinner", "breakfast"),
    "🚗 ធ្វើដំណើរ": ("សាំង", "ម៉ូតូ", "ឡាន", "តុកតុក", "grab", "passapp", "taxi", "fuel", "gas", "ផ្លូវ"),
    "🏠 ផ្ទះ": ("ភ្លើង", "ទឹកភ្លើង", "ជួល", "ផ្ទះ", "rent", "electric", "internet", "wifi"),
    "📱 ទូរស័ព្ទ": ("កាត", "លុយទូរស័ព្ទ", "top up", "topup", "data", "phone"),
    "🛍️ ទិញឥវ៉ាន់": ("ទិញ", "អាវ", "ខោ", "ឥវ៉ាន់", "shop", "shopping", "ផ្សារ"),
    "💊 សុខភាព": ("ថ្នាំ", "ពេទ្យ", "clinic", "hospital", "medicine"),
    "🎉 កម្សាន្ត": ("ភាពយន្ត", "game", "ហ្គេម", "party", "ស្រា", "ពិធី", "concert"),
}
INCOME_WORDS = ("លក់", "ចំណូល", "ទទួល", "ប្រាក់ខែ", "បានលុយ", "income", "sold", "sale", "salary", "earn", "+")
EXPENSE_WORDS = ("ចាយ", "ទិញ", "បង់", "spent", "spend", "pay", "paid", "-")


def kh_digits_to_ascii(text):
    return (text or "").translate(str.maketrans("០១២៣៤៥៦៧៨៩", "0123456789"))


def parse_cash(text):
    """'ចាយ 5000 រៀល កាហ្វេ' -> {'kind': 'out', 'amount': 5000.0, 'currency': 'KHR', 'note': 'កាហ្វេ', 'category': ...}"""
    t = kh_digits_to_ascii(text).strip()
    m = re.search(r"(\$\s*)?(\d[\d,]*(?:\.\d+)?)\s*(\$|usd|ដុល្លារ|dollar|រៀល|៛|khr|riel|k\b)?", t, re.I)
    if not m:
        return None
    amount = float(m.group(2).replace(",", ""))
    unit = (m.group(3) or "").lower()
    if m.group(1) or unit in ("$", "usd", "ដុល្លារ", "dollar"):
        currency = "USD"
    elif unit in ("រៀល", "៛", "khr", "riel"):
        currency = "KHR"
    elif unit == "k":
        currency, amount = "KHR", amount * 1000
    else:
        currency = "KHR" if amount >= 100 else "USD"
    low = t.lower()
    kind = "in" if any(w in low for w in INCOME_WORDS) and not any(w in low for w in ("ចាយ", "ទិញ", "spent", "spend")) else "out"
    note = (t[:m.start()] + " " + t[m.end():]).strip()
    for w in INCOME_WORDS + EXPENSE_WORDS + ("បាន",):
        note = re.sub(rf"(^|\s){re.escape(w)}(\s|$)", " ", note, flags=re.I) if w.isascii() else note.replace(w, " ")
    note = re.sub(r"\s+", " ", note).strip(" -:,")[:60]
    category = "💰 ចំណូល" if kind == "in" else next(
        (c for c, words in CATEGORIES.items() if any(w in low for w in words)), "📦 ផ្សេងៗ")
    if amount <= 0:
        return None
    return {"kind": kind, "amount": amount, "currency": currency, "note": note, "category": category}


def parse_reminder_json(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    if data.get("error") or not data.get("when"):
        return None
    try:
        when = datetime.strptime(data["when"].strip()[:16], "%Y-%m-%d %H:%M").replace(tzinfo=TZ_KH)
    except ValueError:
        return None
    repeat = data.get("repeat") if data.get("repeat") in ("none", "daily", "weekly", "monthly") else "none"
    return {"when": when, "repeat": repeat, "what": str(data.get("what") or "")[:200]}


def next_time(when, repeat):
    if repeat == "daily":
        return when + timedelta(days=1)
    if repeat == "weekly":
        return when + timedelta(days=7)
    if repeat == "monthly":
        month = when.month % 12 + 1
        year = when.year + (1 if when.month == 12 else 0)
        for day in (when.day, 30, 29, 28):
            try:
                return when.replace(year=year, month=month, day=day)
            except ValueError:
                continue
    return None


def _db(config):
    conn = sqlite3.connect(getattr(config, "BOT_DB_PATH", "bot_database.db"), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(config):
    conn = _db(config)
    try:
        with conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, due INTEGER NOT NULL, repeat TEXT NOT NULL,
                what TEXT NOT NULL, created_at INTEGER NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS reminders_due ON reminders(due)")
            conn.execute("""CREATE TABLE IF NOT EXISTS cashbook (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
                user_id INTEGER, kind TEXT NOT NULL, amount REAL NOT NULL, currency TEXT NOT NULL, category TEXT,
                note TEXT, at INTEGER NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS cashbook_chat ON cashbook(chat_id, at)")
    finally:
        conn.close()


def fmt_money(amount, currency):
    return f"{amount:,.0f} ៛" if currency == "KHR" else f"${amount:,.2f}".replace(".00", "")


def register(bot, config, ai=None, rate=None):
    """ai(parts, prompt_text) -> text (parts = Gemini inline parts, may be []); rate() -> KHR per USD."""
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    init_schema(config)
    if ai is None:
        def ai(parts, prompt):
            import knowledge_base
            return knowledge_base.gemini(getattr(config, "GEMINI_API_KEY", ""), list(parts) + [{"text": prompt}],
                                         max_tokens=2500, temperature=0.2)
    if rate is None:
        def rate():
            try:
                import market_info
                r = market_info.fetch_rates()
                return float(r["rates"]["KHR"]) if r else KHR_PER_USD_FALLBACK
            except Exception:
                return KHR_PER_USD_FALLBACK
    waiting_voice = {}   # user_id -> expires (monotonic)

    def now_kh():
        return datetime.now(TZ_KH)

    def media_parts(msg):
        """Gemini inline parts for a photo / voice / audio / document image in a message."""
        if msg is None:
            return [], ""
        file_id, mime = None, None
        if getattr(msg, "photo", None):
            file_id, mime = msg.photo[-1].file_id, "image/jpeg"
        elif getattr(msg, "voice", None):
            file_id, mime = msg.voice.file_id, getattr(msg.voice, "mime_type", None) or "audio/ogg"
        elif getattr(msg, "audio", None):
            file_id, mime = msg.audio.file_id, getattr(msg.audio, "mime_type", None) or "audio/mpeg"
        elif getattr(msg, "document", None) and (getattr(msg.document, "mime_type", "") or "").startswith(("image/", "application/pdf")):
            file_id, mime = msg.document.file_id, msg.document.mime_type
        text = getattr(msg, "text", None) or getattr(msg, "caption", None) or ""
        if not file_id:
            return [], text
        data = bot.download_file(bot.get_file(file_id).file_path)
        if len(data) > 15 * 1024 * 1024:
            raise ValueError("file too big")
        return [{"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}}], text

    def run_ai(message, parts, prompt, header, empty_hint):
        try:
            bot.send_chat_action(message.chat.id, "typing")
            out = (ai(parts, prompt) or "").strip()
        except Exception as exc:
            logger.warning("daily tool AI failed (%s)", type(exc).__name__)
            out = ""
        if not out:
            bot.reply_to(message, "😔 AI រវល់ — សាកម្ដងទៀតបន្តិចទៀត")
            return
        bot.reply_to(message, header + html.escape(out[:3800]), parse_mode="HTML")

    def arg_and_source(message):
        parts = (message.text or message.caption or "").split(maxsplit=1)
        return (parts[1].strip() if len(parts) > 1 else ""), getattr(message, "reply_to_message", None)

    # ---------- 🚨 scam ----------
    @bot.message_handler(commands=["scam", "checkscam", "bok"])
    def scam_cmd(message):
        arg, src = arg_and_source(message)
        try:
            parts, src_text = media_parts(src)
        except Exception:
            parts, src_text = [], ""
        content = "\n".join(x for x in (src_text, arg) if x)
        if not content and not parts:
            bot.reply_to(message, "🚨 <b>ឆែក Scam</b>\nReply <code>/scam</code> លើសារ/រូប ដែលគួរសង្ស័យ ឬវាយ៖\n"
                                  "<code>/scam https://aba-bonus.xyz ឈ្នះ $500</code>", parse_mode="HTML")
            return
        fwd = src is not None and (getattr(src, "forward_origin", None) or getattr(src, "forward_from", None)
                                   or getattr(src, "forward_from_chat", None))
        prompt = SCAM_PROMPT + content[:3000] + ("\n(This was forwarded from someone else.)" if fwd else "")
        run_ai(message, parts, prompt, "🚨 <b>លទ្ធផលឆែក Scam</b>\n\n", "")

    # ---------- 🎤 voice → text ----------
    def transcribe(message, voice_msg):
        try:
            parts, _ = media_parts(voice_msg)
        except Exception:
            parts = []
        if not parts:
            bot.reply_to(message, "🎤 Reply <code>/totext</code> លើសារ Voice ឬវាយ /totext រួចផ្ញើ Voice មក", parse_mode="HTML")
            return
        try:
            bot.send_chat_action(message.chat.id, "typing")
            text = (ai(parts, TOTEXT_PROMPT) or "").strip()
        except Exception:
            text = ""
        if not text:
            bot.reply_to(message, "😔 ស្ដាប់មិនច្បាស់ — សាកថតម្ដងទៀតនៅកន្លែងស្ងាត់")
            return
        bot.reply_to(message,
                     f"📝 <b>អក្សរ</b> (ចុចលើអក្សរដើម្បី Copy)\n\n<code>{html.escape(text[:3800])}</code>", parse_mode="HTML")

    @bot.message_handler(commands=["totext", "type", "voicetext"])
    def totext_cmd(message):
        _arg, src = arg_and_source(message)
        if src is not None and (getattr(src, "voice", None) or getattr(src, "audio", None)):
            transcribe(message, src)
            return
        waiting_voice[message.from_user.id] = time.monotonic() + 300
        bot.reply_to(message, "🎤 ផ្ញើ Voice មកឥឡូវ — Bot នឹងសរសេរជាអក្សរខ្មែរឲ្យ (ក្នុង ៥ នាទី)")

    def wants_text(message):
        uid = getattr(getattr(message, "from_user", None), "id", None)
        exp = waiting_voice.get(uid)
        return exp is not None and exp > time.monotonic()

    @bot.message_handler(func=wants_text, content_types=["voice", "audio"])
    def voice_to_text(message):
        waiting_voice.pop(message.from_user.id, None)
        transcribe(message, message)

    # ---------- 📷 translate / homework ----------
    def media_tool(message, prompt, header, usage):
        arg, src = arg_and_source(message)
        target = src if src is not None else (message if getattr(message, "photo", None) else None)
        try:
            parts, src_text = media_parts(target)
        except Exception:
            parts, src_text = [], ""
        text = "\n".join(x for x in (src_text if target is not message else "", arg) if x)
        if not parts and not text:
            bot.reply_to(message, usage, parse_mode="HTML")
            return
        run_ai(message, parts, prompt + ("\n\nText:\n" + text[:4000] if text else ""), header, "")

    @bot.message_handler(commands=["translate", "tr", "bokprae"])
    def translate_cmd(message):
        media_tool(message, TRANSLATE_PROMPT, "🌐 <b>បកប្រែ</b>\n\n",
                   "🌐 <b>បកប្រែ</b>\nReply <code>/translate</code> លើរូប (ម៉ឺនុយ ឯកសារ ផ្លាក) ឬសារ ឬវាយ "
                   "<code>/translate Hello, how much?</code>")

    @bot.message_handler(commands=["homework", "hw", "lomhat"])
    def homework_cmd(message):
        media_tool(message, HOMEWORK_PROMPT, "📚 <b>ពន្យល់លំហាត់</b>\n\n",
                   "📚 <b>ជួយលំហាត់</b>\nថតរូបលំហាត់ ហើយ Reply <code>/homework</code> ឬដាក់ <code>/homework</code> "
                   "ជា Caption ឬវាយ <code>/homework 2x + 5 = 15</code>")

    def caption_cmd(message):
        cap = (getattr(message, "caption", None) or "").strip().lower()
        return cap.split()[0].split("@")[0] in ("/translate", "/tr", "/homework", "/hw", "/scam") if cap else False

    @bot.message_handler(func=caption_cmd, content_types=["photo", "document"])
    def caption_tools(message):
        cmd = message.caption.strip().split()[0].split("@")[0].lower()
        message.reply_to_message = message
        if cmd in ("/translate", "/tr"):
            media_tool(message, TRANSLATE_PROMPT, "🌐 <b>បកប្រែ</b>\n\n", "")
        elif cmd == "/scam":
            scam_cmd(message)
        else:
            media_tool(message, HOMEWORK_PROMPT, "📚 <b>ពន្យល់លំហាត់</b>\n\n", "")

    # ---------- ⏰ reminders ----------
    REPEAT_KH = {"none": "", "daily": " · 🔁 រាល់ថ្ងៃ", "weekly": " · 🔁 រាល់សប្ដាហ៍", "monthly": " · 🔁 រាល់ខែ"}

    def fmt_when(ts):
        return datetime.fromtimestamp(ts, TZ_KH).strftime("%d/%m/%Y %H:%M")

    @bot.message_handler(commands=["remind", "reminder", "romlek"])
    def remind_cmd(message):
        arg, _src = arg_and_source(message)
        if not arg:
            bot.reply_to(message, "⏰ <b>រំលឹក</b> — វាយធម្មតា ឧ.\n<code>/remind ស្អែកម៉ោង ២ រសៀល ណាត់ជួបភ្ញៀវ</code>\n"
                                  "<code>/remind ថ្ងៃទី ៥ រៀងរាល់ខែ បង់ថ្លៃភ្លើង</code>\n"
                                  "<code>/remind every day 6am exercise</code>\n📋 /reminders — មើល/លុប", parse_mode="HTML")
            return
        now = now_kh()
        conn = _db(config)
        try:
            count = conn.execute("SELECT COUNT(*) FROM reminders WHERE chat_id=?", (message.chat.id,)).fetchone()[0]
        finally:
            conn.close()
        if count >= 30:
            bot.reply_to(message, "📋 មានការរំលឹក ៣០ ហើយ — លុបខ្លះសិន /reminders")
            return
        try:
            raw = ai([], REMIND_PROMPT.format(now=now.strftime("%Y-%m-%d %H:%M"), weekday=now.strftime("%A"),
                                              text=kh_digits_to_ascii(arg)[:300]))
        except Exception:
            raw = ""
        rem = parse_reminder_json(raw)
        if not rem:
            bot.reply_to(message, "🤔 មិនយល់ពេលវេលា — សាកសរសេរឲ្យច្បាស់ ឧ. «ស្អែក ម៉ោង ៩ ព្រឹក ...»")
            return
        when = rem["when"]
        if when <= now:
            nxt = next_time(when, rem["repeat"])
            while nxt is not None and nxt <= now:
                nxt = next_time(nxt, rem["repeat"])
            if nxt is None:
                bot.reply_to(message, "⌛ ពេលនោះកន្លងហើយ — សូមដាក់ពេលខាងមុខ")
                return
            when = nxt
        what = rem["what"] or arg[:200]
        conn = _db(config)
        try:
            with conn:
                rid = conn.execute("INSERT INTO reminders(chat_id, user_id, due, repeat, what, created_at) VALUES (?,?,?,?,?,?)",
                                   (message.chat.id, message.from_user.id, int(when.timestamp()), rem["repeat"], what,
                                    int(time.time()))).lastrowid
        finally:
            conn.close()
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("🗑️ បោះបង់", callback_data=f"rem:del:{rid}"))
        bot.reply_to(message, f"✅ នឹងរំលឹក <b>{fmt_when(int(when.timestamp()))}</b>{REPEAT_KH[rem['repeat']]}\n📝 "
                              f"{html.escape(what)}", reply_markup=m, parse_mode="HTML")

    @bot.message_handler(commands=["reminders", "myreminders"])
    def reminders_cmd(message):
        conn = _db(config)
        try:
            rows = conn.execute("SELECT * FROM reminders WHERE chat_id=? ORDER BY due LIMIT 30", (message.chat.id,)).fetchall()
        finally:
            conn.close()
        if not rows:
            bot.reply_to(message, "📋 មិនទាន់មានការរំលឹកទេ — /remind ...")
            return
        m = InlineKeyboardMarkup()
        lines = ["⏰ <b>ការរំលឹក</b>"]
        for r in rows:
            lines.append(f"• {fmt_when(r['due'])}{REPEAT_KH.get(r['repeat'], '')} — {html.escape(r['what'][:60])}")
            m.row(InlineKeyboardButton(f"🗑️ {r['what'][:30]}", callback_data=f"rem:del:{r['id']}"))
        bot.reply_to(message, "\n".join(lines), reply_markup=m, parse_mode="HTML")

    @bot.callback_query_handler(func=lambda c: (c.data or "").startswith("rem:del:"))
    def rem_cb(call):
        try:
            rid = int(call.data.split(":")[2])
        except (IndexError, ValueError):
            bot.answer_callback_query(call.id)
            return
        conn = _db(config)
        try:
            with conn:
                row = conn.execute("SELECT user_id FROM reminders WHERE id=? AND chat_id=?",
                                   (rid, call.message.chat.id)).fetchone()
                is_owner_of = row is not None and row["user_id"] == call.from_user.id
                is_admin = False
                if row is not None and not is_owner_of and getattr(call.message.chat, "type", "") != "private":
                    try:
                        is_admin = bot.get_chat_member(call.message.chat.id, call.from_user.id).status in ("creator", "administrator")
                    except Exception:
                        pass
                if row is not None and (is_owner_of or is_admin):
                    conn.execute("DELETE FROM reminders WHERE id=?", (rid,))
                    bot.answer_callback_query(call.id, "🗑️ បានលុប")
                else:
                    bot.answer_callback_query(call.id, "មិនអាចលុបបានទេ", show_alert=True)
        finally:
            conn.close()

    def tick(now_ts=None):
        now_ts = int(now_ts or time.time())
        conn = _db(config)
        try:
            rows = conn.execute("SELECT * FROM reminders WHERE due <= ? ORDER BY due LIMIT 50", (now_ts,)).fetchall()
        finally:
            conn.close()
        sent = 0
        for r in rows:
            nxt = next_time(datetime.fromtimestamp(r["due"], TZ_KH), r["repeat"])
            while nxt is not None and nxt.timestamp() <= now_ts:
                nxt = next_time(nxt, r["repeat"])
            conn = _db(config)
            try:
                with conn:
                    if nxt is None:
                        claimed = conn.execute("DELETE FROM reminders WHERE id=? AND due=?", (r["id"], r["due"])).rowcount
                    else:
                        claimed = conn.execute("UPDATE reminders SET due=? WHERE id=? AND due=?",
                                               (int(nxt.timestamp()), r["id"], r["due"])).rowcount
            finally:
                conn.close()
            if not claimed:
                continue
            mention = "" if r["chat_id"] == r["user_id"] else f'<a href="tg://user?id={r["user_id"]}">🔔</a> '
            try:
                bot.send_message(r["chat_id"], f"⏰ {mention}<b>រំលឹក៖</b> {html.escape(r['what'])}", parse_mode="HTML")
                sent += 1
            except Exception as exc:
                logger.info("reminder send failed (%s)", type(exc).__name__)
        return sent

    def start_loop(interval=30):
        def run():
            time.sleep(15)
            while True:
                try:
                    tick()
                except Exception as exc:
                    logger.warning("reminder tick failed (%s)", type(exc).__name__)
                time.sleep(interval)
        threading.Thread(target=run, name="reminders", daemon=True).start()

    # ---------- 💵 cashbook ----------
    @bot.message_handler(commands=["cash", "spend", "chay", "sale"])
    def cash_cmd(message):
        arg, _src = arg_and_source(message)
        cmd = (message.text or "").split()[0][1:].split("@")[0].lower()
        if cmd == "sale" and arg:
            arg = "លក់ " + arg
        entry = parse_cash(arg) if arg else None
        if not entry:
            bot.reply_to(message, "💵 <b>កត់ចំណាយ / ចំណូល</b>\n<code>/cash ចាយ 5000 រៀល កាហ្វេ</code>\n"
                                  "<code>/cash លក់បាន $20 អាវ</code>\n<code>/cash ចាយ 15k សាំង</code>\n"
                                  "📊 /report — សរុបខែនេះ · ↩️ /undo — លុបចុងក្រោយ", parse_mode="HTML")
            return
        conn = _db(config)
        try:
            with conn:
                conn.execute("INSERT INTO cashbook(chat_id, user_id, kind, amount, currency, category, note, at) "
                             "VALUES (?,?,?,?,?,?,?,?)", (message.chat.id, message.from_user.id, entry["kind"],
                                                          entry["amount"], entry["currency"], entry["category"],
                                                          entry["note"], int(time.time())))
        finally:
            conn.close()
        sign = "➕" if entry["kind"] == "in" else "➖"
        bot.reply_to(message, f"{sign} {fmt_money(entry['amount'], entry['currency'])} · {entry['category']}"
                              f"{' · ' + html.escape(entry['note']) if entry['note'] else ''}\n📊 /report", parse_mode="HTML")

    @bot.message_handler(commands=["undo"])
    def undo_cmd(message):
        conn = _db(config)
        try:
            with conn:
                row = conn.execute("SELECT id, amount, currency FROM cashbook WHERE chat_id=? AND user_id=? ORDER BY id DESC "
                                   "LIMIT 1", (message.chat.id, message.from_user.id)).fetchone()
                if row:
                    conn.execute("DELETE FROM cashbook WHERE id=?", (row["id"],))
        finally:
            conn.close()
        bot.reply_to(message, f"↩️ បានលុប {fmt_money(row['amount'], row['currency'])}" if row else "គ្មានអ្វីលុបទេ")

    def report_text(chat_id, now=None):
        now = now or now_kh()
        start = int(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
        conn = _db(config)
        try:
            rows = conn.execute("SELECT kind, amount, currency, category FROM cashbook WHERE chat_id=? AND at >= ?",
                                (chat_id, start)).fetchall()
        finally:
            conn.close()
        if not rows:
            return "📊 ខែនេះមិនទាន់មានកំណត់ត្រាទេ — /cash ..."
        khr = rate()
        to_usd = lambda r: r["amount"] if r["currency"] == "USD" else r["amount"] / khr
        income = sum(to_usd(r) for r in rows if r["kind"] == "in")
        spent = sum(to_usd(r) for r in rows if r["kind"] == "out")
        cats = {}
        for r in rows:
            if r["kind"] == "out":
                cats[r["category"]] = cats.get(r["category"], 0) + to_usd(r)
        lines = [f"📊 <b>របាយការណ៍ខែ {now.strftime('%m/%Y')}</b>",
                 f"➕ ចំណូល៖ <b>${income:,.2f}</b>", f"➖ ចំណាយ៖ <b>${spent:,.2f}</b>",
                 f"{'💚' if income >= spent else '❤️'} សល់៖ <b>${income - spent:,.2f}</b> (≈ {(income - spent) * khr:,.0f} ៛)"]
        if cats:
            lines.append("\n<b>ចំណាយតាមប្រភេទ</b>")
            for cat, v in sorted(cats.items(), key=lambda kv: -kv[1]):
                pct = round(100 * v / spent) if spent else 0
                lines.append(f"{cat}៖ ${v:,.2f} ({pct}%)")
        lines.append(f"\n<i>1$ ≈ {khr:,.0f} ៛ · {len(rows)} កំណត់ត្រា</i>")
        return "\n".join(lines)

    @bot.message_handler(commands=["report", "cashreport"])
    def report_cmd(message):
        bot.reply_to(message, report_text(message.chat.id), parse_mode="HTML")

    class Api:
        pass
    api = Api()
    api.tick = tick
    api.start_loop = start_loop
    api.report_text = report_text
    return api
