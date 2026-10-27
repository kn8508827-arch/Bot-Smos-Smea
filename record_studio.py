"""🎙️ Record Studio — send a voice note, get it cleaned, with effects, or re-voiced by an AI voice.

/record (or Hub → 🎙️ Record Studio) → send a voice / audio (members: up to REC_MAX_SECONDS = 30 s,
REC_DAILY_LIMIT = 3 recordings a day; owner/admins: no limit). Then, on that same recording, as often as you like
for REC_TTL minutes:
  🧼 Clean    — noise reduction (afftdn) + hum/hiss filter + compressor + loudness → smooth voice
  🎛️ Effects  — Studio vocal, Hall reverb, Echo, Radio, Telephone, Robot, Chipmunk, Deep, Lo-fi
  🤖 AI voice — Gemini hears the words and the mood, then Gemini TTS (gemini-3.8-flash-tts family) says them
                again in the chosen voice: men / women, young / deep / warm / old …
                (the AI re-says the words; it is not a clone of the original timing or melody)
Every result can be downloaded as a WAV file (works in Adobe / CapCut).
"""
import base64
import html
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)
TZ_KH = timezone(timedelta(hours=7))
REC_TTL = 20 * 60

CLEAN = ("highpass=f=80,lowpass=f=12000,afftdn=nr=20:nf=-30:tn=1,"
         "acompressor=threshold=-20dB:ratio=3:attack=5:release=80,loudnorm=I=-16:TP=-1.5:LRA=11")
PITCH = "aresample=48000,asetrate=48000*{r},aresample=48000,atempo={t:.4f}"
EFFECTS = {   # key: (label, ffmpeg filter after cleaning)
    "clean": ("🧼 សម្អាតសំឡេង", ""),
    "studio": ("🎤 Studio Vocal", "equalizer=f=200:t=q:w=1:g=-2,equalizer=f=3500:t=q:w=1:g=3,aecho=0.8:0.5:35:0.12"),
    "hall": ("🏛️ Reverb សាល", "aecho=0.8:0.85:60|120|180:0.35|0.25|0.15"),
    "echo": ("🔁 Echo", "aecho=0.8:0.9:350|700:0.4|0.2"),
    "radio": ("📻 វិទ្យុ", "highpass=f=300,lowpass=f=4500,acompressor=threshold=-25dB:ratio=6,volume=1.4"),
    "phone": ("📞 ទូរស័ព្ទ", "highpass=f=450,lowpass=f=3000,volume=1.6"),
    "robot": ("🤖 Robot", "afftfilt=real='hypot(re,im)*sin(0)':imag='hypot(re,im)*cos(0)':win_size=512:overlap=0.75"),
    "chipmunk": ("🐿️ សំឡេងតូច", PITCH.format(r=1.35, t=1 / 1.35)),
    "deep": ("🐻 សំឡេងធំ", PITCH.format(r=0.78, t=1 / 0.78)),
    "lofi": ("🌙 Lo-fi", "lowpass=f=3800,acrusher=bits=8:mix=0.35,aecho=0.6:0.5:50:0.2"),
}
# key: (label, Gemini voice, how to speak)
AI_VOICES = {
    "m_young": ("👦 ប្រុសក្មេង ស្រស់", "Puck", "a young man around 20, bright and upbeat"),
    "m_deep": ("🎙️ ប្រុសសំឡេងធំ", "Charon", "a man with a deep, rich, calm radio voice"),
    "m_strong": ("💪 ប្រុសរឹងមាំ", "Orus", "a confident, firm adult man"),
    "m_hype": ("🔥 ប្រុស Hype", "Fenrir", "an excited, energetic hype man"),
    "m_rough": ("🎸 ប្រុសសំឡេងគ្រើម", "Algenib", "a man with a gravelly, rough voice"),
    "m_old": ("👴 តាចាស់", "Gacrux", "an old grandfather around 70, slow, warm and wise"),
    "w_sweet": ("🌸 ស្រីផ្អែម", "Leda", "a young woman around 20, sweet and youthful"),
    "w_bright": ("✨ ស្រីស្រស់ថ្លា", "Zephyr", "a bright, clear, cheerful young woman"),
    "w_warm": ("☕ ស្រីទន់ភ្លន់", "Sulafat", "a warm, gentle, caring woman"),
    "w_strong": ("👩‍💼 ស្រីមានទំនុកចិត្ត", "Kore", "a confident, firm professional woman"),
    "w_soft": ("🌙 ស្រីខ្សឹប", "Achernar", "a soft, calm, breathy woman, almost whispering"),
    "w_old": ("👵 យាយចាស់", "Vindemiatrix", "an old grandmother around 70, gentle, slow and kind"),
    "k_kid": ("🧒 ក្មេងតូច", "Laomedeia", "a playful little child about 8 years old, cute and lively"),
}
HEAR_PROMPT = ("Listen to this voice recording. Return ONLY JSON: {\"text\": \"exactly what was said, in the language "
               "spoken (Khmer in Khmer script)\", \"mood\": \"a few English words about the emotion and pace, e.g. "
               "happy and fast, sad and slow, rapping rhythmically\"}. If nothing understandable was said return "
               "{\"text\": \"\", \"mood\": \"\"}.")


def ffmpeg_exe():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def max_seconds():
    try:
        return max(5, int(os.getenv("REC_MAX_SECONDS", "30")))
    except ValueError:
        return 30


def daily_limit():
    try:
        return int(os.getenv("REC_DAILY_LIMIT", "3"))
    except ValueError:
        return 3


def run_ffmpeg(data, filters, out="ogg", seconds=None):
    """Any audio in → OGG/Opus voice (out='ogg') or 48 kHz mono WAV (out='wav')."""
    exe = ffmpeg_exe()
    if not exe:
        raise RuntimeError("ffmpeg missing")
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, "in"), os.path.join(d, "out." + out)
        with open(src, "wb") as f:
            f.write(data)
        cmd = [exe, "-y", "-loglevel", "error", "-i", src]
        if seconds:
            cmd += ["-t", str(seconds)]
        if filters:
            cmd += ["-af", filters]
        cmd += ["-ac", "1", "-ar", "48000"]
        cmd += ["-c:a", "libopus", "-b:a", "64k"] if out == "ogg" else ["-c:a", "pcm_s16le"]
        subprocess.run(cmd + [dst], check=True, timeout=90)
        with open(dst, "rb") as f:
            return f.read()


def effect_chain(key):
    extra = EFFECTS[key][1]
    return CLEAN + ("," + extra if extra else "")


def parse_heard(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) and (data.get("text") or "").strip() else None


def _db(config):
    conn = sqlite3.connect(getattr(config, "BOT_DB_PATH", "bot_database.db"), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(config):
    conn = _db(config)
    try:
        with conn:
            conn.execute("CREATE TABLE IF NOT EXISTS rec_usage (user_id INTEGER, day TEXT, n INTEGER NOT NULL, "
                         "PRIMARY KEY (user_id, day))")
    finally:
        conn.close()


def used_today(config, uid, now=None):
    day = datetime.fromtimestamp(now or time.time(), TZ_KH).strftime("%Y-%m-%d")
    conn = _db(config)
    try:
        row = conn.execute("SELECT n FROM rec_usage WHERE user_id=? AND day=?", (uid, day)).fetchone()
        return row["n"] if row else 0
    finally:
        conn.close()


def count_use(config, uid, now=None):
    day = datetime.fromtimestamp(now or time.time(), TZ_KH).strftime("%Y-%m-%d")
    conn = _db(config)
    try:
        with conn:
            conn.execute("INSERT INTO rec_usage(user_id, day, n) VALUES (?, ?, 1) "
                         "ON CONFLICT(user_id, day) DO UPDATE SET n=n+1", (uid, day))
    finally:
        conn.close()


def register(bot, config, hear=None, speak=None):
    """hear(audio_bytes, mime) -> text; speak(text, voice, style) -> WAV bytes. Injectable for tests.
    Register before the generic voice handlers so a waiting /record wins."""
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    init_schema(config)
    if hear is None:
        def hear(data, mime):
            import knowledge_base
            return knowledge_base.gemini(getattr(config, "GEMINI_API_KEY", ""), [
                {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}},
                {"text": HEAR_PROMPT}], max_tokens=1500, temperature=0.1)
    if speak is None:
        def speak(text, voice, style):
            import voice_studio
            return voice_studio.synthesize(getattr(config, "GEMINI_API_KEY", ""), text, voice, style=style)

    waiting = {}   # uid -> expires
    takes = {}     # uid -> {"wav": bytes, "sec": int, "exp": float, "last": (bytes, label) | None, "heard": dict | None}
    busy = set()

    def privileged(uid):
        return uid == getattr(config, "OWNER_ID", 0) or uid in getattr(config, "ADMIN_IDS", [])

    def left(uid):
        import vip
        return 10 ** 6 if privileged(uid) or daily_limit() <= 0 else max(0, vip.cap(uid, daily_limit(), config) - used_today(config, uid))

    def menu(page="fx"):
        m = InlineKeyboardMarkup()
        if page == "fx":
            btns = [InlineKeyboardButton(v[0], callback_data=f"rec:fx:{k}") for k, v in EFFECTS.items()]
            for i in range(0, len(btns), 2):
                m.row(*btns[i:i + 2])
            m.row(InlineKeyboardButton("🤖 ប្ដូរជាសំឡេង AI ប្រុស/ស្រី", callback_data="rec:page:ai"))
        else:
            btns = [InlineKeyboardButton(v[0], callback_data=f"rec:ai:{k}") for k, v in AI_VOICES.items()]
            for i in range(0, len(btns), 2):
                m.row(*btns[i:i + 2])
            m.row(InlineKeyboardButton("🎛️ Effects", callback_data="rec:page:fx"))
        m.row(InlineKeyboardButton("📥 ទាញយក WAV", callback_data="rec:wav"),
              InlineKeyboardButton("🎙️ ថតថ្មី", callback_data="rec:new"))
        return m

    def intro(uid):
        lim = "គ្មានដែនកំណត់ (Admin)" if left(uid) >= 10 ** 6 else f"នៅសល់ថ្ងៃនេះ {left(uid)}/{daily_limit()} ដង"
        return ("🎙️ <b>Record Studio</b>\n"
                f"សង្កត់ 🎤 ហើយថតសំឡេង (យូរបំផុត {max_seconds()} វិនាទី) ឬផ្ញើឯកសារសំឡេងមក\n\n"
                "🧼 សម្អាតសំឡេងរំខាន · 🎛️ Effects · 🤖 ប្ដូរជាសំឡេង AI ប្រុស/ស្រី\n"
                f"📊 {lim}")

    @bot.message_handler(commands=["record", "rec", "vocal"])
    def record_cmd(message):
        if getattr(message.chat, "type", "") != "private":
            bot.reply_to(message, "🎙️ សូមប្រើ /record ក្នុង Private Chat ជាមួយ Bot")
            return
        uid = message.from_user.id
        src = getattr(message, "reply_to_message", None)
        if src is not None and (getattr(src, "voice", None) or getattr(src, "audio", None)):
            take(src, uid, message.chat.id)
            return
        if left(uid) <= 0:
            bot.reply_to(message, f"⏳ ថ្ងៃនេះថតអស់ {daily_limit()} ដងហើយ — ស្អែកមកម្ដងទៀត 🙏")
            return
        waiting[uid] = time.monotonic() + 600
        bot.reply_to(message, intro(uid), parse_mode="HTML")

    def is_waiting(message):
        exp = waiting.get(getattr(getattr(message, "from_user", None), "id", None))
        return exp is not None and exp > time.monotonic() and getattr(message.chat, "type", "") == "private"

    @bot.message_handler(func=is_waiting, content_types=["voice", "audio", "video_note"])
    def on_voice(message):
        waiting.pop(message.from_user.id, None)
        take(message, message.from_user.id, message.chat.id)

    def take(msg, uid, chat_id):
        f = msg.voice or msg.audio or getattr(msg, "video_note", None)
        sec = int(getattr(f, "duration", 0) or 0)
        if not privileged(uid) and sec > max_seconds():
            bot.send_message(chat_id, f"⏱️ សំឡេងនេះ {sec} វិនាទី — សមាជិកថតបានយូរបំផុត {max_seconds()} វិនាទី។ "
                                      "សូមថតឲ្យខ្លីជាងនេះ ហើយផ្ញើម្ដងទៀត 🙏")
            waiting[uid] = time.monotonic() + 600
            return
        if left(uid) <= 0:
            bot.send_message(chat_id, f"⏳ ថ្ងៃនេះថតអស់ {daily_limit()} ដងហើយ — ស្អែកមកម្ដងទៀត 🙏")
            return
        try:
            raw = bot.download_file(bot.get_file(f.file_id).file_path)
            wav = run_ffmpeg(raw, "", out="wav", seconds=None if privileged(uid) else max_seconds())
        except Exception as exc:
            logger.warning("record load failed (%s)", type(exc).__name__)
            bot.send_message(chat_id, "😔 អានសំឡេងនេះមិនបាន — សាកថតម្ដងទៀត")
            return
        if not privileged(uid):
            count_use(config, uid)
        if len(takes) > 200:
            takes.clear()
        takes[uid] = {"wav": wav, "sec": sec, "exp": time.monotonic() + REC_TTL, "last": None, "heard": None}
        render(chat_id, uid, "clean")   # a clean version straight away

    def current(uid):
        t = takes.get(uid)
        return t if t and t["exp"] > time.monotonic() else None

    def send_result(chat_id, uid, data, label, wav):
        t = takes[uid]
        t["last"] = (wav, label)
        t["exp"] = time.monotonic() + REC_TTL
        bot.send_voice(chat_id, data, caption=f"{label} ✅ — សាកមួយទៀតលើសំឡេងដដែលបាន 👇",
                       reply_markup=menu("fx"))

    def render(chat_id, uid, key):
        t = current(uid)
        if not t:
            bot.send_message(chat_id, "⌛ សំឡេងផុតកំណត់ហើយ — វាយ /record ហើយថតម្ដងទៀត")
            return
        try:
            bot.send_chat_action(chat_id, "record_voice")
            chain = effect_chain(key)
            ogg = run_ffmpeg(t["wav"], chain, out="ogg")
            wav = run_ffmpeg(t["wav"], chain, out="wav")
        except Exception as exc:
            logger.warning("record effect %s failed (%s)", key, type(exc).__name__)
            bot.send_message(chat_id, "😔 ដាក់ Effect នេះមិនបាន — សាក Effect ផ្សេង")
            return
        send_result(chat_id, uid, ogg, EFFECTS[key][0], wav)

    def revoice(chat_id, uid, key):
        t = current(uid)
        if not t:
            bot.send_message(chat_id, "⌛ សំឡេងផុតកំណត់ហើយ — វាយ /record ហើយថតម្ដងទៀត")
            return
        label, voice, how = AI_VOICES[key]
        status = bot.send_message(chat_id, f"🤖 កំពុងប្ដូរជា {label} ... (10–30 វិនាទី)")
        try:
            if not t["heard"]:
                bot.send_chat_action(chat_id, "typing")
                t["heard"] = parse_heard(hear(run_ffmpeg(t["wav"], "", out="wav"), "audio/wav"))
            heard = t["heard"]
            if not heard:
                bot.send_message(chat_id, "😔 ស្ដាប់ពាក្យមិនច្បាស់ — សាកថតម្ដងទៀតឲ្យជិតទូរស័ព្ទ")
                return
            bot.send_chat_action(chat_id, "record_voice")
            mood = (heard.get("mood") or "natural").strip()[:120]
            wav = speak(heard["text"][:700], voice, f"{how}; say it {mood}, natural spoken Khmer if Khmer")
            ogg = run_ffmpeg(wav, "loudnorm=I=-16:TP=-1.5:LRA=11", out="ogg")
            send_result(chat_id, uid, ogg, label, run_ffmpeg(wav, "loudnorm=I=-16:TP=-1.5:LRA=11", out="wav"))
        except Exception as exc:
            logger.warning("record AI voice failed (%s)", type(exc).__name__)
            bot.send_message(chat_id, "😔 AI រវល់ — សាកម្ដងទៀតបន្តិចទៀត")
        finally:
            try:
                bot.delete_message(chat_id, status.message_id)
            except Exception:
                pass

    @bot.callback_query_handler(func=lambda c: (c.data or "").startswith("rec:"))
    def rec_cb(call):
        parts = (call.data or "").split(":")
        uid, chat_id = call.from_user.id, call.message.chat.id
        bot.answer_callback_query(call.id)
        if parts[1] == "page" and len(parts) > 2:
            try:
                bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=menu(parts[2]))
            except Exception:
                bot.send_message(chat_id, "ជ្រើស 👇", reply_markup=menu(parts[2]))
            return
        if parts[1] == "new":
            takes.pop(uid, None)
            if left(uid) <= 0:
                bot.send_message(chat_id, f"⏳ ថ្ងៃនេះថតអស់ {daily_limit()} ដងហើយ — ស្អែកមកម្ដងទៀត 🙏")
                return
            waiting[uid] = time.monotonic() + 600
            bot.send_message(chat_id, intro(uid), parse_mode="HTML")
            return
        if uid in busy:
            bot.send_message(chat_id, "⏳ កំពុងធ្វើមួយមុន សូមរង់ចាំបន្តិច...")
            return
        busy.add(uid)
        try:
            if parts[1] == "fx" and len(parts) > 2 and parts[2] in EFFECTS:
                render(chat_id, uid, parts[2])
            elif parts[1] == "ai" and len(parts) > 2 and parts[2] in AI_VOICES:
                revoice(chat_id, uid, parts[2])
            elif parts[1] == "wav":
                t = current(uid)
                if not t or not t["last"]:
                    bot.send_message(chat_id, "⌛ មិនមានសំឡេង — វាយ /record ហើយថតម្ដងទៀត")
                    return
                wav, label = t["last"]
                name = re.sub(r"[^A-Za-z0-9]+", "-", label).strip("-").lower() or "voice"
                bot.send_document(chat_id, ("cooler-" + name + ".wav", wav),
                                  caption=f"📥 {html.escape(label)} · WAV (ប្រើក្នុង Adobe / CapCut បាន)")
        finally:
            busy.discard(uid)

    return {"takes": takes, "waiting": waiting}
