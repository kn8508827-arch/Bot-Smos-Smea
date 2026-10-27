"""✨ Cooler AI Studio — the Hub's all-in-one creative web app (hub/ai.html) talks to these endpoints.

Eight tools, the same engines (and the same daily limits) as the bot commands:
  🎨 imagine   text → picture                     (ai_extras.generate_image · IMAGINE_DAILY_LIMIT)
  🖼️ photo     your photo → new look (10 styles)   (photo_studio · STUDIO_DAILY_LIMIT, shared with /studio)
  💇 style     your selfie → haircut or pose       (style_studio prompts · same Studio limit)
  🎧 song      topic or own lyrics → full song     (music_studio · SONG_DAILY_LIMIT, shared with /beat)
  🥁 beat      description → 30 s instrumental     (music_studio)
  ✍️ prompt    idea → pro prompts (video/song/image/story)   (prompt_studio systems)
  🎤 rap       a few words → original Khmer verse  (fan_features.RAP_PROMPT)
  🔮 fortune   daily zodiac / lucky stick / life number (instant, no AI) · dream & ask (AI)

Work runs in a background job; the page polls it. Every call carries Telegram's signed initData;
members only (same as the Hub's locked tiles).

  POST /studio/me    {initData}                → member, limits left, catalogs (styles, items, modes …)
  POST /studio/run   {initData, tool, …}       → {job} (or {done, result} for instant fortune readings)
  POST /studio/job   {initData, job}           → {status, result | msg}
  POST /studio/send  {initData, job}           → the bot sends the result to the member's chat
  GET  /studio/file/<id>                       → picture / audio (the id is the secret, kept 1 hour)
"""
import base64
import html
import io
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
TZ_KH = timezone(timedelta(hours=7))
FILE_TTL = 3600
MAX_PHOTO = 6_000_000
HOOKS = {}
_jobs = OrderedDict()       # job id -> dict
_files = OrderedDict()      # file id -> (bytes, mime, expires)
_busy = {}                  # (uid, lane) -> job id
_last_prompt = {}           # uid -> {"mode", "idea", "answer"}
_lock = threading.Lock()

IMAGE_STYLES = {   # key: (Khmer label, English style words added to the request)
    "auto": ("✨ ស្វ័យប្រវត្តិ", ""),
    "pixar": ("🧸 3D Pixar", "3D animated movie style, Pixar-like, soft lighting, cute and expressive"),
    "photo": ("📸 រូបថតពិត", "ultra realistic photograph, 85mm lens, natural light, sharp detail"),
    "anime": ("🌸 Anime", "high quality Japanese anime illustration, vibrant colors"),
    "cinema": ("🎬 ភាពយន្ត", "cinematic film still, dramatic lighting, shallow depth of field, color graded"),
    "khmer": ("🛕 សិល្បៈខ្មែរ", "traditional Khmer art style, Angkor-inspired ornaments, gold and deep red palette"),
    "water": ("🎨 ទឹកពណ៌", "soft watercolor painting on textured paper"),
    "neon": ("🌆 Cyberpunk", "cyberpunk neon city style, cyan and magenta glow, night rain"),
    "poster": ("🖼️ Poster", "bold graphic poster design, strong composition, eye-catching colors"),
    "logo": ("🔷 Logo / Icon", "clean minimal vector logo icon, flat design, centered, plain background"),
}
ASPECTS = ("1:1", "9:16", "16:9", "4:5", "3:4")
BEAT_PRESETS = {
    "trap": ("🌙 Trap ងងឹត", "dark trap, 808 bass, rolling hi-hats, 140 BPM"),
    "boombap": ("📻 Boom Bap", "classic 90s boom-bap hip-hop, dusty drums, jazzy sample feel, 90 BPM"),
    "lofi": ("☕ Lo-fi Chill", "lo-fi chill hip-hop, vinyl crackle, mellow keys, 80 BPM"),
    "khmer": ("🇰🇭 ខ្មែរ Mix", "Cambodian traditional instruments (roneat, tro, skor drums) fused with a modern hip-hop beat, 96 BPM"),
    "drill": ("🔥 Drill", "UK drill beat, sliding 808s, dark piano, 142 BPM"),
    "rnb": ("💜 R&B", "smooth modern R&B beat, warm keys, soft snaps, 85 BPM"),
    "afro": ("🌴 Afrobeats", "bouncy afrobeats groove, bright percussion, guitar licks, 105 BPM"),
    "edm": ("⚡ EDM Party", "festival EDM, big build-up and drop, 128 BPM"),
}
TEXT_LIMITS = {"prompt": ("PROMPT_DAILY_LIMIT", 10), "rap": ("FAN_AI_DAILY_LIMIT", 5),
               "fortune": ("FORTUNE_AI_DAILY_LIMIT", 3)}


def register(bot, config, hooks):
    """hooks: photo_remaining(uid) photo_record(uid) photo_busy(set) photo_run(bytes, prompt)->jpeg,
    prompt_ask(system, prompt)->text, ask(prompt)->text."""
    HOOKS.clear()
    HOOKS.update(hooks or {})
    HOOKS["bot"] = bot
    init_schema(config)


# ---------------- storage ----------------

def _db(config):
    conn = sqlite3.connect(getattr(config, "BOT_DB_PATH", "bot_database.db"), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(config):
    conn = _db(config)
    try:
        with conn:
            conn.execute("CREATE TABLE IF NOT EXISTS studio_usage (user_id INTEGER, day TEXT, tool TEXT, n INTEGER NOT NULL, "
                         "PRIMARY KEY (user_id, day, tool))")
    finally:
        conn.close()


def _day():
    return datetime.now(TZ_KH).strftime("%Y-%m-%d")


def text_limit(tool):
    env, default = TEXT_LIMITS[tool]
    try:
        return int(os.getenv(env, str(default)))
    except ValueError:
        return default


def text_used(config, uid, tool):
    conn = _db(config)
    try:
        row = conn.execute("SELECT n FROM studio_usage WHERE user_id=? AND day=? AND tool=?", (uid, _day(), tool)).fetchone()
        return int(row["n"]) if row else 0
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def text_add(config, uid, tool, delta):
    conn = _db(config)
    try:
        with conn:
            conn.execute("INSERT INTO studio_usage(user_id, day, tool, n) VALUES (?, ?, ?, MAX(0, ?)) ON CONFLICT"
                         "(user_id, day, tool) DO UPDATE SET n=MAX(0, n+?)", (uid, _day(), tool, delta, delta))
    finally:
        conn.close()


def _keep_file(data, mime):
    fid = secrets.token_urlsafe(18)
    with _lock:
        _files[fid] = (data, mime, time.time() + FILE_TTL)
        now = time.time()
        for k in [k for k, v in _files.items() if v[2] < now]:
            _files.pop(k, None)
        while len(_files) > 120:
            _files.popitem(last=False)
    return fid


# ---------------- limits ----------------

def _privileged(config, uid):
    return uid == getattr(config, "OWNER_ID", 0) or uid in (getattr(config, "ADMIN_IDS", []) or [])


def lanes(tool):
    return {"imagine": "image", "photo": "photo", "style": "photo", "song": "music", "beat": "music",
            "prompt": "prompt", "rap": "rap", "fortune": "fortune"}[tool]


def left(config, uid):
    """Uses left today per lane (None = unlimited)."""
    if _privileged(config, uid):
        return {k: None for k in ("image", "photo", "music", "prompt", "rap", "fortune")}
    import ai_extras
    import music_studio
    import vip
    out = {"image": max(0, vip.cap(uid, ai_extras.image_limit(), config) - ai_extras.images_used(config, uid))}
    rem = HOOKS.get("photo_remaining")
    out["photo"] = max(0, int(rem(uid))) if rem else 0
    try:
        song_limit = int(os.getenv("SONG_DAILY_LIMIT", "2"))
    except ValueError:
        song_limit = 2
    out["music"] = max(0, vip.cap(uid, song_limit, config, expensive=True) - music_studio.used_today(config, uid))
    for t in ("prompt", "rap", "fortune"):
        out[t] = max(0, vip.cap(uid, text_limit(t), config) - text_used(config, uid, t))
    return out


class Refused(Exception):
    pass


def _take(config, uid, lane):
    """Reserve one use; returns an undo() or raises Refused(khmer message)."""
    if _privileged(config, uid):
        return lambda: None
    if lane == "image":
        import ai_extras
        if not ai_extras.take_image(config, uid):
            raise Refused(f"🎨 ថ្ងៃនេះគូរអស់ {ai_extras.image_limit()} រូបហើយ — ស្អែកមកម្ដងទៀតណា 🙏")
        return lambda: ai_extras.give_image_back(config, uid)
    if lane == "photo":
        rem = HOOKS.get("photo_remaining")
        if not rem or rem(uid) <= 0:
            raise Refused("🖼️ ថ្ងៃនេះកែរូបអស់ហើយ — ស្អែកមកម្ដងទៀតណា 🙏")
        return lambda: None          # Photo Studio counts a use only after success (photo_record)
    if lane == "music":
        import music_studio
        if left(config, uid)["music"] <= 0:
            raise Refused("🎧 ថ្ងៃនេះធ្វើបទ/Beat អស់ហើយ — ស្អែកមកម្ដងទៀតណា 🙏")
        music_studio.add_use(config, uid)
        return lambda: music_studio.add_use(config, uid, -1)
    import vip
    lim = vip.cap(uid, text_limit(lane), config)
    if text_used(config, uid, lane) >= lim:
        raise Refused(f"⏳ ថ្ងៃនេះប្រើអស់ {lim} ដងហើយ — ស្អែកមកម្ដងទៀតណា 🙏")
    text_add(config, uid, lane, 1)
    return lambda: text_add(config, uid, lane, -1)


# ---------------- catalog ----------------

def catalog():
    import fortune
    import music_studio
    import photo_studio
    import prompt_studio
    import style_studio
    return {
        "imageStyles": [[k, v[0]] for k, v in IMAGE_STYLES.items()],
        "aspects": list(ASPECTS),
        "photoStyles": [[k, v["name_kh"]] for k, v in photo_studio.STYLES.items()],
        "groups": [[k, v[0]] for k, v in style_studio.GROUPS.items()],
        "kinds": [[k, v] for k, v in style_studio.KINDS.items()],
        "items": [[k, g, kind, label] for k, (g, kind, label, _w) in style_studio.ITEMS.items()],
        "songStyles": [[k, v[0]] for k, v in music_studio.STYLES.items()],
        "beats": [[k, v[0], v[1]] for k, v in BEAT_PRESETS.items()],
        "promptModes": [[k, v[0], v[2]] for k, v in prompt_studio.MODES.items()],
        "animals": [list(a) for a in fortune.ANIMALS],
    }


# ---------------- the tools ----------------

def _photo_bytes(data):
    raw = str(data.get("photo") or "")
    if "," in raw[:80]:
        raw = raw.split(",", 1)[1]
    try:
        img = base64.b64decode(raw, validate=False)
    except Exception:
        img = b""
    if len(img) < 1000:
        raise Refused("📷 សូមជ្រើសរូបថតជាមុនសិន")
    if len(img) > MAX_PHOTO:
        raise Refused("📷 រូបធំពេក — សាករូបផ្សេង")
    return img


def _clean(text, n):
    return " ".join(str(text or "").split())[:n]


def plan(config, uid, tool, data):
    """Check the request and return (lane, work) where work() -> result dict. Raises Refused."""
    key = getattr(config, "GEMINI_API_KEY", "")
    if tool == "imagine":
        prompt = _clean(data.get("prompt"), 600)
        if not prompt:
            raise Refused("✍️ សរសេររូបដែលចង់បានជាមុនសិន")
        style = IMAGE_STYLES.get(str(data.get("style") or "auto"), IMAGE_STYLES["auto"])[1]
        aspect = data.get("aspect") if data.get("aspect") in ASPECTS else "1:1"

        def work():
            import ai_extras
            img = ai_extras.generate_image(key, ai_extras.IMAGINE_STYLE + prompt + (f". Style: {style}" if style else ""), aspect)
            return {"kind": "image", "files": [_keep_file(img, "image/png")], "caption": prompt[:200]}
        return "image", work

    if tool in ("photo", "style"):
        img = _photo_bytes(data)
        import photo_studio
        if tool == "photo":
            sk = str(data.get("styleKey") or "realistic")
            if sk not in photo_studio.STYLES:
                raise Refused("សូមជ្រើសម៉ូដម្ដងទៀត")
            custom = _clean(data.get("custom"), 600)
            if sk == "custom" and not custom:
                raise Refused("✍️ សរសេរម៉ូដដែលចង់បាន")
            prompt = photo_studio.build_prompt(sk, custom or None)
            label = photo_studio.STYLES[sk]["name_kh"]
        else:
            import style_studio
            item = str(data.get("item") or "")
            if item not in style_studio.ITEMS:
                raise Refused("សូមជ្រើសម៉ូដសក់ ឬ Pose ម្ដងទៀត")
            prompt = style_studio.build_prompt(item)
            label = style_studio.ITEMS[item][2]
        run = HOOKS.get("photo_run")
        if not run:
            raise Refused("🖼️ Photo Studio មិនទាន់បើកទេ")

        def work():
            out = run(img, prompt)
            rec = HOOKS.get("photo_record")
            if rec and not _privileged(config, uid):
                rec(uid)
            return {"kind": "image", "files": [_keep_file(out, "image/jpeg")], "caption": label}
        return "photo", work

    if tool == "song":
        import music_studio
        sk = str(data.get("styleKey") or "rap")
        if sk not in music_studio.STYLES:
            raise Refused("សូមជ្រើសស្ទីលបទម្ដងទៀត")
        topic = _clean(data.get("topic"), 150)
        own = str(data.get("lyrics") or "").strip()[:2500]
        if not topic and not own:
            raise Refused("✍️ សរសេរប្រធានបទ ឬទំនុកច្រៀងជាមុនសិន")

        def work():
            words = own or music_studio.write_lyrics(key, topic, sk)
            audio, mime, _model = music_studio.generate_music(key, music_studio.song_prompt(sk, words), "song")
            mime = mime if "audio" in (mime or "") else "audio/mpeg"
            title = (topic or "Cooler Rap")[:40]
            return {"kind": "audio", "files": [_keep_file(audio, mime)], "title": f"{title} — {music_studio.STYLES[sk][0]}",
                    "text": words}
        return "music", work

    if tool == "beat":
        import music_studio
        preset = BEAT_PRESETS.get(str(data.get("preset") or ""))
        desc = _clean(data.get("desc"), 160) or (preset[1] if preset else "modern Khmer hip-hop boom-bap, 92 BPM")
        if preset and data.get("desc"):
            desc = f"{preset[1]}; {desc}"
        try:
            bpm = int(data.get("bpm") or 0)
        except (TypeError, ValueError):
            bpm = 0
        if 60 <= bpm <= 180:
            desc = re.sub(r"\b\d{2,3}\s*BPM\b", "", desc).strip(" ,;") + f", {bpm} BPM"

        def work():
            audio, mime, _model = music_studio.generate_music(key, music_studio.beat_prompt(desc), "beat")
            mime = mime if "audio" in (mime or "") else "audio/mpeg"
            return {"kind": "audio", "files": [_keep_file(audio, mime)], "title": f"Beat — {desc[:40]}", "text": ""}
        return "music", work

    if tool == "prompt":
        import prompt_studio
        mode = str(data.get("mode") or "video")
        if mode not in prompt_studio.SYSTEMS:
            raise Refused("សូមជ្រើសប្រភេទ Prompt")
        cont = bool(data.get("cont"))
        prev = _last_prompt.get(uid) if cont else None
        idea = _clean(data.get("idea"), prompt_studio.MAX_IDEA) or (prev or {}).get("idea", "")
        if not idea:
            raise Refused("✍️ សរសេរគំនិតរបស់អ្នកជាមុនសិន")
        ask = HOOKS.get("prompt_ask")
        if not ask:
            raise Refused("AI មិនទាន់បើកទេ")

        def work():
            user_prompt = f"User idea (may be Khmer): {idea}"
            if prev and prev.get("answer"):
                user_prompt += prompt_studio.CONTINUE_NOTE.format(prev=prev["answer"][:6000])
            answer = (ask(prompt_studio.SYSTEMS[mode], user_prompt) or "").strip()
            if not answer:
                raise RuntimeError("empty answer")
            _last_prompt[uid] = {"mode": mode, "idea": idea, "answer": answer}
            return {"kind": "prompt", "text": answer[:12000], "mode": mode, "idea": idea}
        return "prompt", work

    if tool == "rap":
        import fan_features
        words = _clean(data.get("words"), 300)
        if not words:
            raise Refused("✍️ សរសេរពាក្យ ១-៣ ម៉ាត់ជាមុនសិន")
        ask = HOOKS.get("ask")

        def work():
            answer = (ask(fan_features.RAP_PROMPT.format(words=words)) or "").strip()
            if not answer:
                raise RuntimeError("empty answer")
            return {"kind": "text", "text": answer[:3500], "title": "🎤 Cooler Rap"}
        return "rap", work

    if tool == "fortune":
        return _fortune(config, uid, data)
    raise Refused("មិនស្គាល់មុខងារនេះ")


def _fortune(config, uid, data):
    import fortune
    what = str(data.get("what") or "daily")
    keys = [a[0] for a in fortune.ANIMALS]
    if what == "daily":
        animal = str(data.get("animal") or "")
        year = str(data.get("year") or "").strip()
        if year.isdigit() and 1900 <= int(year) <= 2100:
            animal = fortune.animal_for_year(int(year))[0]
        if animal not in keys:
            raise Refused("🐲 ជ្រើសឆ្នាំសត្វ ឬវាយឆ្នាំកំណើត")
        return None, lambda: {"kind": "html", "text": fortune.reading_text(animal), "animal": animal}
    if what == "stick":
        n, text = fortune.stick_for(uid)
        return None, lambda: {"kind": "html", "text": f"🎋 <b>ឆ្នោតលេខ {n}</b>\n{html.escape(text)}\n\n"
                                                      "<i>ម្នាក់បាន ១ ឆ្នោត/ថ្ងៃ · សម្រាប់កម្សាន្ត</i>"}
    if what == "number":
        born = fortune.parse_birth_date(str(data.get("date") or ""))
        if not born:
            raise Refused("📅 វាយថ្ងៃខែឆ្នាំកំណើត ឧ. 15/08/1995")
        return None, lambda: {"kind": "html", "text": fortune.number_text(born)}
    if what in ("dream", "ask"):
        q = _clean(data.get("q"), 300)
        if not q:
            raise Refused("✍️ សរសេរសុបិន ឬសំណួររបស់អ្នក")
        animal = str(data.get("animal") or "")
        name = next((f"{a[2]} ({a[0]})" for a in fortune.ANIMALS if a[0] == animal), "unknown")
        prompt = fortune.DREAM_PROMPT.format(q=q) if what == "dream" else fortune.AI_PROMPT.format(animal=name, q=q)
        ask = HOOKS.get("ask")

        def work():
            answer = (ask(prompt) or "").strip()
            if not answer:
                raise RuntimeError("empty answer")
            head = "💭 <b>ទាយសុបិន</b>" if what == "dream" else "🔮 <b>គ្រូទាយ Cooler AI</b>"
            return {"kind": "html", "text": head + "\n\n" + html.escape(answer[:3000]) + "\n\n<i>✨ សម្រាប់កម្សាន្ត</i>"}
        return "fortune", work
    raise Refused("មិនស្គាល់")


def _run_job(job_id, undo, work):
    job = _jobs[job_id]
    try:
        job["result"] = work()
        job["status"] = "done"
    except Exception as exc:
        logger.warning("ai studio %s failed (%s: %s)", job["tool"], type(exc).__name__, str(exc)[:200])
        try:
            undo()
        except Exception:
            pass
        code = getattr(exc, "code", "")
        job["status"] = "error"
        job["msg"] = ("🙈 AI មិនអាចធ្វើរូប/សំណើនេះបាន — សាកប្ដូរពាក្យ ឬរូបផ្សេង (មិនកាត់ចំនួនទេ)"
                      if code in ("blocked", "no_image") else
                      "😔 ពេលនេះមិនទាន់បាន — សាកម្ដងទៀតបន្តិចទៀត (មិនកាត់ចំនួនទេ)")
        if job.get("owner"):
            job["msg"] += f" · {type(exc).__name__}: {str(exc)[:160]}"
    finally:
        job["ended"] = time.time()
        with _lock:
            if _busy.get(job["busy"]) == job_id:
                _busy.pop(job["busy"], None)
            if job["lane"] == "photo" and HOOKS.get("photo_busy") is not None:
                HOOKS["photo_busy"].discard(job["uid"])


def _public(job):
    out = {"status": job["status"], "tool": job["tool"], "elapsed": int((job.get("ended") or time.time()) - job["started"])}
    if job["status"] == "done":
        out["result"] = job["result"]
    if job["status"] == "error":
        out["msg"] = job.get("msg", "")
    return out


# ---------------- HTTP ----------------

def _json(code, data):
    return code, json.dumps(data, ensure_ascii=False).encode(), "application/json", {}


def handle_post(path, config, body, bot=None, now=None, start_thread=True):
    import voice_web
    try:
        data = json.loads(body.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError
    except (ValueError, AttributeError):
        return _json(400, {"error": "bad_request"})
    user, owner, member = voice_web._user(config, data, now)
    if not user:
        return _json(401, {"error": "open_in_telegram", "msg": "សូមបើកពី Cooler Hub ក្នុង Telegram"})
    uid = int(user["id"])
    path = path.rstrip("/")
    bot = bot or HOOKS.get("bot")
    if path == "/studio/me":
        return _json(200, {"member": member, "owner": owner, "left": left(config, uid) if member else {},
                           "catalog": catalog(), "name": user.get("first_name") or ""})
    if not member:
        return _json(403, {"error": "members_only", "msg": "🔒 AI Studio សម្រាប់សមាជិក — វាយ /pay ក្នុង Bot"})

    if path == "/studio/run":
        tool = str(data.get("tool") or "")
        try:
            if tool not in ("imagine", "photo", "style", "song", "beat", "prompt", "rap", "fortune"):
                raise Refused("មិនស្គាល់មុខងារនេះ")
            if not getattr(config, "GEMINI_API_KEY", "") and not (tool == "fortune" and data.get("what") in ("daily", "stick", "number", None)):
                raise Refused("AI មិនទាន់បើកដំណើរការទេ")
            lane, work = plan(config, uid, tool, data)
            if lane is None:                        # instant (fortune readings)
                return _json(200, {"done": True, "result": work()})
            busy_key = (uid, lane)
            with _lock:
                if busy_key in _busy:
                    raise Refused("⏳ កំពុងធ្វើមួយមុននៅឡើយ — រង់ចាំបន្តិច")
                pb = HOOKS.get("photo_busy")
                if lane == "photo" and pb is not None:
                    if uid in pb:
                        raise Refused("⏳ កំពុងកែរូបមុនរបស់អ្នក — រង់ចាំបន្តិច")
                    pb.add(uid)
                _busy[busy_key] = "…"
            try:
                undo = _take(config, uid, lane)
            except Refused:
                with _lock:
                    _busy.pop(busy_key, None)
                    if lane == "photo" and HOOKS.get("photo_busy") is not None:
                        HOOKS["photo_busy"].discard(uid)
                raise
        except Refused as exc:
            return _json(400, {"error": "refused", "msg": str(exc)})
        job_id = secrets.token_urlsafe(12)
        with _lock:
            _jobs[job_id] = {"uid": uid, "tool": tool, "lane": lane, "busy": busy_key, "status": "running",
                             "started": time.time(), "owner": owner}
            _busy[busy_key] = job_id
            while len(_jobs) > 300:
                _jobs.popitem(last=False)
        if start_thread:
            threading.Thread(target=_run_job, args=(job_id, undo, work), daemon=True, name=f"studio-{tool}").start()
        else:
            _run_job(job_id, undo, work)
        return _json(200, {"job": job_id})

    if path in ("/studio/job", "/studio/send"):
        job = _jobs.get(str(data.get("job") or ""))
        if not job or job["uid"] != uid:
            return _json(404, {"error": "job", "msg": "⌛ រកមិនឃើញ — សាកម្ដងទៀត"})
        if path == "/studio/job":
            return _json(200, _public(job))
        if job["status"] != "done" or bot is None:
            return _json(400, {"error": "not_ready", "msg": "⏳ មិនទាន់រួច"})
        try:
            send_result(bot, uid, job["result"])
        except Exception as exc:
            logger.warning("studio send failed (%s)", type(exc).__name__)
            return _json(502, {"error": "send", "msg": "😔 ផ្ញើមិនបាន — សូមចុច Start ក្នុង Bot ជាមុន"})
        return _json(200, {"ok": True})
    return _json(404, {"error": "not_found"})


def send_result(bot, chat_id, r):
    kind = r.get("kind")
    files = [_files.get(f) for f in r.get("files") or []]
    if any(f is None for f in files):
        raise RuntimeError("expired")
    if kind == "image":
        f = io.BytesIO(files[0][0])
        f.name = "cooler.jpg" if "jpeg" in files[0][1] else "cooler.png"
        bot.send_photo(chat_id, f, caption=("✨ " + r.get("caption", ""))[:900] + "\n— Cooler AI Studio")
    elif kind == "audio":
        f = io.BytesIO(files[0][0])
        f.name = "cooler.wav" if "wav" in files[0][1] else "cooler.mp3"
        bot.send_audio(chat_id, f, title=r.get("title", "Cooler")[:60], performer="Cooler Bot",
                       caption="🎧 Cooler AI Studio")
        if r.get("text"):
            bot.send_message(chat_id, "📝 <b>ទំនុកច្រៀង</b>\n\n" + html.escape(r["text"][:3500]), parse_mode="HTML")
    elif kind == "prompt":
        import prompt_studio
        chunks = prompt_studio.render_chunks(r["text"]) or [html.escape(r["text"][:3800])]
        for i, chunk in enumerate(chunks):
            head = f"✍️ <b>Prompt Studio</b>\n💭 <i>{html.escape(r.get('idea', '')[:150])}</i>\n\n" if i == 0 else ""
            try:
                bot.send_message(chat_id, head + chunk, parse_mode="HTML", disable_web_page_preview=True)
            except Exception:
                bot.send_message(chat_id, html.unescape(re.sub(r"<[^>]+>", "", head + chunk)))
    elif kind == "text":
        bot.send_message(chat_id, f"<b>{html.escape(r.get('title', ''))}</b>\n\n" + html.escape(r["text"][:3500]),
                         parse_mode="HTML")
    else:
        bot.send_message(chat_id, r["text"][:4000], parse_mode="HTML")


def handle_get(path):
    m = re.fullmatch(r"/studio/file/([A-Za-z0-9_-]{10,40})(?:\.\w{2,4})?", path)
    hit = _files.get(m.group(1)) if m else None
    if not hit or hit[2] < time.time():
        return 404, b"expired", "text/plain; charset=utf-8", {}
    ext = {"image/png": "png", "image/jpeg": "jpg", "audio/wav": "wav"}.get(hit[1], "mp3")
    return 200, hit[0], hit[1], {"Cache-Control": "private, max-age=3600",
                                 "Content-Disposition": f'inline; filename="cooler-ai.{ext}"'}
