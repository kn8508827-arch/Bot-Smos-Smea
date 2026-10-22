"""Owner tools to see which groups the bot is in and make the bot leave them.

A bot can always leave a group by itself (Telegram leaveChat), even when the bot's
owner is not an admin or member of that group. This module keeps a small list of
groups the bot has seen and gives the owner/admins these commands:

  /groups           list the groups, with a "🚪 ចាកចេញ" button for each
  /leave CHAT_ID    make the bot leave that group (in a private chat with the bot)
  /leave            sent inside a group by the owner/admin: the bot leaves that group
"""
import html
import logging
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

logger = logging.getLogger(__name__)

GROUP_TYPES = ("group", "supergroup", "channel")
_ACTIVE = ("member", "administrator", "creator", "restricted")


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
        conn.execute("""CREATE TABLE IF NOT EXISTS bot_chats (
            chat_id INTEGER PRIMARY KEY,
            title TEXT,
            chat_type TEXT,
            status TEXT NOT NULL DEFAULT 'member',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        # Seed from groups already seen in the users table (older interactions).
        has_users = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        if has_users:
            conn.execute("""INSERT OR IGNORE INTO bot_chats(chat_id, chat_type, status)
                SELECT DISTINCT chat_id, chat_type, 'member' FROM users
                WHERE chat_type IN ('group', 'supergroup') AND chat_id IS NOT NULL""")


def record_chat(config, chat, status=None):
    """Remember a group the bot is in (or has left). Private chats are ignored."""
    chat_type = getattr(chat, "type", None)
    chat_id = getattr(chat, "id", None)
    if chat_type not in GROUP_TYPES or chat_id is None:
        return
    title = getattr(chat, "title", None)
    try:
        with _connect(config) as conn:
            if status is None:
                conn.execute("""INSERT INTO bot_chats(chat_id, title, chat_type, status)
                    VALUES (?, ?, ?, 'member')
                    ON CONFLICT(chat_id) DO UPDATE SET
                        title=COALESCE(excluded.title, bot_chats.title),
                        chat_type=excluded.chat_type,
                        status=CASE WHEN bot_chats.status='left' THEN 'member' ELSE bot_chats.status END,
                        updated_at=CURRENT_TIMESTAMP""", (chat_id, title, chat_type))
            else:
                conn.execute("""INSERT INTO bot_chats(chat_id, title, chat_type, status)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(chat_id) DO UPDATE SET
                        title=COALESCE(excluded.title, bot_chats.title),
                        chat_type=excluded.chat_type,
                        status=excluded.status,
                        updated_at=CURRENT_TIMESTAMP""", (chat_id, title, chat_type, status))
    except Exception as exc:
        logger.warning("Could not record group chat (%s)", type(exc).__name__)


def active_groups(config):
    with _connect(config) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT chat_id, title, chat_type FROM bot_chats WHERE status != 'left' "
            "ORDER BY updated_at DESC"
        ).fetchall()]


def _mark_left(config, chat_id):
    with _connect(config) as conn:
        conn.execute(
            "UPDATE bot_chats SET status='left', updated_at=CURRENT_TIMESTAMP WHERE chat_id=?",
            (chat_id,),
        )


def is_manager(config, user_id) -> bool:
    return user_id is not None and (
        user_id == getattr(config, "OWNER_ID", 0) or user_id in getattr(config, "ADMIN_IDS", [])
    )


def register(bot, config):
    """Register before the catch-all handlers in bot.py."""
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    init_schema(config)

    def leave(chat_id):
        """Make the bot leave. Returns (ok, title)."""
        title = None
        try:
            title = getattr(bot.get_chat(chat_id), "title", None)
        except Exception:
            pass
        try:
            bot.leave_chat(chat_id)
        except Exception as exc:
            text = str(exc).lower()
            if "not a member" in text or "chat not found" in text or "kicked" in text or "forbidden" in text:
                _mark_left(config, chat_id)  # already gone
                return True, title
            logger.warning("leave_chat failed (%s)", type(exc).__name__)
            return False, title
        _mark_left(config, chat_id)
        return True, title

    def groups_view():
        groups = active_groups(config)
        if not groups:
            return ("📭 Bot មិនទាន់ឃើញក្រុមណាទេ។\n\n"
                    "ក្រុមដែលបន្ថែម bot មុនពេលមុខងារនេះ នឹងបង្ហាញពេលមាននរណាម្នាក់ប្រើ bot ក្នុងក្រុមនោះ។ "
                    "ឬប្រើ <code>/leave CHAT_ID</code> បើដឹង ID ក្រុម។"), None
        lines = [f"👥 <b>ក្រុមដែល Bot នៅក្នុង ({len(groups)})</b>", ""]
        markup = InlineKeyboardMarkup()
        for i, g in enumerate(groups[:30], 1):
            name = html.escape(g["title"] or "(គ្មានឈ្មោះ)")
            lines.append(f"{i}. {name}\n    <code>{g['chat_id']}</code>")
            markup.row(InlineKeyboardButton(
                f"🚪 ចាកចេញ: {(g['title'] or str(g['chat_id']))[:28]}",
                callback_data=f"groups:ask:{g['chat_id']}",
            ))
        lines.append("\nចុចប៊ូតុង ដើម្បីឲ្យ Bot ចាកចេញពីក្រុមនោះ។")
        return "\n".join(lines), markup

    @bot.message_handler(commands=["groups"])
    def groups_command(message):
        user_id = getattr(getattr(message, "from_user", None), "id", None)
        if not is_manager(config, user_id):
            bot.reply_to(message, "⛔ ពាក្យបញ្ជានេះសម្រាប់ម្ចាស់ Bot ប៉ុណ្ណោះ។")
            return
        if getattr(message.chat, "type", None) != "private":
            bot.reply_to(message, "សូមប្រើ /groups ក្នុង Private Chat ជាមួយ Bot។")
            return
        text, markup = groups_view()
        bot.reply_to(message, text, reply_markup=markup, parse_mode="HTML")

    @bot.message_handler(commands=["leave"])
    def leave_command(message):
        user_id = getattr(getattr(message, "from_user", None), "id", None)
        if not is_manager(config, user_id):
            if getattr(message.chat, "type", None) == "private":
                bot.reply_to(message, "⛔ ពាក្យបញ្ជានេះសម្រាប់ម្ចាស់ Bot ប៉ុណ្ណោះ។")
            return  # ignore silently in groups
        parts = (message.text or "").split()
        chat_type = getattr(message.chat, "type", None)
        if chat_type in GROUP_TYPES and len(parts) == 1:
            try:
                bot.send_message(message.chat.id, "👋 លាហើយ! Bot ចាកចេញពីក្រុមនេះតាមការស្នើរបស់ម្ចាស់។")
            except Exception:
                pass
            leave(message.chat.id)
            return
        if len(parts) != 2:
            bot.reply_to(message, "របៀបប្រើ៖ <code>/leave CHAT_ID</code>\nមើល ID ដោយ /groups", parse_mode="HTML")
            return
        try:
            target = int(parts[1])
        except ValueError:
            bot.reply_to(message, "CHAT_ID ត្រូវតែជាលេខ (ឧ. -1001234567890)។")
            return
        ok, title = leave(target)
        name = html.escape(title or str(target))
        bot.reply_to(message, f"✅ Bot បានចាកចេញពី <b>{name}</b> ហើយ។" if ok
                     else f"❌ មិនអាចចាកចេញពី <b>{name}</b> បានទេ។ សូមពិនិត្យ CHAT_ID។", parse_mode="HTML")

    @bot.callback_query_handler(func=lambda call: (call.data or "").startswith("groups:"))
    def groups_callback(call):
        user_id = getattr(getattr(call, "from_user", None), "id", None)
        if not is_manager(config, user_id):
            bot.answer_callback_query(call.id, "⛔ សម្រាប់ម្ចាស់ Bot ប៉ុណ្ណោះ។", show_alert=True)
            return
        parts = (call.data or "").split(":")
        message = call.message

        def show(text, markup=None):
            try:
                bot.edit_message_text(text, message.chat.id, message.message_id,
                                      reply_markup=markup, parse_mode="HTML")
            except Exception:
                bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")

        if parts[1:2] == ["list"]:
            bot.answer_callback_query(call.id)
            show(*groups_view())
            return
        try:
            chat_id = int(parts[2])
        except (IndexError, ValueError):
            bot.answer_callback_query(call.id)
            return
        title = next((g["title"] for g in active_groups(config) if g["chat_id"] == chat_id), None)
        name = html.escape(title or str(chat_id))
        if parts[1] == "ask":
            markup = InlineKeyboardMarkup()
            markup.row(
                InlineKeyboardButton("✅ បាទ ចាកចេញ", callback_data=f"groups:yes:{chat_id}"),
                InlineKeyboardButton("↩️ ទេ", callback_data="groups:list"),
            )
            bot.answer_callback_query(call.id)
            show(f"🚪 ឲ្យ Bot ចាកចេញពី <b>{name}</b> មែនទេ?", markup)
        elif parts[1] == "yes":
            bot.answer_callback_query(call.id, "កំពុងចាកចេញ...")
            ok, _ = leave(chat_id)
            back = InlineKeyboardMarkup()
            back.row(InlineKeyboardButton("👥 មើលក្រុមទាំងអស់", callback_data="groups:list"))
            show(f"✅ Bot បានចាកចេញពី <b>{name}</b> ហើយ។" if ok
                 else f"❌ មិនអាចចាកចេញពី <b>{name}</b> បានទេ។", back)
        else:
            bot.answer_callback_query(call.id)

    @bot.my_chat_member_handler()
    def bot_membership_changed(update):
        """Track when the bot is added to or removed from a group."""
        status = getattr(getattr(update, "new_chat_member", None), "status", None)
        chat = getattr(update, "chat", None)
        if status in _ACTIVE:
            record_chat(config, chat, status="member")
        elif status in ("left", "kicked"):
            record_chat(config, chat, status="left")

    return SimpleNamespace(leave=leave, groups_view=groups_view)
