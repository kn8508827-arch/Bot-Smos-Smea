"""Cooler Music: the bot sings and raps over a real beat.

  • /song <topic>  (/sing) → pick a style → Gemini writes ORIGINAL Khmer lyrics →
    Google Lyria makes a full song with vocals + beat → sent as an MP3 with the lyrics
  • /beat <style>  → a 30-second instrumental beat (for practice / TikTok)
  • 🎧 button under /rap → turns that rap into a song with a beat

Music: Lyria 3.5 (MUSIC_MODEL) for songs, Lyria 3 Clip (BEAT_MODEL) for 30 s beats, via the
Gemini Interactions API with the same GEMINI_API_KEY (paid tier; about $0.08 a song, $0.04 a clip).
SONG_DAILY_LIMIT (default 2 songs+beats per person per day); owner/admins have no limit.
Never imitates a real artist's voice or copies existing lyrics (Lyria blocks that too).
"""
import base64
import html
import logging
import os
import re
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone, timedelta

import httpx

logger = logging.getLogger(__name__)

TZ_KH = timezone(timedelta(hours=7))
API = "https://generativelanguage.googleapis.com/v1beta"
VOICE = ("young Cambodian male vocalist about 20 years old, bright and charismatic, clear Khmer pronunciation")

STYLES = {
    "rap":    ("🎤 Rap / Hip-hop", "modern Khmer hip-hop, hard-hitting boom-bap and trap drums, deep 808 bass, "
                                 "confident rap flow with a catchy sung hook, 92 BPM"),
    "pop":    ("🎶 Pop ច្រៀង", "bright modern Asian pop, catchy melodic chorus, warm synths and guitar, "
                             "polished radio mix, 110 BPM"),
    "love":   ("💔 ស្នេហា Ballad", "emotional romantic ballad, soft piano, strings, gentle drums, heartfelt singing, 72 BPM"),
    "party":  ("🎉 រាំ Party", "energetic dance-pop / EDM party anthem, four-on-the-floor kick, big drop, "
                             "chantable chorus, 124 BPM"),
    "chill":  ("🌙 Chill Lo-fi", "chill lo-fi R&B, dusty drums, mellow Rhodes, relaxed smooth vocals, 80 BPM"),
    "khmer":  ("🇰🇭 ខ្មែរបុរាណ Mix", "modern fusion of Cambodian traditional instruments (tro, roneat xylophone, "
                                   "skor drums, khim) with hip-hop beat, proud and joyful, 96 BPM"),
}

LYRICS_PROMPT = """Write ORIGINAL song lyrics in natural, modern Khmer (Khmer script) about: "{topic}".
Style: {style}. The singer is Cooler Bot, a cheerful young Cambodian guy (AI son of rapper Vid Cooler).
Structure with tags exactly like this: [Intro] (1-2 lines) [Verse 1] (6-8 lines) [Chorus] (4 lines, very catchy,
easy to sing along) [Verse 2] (6-8 lines) [Chorus] [Outro] (1-2 lines).
Use rhymes at line ends, short lines that fit the rhythm, positive and suitable for all ages: no insults, hate,
sexual content, drugs or violence. Never copy lyrics of existing songs and never mention real artists except
"Vid Cooler". Output ONLY the lyrics with the tags, nothing else."""


def _models(kind):
    if kind == "beat":
        chain = [os.getenv("BEAT_MODEL", "").strip(), "lyria-3-clip-preview"]
    else:
        chain = [os.getenv("MUSIC_MODEL", "").strip(), "lyria-3.5", "lyria-3-clip-preview"]
    seen, out = set(), []
    for m in chain:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def song_prompt(style_key, lyrics):
    _label, style = STYLES.get(style_key, STYLES["rap"])
    return (f"A complete original song, about 2 minutes. Genre and sound: {style}. "
            f"Vocals: {VOICE}, singing and rapping these Khmer lyrics exactly, following the section tags. "
            "Professional studio mix, punchy beat, clear vocals above the music.\n\nLyrics:\n" + lyrics.strip())


def beat_prompt(desc):
    return (f"Instrumental only, no vocals. A 30-second loopable beat: {desc}. "
            "Punchy drums, strong groove, clean professional mix, good for rapping or singing over.")


def find_audio_and_text(data):
    """Return (audio_bytes, mime, text) from an Interactions or generateContent response."""
    audio, mime, texts = None, "", []

    def walk(node):
        nonlocal audio, mime
        if isinstance(node, dict):
            inline = node.get("inlineData") or node.get("inline_data")
            if isinstance(inline, dict) and inline.get("data"):
                audio, mime = inline["data"], inline.get("mimeType") or inline.get("mime_type") or ""
            elif node.get("type") == "audio" and isinstance(node.get("data"), str):
                audio, mime = node["data"], node.get("mime_type") or node.get("mimeType") or ""
            elif node.get("type") == "text" and isinstance(node.get("text"), str):
                texts.append(node["text"])
            for k, v in node.items():
                if k not in ("data",):
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return (base64.b64decode(audio) if audio else None), mime, "\n".join(t for t in texts if t.strip())


def generate_music(api_key, prompt, kind="song", client=None):
    """Return (audio_bytes, mime, model). Tries each model in turn."""
    if not api_key:
        raise RuntimeError("no GEMINI_API_KEY")
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(300.0, connect=15.0))
    errors = []
    try:
        for model in _models(kind):
            try:
                r = client.post(f"{API}/interactions", json={"model": model, "input": prompt}, headers=headers)
            except httpx.HTTPError as exc:
                errors.append(f"{model}: {type(exc).__name__}")
                continue
            if r.status_code == 200:
                audio, mime, _text = find_audio_and_text(r.json())
                if audio:
                    return audio, mime, model
                errors.append(f"{model}: no audio (blocked?)")
                continue
            detail = ""
            try:
                detail = (r.json().get("error") or {}).get("message", "")[:120]
            except Exception:
                pass
            errors.append(f"{model}: HTTP {r.status_code} {detail}".strip())
            if r.status_code in (401, 403):
                break
    finally:
        if own:
            client.close()
    raise RuntimeError("; ".join(errors)[:400])


def write_lyrics(api_key, topic, style_key):
    import knowledge_base
    _label, style = STYLES.get(style_key, STYLES["rap"])
    text = knowledge_base.gemini(api_key, [{"text": LYRICS_PROMPT.format(topic=topic, style=style)}],
                                 max_tokens=2000, temperature=0.9)
    text = re.sub(r"^```\w*|```$", "", text.strip(), flags=re.M).strip()
    return text[:2500]


# ---------------- usage limit ----------------

def _db(config):
    conn = sqlite3.connect(getattr(config, "BOT_DB_PATH", "bot_database.db"), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(config):
    conn = _db(config)
    try:
        with conn:
            conn.execute("CREATE TABLE IF NOT EXISTS music_usage (user_id INTEGER, day TEXT, n INTEGER NOT NULL, "
                         "PRIMARY KEY (user_id, day))")
    finally:
        conn.close()


def _today(now=None):
    return datetime.fromtimestamp(now or time.time(), TZ_KH).strftime("%Y-%m-%d")


def used_today(config, uid, now=None):
    conn = _db(config)
    try:
        row = conn.execute("SELECT n FROM music_usage WHERE user_id=? AND day=?", (uid, _today(now))).fetchone()
        return row["n"] if row else 0
    finally:
        conn.close()


def add_use(config, uid, delta=1, now=None):
    conn = _db(config)
    try:
        with conn:
            conn.execute("INSERT INTO music_usage(user_id, day, n) VALUES (?, ?, MAX(0, ?)) "
                         "ON CONFLICT(user_id, day) DO UPDATE SET n=MAX(0, n+?)", (uid, _today(now), delta, delta))
    finally:
        conn.close()


# ---------------- bot module ----------------

def register(bot, config, make=None, lyrics=None):
    """make(prompt, kind) -> (audio, mime, model); lyrics(topic, style) -> text. Injectable for tests."""
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    init_schema(config)
    key = lambda: getattr(config, "GEMINI_API_KEY", "")
    make = make or (lambda prompt, kind: generate_music(key(), prompt, kind))
    lyrics = lyrics or (lambda topic, style: write_lyrics(key(), topic, style))
    pending = OrderedDict()      # req id -> {"topic"|"lyrics": ..., "uid": ...}
    busy = set()                 # users with a song being made
    lock = threading.Lock()
    counter = [0]

    def privileged(uid):
        return uid == getattr(config, "OWNER_ID", 0) or uid in getattr(config, "ADMIN_IDS", [])

    def limit():
        try:
            return int(os.getenv("SONG_DAILY_LIMIT", "2"))
        except ValueError:
            return 2

    def remember(item):
        with lock:
            counter[0] += 1
            rid = f"{counter[0]:x}"
            pending[rid] = item
            while len(pending) > 200:
                pending.popitem(last=False)
        return rid

    def style_markup(rid):
        m = InlineKeyboardMarkup()
        keys = list(STYLES)
        for i in range(0, len(keys), 2):
            m.row(*[InlineKeyboardButton(STYLES[k][0], callback_data=f"song:s:{k}:{rid}") for k in keys[i:i + 2]])
        return m

    def help_text():
        return ("🎧 <b>Cooler Music — Bot ច្រៀង & Rap មាន Beat</b>\n\n"
                "• <code>/song ប្រធានបទ</code> — ឧ. <code>/song ស្រលាញ់ម៉ាក់</code>, "
                "<code>/song Team ស្មោះស្នេហ៍ដើរលេងកំពត</code>\n"
                "  → ជ្រើសស្ទីល → AI សរសេរទំនុក ច្រៀង/Rap ជាមួយភ្លេងពេញ ~២ នាទី\n"
                "• <code>/beat ស្ទីល</code> — Beat ឥតសំឡេងច្រៀង ៣០ វិនាទី ឧ. <code>/beat trap ងងឹត</code>\n"
                "• /rap → ចុច 🎧 ដើម្បីធ្វើ Rap នោះជាបទ\n\n"
                f"⏳ ប្រហែល ១-២ នាទីក្នុងមួយបទ · {limit()} បទ/ថ្ងៃ\n"
                "🎤 សំឡេង Cooler Bot (ក្មេងប្រុស ២០ ឆ្នាំ) · ទំនុកថ្មីទាំងអស់ មិនចម្លងបទគេ")

    def start_job(chat_id, uid, reply_to, label):
        if uid in busy:
            bot.send_message(chat_id, "⏳ បទមុនរបស់អ្នកកំពុងផលិតនៅឡើយ — រង់ចាំបន្តិចណា")
            return False
        if not privileged(uid) and used_today(config, uid) >= limit():
            bot.send_message(chat_id, f"🎧 ថ្ងៃនេះអ្នកធ្វើបទអស់ {limit()} ហើយ — ស្អែកមកម្ដងទៀតណា 🙏")
            return False
        busy.add(uid)
        if not privileged(uid):
            add_use(config, uid)
        try:
            bot.send_message(chat_id, f"🎛️ កំពុងផលិត {label} ... ⏳ ប្រហែល ១-២ នាទី",
                             reply_to_message_id=reply_to)
        except Exception:
            pass
        return True

    def keep_action(chat_id, stop):
        while not stop.is_set():
            try:
                bot.send_chat_action(chat_id, "upload_voice")
            except Exception:
                pass
            stop.wait(5)

    def fail(chat_id, uid, exc):
        logger.warning("music failed (%s: %s)", type(exc).__name__, str(exc)[:300])
        if not privileged(uid):
            add_use(config, uid, -1)
        note = f"\n<code>{html.escape(str(exc)[:300])}</code>" if privileged(uid) else ""
        bot.send_message(chat_id, "😔 ធ្វើបទមិនបានទេពេលនេះ (ចំនួនមិនត្រូវកាត់ទេ) សូមសាកម្ដងទៀតបន្តិចទៀត" + note,
                         parse_mode="HTML")

    def send_track(chat_id, audio, mime, title, caption, reply_to=None):
        ext = "wav" if "wav" in (mime or "") else "mp3"
        import io
        f = io.BytesIO(audio)
        f.name = f"cooler_{re.sub(r'[^A-Za-z0-9]+', '_', title)[:30] or 'song'}.{ext}"
        kwargs = {"reply_to_message_id": reply_to} if reply_to else {}
        bot.send_audio(chat_id, f, title=title[:60], performer="Cooler Bot", caption=caption[:1000],
                       parse_mode="HTML", **kwargs)

    def make_song(chat_id, uid, style_key, topic=None, given_lyrics=None, reply_to=None):
        label = STYLES[style_key][0]
        if not start_job(chat_id, uid, reply_to, f"បទ {label}"):
            return
        stop = threading.Event()
        threading.Thread(target=keep_action, args=(chat_id, stop), daemon=True).start()
        try:
            words = given_lyrics or lyrics(topic, style_key)
            audio, mime, model = make(song_prompt(style_key, words), "song")
            title = (topic or "Cooler Rap")[:40]
            send_track(chat_id, audio, mime, f"{title} — {label}",
                       f"🎧 <b>{html.escape(title)}</b> · {html.escape(label)}\n🎤 Cooler Bot × AI · /song បទថ្មី",
                       reply_to)
            bot.send_message(chat_id, "📝 <b>ទំនុកច្រៀង</b>\n\n" + html.escape(words[:3500]), parse_mode="HTML")
        except Exception as exc:
            fail(chat_id, uid, exc)
        finally:
            stop.set()
            busy.discard(uid)

    def make_beat(chat_id, uid, desc, reply_to=None):
        if not start_job(chat_id, uid, reply_to, "Beat"):
            return
        stop = threading.Event()
        threading.Thread(target=keep_action, args=(chat_id, stop), daemon=True).start()
        try:
            audio, mime, _model = make(beat_prompt(desc), "beat")
            send_track(chat_id, audio, mime, f"Beat — {desc[:40]}",
                       f"🥁 <b>Beat</b> · {html.escape(desc[:80])}\n🎤 យកទៅ Rap លើបានតាមចិត្ត · /beat ថ្មី", reply_to)
        except Exception as exc:
            fail(chat_id, uid, exc)
        finally:
            stop.set()
            busy.discard(uid)

    @bot.message_handler(commands=["song", "sing", "makesong"])
    def song_cmd(message):
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            bot.reply_to(message, help_text(), parse_mode="HTML")
            return
        rid = remember({"topic": " ".join(parts[1].split())[:150], "uid": message.from_user.id})
        bot.reply_to(message, "🎶 ជ្រើសស្ទីលបទ 👇", reply_markup=style_markup(rid))

    @bot.message_handler(commands=["beat", "beats"])
    def beat_cmd(message):
        parts = (message.text or "").split(maxsplit=1)
        desc = parts[1].strip()[:120] if len(parts) > 1 else "modern Khmer hip-hop boom-bap, 92 BPM"
        make_beat(message.chat.id, message.from_user.id, desc, reply_to=message.message_id)

    @bot.callback_query_handler(func=lambda c: (c.data or "").startswith("song:"))
    def song_cb(call):
        parts = (call.data or "").split(":")
        uid, chat_id = call.from_user.id, call.message.chat.id
        if parts[1:2] == ["menu"]:
            bot.answer_callback_query(call.id)
            bot.send_message(chat_id, help_text(), parse_mode="HTML")
            return
        if len(parts) == 4 and parts[1] == "s" and parts[2] in STYLES:
            item = pending.get(parts[3])
            if not item:
                bot.answer_callback_query(call.id, "⌛ សំណើនេះចាស់ — វាយ /song ម្ដងទៀត", show_alert=True)
                return
            if item.get("uid") not in (None, uid) and not privileged(uid):
                bot.answer_callback_query(call.id, "🙏 នេះជាសំណើរបស់អ្នកផ្សេង — វាយ /song ផ្ទាល់ខ្លួន", show_alert=True)
                return
            pending.pop(parts[3], None)
            bot.answer_callback_query(call.id, f"🎛️ {STYLES[parts[2]][0]}")
            make_song(chat_id, uid, parts[2], topic=item.get("topic"), given_lyrics=item.get("lyrics"),
                      reply_to=call.message.message_id)
            return
        bot.answer_callback_query(call.id)

    def rap_markup(rap_text):
        """Button under a /rap answer: turn it into a song with a beat."""
        rid = remember({"lyrics": rap_text, "topic": "Cooler Rap", "uid": None})
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("🎧 ធ្វើជាបទមាន Beat", callback_data=f"song:s:rap:{rid}"))
        return m

    class Api:
        pass
    api = Api()
    api.rap_markup = rap_markup
    api.pending = pending
    return api
