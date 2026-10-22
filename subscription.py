"""Paid membership for Cooler Bot (Khmer UI).

  • Person: SUB_USER_PRICE (1$) → every feature for SUB_DAYS (30) days, counted from approval.
  • Group:  SUB_GROUP_PRICE (5$) → the bot works in that group for SUB_DAYS days,
            whoever pays (group owner or anyone who added the bot).
  • Payment: ABA KHQR / pay link → the payer sends the receipt photo to the bot →
            the owner taps ✅ Approve / ❌ Reject.
  • Unpaid groups get a notice with the QR and SUB_GROUP_GRACE_HOURS (24 h); after that,
    and when a paid month ends (+24 h notice), the bot leaves the group by itself.
  • Unpaid people can still use a few free features (welcome, menu, follow, SOS and
    health tips); everything else shows the payment screen.
  • Owner/admins are never blocked. SUBSCRIPTION_ENABLED=false turns everything off.

Owner commands: /subs, /grant u|g ID [days], /revoke u|g ID
"""
import html
import logging
import os
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
FREE_COMMANDS = {
    "start", "help", "menu", "about", "pay", "subscribe", "member", "myplan", "plan",
    "follow", "social", "tiktok", "contact", "vidcooler",
    "sos", "health", "exercise", "balance", "warning", "water", "sleep",
}
FREE_CALLBACKS = ("nav:main", "nav:about", "sub:", "follow:menu", "health:", "hmore:water", "hmore:sleep")
GROUP_FREE_COMMANDS = {"pay", "subscribe", "member", "myplan", "plan", "leave", "groups", "group"}


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


def paid_until(config, kind, target_id):
    with _connect(config) as conn:
        row = conn.execute("SELECT paid_until FROM subscriptions WHERE kind=? AND target_id=?",
                           (kind, target_id)).fetchone()
    return row["paid_until"] if row else 0


def is_active(config, kind, target_id, now=None):
    return paid_until(config, kind, target_id) > (now or time.time())


def extend(config, kind, target_id, days, now=None):
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

def register(bot, config):
    """Register FIRST (before every other feature) so the gate runs before other handlers."""
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    init_schema(config)
    waiting = {}          # user_id -> (kind, target_id, expires)
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
        if is_active(config, "g", chat_id, now):
            return True
        deadline = group_deadline(config, chat_id)
        return deadline is not None and deadline > now  # still inside the 24 h notice

    # ---------- texts & keyboards ----------

    def benefits():
        s = settings()
        return (f"💳 <b>សមាជិក Cooler Bot</b>\n\n"
                f"👤 <b>ម្នាក់ឯង៖ {html.escape(s.user_price)} / {s.days} ថ្ងៃ</b> — ប្រើគ្រប់មុខងារ (AI ជជែក កែរូប AI "
                f"Prompt Studio ហ្គេម Quiz Fan Card ព័ត៌មាន មាស ដុល្លារ ...)\n"
                f"👥 <b>Group៖ {html.escape(s.group_price)} / {s.days} ថ្ងៃ</b> — Bot ធ្វើការក្នុង Group នោះ "
                f"(អ្នកណាបង់ក៏បាន)\n\n"
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
                "🆘 /sos — លេខទូរស័ព្ទបន្ទាន់\n🧓 /health — សុខភាព និងលំហាត់ប្រាណ\n"
                "🏃 /exercise · ⚖️ /balance · 🚨 /warning · 💧 /water · 😴 /sleep\n"
                "📲 /follow — តាមដាន Vid Cooler\n\n"
                "🔒 មុខងារផ្សេងទៀតសម្រាប់សមាជិក — /pay")

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
        what = (f"👥 Group <b>{html.escape(title or str(target_id))}</b> — {html.escape(s.group_price)} / {s.days} ថ្ងៃ"
                if kind == "g" else f"👤 សមាជិកម្នាក់ — {html.escape(s.user_price)} / {s.days} ថ្ងៃ")
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
        if not s.enabled:
            return False
        uid = getattr(getattr(message, "from_user", None), "id", None)
        chat = getattr(message, "chat", None)
        if privileged(uid) or chat is None:
            return False
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
        return not is_active(config, "u", uid)

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
            text = plan_text(message.from_user.id)
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
        plan = (f"👥 Group {s.group_price} — {html.escape(str(target_id))}" if kind == "g" else f"👤 សមាជិក {s.user_price}")
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
        if approve:
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
            send_payment(chat_id, uid, "u", uid)
        elif action == "free":
            bot.send_message(chat_id, free_text(), parse_mode="HTML")
        elif action == "plan":
            bot.send_message(chat_id, plan_text(uid), parse_mode="HTML")
        elif action == "cancel":
            waiting.pop(uid, None)
            bot.send_message(chat_id, "បានបោះបង់ការបង់ប្រាក់ហើយ។")

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

    @bot.message_handler(commands=["grant", "revoke"], func=owner_only)
    def grant_cmd(message):
        parts = (message.text or "").split()
        cmd = parts[0][1:].split("@")[0].lower()
        try:
            kind = {"u": "u", "user": "u", "g": "g", "group": "g"}[parts[1].lower()]
            target = int(parts[2])
            days = int(parts[3]) if len(parts) > 3 else settings().days
        except (IndexError, KeyError, ValueError):
            bot.reply_to(message, "របៀបប្រើ៖ <code>/grant u USER_ID 30</code> · <code>/grant g -100GROUP_ID</code> · "
                         "<code>/revoke u USER_ID</code>", parse_mode="HTML")
            return
        if cmd == "revoke":
            revoke(config, kind, target)
            bot.reply_to(message, f"🗑️ បានដកសមាជិកភាព {target}")
            return
        until = extend(config, kind, target, days)
        bot.reply_to(message, f"✅ {target} ជាសមាជិករហូតដល់ {fmt_date(until)}")

    # ---------- background: group notices, auto-leave, reminders ----------

    def tick(now=None):
        s = settings()
        if not s.enabled:
            return
        now = int(now or time.time())
        grace = s.grace_hours * 3600
        for g in _active_groups(config):
            cid = g["chat_id"]
            until = paid_until(config, "g", cid)
            if until > now:
                if until - now < 2 * DAY:
                    with _connect(config) as conn:
                        row = conn.execute("SELECT reminded_for FROM subscriptions WHERE kind='g' AND target_id=?",
                                           (cid,)).fetchone()
                    if not row or row["reminded_for"] != until:
                        try:
                            bot.send_message(cid, f"⏰ សមាជិកភាព Group នេះនឹងផុតកំណត់ <b>{fmt_date(until)}</b>\n"
                                             f"បង់ {html.escape(s.group_price)} ដើម្បីបន្ត {s.days} ថ្ងៃទៀត 🙏",
                                             reply_markup=group_pay_markup(cid), parse_mode="HTML")
                        except Exception:
                            pass
                        with _connect(config) as conn:
                            conn.execute("UPDATE subscriptions SET reminded_for=? WHERE kind='g' AND target_id=?", (until, cid))
                continue
            deadline = group_deadline(config, cid)
            if deadline is None:
                deadline = now + grace
                set_group_deadline(config, cid, deadline)
                try:
                    bot.send_message(cid, group_notice_text(deadline, expired=bool(until)),
                                     reply_markup=group_pay_markup(cid), parse_mode="HTML")
                    with open(s.qr_path, "rb") as qr:
                        bot.send_photo(cid, qr, caption=f"💳 KHQR — {html.escape(s.account)}", parse_mode="HTML")
                except Exception as exc:
                    text = str(exc).lower()
                    if "kicked" in text or "not found" in text or "not a member" in text or "forbidden" in text:
                        _mark_group_left(config, cid)
                        clear_group_deadline(config, cid)
            elif deadline <= now:
                try:
                    bot.send_message(cid, "👋 Group នេះមិនទាន់បង់ទេ Cooler Bot សូមលាសិន។\n"
                                     f"ចង់ប្រើម្ដងទៀត បង់ {html.escape(s.group_price)} ហើយ Add Bot ចូលវិញ 🙏❤️")
                except Exception:
                    pass
                try:
                    bot.leave_chat(cid)
                except Exception as exc:
                    logger.warning("auto-leave failed for a group (%s)", type(exc).__name__)
                _mark_group_left(config, cid)
                clear_group_deadline(config, cid)
        # personal reminders 2 days before the end
        with _connect(config) as conn:
            rows = conn.execute("SELECT target_id, paid_until FROM subscriptions WHERE kind='u' AND paid_until > ? "
                                "AND paid_until < ? AND (reminded_for IS NULL OR reminded_for != paid_until)",
                                (now, now + 2 * DAY)).fetchall()
        for r in rows:
            try:
                bot.send_message(r["target_id"], f"⏰ សមាជិកភាពរបស់អ្នកនឹងផុតកំណត់ <b>{fmt_date(r['paid_until'])}</b>\n"
                                 f"បង់ {html.escape(s.user_price)} ដើម្បីប្រើបន្ត 👉 /pay 🙏", parse_mode="HTML")
            except Exception:
                pass
            with _connect(config) as conn:
                conn.execute("UPDATE subscriptions SET reminded_for=? WHERE kind='u' AND target_id=?",
                             (r["paid_until"], r["target_id"]))

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

    return SimpleNamespace(tick=tick, start_loop=start_loop, waiting=waiting, blocked_message=blocked_message,
                           blocked_call=blocked_call, benefits=benefits, paywall_markup=paywall_markup)
