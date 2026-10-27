"""Cooler Voice: the bot can speak Khmer.

  • /say <text>  (or reply /say to any message) → a Khmer voice message
  • /voice       → about Cooler Bot's voice (one voice: a young guy, 18-22) + a sample
  • 🔊 button under AI answers to voice notes → hear the answer

Speech comes from Gemini TTS (gemini-3.8-flash-lite-tts first, then gemini-3.8-flash-tts;
TTS_MODEL overrides). Gemini returns WAV; when ffmpeg is installed it is turned into an
OGG/Opus voice bubble, otherwise the WAV is sent as an audio file.
Per-user daily limit TTS_DAILY_LIMIT (default 20); owner/admins have no limit.
"""
import base64
import html
import io
import logging
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import tempfile
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone, timedelta

import httpx

logger = logging.getLogger(__name__)

TZ_KH = timezone(timedelta(hours=7))
API = "https://generativelanguage.googleapis.com/v1beta"
# Cooler Bot has ONE voice: a young (18-22) upbeat Cambodian guy. TTS_VOICE swaps the Gemini voice.
DEFAULT_VOICE = "Puck"
MAX_CHARS = 700
STYLE = ("a young Cambodian man about 20 years old: bright, upbeat, energetic and friendly, a bit cool like a "
         "young rapper's sidekick, natural modern Khmer, clear pronunciation, lively but not rushed")


def voice_name():
    return os.getenv("TTS_VOICE", "").strip() or DEFAULT_VOICE


def tts_models():
    chain = [os.getenv("TTS_MODEL", "").strip(), "gemini-3.8-flash-lite-tts", "gemini-3.8-flash-tts"]
    seen, out = set(), []
    for m in chain:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def clean_for_speech(text):
    """Drop markdown, links, code and most emoji so the voice reads only words."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[*_`#>|~\[\]]", " ", text)
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️‍]", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    if len(text) > MAX_CHARS:  # cut at a sentence end when possible
        cut = max(text.rfind(ch, 0, MAX_CHARS) for ch in ("។", ".", "!", "?", "\n"))
        text = text[: cut + 1 if cut > MAX_CHARS // 2 else MAX_CHARS].strip()
    return text


def pcm_to_wav(pcm, rate=24000):
    buf = io.BytesIO()
    buf.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt ")
    buf.write(struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16))
    buf.write(b"data" + struct.pack("<I", len(pcm)) + pcm)
    return buf.getvalue()


def _find_audio(node):
    """Find base64 audio in either the Interactions or the generateContent response."""
    if isinstance(node, dict):
        inline = node.get("inlineData") or node.get("inline_data")
        if isinstance(inline, dict) and inline.get("data") and "audio" in (inline.get("mimeType") or inline.get("mime_type") or "audio"):
            return inline["data"], inline.get("mimeType") or inline.get("mime_type") or ""
        if node.get("type") == "audio" and isinstance(node.get("data"), str):
            return node["data"], node.get("mime_type") or node.get("mimeType") or "audio/wav"
        for v in node.values():
            found = _find_audio(v)
            if found:
                return found
    elif isinstance(node, list):
        for v in reversed(node):  # last audio part wins
            found = _find_audio(v)
            if found:
                return found
    return None


def _as_wav(b64, mime):
    data = base64.b64decode(b64)
    if data[:4] == b"RIFF":
        return data
    rate = 24000
    m = re.search(r"rate=(\d+)", mime or "")
    if m:
        rate = int(m.group(1))
    return pcm_to_wav(data, rate)


def synthesize(api_key, text, voice=None, client=None, timeout=60.0, style=None, models=None):
    """Return WAV bytes, trying the Interactions API then the older generateContent form.
    style: how to speak (default: Cooler Bot's young-guy STYLE)."""
    return synthesize_turns(api_key, [{"text": text}], [voice or voice_name()], style=style or STYLE,
                            client=client, timeout=timeout, models=models)


def _requests(model, turns, voices, style):
    """(url, payload) pairs for one model: Interactions API first, then generateContent."""
    if len(voices) == 1:
        text = turns[0]["text"]
        return (
            (f"{API}/interactions", {
                "model": model,
                "input": [{"type": "user_input", "content": [{
                    "type": "text", "text": text,
                    "annotations": [{"type": "speech_metadata", "style": style}]}]}],
                "response_format": {"type": "audio"},
                "generation_config": {"speech_config": [{"voice": voices[0]}]},
            }),
            (f"{API}/models/{model}:generateContent", {
                "contents": [{"parts": [{"text": f"Say in a {style} voice:\n{text}"}]}],
                "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voices[0]}}}},
            }),
        )
    names = ["Joe", "Jane"]     # 👥 two speakers (the most Gemini allows); names only map turns → voices
    content = [{"type": "text", "text": t["text"],
                "annotations": [{"type": "speech_metadata", "speaker": names[t["speaker"]], "style": style}]}
               for t in turns]
    speakers = [{"speaker": names[i], "voice": v} for i, v in enumerate(voices[:2])]
    base = {"model": model, "input": [{"type": "user_input", "content": content}], "response_format": {"type": "audio"}}
    return (
        (f"{API}/interactions", {**base, "generation_config": {"speech_config": {"mode": "conversational",
                                                                                 "speakers": speakers}}}),
        (f"{API}/interactions", {**base, "generation_config": {"speech_config": {"speakers": speakers}}}),
        (f"{API}/models/{model}:generateContent", {
            "contents": [{"parts": [{"text": f"TTS the following conversation ({style}):\n" + "\n".join(
                f"{names[t['speaker']]}: {t['text']}" for t in turns)}]}],
            "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                "multiSpeakerVoiceConfig": {"speakerVoiceConfigs": [
                    {"speaker": names[i], "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": v}}}
                    for i, v in enumerate(voices[:2])]}}},
        }),
    )


LAST_DIALOGUE = {"how": ""}     # how the last 2-person clip was made (shown in /selftest)


def wav_pcm(wav):
    """(pcm bytes, sample rate) from a 16-bit mono WAV."""
    rate, pos = 24000, 12
    while pos + 8 <= len(wav):
        cid, size = wav[pos:pos + 4], struct.unpack("<I", wav[pos + 4:pos + 8])[0]
        if cid == b"fmt ":
            rate = struct.unpack("<I", wav[pos + 12:pos + 16])[0]
        elif cid == b"data":
            return wav[pos + 8:pos + 8 + size], rate
        pos += 8 + size + (size & 1)
    return wav[44:], rate


def stitch_turns(api_key, turns, voices, style, client=None, timeout=60.0, models=None, gap=0.3):
    """Dialogue the safe way: each turn in its own voice, joined with a short pause (one call per turn)."""
    pcm, rate = b"", 24000
    for t in turns:
        wav = synthesize_turns(api_key, [{"text": t["text"], "speaker": 0}], [voices[t["speaker"]]], style=style,
                               client=client, timeout=timeout, models=models)
        part, r = wav_pcm(wav)
        if pcm and r != rate:
            raise RuntimeError("sample rates differ")
        rate = r
        pcm += (b"\x00\x00" * int(rate * gap) if pcm else b"") + part
    return pcm_to_wav(pcm, rate)


def synthesize_turns(api_key, turns, voices, style=None, client=None, timeout=60.0, models=None):
    """turns: [{"text", "speaker": 0|1}], voices: [voice] or [voice A, voice B]. Returns WAV bytes."""
    style = style or STYLE
    if not api_key:
        raise RuntimeError("no GEMINI_API_KEY")
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0))
    errors = []
    try:
        for model in (models or tts_models()):
            requests = _requests(model, turns, voices, style)
            for url, payload in requests:
                try:
                    r = client.post(url, json=payload, headers=headers)
                except httpx.HTTPError as exc:
                    errors.append(f"{model}: {type(exc).__name__}")
                    continue
                if r.status_code == 200:
                    found = _find_audio(r.json())
                    if found:
                        if len(voices) == 2:
                            LAST_DIALOGUE["how"] = "native 2-speaker" + (" (generateContent)" if "generateContent" in url else "")
                        return _as_wav(*found)
                    errors.append(f"{model}: no audio")
                    continue
                why = ""
                try:
                    why = str(((r.json() or {}).get("error") or {}).get("message") or "")[:90]
                except Exception:
                    pass
                errors.append(f"{model}: HTTP {r.status_code}" + (f" {why}" if why else ""))
                if r.status_code in (401, 403):
                    raise PermissionError("Invalid Key")
        if len(voices) == 2 and len(turns) <= 16:
            logger.warning("2-speaker TTS failed (%s) — joining single-voice turns", "; ".join(errors)[:300])
            try:
                wav = stitch_turns(api_key, turns, voices, style, client=client, timeout=timeout, models=models)
                LAST_DIALOGUE["how"] = "joined turns (" + "; ".join(errors)[-120:] + ")"
                return wav
            except Exception as exc:
                errors.append(f"stitch: {str(exc)[:120]}")
    finally:
        if own:
            client.close()
    raise RuntimeError("; ".join(errors)[:400])


def wav_to_ogg(wav):
    """OGG/Opus for a real Telegram voice bubble; None when ffmpeg is missing or fails."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        try:  # static ffmpeg from the imageio-ffmpeg pip package (no system packages needed)
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            ffmpeg = None
    if not ffmpeg:
        return None
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, "in.wav"), os.path.join(d, "out.ogg")
        with open(src, "wb") as f:
            f.write(wav)
        try:
            subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", src, "-c:a", "libopus", "-b:a", "32k",
                            "-ac", "1", dst], check=True, timeout=60)
            with open(dst, "rb") as f:
                return f.read()
        except Exception as exc:
            logger.warning("ffmpeg conversion failed (%s)", type(exc).__name__)
            return None


def wav_seconds(wav):
    try:
        return max(1, int((len(wav) - 44) / 48000))
    except Exception:
        return 1


# ---------------- storage ----------------

def _db(config):
    conn = sqlite3.connect(getattr(config, "BOT_DB_PATH", "bot_database.db"), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(config):
    conn = _db(config)
    try:
        with conn:
            conn.execute("CREATE TABLE IF NOT EXISTS voice_usage (user_id INTEGER, day TEXT, n INTEGER NOT NULL, "
                         "PRIMARY KEY (user_id, day))")
            conn.execute("CREATE TABLE IF NOT EXISTS voice_prefs (user_id INTEGER PRIMARY KEY, voice TEXT, style TEXT)")
    finally:
        conn.close()


def take_quota(config, uid, limit, now=None):
    """Count one use; False when the user is over today's limit."""
    if limit <= 0:
        return True
    day = datetime.fromtimestamp(now or time.time(), TZ_KH).strftime("%Y-%m-%d")
    conn = _db(config)
    try:
        with conn:
            row = conn.execute("SELECT n FROM voice_usage WHERE user_id=? AND day=?", (uid, day)).fetchone()
            if row and row["n"] >= limit:
                return False
            conn.execute("INSERT INTO voice_usage(user_id, day, n) VALUES (?, ?, 1) "
                         "ON CONFLICT(user_id, day) DO UPDATE SET n=n+1", (uid, day))
            conn.execute("DELETE FROM voice_usage WHERE day < ?", (
                datetime.fromtimestamp((now or time.time()) - 7 * 86400, TZ_KH).strftime("%Y-%m-%d"),))
        return True
    finally:
        conn.close()


def used_today(config, uid, now=None):
    day = datetime.fromtimestamp(now or time.time(), TZ_KH).strftime("%Y-%m-%d")
    conn = _db(config)
    try:
        row = conn.execute("SELECT n FROM voice_usage WHERE user_id=? AND day=?", (uid, day)).fetchone()
        return int(row["n"]) if row else 0
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def get_pref(config, uid):
    """(voice, style) the member picked in Voice Studio for /say — (None, None) when not set."""
    conn = _db(config)
    try:
        row = conn.execute("SELECT voice, style FROM voice_prefs WHERE user_id=?", (uid,)).fetchone()
        return (row["voice"], row["style"]) if row else (None, None)
    except sqlite3.Error:
        return None, None
    finally:
        conn.close()


def set_pref(config, uid, voice, style=None):
    init_schema(config)
    conn = _db(config)
    try:
        with conn:
            if voice:
                conn.execute("INSERT INTO voice_prefs(user_id, voice, style) VALUES (?, ?, ?) ON CONFLICT(user_id) "
                             "DO UPDATE SET voice=excluded.voice, style=excluded.style", (uid, voice, style or None))
            else:
                conn.execute("DELETE FROM voice_prefs WHERE user_id=?", (uid,))
    finally:
        conn.close()


def daily_limit():
    try:
        return int(os.getenv("TTS_DAILY_LIMIT", "20"))
    except ValueError:
        return 20


def refund_quota(config, uid, now=None):
    day = datetime.fromtimestamp(now or time.time(), TZ_KH).strftime("%Y-%m-%d")
    conn = _db(config)
    try:
        with conn:
            conn.execute("UPDATE voice_usage SET n=MAX(0, n-1) WHERE user_id=? AND day=?", (uid, day))
    finally:
        conn.close()


# ---------------- bot module ----------------

def register(bot, config, synth=None):
    """synth(text, voice_name) -> WAV bytes (injectable for tests)."""
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    init_schema(config)
    synth = synth or (lambda text, voice, style=None: synthesize(getattr(config, "GEMINI_API_KEY", ""), text, voice,
                                                                style=style))
    spoken = OrderedDict()   # key -> text waiting behind a 🔊 button
    lock = threading.Lock()
    counter = [0]

    def privileged(uid):
        return uid == getattr(config, "OWNER_ID", 0) or uid in getattr(config, "ADMIN_IDS", [])

    def limit():
        return daily_limit()

    def remember(text):
        with lock:
            counter[0] += 1
            key = f"{counter[0]:x}"
            spoken[key] = text
            while len(spoken) > 300:
                spoken.popitem(last=False)
        return key

    def listen_markup(text):
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("🔊 ស្ដាប់ជាសំឡេង", callback_data=f"tts:p:{remember(text)}"))
        return m

    def speak(chat_id, uid, text, reply_to=None):
        text = clean_for_speech(text)
        if not text:
            bot.send_message(chat_id, "✍️ គ្មានពាក្យសម្រាប់អានទេ។ ឧ. <code>/say សួស្ដីបងប្អូន!</code>", parse_mode="HTML")
            return False
        import vip
        lim = vip.cap(uid, limit(), config)
        if not privileged(uid) and not take_quota(config, uid, lim):
            bot.send_message(chat_id, f"⏳ ថ្ងៃនេះប្រើសំឡេងអស់ {lim} ដងហើយ — ស្អែកមកម្ដងទៀតណា 🙏")
            return False
        try:
            bot.send_chat_action(chat_id, "record_voice")
        except Exception:
            pass
        try:
            voice, style = get_pref(config, uid)
            wav = synth(text, voice, style) if voice else synth(text, voice_name())
        except Exception as exc:
            logger.warning("TTS failed (%s: %s)", type(exc).__name__, str(exc)[:200])
            if not privileged(uid):
                refund_quota(config, uid)
            note = f"\n<code>{html.escape(str(exc)[:200])}</code>" if privileged(uid) else ""
            bot.send_message(chat_id, "😔 បង្កើតសំឡេងមិនបានទេពេលនេះ សូមសាកម្ដងទៀតបន្តិចទៀត" + note, parse_mode="HTML")
            return False
        kwargs = {"reply_to_message_id": reply_to} if reply_to else {}
        ogg = wav_to_ogg(wav)
        if ogg:
            bot.send_voice(chat_id, ogg, duration=wav_seconds(wav), **kwargs)
        else:
            bot.send_audio(chat_id, io.BytesIO(wav), title="Cooler Voice", performer="Cooler Bot",
                           duration=wav_seconds(wav), **kwargs)
        return True

    me = {}

    def bot_username():
        if "u" not in me:
            try:
                me["u"] = bot.get_me().username
            except Exception:
                return "TeamSmosSneabot"
        return me["u"]

    def voice_menu(uid, private=True):
        from telebot.types import WebAppInfo
        import voice_web
        m = InlineKeyboardMarkup()
        url = voice_web.studio_url()
        if private:
            m.row(InlineKeyboardButton("🎙️ បើក Voice Studio — ៣០ សំឡេង AI", web_app=WebAppInfo(url=url)))
        else:   # web apps can't open from a group button — go through the bot's private chat
            m.row(InlineKeyboardButton("🎙️ Voice Studio — ៣០ សំឡេង AI", url=f"https://t.me/{bot_username()}?start=go_say"))
        m.row(InlineKeyboardButton("🎧 ស្ដាប់សំឡេង Cooler Bot", callback_data="tts:hi"))
        voice, _style = get_pref(config, uid)
        mine = f"⭐ សំឡេង /say របស់អ្នក៖ <b>{html.escape(voice)}</b>\n\n" if voice else ""
        text = ("🎙️ <b>Cooler Voice — Voice-over AI</b>\n\n"
                "🎛️ <b>Voice Studio</b> (ចុចប៊ូតុងខាងក្រោម)៖ ជ្រើស ៣០ សំឡេង ប្រុស/ស្រី · "
                "ស្ទីល (ផ្សាយពាណិជ្ជកម្ម · ព័ត៌មាន · និទានរឿង · ខ្សឹប …) · សន្ទនា ២ នាក់ · "
                "ស្ដាប់ គំរូមុន · ទាញយក WAV ឬផ្ញើចូល Telegram\n\n" + mine +
                "• <code>/say អត្ថបទ</code> — Bot អានជាសំឡេង\n"
                "• Reply <code>/say</code> លើសារណាមួយ — អានសារនោះ\n"
                "• ផ្ញើ Voice Note — AI ស្ដាប់ ឆ្លើយ ហើយចុច 🔊 ដើម្បីស្ដាប់ចម្លើយ\n\n"
                "💡 ល្អសម្រាប់ធ្វើ Voice-over TikTok / YouTube")
        return text, m

    @bot.message_handler(commands=["say", "speak", "tts"])
    def say_cmd(message):
        parts = (message.text or "").split(maxsplit=1)
        text = parts[1] if len(parts) > 1 else ""
        src = getattr(message, "reply_to_message", None)
        if not text and src is not None:
            text = getattr(src, "text", None) or getattr(src, "caption", None) or ""
        if not text.strip():
            body, markup = voice_menu(message.from_user.id, message.chat.type == "private")
            bot.reply_to(message, body, reply_markup=markup, parse_mode="HTML")
            return
        speak(message.chat.id, message.from_user.id, text, reply_to=message.message_id)

    @bot.message_handler(commands=["voice", "voices"])
    def voice_cmd(message):
        body, markup = voice_menu(message.from_user.id, message.chat.type == "private")
        bot.reply_to(message, body, reply_markup=markup, parse_mode="HTML")

    @bot.callback_query_handler(func=lambda c: (c.data or "").startswith("tts:"))
    def tts_cb(call):
        parts = (call.data or "").split(":")
        action, arg = (parts + ["", ""])[1:3]
        uid, chat_id = call.from_user.id, call.message.chat.id
        if action == "hi":
            bot.answer_callback_query(call.id, "🔊 ...")
            speak(chat_id, uid, "សួស្ដីបងប្អូន! ខ្ញុំ Cooler Bot ជាកូនរបស់ Vid Cooler។ មានអ្វីឲ្យខ្ញុំជួយ ប្រាប់មកបានណា!",
                  reply_to=call.message.message_id)
            return
        if action == "menu":
            bot.answer_callback_query(call.id)
            body, markup = voice_menu(uid, getattr(call.message.chat, "type", "private") == "private")
            bot.send_message(chat_id, body, reply_markup=markup, parse_mode="HTML")
            return
        if action == "p":
            text = spoken.get(arg)
            if not text:
                bot.answer_callback_query(call.id, "⌛ សារនេះចាស់ពេក — សួរម្ដងទៀតណា", show_alert=True)
                return
            bot.answer_callback_query(call.id, "🔊 កំពុងបង្កើតសំឡេង...")
            speak(chat_id, uid, text, reply_to=call.message.message_id)
            return
        bot.answer_callback_query(call.id)

    class Api:
        pass
    api = Api()
    api.listen_markup = listen_markup
    api.speak = speak
    api.spoken = spoken
    return api
