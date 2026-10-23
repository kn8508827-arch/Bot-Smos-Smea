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
