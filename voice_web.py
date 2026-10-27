"""🎙️ Cooler Voice Studio — the Hub's voice-over web app (hub/voice.html) talks to these endpoints.

Every call carries Telegram's signed initData (same check as ICOM and /hub/me); only members
(paid, or owner/admin) get audio. Gemini TTS (gemini-3.8-flash-tts family) does the speaking with
any of its 30 prebuilt voices, a delivery style, audio tags (<laugh>, <short pause> …) and an
optional 2-person dialogue. Each generated clip counts against the same daily limit as /say
(TTS_DAILY_LIMIT, default 20; owner/admins unlimited). Voice samples are made once and cached.

  POST /voice/me      {initData}                       → member, uses left, saved /say voice, extra voices
  POST /voice/sample  {initData, voice}                → audio/wav (cached on disk, free)
  POST /voice/tts     {initData, turns, voices, style, quality}  → audio/wav (+ X-Clip / X-Left headers)
  POST /voice/send    {initData, clip}                 → the bot sends the clip to the member's chat
  POST /voice/pref    {initData, voice, style}         → that voice becomes the member's /say voice
  GET  /voice/file/<clip>.wav                           → download (Telegram.WebApp.downloadFile)
"""
import io
import json
import logging
import os
import re
import secrets
import threading
import time
from collections import OrderedDict

import httpx

logger = logging.getLogger(__name__)

# name: (gender m/f, English trait from Google, Khmer description)
VOICES = {
    "Puck": ("m", "Upbeat", "ស្វាហាប់ រីករាយ"), "Charon": ("m", "Informative", "អានព័ត៌មាន ច្បាស់"),
    "Fenrir": ("m", "Excitable", "រំភើប ខ្លាំង"), "Orus": ("m", "Firm", "រឹងមាំ ម៉ឺងម៉ាត់"),
    "Enceladus": ("m", "Breathy", "ទន់ មានខ្យល់ដង្ហើម"), "Iapetus": ("m", "Clear", "ច្បាស់ល្អ"),
    "Umbriel": ("m", "Easy-going", "ស្រួលៗ សប្បាយ"), "Algieba": ("m", "Smooth", "រលូន ផ្អែម"),
    "Algenib": ("m", "Gravelly", "ស្អកៗ ក្រាស់"), "Rasalgethi": ("m", "Informative", "បែបអ្នកជំនាញ"),
    "Alnilam": ("m", "Firm", "ធ្ងន់ មានអំណាច"), "Schedar": ("m", "Even", "ស្មើៗ ស្ងប់"),
    "Achird": ("m", "Friendly", "រួសរាយ រាក់ទាក់"), "Zubenelgenubi": ("m", "Casual", "ធម្មតាៗ ដូចមិត្ត"),
    "Sadachbia": ("m", "Lively", "រស់រវើក"), "Sadaltager": ("m", "Knowledgeable", "ចេះដឹង គួរឲ្យទុកចិត្ត"),
    "Zephyr": ("f", "Bright", "ភ្លឺស្វាង"), "Kore": ("f", "Firm", "រឹងមាំ ច្បាស់"),
    "Leda": ("f", "Youthful", "ក្មេងស្រស់"), "Aoede": ("f", "Breezy", "ស្រាលៗ ស្រស់"),
    "Callirrhoe": ("f", "Easy-going", "ស្រួលៗ"), "Autonoe": ("f", "Bright", "ភ្លឺ ស្វាហាប់"),
    "Despina": ("f", "Smooth", "រលូន ផ្អែម"), "Erinome": ("f", "Clear", "ច្បាស់ល្អ"),
    "Laomedeia": ("f", "Upbeat", "រីករាយ ស្វាហាប់"), "Achernar": ("f", "Soft", "ទន់ភ្លន់"),
    "Gacrux": ("f", "Mature", "ចាស់ទុំ"), "Pulcherrima": ("f", "Forward", "ត្រង់ៗ មានទំនុកចិត្ត"),
    "Vindemiatrix": ("f", "Gentle", "ស្លូតបូត"), "Sulafat": ("f", "Warm", "កក់ក្ដៅ"),
}
MAX_CHARS = 1200
MAX_TURNS = 40
CLIP_TTL = 1800
TAGS = ("laugh", "sigh", "short pause", "long pause", "breath", "whispering", "cough", "gasp", "yawn")
SAMPLE_TEXT = "សួស្ដី! ខ្ញុំជាសំឡេង {name} នៅក្នុង Cooler Voice Studio។ ឲ្យខ្ញុំអាន Voice-over ឲ្យអ្នកបានណា!"
SAMPLE_STYLE = "natural, friendly and clear, normal pace"
DEFAULT_STYLE = "natural, clear and friendly, normal pace"

_clips = OrderedDict()      # clip id -> (uid, wav, expires)
_busy = set()
_lock = threading.Lock()
_extra = {"t": 0.0, "voices": []}


def studio_url():
    base = (os.getenv("HUB_URL", "").strip() or "https://kn8508827-arch.github.io/Blue-Gold-Rush/hub/").split("#")[0].split("?")[0]
    if base.endswith(".html"):
        base = base.rsplit("/", 1)[0] + "/"
    if not base.endswith("/"):
        base += "/"
    return base + "voice.html"


def clean_script(text):
    """Keep words + Gemini audio tags; drop HTML, links, markdown and emoji."""
    text = text or ""
    keep = {}

    def hold(m):
        tag = m.group(1).strip().lower()
        if tag not in TAGS:
            return " "
        key = f"\x00{len(keep)}\x00"
        keep[key] = f"<{tag}>"
        return key
    text = re.sub(r"<\s*([a-zA-Z ]{2,20})\s*>", hold, text)
    text = re.sub(r"<[^>]*>", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[*_`#>~\[\]]", " ", text)
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️‍]", " ", text)
    for key, tag in keep.items():
        text = text.replace(key, tag)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def models_for(quality):
    env = os.getenv("TTS_MODEL", "").strip()
    chain = ["gemini-3.8-flash-lite-tts", "gemini-3.8-flash-tts"] if quality == "fast" else \
        ["gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"]
    return [m for m in dict.fromkeys([env, *chain]) if m]


def valid_voice(name):
    return name in VOICES or any(v["id"] == name for v in _extra["voices"])


def _user(config, data, now=None):
    import icom_live
    user = icom_live.verify_init_data((data or {}).get("initData", ""), getattr(config, "BOT_TOKEN", ""), now)
    if not user:
        return None, False, False
    uid = int(user["id"])
    owner = uid in icom_live._staff(config)
    return user, owner, bool(owner or icom_live.is_allowed(config, uid, now))


def _json(code, data):
    return code, json.dumps(data, ensure_ascii=False).encode(), "application/json", {}


def _sample_dir(config):
    db = getattr(config, "BOT_DB_PATH", "bot_database.db")
    d = os.path.join(os.path.dirname(os.path.abspath(db)), "voice_samples")
    os.makedirs(d, exist_ok=True)
    return d


def extra_voices(config, client=None):
    """🇰🇭 extra Khmer voices from Gemini's Extended Voice Library (cached 6 h; [] when none/unavailable)."""
    if time.time() - _extra["t"] < 6 * 3600:
        return _extra["voices"]
    _extra["t"] = time.time()
    key = getattr(config, "GEMINI_API_KEY", "")
    if not key or os.getenv("TTS_EXTRA_VOICES", "true").strip().lower() in ("0", "false", "no", "off"):
        return _extra["voices"]
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(12.0, connect=6.0))
    out = []
    try:
        r = client.get("https://generativelanguage.googleapis.com/v1beta/voices",
                       params={"language_code": "km", "page_size": 100}, headers={"x-goog-api-key": key})
        if r.status_code == 200:
            for v in (r.json().get("voices") or [])[:60]:
                if not isinstance(v, dict):
                    continue
                vid = v.get("voice") or v.get("voiceId") or v.get("voice_id") or v.get("name") or ""
                vid = str(vid).replace("voices/", "")
                if vid and vid not in VOICES:
                    out.append({"id": vid[:80], "name": str(v.get("displayName") or v.get("display_name") or vid)[:40],
                                "desc": str(v.get("description") or "")[:80],
                                "g": {"female": "f", "male": "m"}.get(str(v.get("gender") or "").lower(), "")})
    except Exception as exc:
        logger.info("extra voices unavailable (%s)", type(exc).__name__)
    finally:
        if own:
            client.close()
    _extra["voices"] = out
    return out


def _parse_tts(data):
    """(turns, voices, style, quality) or raises ValueError(khmer message)."""
    voices = [v for v in (data.get("voices") or []) if isinstance(v, str)][:2]
    if not voices or not all(valid_voice(v) for v in voices):
        raise ValueError("សូមជ្រើសសំឡេងម្ដងទៀត")
    raw = data.get("turns")
    if not isinstance(raw, list):
        raw = [{"text": data.get("text", ""), "speaker": 0}]
    turns, total = [], 0
    for t in raw[:MAX_TURNS]:
        if not isinstance(t, dict):
            continue
        text = clean_script(str(t.get("text") or ""))
        if not text:
            continue
        spk = 1 if len(voices) == 2 and t.get("speaker") in (1, "1", "B") else 0
        turns.append({"text": text, "speaker": spk})
        total += len(text)
    if not turns:
        raise ValueError("សូមសរសេរអត្ថបទជាមុនសិន ✍️")
    if total > MAX_CHARS:
        raise ValueError(f"អត្ថបទវែងពេក ({total}/{MAX_CHARS} តួអក្សរ) — កាត់ជាពីរផ្នែកណា")
    if len(voices) == 2 and len({t["speaker"] for t in turns}) < 2:
        voices = [voices[turns[0]["speaker"]]]      # only one person talks → plain single voice
        turns = [{"text": "\n".join(t["text"] for t in turns), "speaker": 0}]
    elif len(voices) == 1:
        turns = [{"text": "\n".join(t["text"] for t in turns), "speaker": 0}]
    style = re.sub(r"[<>\n\r]", " ", str(data.get("style") or ""))[:300].strip() or DEFAULT_STYLE
    return turns, voices, style, ("fast" if data.get("quality") == "fast" else "hd")


def _keep_clip(uid, wav):
    clip = secrets.token_urlsafe(18)
    with _lock:
        _clips[clip] = (uid, wav, time.time() + CLIP_TTL)
        now = time.time()
        for k in [k for k, v in _clips.items() if v[2] < now]:
            _clips.pop(k, None)
        while len(_clips) > 60:
            _clips.popitem(last=False)
    return clip


def handle_post(path, config, body, bot=None, synth=None, now=None):
    """(status, bytes, content-type, extra headers). synth(turns, voices, style, models) is injectable for tests."""
    import voice_studio as vs
    try:
        data = json.loads(body.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError
    except (ValueError, AttributeError):
        return _json(400, {"error": "bad_request"})
    user, owner, member = _user(config, data, now)
    if not user:
        return _json(401, {"error": "open_in_telegram", "msg": "សូមបើកពី Cooler Hub ក្នុង Telegram"})
    uid = int(user["id"])
    path = path.rstrip("/")
    limit = 0 if owner else vs.daily_limit()

    def left():
        return None if limit <= 0 else max(0, limit - vs.used_today(config, uid, now))

    if path == "/voice/me":
        voice, style = vs.get_pref(config, uid)
        return _json(200, {"member": member, "owner": owner, "left": left(), "limit": limit,
                           "pref": {"voice": voice, "style": style}, "extra": extra_voices(config),
                           "max": MAX_CHARS, "name": user.get("first_name") or ""})
    if not member:
        return _json(403, {"error": "members_only", "msg": "🔒 Voice Studio សម្រាប់សមាជិក — វាយ /pay ក្នុង Bot"})
    synth = synth or (lambda turns, voices, style, models: vs.synthesize_turns(
        getattr(config, "GEMINI_API_KEY", ""), turns, voices, style=style, models=models, timeout=90.0))

    if path == "/voice/sample":
        voice = str(data.get("voice") or "")
        if not valid_voice(voice):
            return _json(400, {"error": "voice"})
        fn = os.path.join(_sample_dir(config), re.sub(r"[^A-Za-z0-9_-]", "_", voice)[:80] + ".wav")
        if not os.path.exists(fn):
            try:
                wav = synth([{"text": SAMPLE_TEXT.format(name=voice), "speaker": 0}], [voice], SAMPLE_STYLE,
                            models_for("fast"))
            except Exception as exc:
                logger.warning("voice sample failed (%s: %s)", type(exc).__name__, str(exc)[:160])
                return _json(502, {"error": "tts", "msg": "😔 គំរូសំឡេងមិនទាន់បាន — សាកម្ដងទៀត"})
            with open(fn + ".tmp", "wb") as f:
                f.write(wav)
            os.replace(fn + ".tmp", fn)
        with open(fn, "rb") as f:
            return 200, f.read(), "audio/wav", {"Cache-Control": "max-age=86400"}

    if path == "/voice/tts":
        try:
            turns, voices, style, quality = _parse_tts(data)
        except ValueError as exc:
            return _json(400, {"error": "input", "msg": str(exc)})
        with _lock:
            if uid in _busy:
                return _json(429, {"error": "busy", "msg": "⏳ កំពុងបង្កើតសំឡេងមុន — រង់ចាំបន្តិច"})
            _busy.add(uid)
        try:
            if limit > 0 and not vs.take_quota(config, uid, limit, now):
                return _json(429, {"error": "quota", "msg": f"⏳ ថ្ងៃនេះប្រើអស់ {limit} ដងហើយ — ស្អែកមកម្ដងទៀតណា 🙏"})
            try:
                wav = synth(turns, voices, style, models_for(quality))
            except Exception as exc:
                logger.warning("voice studio TTS failed (%s: %s)", type(exc).__name__, str(exc)[:200])
                if limit > 0:
                    vs.refund_quota(config, uid, now)
                note = f" ({str(exc)[:120]})" if owner else ""
                return _json(502, {"error": "tts", "msg": "😔 បង្កើតសំឡេងមិនបាន — សាកម្ដងទៀត ឬប្ដូរ ⚡ លឿន" + note})
        finally:
            with _lock:
                _busy.discard(uid)
        clip = _keep_clip(uid, wav)
        return 200, wav, "audio/wav", {"X-Clip": clip, "X-Left": "" if left() is None else str(left()),
                                       "X-Seconds": str(vs.wav_seconds(wav))}

    if path == "/voice/send":
        hit = _clips.get(str(data.get("clip") or ""))
        if not hit or hit[0] != uid or hit[2] < time.time():
            return _json(404, {"error": "clip", "msg": "⌛ សំឡេងនេះផុតពេលហើយ — បង្កើតម្ដងទៀត"})
        if bot is None:
            return _json(500, {"error": "server"})
        title = re.sub(r"[\r\n]", " ", str(data.get("title") or "Cooler Voice-over"))[:60]
        try:
            bot.send_audio(uid, io.BytesIO(hit[1]), title=title, performer="Cooler Voice Studio",
                           duration=vs.wav_seconds(hit[1]),
                           caption="🎙️ Voice-over ពី Cooler Voice Studio — ទាញយកទៅដាក់ CapCut / TikTok បាន ✅")
        except Exception as exc:
            logger.warning("voice send failed (%s)", type(exc).__name__)
            return _json(502, {"error": "send", "msg": "😔 ផ្ញើមិនបាន — សូមចុច Start ក្នុង Bot ជាមុន"})
        return _json(200, {"ok": True})

    if path == "/voice/pref":
        voice = str(data.get("voice") or "")
        if voice and not valid_voice(voice):
            return _json(400, {"error": "voice"})
        style = re.sub(r"[<>\n\r]", " ", str(data.get("style") or ""))[:300].strip()
        vs.set_pref(config, uid, voice or None, style or None)
        return _json(200, {"ok": True})

    return _json(404, {"error": "not_found"})


def handle_get(path):
    """GET /voice/file/<clip>.wav → (status, bytes, ctype, headers). The clip id itself is the secret."""
    m = re.fullmatch(r"/voice/file/([A-Za-z0-9_-]{10,40})(?:\.wav)?", path)
    hit = _clips.get(m.group(1)) if m else None
    if not hit or hit[2] < time.time():
        return 404, b"expired", "text/plain; charset=utf-8", {}
    return 200, hit[1], "audio/wav", {"Content-Disposition": 'attachment; filename="cooler-voiceover.wav"'}
