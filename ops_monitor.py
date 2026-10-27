"""Keeps the owner informed when something breaks.

  • ExceptionHandler for TeleBot: a crash inside any handler is logged, counted and sent to the
    owner (the same kind of error at most once every OPS_ALERT_MINUTES, default 15).
  • Every ERROR log line is kept (last 50) for /status.
  • /status (owner/admins): uptime, version, worker threads, database size, errors in the last 24 h,
    and a 🩺 button that tests Gemini right now.
"""
import html
import logging
import os
import threading
import time
import traceback
from collections import deque

logger = logging.getLogger(__name__)

STARTED = time.time()
_errors = deque(maxlen=50)          # (time, source, text)
_lock = threading.Lock()


class _Collector(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.ERROR and record.name != __name__:
            try:
                with _lock:
                    _errors.append((record.created, record.name, record.getMessage()[:300]))
            except Exception:
                pass


def install_log_collector():
    root = logging.getLogger()
    if not any(isinstance(h, _Collector) for h in root.handlers):
        root.addHandler(_Collector(level=logging.ERROR))


def recent_errors(window=86400, now=None):
    now = now or time.time()
    with _lock:
        return [e for e in _errors if now - e[0] <= window]


def fmt_uptime(seconds):
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return (f"{d} ថ្ងៃ " if d else "") + f"{h} ម៉ោង {m} នាទី"


class AlertingExceptionHandler:
    """telebot ExceptionHandler: returns True so polling keeps going."""

    def __init__(self, config, send):
        self.config = config
        self.send = send             # send(chat_id, text)
        self.last = {}

    def handle(self, exc):
        kind = type(exc).__name__
        where = ""
        tb = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
        for frame in reversed(tb):
            if "site-packages" not in frame.filename and "/telebot/" not in frame.filename:
                where = f"{os.path.basename(frame.filename)}:{frame.lineno} {frame.name}"
                break
        logger.error("Handler crashed: %s at %s: %s", kind, where or "?", str(exc)[:200])
        with _lock:
            _errors.append((time.time(), "handler", f"{kind} {where} {str(exc)[:200]}"))
        owner = getattr(self.config, "OWNER_ID", 0)
        try:
            gap = 60 * int(os.getenv("OPS_ALERT_MINUTES", "15") or 15)
        except ValueError:
            gap = 900
        key = (kind, where)
        now = time.monotonic()
        if owner and (key not in self.last or now - self.last[key] > gap):
            self.last[key] = now
            try:
                self.send(owner, f"⚠️ <b>Bot មានបញ្ហា</b> (Bot នៅដំណើរការធម្មតា)\n"
                                 f"<code>{html.escape(kind)}</code> · <code>{html.escape(where or '?')}</code>\n"
                                 f"{html.escape(str(exc)[:300])}\n\nមើល៖ /status")
            except Exception:
                pass
        return True


def db_storage_status(config):
    """(ok, text): is the SQLite file on a persistent Railway volume?"""
    db = os.path.abspath(getattr(config, "BOT_DB_PATH", "bot_database.db"))
    mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    on_railway = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"))
    if mount and db.startswith(os.path.abspath(mount).rstrip("/") + "/"):
        return True, f"✅ នៅលើ Volume ({mount})"
    if on_railway:
        return False, ("❌ <b>មិននៅលើ Volume</b> — រាល់ពេល Deploy ទិន្នន័យ (សមាជិកបង់លុយ, Group, ថ្ងៃផុតកំណត់) "
                       f"នឹងបាត់! ភ្ជាប់ Volume ហើយកំណត់ BOT_DB_PATH ទៅក្នុងវា (ឥឡូវ: {db})")
    return True, f"💻 {db}"


def register(bot, config, ai_ping=None, workers=None):
    """ai_ping() -> short text or raises. workers = number of handler threads."""
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    install_log_collector()

    def privileged(uid):
        return uid == getattr(config, "OWNER_ID", 0) or uid in getattr(config, "ADMIN_IDS", [])

    def status_text():
        db = getattr(config, "BOT_DB_PATH", "bot_database.db")
        try:
            size = f"{os.path.getsize(db) / 1024 / 1024:.1f} MB"
        except OSError:
            size = "?"
        errs = recent_errors()
        version = (os.getenv("RAILWAY_GIT_COMMIT_SHA") or "")[:7] or "local"
        lines = ["🩺 <b>ស្ថានភាព Bot</b>", "",
                 f"⏱️ ដំណើរការ៖ {fmt_uptime(time.time() - STARTED)}",
                 f"🏷️ Version៖ <code>{html.escape(version)}</code>",
                 f"🧵 Worker៖ {workers or '?'} · Thread សកម្ម៖ {threading.active_count()}",
                 f"🗄️ Database៖ {size}",
                 f"💾 {db_storage_status(config)[1]}",
                 f"🔑 Gemini Key៖ {'✅ មាន' if getattr(config, 'GEMINI_API_KEY', '') else '❌ គ្មាន'}",
                 f"{'✅' if not errs else '⚠️'} Error ២៤ ម៉ោង៖ <b>{len(errs)}</b>"]
        for t, src, text in errs[-5:]:
            stamp = time.strftime("%d/%m %H:%M", time.gmtime(t + 7 * 3600))
            lines.append(f"• {stamp} <code>{html.escape(src)}</code> {html.escape(text[:120])}")
        return "\n".join(lines)

    def markup():
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("🩺 សាក AI ឥឡូវ", callback_data="ops:ping"),
              InlineKeyboardButton("🔄 Refresh", callback_data="ops:refresh"))
        return m

    @bot.message_handler(commands=["status", "uptime"], func=lambda m: privileged(getattr(m.from_user, "id", None)))
    def status_cmd(message):
        bot.reply_to(message, status_text(), reply_markup=markup(), parse_mode="HTML")

    @bot.callback_query_handler(func=lambda c: (c.data or "").startswith("ops:"))
    def ops_cb(call):
        if not privileged(call.from_user.id):
            bot.answer_callback_query(call.id, "⛔")
            return
        if call.data == "ops:ping":
            bot.answer_callback_query(call.id, "🩺 កំពុងសាក...")
            started = time.monotonic()
            try:
                reply = (ai_ping() if ai_ping else "no ping") or ""
                text = f"✅ AI ឆ្លើយក្នុង {time.monotonic() - started:.1f}s — «{html.escape(reply[:60])}»"
            except Exception as exc:
                text = f"❌ AI មិនឆ្លើយ៖ <code>{html.escape(str(exc)[:300])}</code>"
            bot.send_message(call.message.chat.id, text, parse_mode="HTML")
            return
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_text(status_text(), call.message.chat.id, call.message.message_id,
                                  reply_markup=markup(), parse_mode="HTML")
        except Exception:
            pass

    return status_text


# ---------------- /selftest: try every outside service the bot depends on ----------------

def _checks(bot, config):
    """name -> zero-arg callable returning a short OK text (raises on failure)."""
    import knowledge_base
    key = getattr(config, "GEMINI_API_KEY", "")

    def telegram():
        return "@" + bot.get_me().username

    def ai_text():
        return knowledge_base.gemini(key, [{"text": "Reply with one word: OK"}], max_tokens=1024)[:20]

    def ai_voice():
        import voice_studio
        wav = voice_studio.synthesize(key, "សួស្ដី", timeout=40)
        ogg = voice_studio.wav_to_ogg(wav)
        return f"{len(wav) // 1024} KB" + (" · voice bubble ✅" if ogg else " · ffmpeg ❌ (audio file)")

    def voice_studio_check():
        """🎙️ Voice Studio: the HD model with 2 speakers (what the Hub's dialogue mode uses)."""
        import voice_studio
        wav = voice_studio.synthesize_turns(key, [{"text": "សួស្ដី", "speaker": 0}, {"text": "បាទ សួស្ដី", "speaker": 1}],
                                            ["Puck", "Kore"], style="friendly", timeout=50,
                                            models=["gemini-3.8-flash-tts"])
        return f"HD · 2 voices · {len(wav) // 1024} KB · {voice_studio.LAST_DIALOGUE['how']}"

    def weather():
        import weather_service
        d = weather_service.weather_service.fetch("phnom_penh")["days"][0]
        return f"{d['tmin']}–{d['tmax']}°C"

    def rates():
        import market_info
        r = market_info.fetch_rates()
        if not r:
            raise RuntimeError("no data")
        return f"1$ = {r['rates']['KHR']:,.0f} ៛"

    def gold():
        import market_info
        g = market_info.fetch_gold()
        if not g:
            raise RuntimeError("no data")
        return f"${g['oz']:,.0f}/oz"

    def news():
        import market_info
        items = market_info.fetch_news("top")
        if not items:
            raise RuntimeError("no items")
        return f"{len(items)} items"

    def maps():
        """OpenStreetMap's free servers often rate-limit cloud hosts — when they all time out, /ev still
        answers with Google Maps or the 🌐 Google Maps button, so say that instead of a bare error."""
        import location_service
        data = location_service.location_service._overpass(
            '[out:json][timeout:15];node(around:300,11.5564,104.9282)["amenity"="fuel"];out 1;')
        return f"OpenStreetMap ✅ ({len(data.get('elements', []))})"

    def gmaps():
        import maps_places
        if not maps_places.enabled(key):
            return "off (MAPS_GROUNDING=false)"
        found = maps_places.search(key, "ev", lat=11.5564, lon=104.9282, timeout=55)
        if not found:
            raise RuntimeError("no places")
        return f"Google Maps ✅ ({len(found)} EV) · ប្រើ {maps_places.used_this_month()}/{maps_places.monthly_limit()} ខែនេះ (ឥតគិតថ្លៃ)"

    def cards():
        import card_render
        card_render.membership_card("Test", "01/01/2027", 10)
        return "Khmer ✅" if card_render.KHMER_OK else "English labels (no Khmer shaping)"

    def storage():
        ok, text = db_storage_status(config)
        if not ok:
            raise RuntimeError("not on a volume")
        return text.replace("✅ ", "")

    def bakong():
        import khqr_pay
        s = khqr_pay.settings()
        if not s["account"]:
            raise RuntimeError("BAKONG_ACCOUNT_ID not set")
        if not s["token"]:
            return f"{s['account']} · ⏳ waiting for BAKONG_API_TOKEN (receipts mode)"
        return f"{s['account']} · token set — test with /khqrtest"

    return [("📡 Telegram", telegram), ("🤖 AI (Gemini)", ai_text), ("🎙️ Voice (TTS)", ai_voice),
            ("🎛️ Voice Studio (HD · 2 នាក់)", voice_studio_check),
            ("🌤️ Weather", weather), ("💵 Dollar", rates), ("🪙 Gold", gold), ("📰 News", news),
            ("⛽ Maps (/fuel /ev)", maps), ("📍 Google Maps (/ev)", gmaps), ("🎨 Picture cards", cards), ("💾 Database", storage),
            ("💳 KHQR / Bakong", bakong)]


def run_selftest(bot, config, timeout=70):
    from concurrent.futures import ThreadPoolExecutor
    checks = _checks(bot, config)
    results = []
    pool = ThreadPoolExecutor(max_workers=len(checks))
    futures = [(name, pool.submit(fn), time.monotonic()) for name, fn in checks]
    for name, fut, started in futures:
        try:
            out = fut.result(timeout=max(1, timeout - (time.monotonic() - started)))
            results.append((name, True, str(out)))
        except Exception as exc:
            msg = f"{type(exc).__name__}: {str(exc)[:120]}"
            if "Maps" in name and "Timeout" in msg:
                msg += " · ឥឡូវយឺត — /ev នៅមានប៊ូតុង 🌐 Google Maps"
            results.append((name, False, msg))
    pool.shutdown(wait=False, cancel_futures=True)
    return results


def register_selftest(bot, config):
    def privileged(uid):
        return uid == getattr(config, "OWNER_ID", 0) or uid in getattr(config, "ADMIN_IDS", [])

    @bot.message_handler(commands=["selftest", "check"], func=lambda m: privileged(getattr(m.from_user, "id", None)))
    def selftest_cmd(message):
        wait = bot.reply_to(message, "🧪 កំពុងសាកគ្រប់សេវា ... (≈ ៣០–៧០ វិនាទី)")
        results = run_selftest(bot, config)
        good = sum(1 for _n, ok, _t in results if ok)
        lines = [f"🧪 <b>សាកគ្រប់សេវា — {good}/{len(results)} ✅</b>", ""]
        for name, ok, text in results:
            lines.append(f"{'✅' if ok else '❌'} {name} — <code>{html.escape(text)}</code>")
        lines.append("\nℹ️ មិនសាក៖ 🎨 /imagine និង 🎧 /song (គិតលុយ Google) — សាកដោយផ្ទាល់")
        try:
            bot.edit_message_text("\n".join(lines), message.chat.id, wait.message_id, parse_mode="HTML")
        except Exception:
            bot.send_message(message.chat.id, "\n".join(lines), parse_mode="HTML")
