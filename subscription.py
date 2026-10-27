"""Paid membership for Cooler Bot (Khmer UI).

  • Person: SUB_USER_PRICE (1$) → every feature for SUB_DAYS (30) days, counted from approval.
  • Group:  SUB_GROUP_PRICE (5$) → the bot works in that group for SUB_DAYS days,
            whoever pays (group owner or anyone who added the bot).
  • Payment: ABA KHQR / pay link → the payer sends the receipt photo to the bot →
            the owner taps ✅ Approve / ❌ Reject.
  • Adding the bot to a group: ONLY active members (1$) can do it, and they must buy a
    group pass (5$, kind "c" = one credit) BEFORE adding. When the bot joins a group it checks
    who added it: member + credit → the credit is used and the group is paid for SUB_DAYS days;
    otherwise the bot explains why and leaves the group immediately. (/addgroup, Hub "+ Group")
  • When a paid group month ends, the group gets a notice and SUB_GROUP_GRACE_HOURS (24 h)
    to renew; after that the bot leaves by itself.
  • Non-members can only chat with the bot in plain text (AI chat) plus /start, /pay, /hub,
    /follow, /contact and /sos; every other feature shows the payment screen.
  • Owner/admins are never blocked. SUBSCRIPTION_ENABLED=false turns everything off.

Keeping members: reminders 3 days and 1 day before the end (with a renew button) and a
"expired" message with the same button; the owner gets a daily report at SUB_REPORT_HOUR
(8:00 Cambodia time) and a nudge when a receipt waits more than 3 hours.

Owner commands: /subs, /stats (revenue dashboard), /grant u|g ID [days], /revoke u|g ID
"""
import html
import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

logger = logging.getLogger(__name__)

TZ_KH = timezone(timedelta(hours=7))
DAY = 86400
_HERE = os.path.dirname(os.path.abspath(__file__))

ALL_CONTENT = ["text", "photo", "voice", "audio", "video", "video_note", "document", "sticker",
               "animation", "location", "contact"]
# Non-members: plain-text chat only, plus what they need to look around, pay and follow Vid Cooler.
FREE_COMMANDS = {
    "start", "help", "menu", "about", "pay", "subscribe", "member", "myplan", "plan", "addgroup",
    "follow", "social", "tiktok", "contact", "vidcooler", "sos", "hub", "app",
    "work", "workin", "workout", "workscan", "workqr", "joinstaff", "staff", "setwork", "tpnews", "msgconnect", "msglink", "msgunlink",   # 🕘 staff attendance (staff may not be members)
}
FREE_CALLBACKS = ("hub:", "khqr:", "att:", "tpn:", "nav:main", "nav:about", "nav:cat:", "nav:team", "nav:events", "sub:", "follow:menu")
GROUP_FREE_COMMANDS = {"pay", "subscribe", "member", "myplan", "plan", "leave", "groups", "group", "tpnews",
                       "work", "workin", "workout"}   # 🕘 staff get the check-in buttons in any group
KINDS = ("u", "g", "c")   # person · group · group pass (credit to add the bot to one new group)


def settings():
    e = os.getenv
    return SimpleNamespace(
        enabled=e("SUBSCRIPTION_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on"),
        user_price=e("SUB_USER_PRICE", "1$").strip() or "1$",
        group_price=e("SUB_GROUP_PRICE", "5$").strip() or "5$",
        days=max(1, int(e("SUB_DAYS", "30") or 30)),
        grace_hours=max(1, int(e("SUB_GROUP_GRACE_HOURS", "24") or 24)),
        pay_link=e("PAYMENT_LINK", "https://pay.ababank.com/oRF8/0so1ducp").strip(),
        account=e("PAYMENT_ACCOUNT_TEXT", "ABA · NET CHAN TARAVID · USD 001 087 144").strip(),
        qr_path=e("PAYMENT_QR_PATH", os.path.join(_HERE, "assets", "payment_qr.jpg")),
    )


# ---------------- storage ----------------

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
        conn.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
            kind TEXT NOT NULL, target_id INTEGER NOT NULL, paid_until INTEGER NOT NULL,
            reminded_for INTEGER, PRIMARY KEY (kind, target_id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS sub_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, target_id INTEGER NOT NULL,
            payer_id INTEGER NOT NULL, payer_name TEXT, file_id TEXT, status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER NOT NULL, decided_at INTEGER)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS sub_group_notices (
            chat_id INTEGER PRIMARY KEY, deadline INTEGER NOT NULL)""")
        # one row per reminder sent, so each stage goes out once per paid period
        conn.execute("""CREATE TABLE IF NOT EXISTS sub_reminders (
            kind TEXT NOT NULL, target_id INTEGER NOT NULL, period INTEGER NOT NULL, stage TEXT NOT NULL,
            sent_at INTEGER NOT NULL, PRIMARY KEY (kind, target_id, period, stage))""")
        conn.execute("CREATE TABLE IF NOT EXISTS sub_meta (key TEXT PRIMARY KEY, value TEXT)")
        # paid group passes: one credit = the right to add the bot to one new group
        conn.execute("""CREATE TABLE IF NOT EXISTS group_credits (
            user_id INTEGER PRIMARY KEY, credits INTEGER NOT NULL DEFAULT 0, updated_at INTEGER)""")


def credits(config, user_id):
    with _connect(config) as conn:
        row = conn.execute("SELECT credits FROM group_credits WHERE user_id=?", (user_id,)).fetchone()
    return row["credits"] if row else 0


def add_credit(config, user_id, n=1):
    """Add n group passes; returns the new total."""
    with _connect(config) as conn:
        conn.execute("""INSERT INTO group_credits(user_id, credits, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET credits=max(0, credits + excluded.credits),
            updated_at=excluded.updated_at""", (user_id, int(n), int(time.time())))
    return credits(config, user_id)


def use_credit(config, user_id):
    """Spend one group pass atomically. True when one was available."""
    with _connect(config) as conn:
        cur = conn.execute("UPDATE group_credits SET credits=credits-1, updated_at=? "
                           "WHERE user_id=? AND credits > 0", (int(time.time()), user_id))
        return cur.rowcount == 1


def price_value(text):
    """'1$' -> 1.0, '5 USD' -> 5.0, 'free' -> 0.0"""
    m = re.search(r"\d+(?:[.,]\d+)?", text or "")
    return float(m.group(0).replace(",", ".")) if m else 0.0


def _once(config, kind, target_id, period, stage, now):
    """True the first time (kind, target, period, stage) is seen — then never again."""
    with _connect(config) as conn:
        cur = conn.execute("INSERT OR IGNORE INTO sub_reminders(kind, target_id, period, stage, sent_at) "
                           "VALUES (?, ?, ?, ?, ?)", (kind, target_id, int(period), stage, int(now)))
        return cur.rowcount == 1


def _meta(config, key, value=None):
    with _connect(config) as conn:
        if value is None:
            row = conn.execute("SELECT value FROM sub_meta WHERE key=?", (key,)).fetchone()
            return row["value"] if row else None
        conn.execute("INSERT OR REPLACE INTO sub_meta(key, value) VALUES (?, ?)", (key, str(value)))
        return value


def month_start(ts, months_back=0):
    d = datetime.fromtimestamp(ts, TZ_KH)
    y, m = d.year, d.month - months_back
    while m <= 0:
        y, m = y - 1, m + 12
    return int(datetime(y, m, 1, tzinfo=TZ_KH).timestamp())


def stats(config, now=None):
    """Numbers for the owner dashboard and the daily report."""
    s = settings()
    now = int(now or time.time())
    price = {"u": price_value(s.user_price), "g": price_value(s.group_price), "c": price_value(s.group_price)}
    this_m, last_m = month_start(now), month_start(now, 1)
    day0 = int(datetime.fromtimestamp(now, TZ_KH).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    with _connect(config) as conn:
        subs = conn.execute("SELECT kind, target_id, paid_until FROM subscriptions").fetchall()
        pays = conn.execute("SELECT kind, payer_id, target_id, decided_at FROM sub_payments "
                            "WHERE status='approved' AND decided_at IS NOT NULL").fetchall()
        pending = conn.execute("SELECT COUNT(*) FROM sub_payments WHERE status='pending'").fetchone()[0]

    def revenue(start, end):
        return sum(price.get(p["kind"], 0) for p in pays if start <= p["decided_at"] < end)

    def count(start, end, kind):
        kinds = ("g", "c") if kind == "g" else (kind,)
        return sum(1 for p in pays if p["kind"] in kinds and start <= p["decided_at"] < end)

    per_target = {}
    for p in pays:
        per_target[(p["kind"], p["target_id"])] = per_target.get((p["kind"], p["target_id"]), 0) + 1
    payers = len(per_target)
    return SimpleNamespace(
        active_u=sum(1 for r in subs if r["kind"] == "u" and r["paid_until"] > now),
        active_g=sum(1 for r in subs if r["kind"] == "g" and r["paid_until"] > now),
        expiring=[dict(r) for r in subs if now < r["paid_until"] <= now + 7 * DAY],
        lapsed=[dict(r) for r in subs if now - 14 * DAY < r["paid_until"] <= now],
        rev_month=revenue(this_m, now + 1), rev_last=revenue(last_m, this_m), rev_all=revenue(0, now + 1),
        rev_today=revenue(day0, now + 1), rev_yesterday=revenue(day0 - DAY, day0),
        new_u=count(this_m, now + 1, "u"), new_g=count(this_m, now + 1, "g"),
        pays_yesterday=sum(1 for p in pays if day0 - DAY <= p["decided_at"] < day0),
        pending=pending,
        renew_rate=round(100 * sum(1 for n in per_target.values() if n > 1) / payers) if payers else 0,
    )


def fmt_money(v):
    return f"{v:,.0f}$" if float(v).is_integer() else f"{v:,.2f}$"


def paid_until(config, kind, target_id):
    with _connect(config) as conn:
        row = conn.execute("SELECT paid_until FROM subscriptions WHERE kind=? AND target_id=?",
                           (kind, target_id)).fetchone()
    return row["paid_until"] if row else 0


def is_active(config, kind, target_id, now=None):
    return paid_until(config, kind, target_id) > (now or time.time())


def extend(config, kind, target_id, days, now=None):
    """Switch a paid period on. For kind "c" (group pass) target_id is the payer: one credit is
    added and the new credit total is returned instead of a date."""
    if kind == "c":
        return add_credit(config, target_id, 1)
    now = int(now or time.time())
    start = max(now, paid_until(config, kind, target_id))
    until = start + days * DAY
    with _connect(config) as conn:
        conn.execute("""INSERT INTO subscriptions(kind, target_id, paid_until, reminded_for) VALUES (?, ?, ?, NULL)
            ON CONFLICT(kind, target_id) DO UPDATE SET paid_until=excluded.paid_until, reminded_for=NULL""",
                     (kind, target_id, until))
        if kind == "g":
            conn.execute("DELETE FROM sub_group_notices WHERE chat_id=?", (target_id,))
    return until


def revoke(config, kind, target_id):
    with _connect(config) as conn:
        conn.execute("DELETE FROM subscriptions WHERE kind=? AND target_id=?", (kind, target_id))


def fmt_date(ts):
    return datetime.fromtimestamp(ts, TZ_KH).strftime("%d/%m/%Y %H:%M")


def group_deadline(config, chat_id):
    with _connect(config) as conn:
        row = conn.execute("SELECT deadline FROM sub_group_notices WHERE chat_id=?", (chat_id,)).fetchone()
    return row["deadline"] if row else None


def set_group_deadline(config, chat_id, deadline):
    with _connect(config) as conn:
        conn.execute("INSERT OR REPLACE INTO sub_group_notices(chat_id, deadline) VALUES (?, ?)", (chat_id, deadline))


def clear_group_deadline(config, chat_id):
    with _connect(config) as conn:
        conn.execute("DELETE FROM sub_group_notices WHERE chat_id=?", (chat_id,))


def free_group_ids():
    ids = set()
    for part in os.getenv("SUB_FREE_GROUPS", "").replace(" ", "").split(","):
        try:
            ids.add(int(part))
        except ValueError:
            pass
    return ids


def _active_groups(config):
    try:
        with _connect(config) as conn:
            return [dict(r) for r in conn.execute(
                "SELECT chat_id, title FROM bot_chats WHERE status != 'left'").fetchall()]
    except sqlite3.OperationalError:
        return []


def _mark_group_left(config, chat_id):
    try:
        with _connect(config) as conn:
            conn.execute("UPDATE bot_chats SET status='left', updated_at=CURRENT_TIMESTAMP WHERE chat_id=?", (chat_id,))
    except sqlite3.OperationalError:
        pass


# ---------------- module ----------------

# ---------- 💬 understand plain questions about paying (no need to know /pay) ----------
_QR_WORDS = ("qr", "khqr", "ស្កេន", "scan", "ឃ្យូអា", "គ្យូអា")
_PAY_WORDS = ("បង់", "ទូទាត់", "pay", "payment", "aba", "លេខគណនី", "account", "ផ្ទេរលុយ", "ផ្ទេរប្រាក់", "transfer",
              "wing", "acleda", "bakong")
_MEMBER_WORDS = ("សមាជិក", "member", "vip", "subscribe", "join", "ចូលរួម", "ចុះឈ្មោះ")
_PRICE_WORDS = ("តម្លៃ", "ថ្លៃ", "ប៉ុន្មាន", "price", "how much", "cost", "$", "ដុល្លារ", "លុយ")
_RENEW_WORDS = ("បន្ត", "ផុតកំណត់", "ហួសកំណត់", "expire", "renew", "នៅសល់ប៉ុន្មានថ្ងៃ", "ដល់ថ្ងៃណា")
_GROUP_WORDS = ("group", "ក្រុម", "គ្រុប", "ហ្គ្រុប")
_BOT_WORDS = ("bot", "បូត", "cooler", "hub", "ហាប់", "1$", "១$", "5$", "៥$")
_ASK_WORDS = ("?", "ម៉េច", "យ៉ាងម៉េច", "ណា", "អត់", "ទេ", "ចង់", "សុំ", "how", "where", "want", "need", "ផង", "ឲ្យ", "អោយ", "ខ្ញុំ")


def pay_intent(text):
    """'qr' | 'group' | 'renew' | 'info' | None for a plain message."""
    t = (text or "").casefold()
    if not t or t.startswith("/") or len(t) > 300:
        return None
    try:
        import attendance
        if attendance.work_intent(t):
            return None          # «QR ចូលធ្វើការ» is the staff check-in QR, not the payment QR
    except Exception:
        pass
    has = lambda words: any(w in t for w in words)
    group = has(_GROUP_WORDS) and (has(("ដាក់", "add", "ចូល")) or has(_PAY_WORDS))
    if has(_QR_WORDS):
        return "group" if group and has(_PAY_WORDS + ("ដាក់", "add")) else "qr"
    if has(_PAY_WORDS) and (has(_MEMBER_WORDS) or has(_BOT_WORDS) or has(_ASK_WORDS) or has(_PRICE_WORDS)):
        if group:
            return "group"
        return "renew" if has(_RENEW_WORDS) else "qr"
    if has(_RENEW_WORDS) and has(_MEMBER_WORDS + _BOT_WORDS):
        return "renew"
    if has(_MEMBER_WORDS) and (has(_PRICE_WORDS) or has(_ASK_WORDS)):
        return "info"
    return None


def register(bot, config):
    """Register FIRST (before every other feature) so the gate runs before other handlers."""
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    init_schema(config)
    waiting = {}          # user_id -> (kind, target_id, expires)
    hooks = {}            # "khqr": fn(chat_id, uid, kind, target_id, amount, title) -> bool
    bot._cooler_sub_waiting = waiting
    group_nag = {}        # chat_id -> last paywall reply (monotonic)
    me = {}

    def privileged(uid):
        return uid is not None and (uid == getattr(config, "OWNER_ID", 0) or uid in getattr(config, "ADMIN_IDS", []))

    def bot_username():
        if "u" not in me:
            try:
                me["u"] = bot.get_me().username
            except Exception:
                return None
        return me["u"]

    def is_group(chat):
        return getattr(chat, "type", None) in ("group", "supergroup")

    def command_of(message):
        text = getattr(message, "text", None) or ""
        if not text.startswith("/"):
            return None
        return text.split()[0][1:].split("@")[0].lower()

    def group_allowed(chat_id, now=None):
        now = now or time.time()
        if is_active(config, "g", chat_id, now) or exempt(chat_id):
            return True
        deadline = group_deadline(config, chat_id)
        return deadline is not None and deadline > now  # still inside the 24 h notice

    # ---------- texts & keyboards ----------

    def benefits():
        s = settings()
        return (f"💳 <b>សមាជិក Cooler Bot</b>\n\n"
                "🆓 <b>មិនទាន់ជាសមាជិក៖</b> ជជែកអក្សរជាមួយ Bot បានតែប៉ុណ្ណោះ\n"
                f"👤 <b>សមាជិក៖ {html.escape(s.user_price)} / {s.days} ថ្ងៃ</b> — ប្រើគ្រប់មុខងារ (កែរូប AI "
                f"Prompt Studio ហ្គេម Quiz Fan Card ព័ត៌មាន មាស ដុល្លារ ...)\n"
                f"👥 <b>ដាក់ Bot ចូល Group៖ {html.escape(s.group_price)} / {s.days} ថ្ងៃ</b> — សម្រាប់សមាជិកប៉ុណ្ណោះ "
                "បង់មុន ទើប Add បាន (/addgroup)\n\n"
                f"📅 គិត {s.days} ថ្ងៃ ចាប់ពីថ្ងៃ Approve\n"
                "🙏 លុយនេះជួយ Vid Cooler រក្សា Bot ឲ្យដំណើរការ និង Update មុខងារថ្មីៗសម្រាប់ហ្វេន ❤️")

    def paywall_markup():
        m = InlineKeyboardMarkup()
        s = settings()
        m.row(InlineKeyboardButton(f"💳 បង់ {s.user_price} ប្រើ {s.days} ថ្ងៃ", callback_data="sub:pay:u"))
        m.row(InlineKeyboardButton("🆓 មុខងារឥតគិតថ្លៃ", callback_data="sub:free"),
              InlineKeyboardButton("📅 ស្ថានភាពខ្ញុំ", callback_data="sub:plan"))
        return m

    def free_text():
        return ("🆓 <b>មុខងារឥតគិតថ្លៃ</b>\n\n"
                "💬 ជជែកអក្សរជាមួយ Bot — គ្រាន់តែវាយសារមក\n"
                "🆘 /sos — លេខទូរស័ព្ទបន្ទាន់\n"
                "📲 /follow — តាមដាន Vid Cooler\n\n"
                "🔒 មុខងារផ្សេងទាំងអស់ និងការដាក់ Bot ចូល Group សម្រាប់សមាជិក — /pay")

    def plan_text(uid):
        until = paid_until(config, "u", uid)
        if privileged(uid):
            return "👑 អ្នកជា Owner/Admin — ប្រើគ្រប់មុខងារដោយមិនកំណត់"
        if until > time.time():
            left = int((until - time.time()) // DAY)
            return f"✅ អ្នកជាសមាជិក\n📅 ផុតកំណត់៖ <b>{fmt_date(until)}</b> (នៅសល់ {left} ថ្ងៃ)"
        if until:
            return f"⌛ សមាជិកភាពផុតកំណត់នៅ {fmt_date(until)}\nបង់ម្ដងទៀតដើម្បីប្រើបន្ត 👉 /pay"
        return "🔒 អ្នកមិនទាន់ជាសមាជិកទេ 👉 /pay"

    def send_paywall(chat_id, intro="🔒 <b>មុខងារនេះសម្រាប់សមាជិក</b>\n\n"):
        bot.send_message(chat_id, intro + benefits(), reply_markup=paywall_markup(), parse_mode="HTML")

    def send_payment(chat_id, uid, kind, target_id, title=None):
        s = settings()
        waiting[uid] = (kind, target_id, time.monotonic() + 3600)
        hook = hooks.get("khqr")
        if hook is not None:
            # ⚡ automatic KHQR (Bakong); the receipt photo still works as a fallback
            try:
                price = price_value(s.group_price if kind in ("g", "c") else s.user_price)
                if price > 0 and hook(chat_id, uid, kind, target_id, price, title):
                    return
            except Exception as exc:
                logger.warning("KHQR hook failed (%s)", type(exc).__name__)
        what = (f"👥 Group <b>{html.escape(title or str(target_id))}</b> — {html.escape(s.group_price)} / {s.days} ថ្ងៃ"
                if kind == "g" else
                f"➕ សិទ្ធិដាក់ Bot ចូល Group ១ — {html.escape(s.group_price)} / {s.days} ថ្ងៃ" if kind == "c"
                else f"👤 សមាជិកម្នាក់ — {html.escape(s.user_price)} / {s.days} ថ្ងៃ")
        caption = (f"💳 <b>បង់ប្រាក់</b>\n{what}\n\n"
                   f"1️⃣ Scan KHQR ឬចុច «បង់តាម ABA»\n🏦 {html.escape(s.account)}\n"
                   "2️⃣ ថតរូប/Screenshot បង្កាន់ដៃ ហើយ <b>ផ្ញើរូបនោះមកទីនេះ</b>\n"
                   "3️⃣ Owner ពិនិត្យ ហើយ Approve — អ្នកនឹងទទួលសារភ្លាម ✅")
        m = InlineKeyboardMarkup()
        if s.pay_link.startswith("https://"):
            m.row(InlineKeyboardButton("💳 បង់តាម ABA", url=s.pay_link))
        m.row(InlineKeyboardButton("❌ បោះបង់", callback_data="sub:cancel"))
        try:
            with open(s.qr_path, "rb") as qr:
                bot.send_photo(chat_id, qr, caption=caption, reply_markup=m, parse_mode="HTML")
        except OSError:
            bot.send_message(chat_id, caption, reply_markup=m, parse_mode="HTML")

    def group_pay_markup(chat_id):
        m = InlineKeyboardMarkup()
        username = bot_username()
        if username:
            m.row(InlineKeyboardButton(f"💳 បង់ {settings().group_price} សម្រាប់ Group នេះ",
                                       url=f"https://t.me/{username}?start=payg_{chat_id}"))
        return m

    def group_notice_text(deadline, expired=False):
        s = settings()
        head = ("⌛ <b>សមាជិកភាព Group នេះផុតកំណត់ហើយ</b>" if expired
                else "👋 <b>សួស្ដី! Cooler Bot ឥឡូវត្រូវការការគាំទ្រ</b>")
        return (f"{head}\n\n"
                f"👥 Bot ក្នុង Group៖ <b>{html.escape(s.group_price)} / {s.days} ថ្ងៃ</b> (Owner Group ឬអ្នកណាក៏បង់បាន)\n"
                f"⏰ បើមិនទាន់បង់ត្រឹម <b>{fmt_date(deadline)}</b> Bot នឹងចាកចេញពី Group ដោយស្វ័យប្រវត្តិ\n\n"
                "🙏 លុយនេះជួយ Vid Cooler រក្សា Bot និង Update មុខងារថ្មីៗ ❤️\n"
                "👇 ចុចប៊ូតុងខាងក្រោម ដើម្បីបង់ក្នុង Private Chat")

    # ---------- gate ----------

    def blocked_message(message):
        s = settings()
        uid = getattr(getattr(message, "from_user", None), "id", None)
        chat = getattr(message, "chat", None)
        if getattr(chat, "type", None) == "private":
            try:   # 🧠 so the AI understands a follow-up like «ធ្វើម៉េច?» after a command
                import ai_assistant
                ai_assistant.remember_command(uid, getattr(message, "text", None))
            except Exception:
                pass
        if not s.enabled:
            return False
        if privileged(uid) or chat is None:
            return False
        att_busy = getattr(bot, "_cooler_att_busy", None)
        if att_busy is not None and getattr(chat, "type", None) == "private" and att_busy(uid):
            return False   # 🕘 attendance step: location / selfie
        cmd = command_of(message)
        if is_group(chat):
            if cmd in GROUP_FREE_COMMANDS:
                return False
            return not group_allowed(chat.id)
        if getattr(chat, "type", None) != "private":
            return False
        state = waiting.get(uid)
        if state and state[2] > time.monotonic() and getattr(message, "content_type", "") in ("photo", "document"):
            return False  # payment receipt
        if cmd in FREE_COMMANDS:
            return False
        if is_active(config, "u", uid):
            return False
        if getattr(message, "content_type", "") == "text" and cmd is None:
            message._cooler_text_only = True   # non-member: plain AI chat only (bot.py skips extras)
            return False
        return True

    @bot.message_handler(func=blocked_message, content_types=ALL_CONTENT)
    def gate_message(message):
        chat = message.chat
        if is_group(chat):
            try:  # make sure the group is known so the notice loop can handle it
                import group_manager
                group_manager.record_chat(config, chat)
            except Exception:
                pass
            if command_of(message) is None:
                return  # stay quiet on normal chatter
            last = group_nag.get(chat.id)
            if last is not None and time.monotonic() - last < 1800:
                return
            group_nag[chat.id] = time.monotonic()
            deadline = group_deadline(config, chat.id) or int(time.time())
            bot.send_message(chat.id, group_notice_text(deadline, expired=True),
                             reply_markup=group_pay_markup(chat.id), parse_mode="HTML")
            return
        send_paywall(chat.id)

    def blocked_call(call):
        s = settings()
        if not s.enabled:
            return False
        uid = getattr(getattr(call, "from_user", None), "id", None)
        message = getattr(call, "message", None)
        chat = getattr(message, "chat", None)
        if privileged(uid) or chat is None:
            return False
        data = call.data or ""
        if data.startswith(FREE_CALLBACKS) or data.startswith("groups:"):
            return False
        if is_group(chat):
            return not group_allowed(chat.id)
        return not is_active(config, "u", uid)

    @bot.callback_query_handler(func=blocked_call)
    def gate_call(call):
        try:
            bot.answer_callback_query(call.id, f"🔒 សម្រាប់សមាជិក — បង់ {settings().user_price} ប្រើ {settings().days} ថ្ងៃ",
                                      show_alert=True)
        except Exception:
            pass
        if not is_group(call.message.chat):
            send_paywall(call.message.chat.id)

    # ---------- payment flow ----------

    @bot.message_handler(commands=["pay", "subscribe", "member"])
    def pay_cmd(message):
        chat = message.chat
        if is_group(chat):
            deadline = group_deadline(config, chat.id)
            if is_active(config, "g", chat.id):
                bot.reply_to(message, f"✅ Group នេះជាសមាជិករហូតដល់ <b>{fmt_date(paid_until(config, 'g', chat.id))}</b>\n"
                             "ចង់បន្តមុន ចុចប៊ូតុងខាងក្រោម", reply_markup=group_pay_markup(chat.id), parse_mode="HTML")
            else:
                bot.reply_to(message, group_notice_text(deadline or int(time.time()), expired=deadline is None),
                             reply_markup=group_pay_markup(chat.id), parse_mode="HTML")
            return
        bot.reply_to(message, benefits(), reply_markup=paywall_markup(), parse_mode="HTML")

    @bot.message_handler(commands=["myplan", "plan"])
    def plan_cmd(message):
        if is_group(message.chat):
            until = paid_until(config, "g", message.chat.id)
            text = (f"✅ Group ជាសមាជិករហូតដល់ <b>{fmt_date(until)}</b>" if until > time.time()
                    else "🔒 Group នេះមិនទាន់បង់ — /pay")
        else:
            uid = message.from_user.id
            text = plan_text(uid)
            until = paid_until(config, "u", uid)
            if privileged(uid) or until > time.time():
                import card_render
                user = message.from_user
                name = " ".join(x for x in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if x)
                left = 999 if privileged(uid) else int((until - time.time()) // DAY) + 1
                until_txt = "∞" if privileged(uid) else fmt_date(until)[:10]
                card_render.send_card(bot, message.chat.id,
                                      lambda: card_render.membership_card(name, until_txt, min(left, 999),
                                                                          settings().days if left < 999 else 1),
                                      text, reply_to=message.message_id)
                return
        bot.reply_to(message, text, parse_mode="HTML")

    @bot.message_handler(commands=["start"], func=lambda m: len((m.text or "").split()) > 1
                         and (m.text or "").split()[1].startswith(("payg_", "pay")))
    def start_pay(message):
        payload = (message.text or "").split()[1]
        uid = message.from_user.id
        if payload.startswith("payg_"):
            try:
                chat_id = int(payload[5:])
            except ValueError:
                chat_id = None
            if chat_id is None or chat_id >= 0:
                bot.reply_to(message, "❓ Link មិនត្រឹមត្រូវ។ សូមចុចប៊ូតុងបង់ក្នុង Group ម្ដងទៀត។")
                return
            title = next((g["title"] for g in _active_groups(config) if g["chat_id"] == chat_id), None)
            if title is None:
                try:
                    title = getattr(bot.get_chat(chat_id), "title", None)
                except Exception:
                    pass
            send_payment(message.chat.id, uid, "g", chat_id, title)
        else:
            send_payment(message.chat.id, uid, "u", uid)

    def is_receipt(message):
        state = waiting.get(getattr(getattr(message, "from_user", None), "id", None))
        return (getattr(getattr(message, "chat", None), "type", None) == "private"
                and bool(state) and state[2] > time.monotonic())

    @bot.message_handler(func=is_receipt, content_types=["photo", "document"])
    def receipt(message):
        uid = message.from_user.id
        kind, target_id, _exp = waiting.pop(uid)
        owner = getattr(config, "OWNER_ID", 0)
        file_id = (message.photo[-1].file_id if getattr(message, "photo", None)
                   else getattr(getattr(message, "document", None), "file_id", None))
        user = message.from_user
        name = " ".join(x for x in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if x) or str(uid)
        with _connect(config) as conn:
            pid = conn.execute("INSERT INTO sub_payments(kind, target_id, payer_id, payer_name, file_id, created_at) "
                               "VALUES (?, ?, ?, ?, ?, ?)", (kind, target_id, uid, name, file_id, int(time.time()))).lastrowid
        if not owner:
            bot.reply_to(message, "⚠️ Bot មិនទាន់កំណត់ Owner ទេ។ សូមទាក់ទង Vid Cooler ផ្ទាល់ (/contact) 🙏")
            return
        s = settings()
        plan = (f"👥 Group {s.group_price} — {html.escape(str(target_id))}" if kind == "g"
                else f"➕ សិទ្ធិដាក់ Bot ចូល Group {s.group_price}" if kind == "c" else f"👤 សមាជិក {s.user_price}")
        if kind == "g":
            title = next((g["title"] for g in _active_groups(config) if g["chat_id"] == target_id), None)
            if title:
                plan += f" ({html.escape(title)})"
        uname = f" @{user.username}" if getattr(user, "username", None) else ""
        caption = (f"🧾 <b>ការបង់ប្រាក់ថ្មី #{pid}</b>\n{plan}\n"
                   f"👤 {html.escape(name)}{html.escape(uname)} · <code>{uid}</code>\n\n"
                   "ពិនិត្យលុយចូល ABA ហើយចុច Approve ✅")
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("✅ Approve", callback_data=f"sub:ok:{pid}"),
              InlineKeyboardButton("❌ Reject", callback_data=f"sub:no:{pid}"))
        try:
            if getattr(message, "photo", None):
                bot.send_photo(owner, file_id, caption=caption, reply_markup=m, parse_mode="HTML")
            else:
                bot.send_document(owner, file_id, caption=caption, reply_markup=m, parse_mode="HTML")
        except Exception as exc:
            logger.warning("could not forward receipt (%s)", type(exc).__name__)
            bot.reply_to(message, "⚠️ ផ្ញើទៅ Owner មិនបានទេ។ សូមទាក់ទង Vid Cooler ផ្ទាល់ (/contact) 🙏")
            return
        bot.reply_to(message, "🙏 បានទទួលបង្កាន់ដៃហើយ! Owner នឹងពិនិត្យ ហើយ Approve ឆាប់ៗ ✅\n"
                     "អ្នកនឹងទទួលសារពេលរួចរាល់។")

    def decide(call, pid, approve):
        with _connect(config) as conn:
            row = conn.execute("SELECT * FROM sub_payments WHERE id=?", (pid,)).fetchone()
        if row is None or row["status"] != "pending":
            bot.answer_callback_query(call.id, "ការបង់នេះត្រូវបានសម្រេចរួចហើយ", show_alert=True)
            return
        s = settings()
        status = "approved" if approve else "rejected"
        with _connect(config) as conn:
            conn.execute("UPDATE sub_payments SET status=?, decided_at=? WHERE id=?", (status, int(time.time()), pid))
        kind, target, payer = row["kind"], row["target_id"], row["payer_id"]
        if approve and kind == "c":
            total = extend(config, "c", payer, s.days)
            note = f"✅ Approved #{pid} — សិទ្ធិ Group (មាន {total})"
            credit_notice(payer)
        elif approve:
            until = extend(config, kind, target, s.days)
            note = f"✅ Approved #{pid} — រហូតដល់ {fmt_date(until)}"
            try:
                if kind == "g":
                    bot.send_message(payer, f"🎉 អរគុណ! Group បានបើកដំណើរការរហូតដល់ <b>{fmt_date(until)}</b> ✅", parse_mode="HTML")
                    bot.send_message(target, f"🎉 Group នេះបានបង់រួចហើយ! Cooler Bot ធ្វើការរហូតដល់ <b>{fmt_date(until)}</b> 🙏❤️",
                                     parse_mode="HTML")
                else:
                    bot.send_message(payer, f"🎉 អរគុណដែលគាំទ្រ Vid Cooler!\n✅ អ្នកជាសមាជិករហូតដល់ <b>{fmt_date(until)}</b>\n"
                                     "វាយ /menu ដើម្បីចាប់ផ្ដើម 🚀", parse_mode="HTML")
            except Exception as exc:
                logger.warning("approve notice failed (%s)", type(exc).__name__)
            try:
                import ai_extras  # 🎁 referral reward
                ai_extras.referral_paid(bot, config, payer)
            except Exception as exc:
                logger.info("referral reward skipped (%s)", type(exc).__name__)
        else:
            note = f"❌ Rejected #{pid}"
            try:
                bot.send_message(payer, "❌ Owner មិនទាន់ឃើញលុយចូលទេ។ សូមពិនិត្យបង្កាន់ដៃ ហើយផ្ញើម្ដងទៀត (/pay) "
                                 "ឬទាក់ទង Vid Cooler (/contact) 🙏")
            except Exception:
                pass
        bot.answer_callback_query(call.id, note)
        try:
            bot.edit_message_caption((call.message.caption or "") + f"\n\n{note}", call.message.chat.id,
                                     call.message.message_id)
        except Exception:
            pass

    @bot.callback_query_handler(func=lambda call: (call.data or "").startswith("sub:"))
    def sub_cb(call):
        parts = (call.data or "").split(":")
        action = parts[1] if len(parts) > 1 else ""
        uid = call.from_user.id
        chat_id = call.message.chat.id
        if action in ("leave", "leaveall"):
            if uid != getattr(config, "OWNER_ID", 0) and uid not in getattr(config, "ADMIN_IDS", []):
                bot.answer_callback_query(call.id, "⛔ សម្រាប់ Owner ប៉ុណ្ណោះ", show_alert=True)
                return
            targets = []
            if action == "leave" and len(parts) > 2:
                try:
                    targets = [int(parts[2])]
                except ValueError:
                    targets = []
            elif action == "leaveall":
                now = time.time()
                targets = [g["chat_id"] for g in _active_groups(config)
                           if not is_active(config, "g", g["chat_id"], now) and not exempt(g["chat_id"])
                           and (len(parts) > 2 and parts[2] == "now" or (group_deadline(config, g["chat_id"]) or now + 1) <= now)]
            done = 0
            for cid in targets:
                if is_active(config, "g", cid):
                    continue
                try:
                    bot.send_message(cid, "👋 Group នេះមិនទាន់បង់ទេ Cooler Bot សូមលាសិន។\n"
                                          f"ចង់ប្រើម្ដងទៀត បង់ {html.escape(settings().group_price)} ហើយ Add Bot ចូលវិញ 🙏❤️")
                except Exception:
                    pass
                try:
                    bot.leave_chat(cid)
                    done += 1
                except Exception as exc:
                    logger.warning("manual leave failed (%s)", type(exc).__name__)
                _mark_group_left(config, cid)
                clear_group_deadline(config, cid)
            bot.answer_callback_query(call.id, f"🚪 បានចេញពី {done} Group", show_alert=True)
            return
        if action in ("ok", "no"):
            if uid != getattr(config, "OWNER_ID", 0) and uid not in getattr(config, "ADMIN_IDS", []):
                bot.answer_callback_query(call.id, "⛔ សម្រាប់ Owner ប៉ុណ្ណោះ", show_alert=True)
                return
            try:
                decide(call, int(parts[2]), action == "ok")
            except (IndexError, ValueError):
                bot.answer_callback_query(call.id)
            return
        bot.answer_callback_query(call.id)
        if action == "pay":
            kind = parts[2] if len(parts) > 2 and parts[2] in ("u", "c") else "u"
            if kind == "c" and not (privileged(uid) or is_active(config, "u", uid)):
                send_paywall(chat_id, "🔒 <b>ត្រូវចូលជាសមាជិកសិន</b> ទើបអាចដាក់ Bot ចូល Group បាន\n\n")
                return
            send_payment(chat_id, uid, kind, uid)
        elif action == "addgroup":
            show_addgroup(chat_id, uid)
        elif action == "free":
            bot.send_message(chat_id, free_text(), parse_mode="HTML")
        elif action == "plan":
            bot.send_message(chat_id, plan_text(uid), parse_mode="HTML")
        elif action == "cancel":
            waiting.pop(uid, None)
            bot.send_message(chat_id, "បានបោះបង់ការបង់ប្រាក់ហើយ។")

    # ---------- adding the bot to a group: members only, 5$ pass first ----------

    def add_link():
        username = bot_username()
        return f"https://t.me/{username}?startgroup=cooler" if username else None

    def credit_notice(uid):
        """Tell a payer they can now add the bot (button opens Telegram's group picker)."""
        n = credits(config, uid)
        m = InlineKeyboardMarkup()
        link = add_link()
        if link:
            m.row(InlineKeyboardButton("➕ ជ្រើស Group ដាក់ Bot ចូល", url=link))
        try:
            bot.send_message(uid, f"🎉 <b>បង់រួចរាល់!</b> អ្នកមានសិទ្ធិដាក់ Bot ចូល Group៖ <b>{n}</b>\n\n"
                                  "👇 ចុចប៊ូតុង ជ្រើស Group ហើយ Bot នឹងដំណើរការភ្លាម "
                                  f"{settings().days} ថ្ងៃ ✅\n"
                                  "💡 Group នោះត្រូវអនុញ្ញាតឲ្យអ្នក Add សមាជិកបាន", reply_markup=m, parse_mode="HTML")
        except Exception as exc:
            logger.warning("credit notice failed (%s)", type(exc).__name__)
    bot._cooler_credit_notice = credit_notice

    def show_addgroup(chat_id, uid):
        s = settings()
        m = InlineKeyboardMarkup()
        link = add_link()
        if not s.enabled or privileged(uid):
            if link:
                m.row(InlineKeyboardButton("➕ ជ្រើស Group ដាក់ Bot ចូល", url=link))
            bot.send_message(chat_id, "👑 ដាក់ Bot ចូល Group ណាក៏បាន 👇", reply_markup=m)
            return
        if not is_active(config, "u", uid):
            m.row(InlineKeyboardButton(f"💳 ចូលជាសមាជិក {s.user_price}", callback_data="sub:pay:u"))
            bot.send_message(chat_id, "🔒 <b>ដាក់ Bot ចូល Group បានតែសមាជិកប៉ុណ្ណោះ</b>\n\n"
                                      f"1️⃣ ចូលជាសមាជិក {html.escape(s.user_price)}\n"
                                      f"2️⃣ បង់ {html.escape(s.group_price)} សម្រាប់ Group ១ ({s.days} ថ្ងៃ)\n"
                                      "3️⃣ ចុច ➕ ជ្រើស Group — Bot ដំណើរការភ្លាម ✅",
                             reply_markup=m, parse_mode="HTML")
            return
        n = credits(config, uid)
        if n > 0:
            if link:
                m.row(InlineKeyboardButton("➕ ជ្រើស Group ដាក់ Bot ចូល", url=link))
            bot.send_message(chat_id, f"✅ អ្នកមានសិទ្ធិដាក់ Bot ចូល Group៖ <b>{n}</b>\n👇 ចុចប៊ូតុង ហើយជ្រើស Group",
                             reply_markup=m, parse_mode="HTML")
            return
        m.row(InlineKeyboardButton(f"💳 បង់ {s.group_price} ដាក់ Bot ចូល Group", callback_data="sub:pay:c"))
        bot.send_message(chat_id, f"👥 <b>ដាក់ Bot ចូល Group</b> — {html.escape(s.group_price)} / {s.days} ថ្ងៃ\n\n"
                                  "បង់មុន → ទទួលប៊ូតុង ➕ ជ្រើស Group → Bot ដំណើរការភ្លាម ✅\n"
                                  "⚠️ បើ Add ដោយមិនទាន់បង់ Bot នឹងចាកចេញភ្លាម",
                         reply_markup=m, parse_mode="HTML")

    @bot.message_handler(commands=["addgroup"])
    def addgroup_cmd(message):
        if getattr(message.chat, "type", None) != "private":
            m = InlineKeyboardMarkup()
            username = bot_username()
            if username:
                m.row(InlineKeyboardButton("➕ ដាក់ Bot ចូល Group", url=f"https://t.me/{username}?start=addgroup"))
            bot.reply_to(message, "សូមប្រើ /addgroup ក្នុង Private Chat ជាមួយ Bot 👇", reply_markup=m)
            return
        show_addgroup(message.chat.id, message.from_user.id)

    @bot.message_handler(commands=["start"], func=lambda m: (m.text or "").split()[1:2] == ["addgroup"])
    def start_addgroup(message):
        show_addgroup(message.chat.id, message.from_user.id)

    def on_bot_added(update):
        """Called by group_manager when the bot's own membership changes.
        Only an active member holding a group pass can bring the bot into a new group."""
        s = settings()
        chat = getattr(update, "chat", None)
        if not s.enabled or not is_group(chat):
            return
        old = getattr(getattr(update, "old_chat_member", None), "status", None)
        new = getattr(getattr(update, "new_chat_member", None), "status", None)
        if new not in ("member", "administrator") or old in ("member", "administrator", "creator", "restricted"):
            return  # not a fresh join (e.g. promoted to admin)
        adder = getattr(update, "from_user", None)
        uid = getattr(adder, "id", None)
        cid = chat.id
        title = html.escape((getattr(chat, "title", None) or str(cid))[:40])
        if privileged(uid) or exempt(cid) or is_active(config, "g", cid):
            return  # owner/admin, owner's own group, or the group is still paid
        member = uid is not None and is_active(config, "u", uid)
        if member and use_credit(config, uid):
            until = extend(config, "g", cid, s.days)
            clear_group_deadline(config, cid)
            try:
                bot.send_message(cid, f"🎉 <b>សួស្ដី! Cooler Bot បានចូល Group ហើយ</b>\n"
                                      f"✅ ដំណើរការរហូតដល់ <b>{fmt_date(until)}</b> — វាយ /menu ដើម្បីចាប់ផ្ដើម 🚀",
                                 parse_mode="HTML")
            except Exception:
                pass
            try:
                bot.send_message(uid, f"✅ Bot បានចូល <b>{title}</b> ហើយ — ដំណើរការរហូតដល់ {fmt_date(until)}\n"
                                      f"➕ សិទ្ធិនៅសល់៖ {credits(config, uid)}", parse_mode="HTML")
            except Exception:
                pass
            owner_alert(f"➕ Group ថ្មី <b>{title}</b> (ដោយ <code>{uid}</code>) — បើកដល់ {fmt_date(until)}")
            return
        why = ("🔒 មានតែ <b>សមាជិក Cooler Bot</b> ដែលបានបង់ {g} សម្រាប់ Group ទើបអាចដាក់ Bot ចូលបាន"
               if not member else "🔒 ត្រូវបង់ <b>{g}</b> ជាមុនសិន ទើបអាចដាក់ Bot ចូល Group បាន")
        why = why.format(g=html.escape(s.group_price))
        try:
            bot.send_message(cid, f"{why}\n👋 Cooler Bot សូមលាសិន 🙏", parse_mode="HTML")
        except Exception:
            pass
        if uid is not None:
            m = InlineKeyboardMarkup()
            m.row(InlineKeyboardButton(f"💳 បង់ {s.group_price} ដាក់ Bot ចូល Group" if member
                                       else f"💳 ចូលជាសមាជិក {s.user_price}",
                                       callback_data="sub:pay:c" if member else "sub:pay:u"))
            try:
                bot.send_message(uid, f"{why}\n\nBot បានចាកចេញពី <b>{title}</b> វិញហើយ។ "
                                      "បង់រួច ចុច ➕ ជ្រើស Group ម្ដងទៀត ✅", reply_markup=m, parse_mode="HTML")
            except Exception:
                pass  # they never opened the bot in private
        try:
            bot.leave_chat(cid)
        except Exception as exc:
            logger.warning("refused-group leave failed (%s)", type(exc).__name__)
        _mark_group_left(config, cid)
        clear_group_deadline(config, cid)
        owner_alert(f"🚫 មានគេ Add Bot ចូល <b>{title}</b> ដោយមិនបានបង់ (<code>{uid}</code>) — Bot ចាកចេញភ្លាម")
    bot._cooler_on_bot_added = on_bot_added

    # ---------- owner tools ----------

    def owner_only(message):
        return privileged(getattr(getattr(message, "from_user", None), "id", None))

    @bot.message_handler(commands=["subs"], func=owner_only)
    def subs_cmd(message):
        now = time.time()
        with _connect(config) as conn:
            rows = conn.execute("SELECT kind, target_id, paid_until FROM subscriptions WHERE paid_until > ? "
                                "ORDER BY paid_until", (now,)).fetchall()
            pending = conn.execute("SELECT COUNT(*) FROM sub_payments WHERE status='pending'").fetchone()[0]
            approved = conn.execute("SELECT kind, COUNT(*) FROM sub_payments WHERE status='approved' GROUP BY kind").fetchall()
        users = [r for r in rows if r["kind"] == "u"]
        groups = [r for r in rows if r["kind"] == "g"]
        lines = [f"💳 <b>សមាជិកសកម្ម</b> — 👤 {len(users)} · 👥 {len(groups)} · ⏳ រង់ចាំ {pending}",
                 "🧾 Approve សរុប៖ " + (", ".join(f"{'👤' if k == 'u' else '👥'} {n}" for k, n in approved) or "0"), ""]
        for r in (groups + users)[:40]:
            lines.append(f"{'👥' if r['kind'] == 'g' else '👤'} <code>{r['target_id']}</code> → {fmt_date(r['paid_until'])}")
        lines.append("\n/grant u|g ID [ថ្ងៃ] · /revoke u|g ID")
        bot.reply_to(message, "\n".join(lines), parse_mode="HTML")

    @bot.message_handler(commands=["stats", "money", "dashboard"], func=owner_only)
    def stats_cmd(message):
        st = stats(config)
        change = ""
        if st.rev_last:
            pct = round(100 * (st.rev_month - st.rev_last) / st.rev_last)
            change = f" ({'📈 +' if pct >= 0 else '📉 '}{pct}% ធៀបខែមុន)"
        lines = ["📊 <b>ផ្ទាំងគ្រប់គ្រង — Cooler Bot</b>", "",
                 f"💵 ថ្ងៃនេះ៖ <b>{fmt_money(st.rev_today)}</b>",
                 f"📆 ខែនេះ៖ <b>{fmt_money(st.rev_month)}</b>{change}",
                 f"🗓️ ខែមុន៖ {fmt_money(st.rev_last)} · 💰 សរុប៖ {fmt_money(st.rev_all)}", "",
                 f"✅ សកម្ម៖ 👤 <b>{st.active_u}</b> នាក់ · 👥 <b>{st.active_g}</b> Group",
                 f"🆕 ការបង់ខែនេះ៖ 👤 {st.new_u} · 👥 {st.new_g}",
                 f"🔁 អ្នកបង់ម្ដងទៀត៖ <b>{st.renew_rate}%</b>",
                 f"🧾 រង់ចាំ Approve៖ <b>{st.pending}</b>"]
        if st.expiring:
            lines += ["", f"⏰ <b>ផុតកំណត់ក្នុង ៧ ថ្ងៃ ({len(st.expiring)})</b> — Bot រំលឹកដោយស្វ័យប្រវត្តិ"]
            for e in sorted(st.expiring, key=lambda e: e["paid_until"])[:10]:
                lines.append(f"{'👥' if e['kind'] == 'g' else '👤'} <code>{e['target_id']}</code> → {fmt_date(e['paid_until'])}")
        if st.lapsed:
            lines += ["", f"💤 <b>ផុតហើយ មិនទាន់បន្ត ({len(st.lapsed)})</b> — អាចផ្ញើសារសួរសុខទុក្ខ"]
            for e in sorted(st.lapsed, key=lambda e: -e["paid_until"])[:10]:
                lines.append(f"{'👥' if e['kind'] == 'g' else '👤'} <code>{e['target_id']}</code> · {fmt_date(e['paid_until'])}")
        lines.append("\n/subs · /grant · /revoke")
        bot.reply_to(message, "\n".join(lines), parse_mode="HTML")

    @bot.message_handler(commands=["subgroups", "groupsubs", "unpaid"], func=owner_only)
    def subgroups_cmd(message):
        """Every known group: paid until / notice deadline / what happens next."""
        now = time.time()
        groups = _active_groups(config)
        lines = [f"👥 <b>Group ដែល Bot នៅក្នុង ({len(groups)})</b>", ""]
        m = InlineKeyboardMarkup()
        overdue = 0
        for g in groups[:40]:
            cid, title = g["chat_id"], html.escape((g.get("title") or str(g["chat_id"]))[:30])
            until = paid_until(config, "g", cid)
            deadline = group_deadline(config, cid)
            if until > now:
                lines.append(f"✅ {title} — បង់ដល់ {fmt_date(until)}")
                continue
            if exempt(cid):
                lines.append(f"👑 {title} — Group របស់អ្នក (មិនចេញ)")
                continue
            if deadline is None:
                state = "⏳ នឹងផ្ញើដំណឹង ២៤ម៉ោង ក្នុង ៥ នាទី"
            elif deadline > now:
                state = f"⏳ នឹងចេញ {fmt_date(deadline)} (នៅ {int((deadline - now) // 3600)} ម៉ោង)"
            else:
                state = "🔴 ហួសពេល — នឹងចេញក្នុង ៥ នាទី"
                overdue += 1
            lines.append(f"• {title} <code>{cid}</code>\n   {state}")
            m.row(InlineKeyboardButton(f"🚪 ចេញឥឡូវ — {(g.get('title') or str(cid))[:25]}", callback_data=f"sub:leave:{cid}"))
        if overdue:
            m.row(InlineKeyboardButton(f"🚪 ចេញពី Group ហួសពេលទាំង {overdue}", callback_data="sub:leaveall"))
        unpaid = [g for g in groups if paid_until(config, "g", g["chat_id"]) <= now and not exempt(g["chat_id"])]
        if unpaid:
            m.row(InlineKeyboardButton(f"⛔ ចេញពី Group មិនបង់ទាំងអស់ ({len(unpaid)}) ឥឡូវ", callback_data="sub:leaveall:now"))
        try:
            import ops_monitor
            ok, storage = ops_monitor.db_storage_status(config)
            if not ok:
                lines += ["", "⚠️ " + storage]
        except Exception:
            pass
        lines.append(f"\n{'✅ ប្រព័ន្ធបង់លុយបើក' if settings().enabled else '⛔ SUBSCRIPTION_ENABLED=false — Bot មិនចេញពី Group ទេ'}")
        lines.append("ℹ️ Group ដែល Bot មិនទាន់ឃើញសារសោះ មិនស្ថិតក្នុងបញ្ជីទេ")
        bot.reply_to(message, "\n".join(lines), reply_markup=m if m.keyboard else None, parse_mode="HTML")

    @bot.message_handler(commands=["grant", "revoke"], func=owner_only)
    def grant_cmd(message):
        parts = (message.text or "").split()
        cmd = parts[0][1:].split("@")[0].lower()
        try:
            kind = {"u": "u", "user": "u", "g": "g", "group": "g", "c": "c", "pass": "c"}[parts[1].lower()]
            target = int(parts[2])
            days = int(parts[3]) if len(parts) > 3 else settings().days
        except (IndexError, KeyError, ValueError):
            bot.reply_to(message, "របៀបប្រើ៖ <code>/grant u USER_ID 30</code> · <code>/grant g -100GROUP_ID</code> · "
                         "<code>/grant c USER_ID 1</code> (សិទ្ធិដាក់ចូល Group) · <code>/revoke u USER_ID</code>", parse_mode="HTML")
            return
        if kind == "c":   # /grant c USER_ID [n] → n group passes · /revoke c USER_ID → all removed
            n = int(parts[3]) if len(parts) > 3 else 1
            total = add_credit(config, target, -credits(config, target) if cmd == "revoke" else n)
            bot.reply_to(message, f"➕ {target} មានសិទ្ធិដាក់ Bot ចូល Group៖ {total}")
            if cmd == "grant":
                credit_notice(target)
            return
        if cmd == "revoke":
            revoke(config, kind, target)
            bot.reply_to(message, f"🗑️ បានដកសមាជិកភាព {target}")
            return
        until = extend(config, kind, target, days)
        bot.reply_to(message, f"✅ {target} ជាសមាជិករហូតដល់ {fmt_date(until)}")

    # ---------- background: group notices, auto-leave, reminders ----------

    exempt_cache = {}

    def exempt(chat_id):
        """Groups the owner created (or listed in SUB_FREE_GROUPS) never get the paywall or auto-leave."""
        if chat_id in free_group_ids():
            return True
        hit = exempt_cache.get(chat_id)
        if hit is not None and hit[1] > time.monotonic():
            return hit[0]
        owner = getattr(config, "OWNER_ID", 0)
        ok = False
        if owner:
            try:
                ok = bot.get_chat_member(chat_id, owner).status == "creator"
            except Exception:
                ok = False
        exempt_cache[chat_id] = (ok, time.monotonic() + 3600)
        return ok

    def owner_alert(text):
        owner = getattr(config, "OWNER_ID", 0)
        if not owner or os.getenv("SUB_OWNER_GROUP_ALERTS", "true").strip().lower() in ("0", "false", "no", "off"):
            return
        try:
            bot.send_message(owner, text, parse_mode="HTML")
        except Exception:
            pass

    def gname(g):
        return html.escape((g.get("title") or str(g.get("chat_id")))[:40])

    def tick(now=None):
        s = settings()
        if not s.enabled:
            return
        now = int(now or time.time())
        grace = s.grace_hours * 3600
        for g in _active_groups(config):
            cid = g["chat_id"]
            if exempt(cid):
                continue
            until = paid_until(config, "g", cid)
            if until > now:
                stage = "1d" if until - now <= DAY else "3d" if until - now <= 3 * DAY else None
                if stage and _once(config, "g", cid, until, stage, now):
                    try:
                        bot.send_message(cid, f"⏰ សមាជិកភាព Group នេះនឹងផុតកំណត់ <b>{fmt_date(until)}</b>"
                                         f"{' (ថ្ងៃស្អែក!)' if stage == '1d' else ''}\n"
                                         f"បង់ {html.escape(s.group_price)} ដើម្បីបន្ត {s.days} ថ្ងៃទៀត "
                                         "— ថ្ងៃដែលនៅសល់មិនបាត់ទេ (ថែមពីលើ) 🙏",
                                         reply_markup=group_pay_markup(cid), parse_mode="HTML")
                    except Exception:
                        pass
                continue
            deadline = group_deadline(config, cid)
            if deadline is None:
                deadline = now + grace
                set_group_deadline(config, cid, deadline)
                try:
                    bot.send_message(cid, group_notice_text(deadline, expired=bool(until)),
                                     reply_markup=group_pay_markup(cid), parse_mode="HTML")
                    try:
                        with open(s.qr_path, "rb") as qr:
                            bot.send_photo(cid, qr, caption=f"💳 KHQR — {html.escape(s.account)}", parse_mode="HTML")
                    except OSError:
                        pass
                    owner_alert(f"📢 ផ្ញើដំណឹង ២៤ ម៉ោង → <b>{gname(g)}</b>\n🚪 Bot នឹងចេញ {fmt_date(deadline)} បើមិនបង់")
                except Exception as exc:
                    text = str(exc).lower()
                    if "kicked" in text or "not found" in text or "not a member" in text or "forbidden" in text:
                        _mark_group_left(config, cid)
                        clear_group_deadline(config, cid)
                        owner_alert(f"ℹ️ Bot មិននៅក្នុង <b>{gname(g)}</b> ទៀតហើយ (គេដកចេញ) — លុបពីបញ្ជី")
            elif deadline <= now:
                try:
                    bot.send_message(cid, "👋 Group នេះមិនទាន់បង់ទេ Cooler Bot សូមលាសិន។\n"
                                     f"ចង់ប្រើម្ដងទៀត បង់ {html.escape(s.group_price)} ហើយ Add Bot ចូលវិញ 🙏❤️")
                except Exception:
                    pass
                try:
                    bot.leave_chat(cid)
                    owner_alert(f"🚪 Bot បានចេញពី <b>{gname(g)}</b> ដោយស្វ័យប្រវត្តិ (មិនបង់ក្នុង ២៤ ម៉ោង) ✅")
                except Exception as exc:
                    logger.warning("auto-leave failed for a group (%s)", type(exc).__name__)
                    owner_alert(f"❌ ចេញពី <b>{gname(g)}</b> មិនបាន ({html.escape(type(exc).__name__)}) — "
                                "ពិនិត្យដោយ /subgroups")
                _mark_group_left(config, cid)
                clear_group_deadline(config, cid)
        # personal reminders: 3 days, 1 day, and once right after it ends
        with _connect(config) as conn:
            rows = conn.execute("SELECT target_id, paid_until FROM subscriptions WHERE kind='u' "
                                "AND paid_until > ? AND paid_until < ?", (now - 3 * DAY, now + 3 * DAY)).fetchall()
        for r in rows:
            uid, until = r["target_id"], r["paid_until"]
            if until <= now:
                stage, text = "exp", (f"⌛ <b>សមាជិកភាពរបស់អ្នកផុតកំណត់ហើយ</b> ({fmt_date(until)})\n\n"
                                      f"អរគុណដែលបានគាំទ្រ Vid Cooler ❤️ បង់ {html.escape(s.user_price)} "
                                      f"ដើម្បីប្រើ AI ហ្គេម និងគ្រប់មុខងារបន្ត {s.days} ថ្ងៃទៀត 👇")
            else:
                stage = "1d" if until - now <= DAY else "3d"
                text = (f"⏰ សមាជិកភាពរបស់អ្នកនឹងផុតកំណត់ <b>{fmt_date(until)}</b>"
                        f"{' (ថ្ងៃស្អែក!)' if stage == '1d' else ''}\n"
                        f"បង់ {html.escape(s.user_price)} ឥឡូវ — ថ្ងៃដែលនៅសល់ត្រូវបានថែមពីលើ មិនបាត់ទេ 🙏")
            if not _once(config, "u", uid, until, stage, now):
                continue
            try:
                bot.send_message(uid, text, reply_markup=renew_markup(), parse_mode="HTML")
            except Exception:
                pass
        owner_jobs(now)

    def renew_markup():
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton(f"💳 បន្តសមាជិក {settings().user_price}", callback_data="sub:pay:u"))
        m.row(InlineKeyboardButton("📅 ស្ថានភាពខ្ញុំ", callback_data="sub:plan"))
        return m

    def owner_jobs(now):
        owner = getattr(config, "OWNER_ID", 0)
        if not owner:
            return
        # receipts waiting more than 3 h → one nudge each
        with _connect(config) as conn:
            late = conn.execute("SELECT id FROM sub_payments WHERE status='pending' AND created_at < ?",
                                (now - 3 * 3600,)).fetchall()
        late = [r["id"] for r in late if _once(config, "p", r["id"], 0, "late", now)]
        if late:
            try:
                bot.send_message(owner, f"🧾 មានបង្កាន់ដៃ {len(late)} កំពុងរង់ចាំ Approve យូរជាង ៣ ម៉ោង "
                                 f"(#{', #'.join(map(str, late))}) — ភ្ញៀវកំពុងរង់ចាំ 🙏\nមើល៖ /stats")
            except Exception:
                pass
        # daily report once a day after SUB_REPORT_HOUR (Cambodia time)
        hour = int(os.getenv("SUB_REPORT_HOUR", "8") or 8)
        local = datetime.fromtimestamp(now, TZ_KH)
        if hour < 0 or local.hour < hour:
            return
        today = local.strftime("%Y-%m-%d")
        if _meta(config, "daily_report") == today:
            return
        _meta(config, "daily_report", today)
        st = stats(config, now)
        try:
            bot.send_message(owner, daily_text(st), parse_mode="HTML")
        except Exception:
            pass

    def daily_text(st):
        lines = ["☀️ <b>របាយការណ៍ប្រចាំថ្ងៃ — Cooler Bot</b>",
                 f"💵 ម្សិលមិញ៖ <b>{fmt_money(st.rev_yesterday)}</b> ({st.pays_yesterday} ការបង់)",
                 f"📆 ខែនេះ៖ <b>{fmt_money(st.rev_month)}</b>",
                 f"✅ សមាជិកសកម្ម៖ 👤 {st.active_u} · 👥 {st.active_g}"]
        if st.pending:
            lines.append(f"🧾 រង់ចាំ Approve៖ <b>{st.pending}</b>")
        soon = [e for e in st.expiring if e["paid_until"] <= time.time() + DAY]
        if soon:
            lines.append(f"⏰ ផុតកំណត់ក្នុង ២៤ ម៉ោង៖ {len(soon)} (Bot រំលឹកពួកគេរួចហើយ)")
        lines.append("\n📊 លម្អិត៖ /stats")
        return "\n".join(lines)

    def start_loop(interval=300):
        def run():
            time.sleep(20)
            while True:
                try:
                    tick()
                except Exception as exc:
                    logger.warning("subscription tick failed (%s)", type(exc).__name__)
                time.sleep(interval)
        t = threading.Thread(target=run, name="subscription-loop", daemon=True)
        t.start()
        return t

    def text_intent(message):
        """Answer payment questions straight away (QR, price, renew, group) — True when handled."""
        kind = pay_intent(getattr(message, "text", None))
        if not kind:
            return False
        chat, uid = message.chat, message.from_user.id
        s = settings()
        if is_group(chat):
            bot.reply_to(message, "💳 <b>បង់ប្រាក់សម្រាប់ Group នេះ</b>\n"
                                  f"{html.escape(s.group_price)} / {s.days} ថ្ងៃ — ចុចប៊ូតុងខាងក្រោម បង់ក្នុង Chat ផ្ទាល់ (មាន QR) 👇",
                         reply_markup=group_pay_markup(chat.id), parse_mode="HTML")
            return True
        member = privileged(uid) or is_active(config, "u", uid)
        if kind == "group":
            if not member:
                bot.reply_to(message, "👥 <b>ដាក់ Bot ចូល Group</b> — សម្រាប់សមាជិកប៉ុណ្ណោះ\n"
                                      f"1️⃣ ចូលជាសមាជិកសិន {html.escape(s.user_price)} / {s.days} ថ្ងៃ (QR ខាងក្រោម 👇)\n"
                                      f"2️⃣ បន្ទាប់មកបង់ {html.escape(s.group_price)} សម្រាប់ Group ១ → ចុច «ជ្រើស Group»",
                             parse_mode="HTML")
                send_payment(chat.id, uid, "u", uid)
                return True
            bot.reply_to(message, f"👥 <b>ដាក់ Bot ចូល Group</b> — {html.escape(s.group_price)} / {s.days} ថ្ងៃ\n"
                                  "Scan QR ខាងក្រោម → បង់រួច Bot ផ្ញើប៊ូតុង «ជ្រើស Group» ឲ្យ ✅", parse_mode="HTML")
            send_payment(chat.id, uid, "c", uid)
            return True
        if kind == "info":
            bot.reply_to(message, "😊 នេះជាព័ត៌មានសមាជិកភាព — ចុច 💳 ដើម្បីឃើញ QR ភ្លាម 👇\n\n" + benefits(),
                         reply_markup=paywall_markup(), parse_mode="HTML")
            return True
        intro = ("📅 " + plan_text(uid) + "\n\n🔄 ចង់បន្តមុន? Scan QR ខាងក្រោម 👇") if (kind == "renew" or member) and not privileged(uid) \
            else f"😊 បាទ! នេះជា <b>QR</b> សម្រាប់ចូលជាសមាជិក Cooler Bot — {html.escape(s.user_price)} / {s.days} ថ្ងៃ 👇"
        if privileged(uid):
            intro = "👑 Papa ជា Owner — នេះជា QR ដូចដែលភ្ញៀវឃើញ 👇"
        bot.reply_to(message, intro, parse_mode="HTML")
        send_payment(chat.id, uid, "u", uid)
        return True

    return SimpleNamespace(tick=tick, start_loop=start_loop, waiting=waiting, hooks=hooks, blocked_message=blocked_message,
                           blocked_call=blocked_call, benefits=benefits, paywall_markup=paywall_markup,
                           text_intent=text_intent, send_payment=send_payment)
