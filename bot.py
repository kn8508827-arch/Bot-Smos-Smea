"""
Main Entry Point សម្រាប់ Team ស្មោះស្នេហ៍ Telegram Bot
រួមបញ្ចូល Web App, Gemini AI Assistant, Voice Note Listening, និង SQLite
"""
import os
import sys
import time
import asyncio
import logging
import threading
from functools import wraps
import telebot
from telebot.types import (
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    WebAppInfo, 
    BotCommand
)

import config
import database
import messages
import ai_assistant
import scheduler

logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s - [%(levelname)s] - %(name)s: %(message)s"
)
logger = logging.getLogger("TeamSmosSneaBot")

TOKEN = getattr(config, "BOT_TOKEN", "") or os.getenv("BOT_TOKEN", "")

if not TOKEN:
    logger.critical("រកមិនឃើញ BOT_TOKEN ទេ! សូមពិនិត្យមើល Environment Variables លើ Railway។")
    sys.exit(1)

# បើក threaded=True សម្រាប់ដំណើរការរលូនពេល User ផ្ញើសារច្រើនដំណាលគ្នា
bot = telebot.TeleBot(TOKEN, threaded=True)


def register_interaction(from_user, chat) -> bool:
    """Record only real Telegram updates; private chat type gates DM eligibility."""
    if from_user is None or chat is None or getattr(from_user, "id", None) is None:
        return False
    try:
        database.register_or_update_user(
            user_id=from_user.id,
            chat_id=chat.id,
            username=getattr(from_user, "username", None),
            first_name=getattr(from_user, "first_name", None),
            last_name=getattr(from_user, "last_name", None),
            chat_type=getattr(chat, "type", "unknown"),
        )
        return True
    except Exception as exc:
        logger.warning("Could not register Telegram interaction (%s)", type(exc).__name__)
        return False


def registered_message_handler(*handler_args, **handler_kwargs):
    """Wrap every message handler so real users are recorded before dispatch."""
    def decorate(handler):
        @wraps(handler)
        def wrapped(message, *args, **kwargs):
            register_interaction(
                getattr(message, "from_user", None),
                getattr(message, "chat", None),
            )
            return handler(message, *args, **kwargs)

        return bot.message_handler(*handler_args, **handler_kwargs)(wrapped)

    return decorate


def registered_callback_handler(*handler_args, **handler_kwargs):
    """Wrap callback handlers so callback users are recorded as real interactions."""
    def decorate(handler):
        @wraps(handler)
        def wrapped(call, *args, **kwargs):
            message = getattr(call, "message", None)
            register_interaction(
                getattr(call, "from_user", None),
                getattr(message, "chat", None),
            )
            return handler(call, *args, **kwargs)

        return bot.callback_query_handler(*handler_args, **handler_kwargs)(wrapped)

    return decorate


def _is_dailycare_admin(user_id: int) -> bool:
    return user_id in getattr(config, "ADMIN_IDS", [])


def _notifications_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row_width = 1
    markup.add(
        InlineKeyboardButton("🔔 បើកការជូនដំណឹង", callback_data="notifications:enable"),
        InlineKeyboardButton("🔕 បិទការជូនដំណឹង", callback_data="notifications:disable"),
        InlineKeyboardButton("🕒 មើលម៉ោងផ្ញើ", callback_data="notifications:schedule"),
    )
    return markup


def _dailycare_admin_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row_width = 1
    markup.add(
        InlineKeyboardButton("✅ បើក Daily Care", callback_data="dailycare:enable"),
        InlineKeyboardButton("⏸️ បិទ Daily Care", callback_data="dailycare:disable"),
        InlineKeyboardButton("🧪 ផ្ញើសារសាកល្បងមកខ្ញុំ", callback_data="dailycare:test"),
    )
    return markup


def setup_bot_commands():
    """ចុះឈ្មោះ Menu Button ជាប់ប្រអប់ Chat លើ Telegram UI"""
    try:
        commands = [
            BotCommand("start", "🌟 ចាប់ផ្តើម និងបើក Menu មេ"),
            BotCommand("menu", "📋 មឺនុយសេវាកម្ម"),
            BotCommand("about", "ℹ️ អំពី Bot និងក្រុម"),
            BotCommand("team", "👥 ព័ត៌មាន និងបេសកកម្មក្រុម"),
            BotCommand("events", "📅 កាលវិភាគព្រឹត្តិការណ៍"),
            BotCommand("register", "🚀 បើកទម្រង់ចុះឈ្មោះ (Web App)"),
            BotCommand("help", "📖 សៀវភៅណែនាំរបៀបប្រើប្រាស់"),
            BotCommand("reset", "🧹 សម្អាតប្រវត្តិសន្ទនាជាមួយ AI"),
            BotCommand("notifications", "🔔 កំណត់ការជូនដំណឹង Daily Care"),
            BotCommand("dailycare", "🛠️ គ្រប់គ្រង Daily Care (Admin)"),
        ]
        bot.set_my_commands(commands)
        logger.info("Bot commands menu registered successfully.")
    except Exception as e:
        logger.warning(f"Could not register bot commands menu: {e}")


def keep_typing(chat_id: int, stop_event: threading.Event):
    """បញ្ជូនសញ្ញា typing រៀងរាល់ ៤ វិនាទីម្តង រហូតដល់ AI រៀបចំចម្លើយរួចរាល់"""
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, 'typing')
        except Exception:
            pass
        time.sleep(4)


# =========================================================
# Commands Handlers
# =========================================================

@registered_message_handler(commands=['start'])
def send_welcome(message):
    markup = InlineKeyboardMarkup()
    markup.row_width = 2
    markup.add(
        InlineKeyboardButton("📋 មឺនុយ", callback_data="menu"),
        InlineKeyboardButton("ℹ️ អំពីយើង", callback_data="about"),
        InlineKeyboardButton("🚀 បើក Web App", web_app=WebAppInfo(url="https://team-smos-snea.lovable.app"))
    )
    
    welcome_text = (
        "សួស្ដី! សូមស្វាគមន៍មកកាន់ Bot-Smos-Snea 🤖✨\n"
        "ខ្ញុំជាជំនួយការ AI ផ្លូវការរបស់ Team ស្មោះស្នេហ៍។ តើមានអ្វីឱ្យខ្ញុំជួយអ្នកថ្ងៃនេះទេ?"
    )
    bot.reply_to(message, welcome_text, reply_markup=markup)


@registered_message_handler(commands=['menu'])
def send_menu(message):
    bot.reply_to(message, "សូមជ្រើសរើសជម្រើសខាងក្រោម៖\n1. សេវាកម្មរបស់យើង\n2. ទំនាក់ទំនងមកយើងខ្ញុំ\n3. សួរសំណួរ AI ដោយផ្ទាល់")


@registered_message_handler(commands=['about'])
def send_about(message):
    bot.reply_to(message, "នេះគឺជា Bot-Smos-Snea 🤖 ដែលបង្កើតឡើងដើម្បីជួយសម្រួលការងារ និងទំនាក់ទំនងរបស់អ្នក។")


@registered_message_handler(commands=['team'])
def send_team(message):
    try:
        bot.reply_to(message, messages.TEAM_MESSAGE, parse_mode="Markdown")
    except Exception:
        bot.reply_to(message, messages.TEAM_MESSAGE)


@registered_message_handler(commands=['events'])
def send_events(message):
    try:
        bot.reply_to(message, messages.EVENTS_MESSAGE, parse_mode="Markdown")
    except Exception:
        bot.reply_to(message, messages.EVENTS_MESSAGE)


@registered_message_handler(commands=['help'])
def send_help(message):
    help_text = (
        "📖 **បញ្ជីពាក្យបញ្ជាដែលមាន៖**\n\n"
        "• /start - ចាប់ផ្ដើម និងបើក Menu\n"
        "• /menu - បង្ហាញមឺនុយ\n"
        "• /about - អំពី Bot\n"
        "• /team - មើលព័ត៌មានក្រុម Team ស្មោះស្នេហ៍\n"
        "• /events - កាលវិភាគព្រឹត្តិការណ៍\n"
        "• /register - ចុះឈ្មោះ (Mini Web App)\n"
        "• /reset - សម្អាតប្រវត្តិសន្ទនាជាមួយ AI\n\n"
        "• /notifications - បើក ឬបិទសារ Daily Care\n\n"
        "💡 *អ្នកអាចវាយសំណួរ ឬផ្ញើសារជាសំឡេង (Voice Note) មកជជែកជាមួយ AI បានគ្រប់ពេល!*"
    )
    try:
        bot.reply_to(message, help_text, parse_mode="Markdown")
    except Exception:
        bot.reply_to(message, help_text)


@registered_message_handler(commands=['register'])
def send_register(message):
    markup = InlineKeyboardMarkup()
    web_app = WebAppInfo(url="https://team-smos-snea.lovable.app")
    markup.add(InlineKeyboardButton("🚀 បើកទម្រង់ចុះឈ្មោះ (Register)", web_app=web_app))
    bot.reply_to(message, "សូមចុចប៊ូតុងខាងក្រោមដើម្បីបើកមើលទម្រង់ចុះឈ្មោះក្នុង Mini App:", reply_markup=markup)


@registered_message_handler(commands=['reset'])
def send_reset(message):
    try:
        database.clear_chat_history(message.from_user.id)
        bot.reply_to(message, messages.RESET_SUCCESS, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error resetting chat context: {e}")
        bot.reply_to(message, messages.RESET_SUCCESS)


# =========================================================
# Callback Queries Handlers
# =========================================================

@registered_callback_handler(func=lambda call: True)
def callback_query(call):
    try:
        data = call.data or ""
        message = getattr(call, "message", None)
        chat = getattr(message, "chat", None)
        user_id = getattr(getattr(call, "from_user", None), "id", None)

        if data.startswith("notifications:"):
            if chat is None or getattr(chat, "type", None) != "private":
                bot.answer_callback_query(call.id, messages.NOTIFICATIONS_PRIVATE_ONLY, show_alert=True)
                return
            if data == "notifications:enable":
                database.set_notification_enabled(user_id, True)
                bot.answer_callback_query(call.id, "បានបើកការជូនដំណឹងរួចហើយ។")
                bot.send_message(chat.id, messages.NOTIFICATIONS_ENABLED_TEXT)
            elif data == "notifications:disable":
                database.set_notification_enabled(user_id, False)
                bot.answer_callback_query(call.id, "បានបិទការជូនដំណឹងរួចហើយ។")
                bot.send_message(chat.id, messages.NOTIFICATIONS_DISABLED_TEXT)
            elif data == "notifications:schedule":
                bot.answer_callback_query(call.id, messages.DAILYCARE_SCHEDULE_TEXT, show_alert=True)
            return

        if data.startswith("dailycare:"):
            if not _is_dailycare_admin(user_id):
                bot.answer_callback_query(call.id, messages.DAILYCARE_ADMIN_ONLY, show_alert=True)
                return
            if chat is None or getattr(chat, "type", None) != "private":
                bot.answer_callback_query(call.id, messages.DAILYCARE_PRIVATE_ONLY, show_alert=True)
                return
            if data == "dailycare:enable":
                if not scheduler.is_persistent_storage_ready():
                    bot.answer_callback_query(call.id, messages.DAILYCARE_STORAGE_REQUIRED, show_alert=True)
                    return
                database.set_dailycare_enabled(True)
                bot.answer_callback_query(call.id, "បានបើក Daily Care។")
                bot.send_message(chat.id, messages.DAILYCARE_ENABLED_TEXT)
            elif data == "dailycare:disable":
                database.set_dailycare_enabled(False)
                bot.answer_callback_query(call.id, "បានបិទ Daily Care។")
                bot.send_message(chat.id, messages.DAILYCARE_DISABLED_TEXT)
            elif data == "dailycare:test":
                try:
                    test_text = scheduler.generate_care_message("good_morning")
                    bot.send_message(chat.id, test_text)
                    bot.answer_callback_query(call.id, messages.DAILYCARE_TEST_SENT_TEXT)
                except Exception as exc:
                    logger.warning("Daily-care test send failed (%s)", type(exc).__name__)
                    bot.answer_callback_query(call.id, messages.DAILYCARE_TEST_FAILED_TEXT, show_alert=True)
            return

        if data == "menu":
            bot.answer_callback_query(call.id, "កំពុងបើកមឺនុយ...")
            bot.send_message(chat.id, "នេះជាបញ្ជីមឺនុយរបស់អ្នក! អ្នកអាចសាកល្បងចុច /team, /events ឬសួរ AI ដោយសេរី។")
        elif data == "about":
            bot.answer_callback_query(call.id, "ព័ត៌មាន Bot")
            bot.send_message(chat.id, "Bot-Smos-Snea ត្រូវបានបង្កើតឡើងយ៉ាងពិសេសសម្រាប់អ្នក!")
    except Exception as e:
        logger.error("Error handling callback (%s)", type(e).__name__)


@registered_message_handler(commands=['notifications'])
def notifications_command(message):
    if getattr(message.chat, "type", None) != "private":
        bot.reply_to(message, messages.NOTIFICATIONS_PRIVATE_ONLY)
        return

    enabled = database.get_notification_enabled(message.from_user.id)
    state_text = messages.NOTIFICATIONS_ENABLED_TEXT if enabled else messages.NOTIFICATIONS_DISABLED_BY_DEFAULT
    bot.reply_to(
        message,
        f"{state_text}\n\n{messages.DAILYCARE_SCHEDULE_TEXT}",
        reply_markup=_notifications_keyboard(),
    )


@registered_message_handler(commands=['dailycare'])
def dailycare_admin_command(message):
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if not _is_dailycare_admin(user_id):
        bot.reply_to(message, messages.DAILYCARE_ADMIN_ONLY)
        return
    if getattr(message.chat, "type", None) != "private":
        bot.reply_to(message, messages.DAILYCARE_PRIVATE_ONLY)
        return

    from datetime import datetime
    from zoneinfo import ZoneInfo

    local_date = datetime.now(ZoneInfo("Asia/Phnom_Penh")).date().isoformat()
    enabled = database.get_dailycare_enabled()
    eligible_count = len(database.get_eligible_notification_recipients())
    stats = database.get_notification_delivery_stats(local_date)
    last_run = database.get_last_notification_delivery_at() or "មិនទាន់មាន"
    storage_ready = scheduler.is_persistent_storage_ready()
    status_text = "បើក" if enabled else "បិទ"
    text = (
        "🛠️ **Daily Care Admin**\n"
        f"ស្ថានភាពសកល៖ {status_text}\n"
        f"សមាជិកបានជ្រើសរើសទទួល៖ {eligible_count}\n"
        f"ម៉ោងកម្ពុជា៖\n{messages.DAILYCARE_SCHEDULE_TEXT}\n"
        f"កំណត់ត្រាចុងក្រោយ៖ {last_run}\n"
        f"ថ្ងៃនេះ៖ ផ្ញើបាន {stats.get('sent', 0)} · បរាជ័យ {stats.get('failed', 0)} · បិទ/រកមិនឃើញ {stats.get('blocked', 0)}"
    )
    if not storage_ready:
        text += f"\n\n{messages.DAILYCARE_STORAGE_REQUIRED}"
    bot.reply_to(message, text, parse_mode="Markdown", reply_markup=_dailycare_admin_keyboard())


# =========================================================
# Voice Note Handler (AI ស្តាប់សំឡេង Voice)
# =========================================================

@registered_message_handler(content_types=['voice'])
def handle_voice(message):
    user_id = message.from_user.id
    user_name = message.from_user.first_name

    # បើក Typing Action ជាប់រហូតដល់ AI ស្តាប់ និងឆ្លើយចប់
    stop_typing_event = threading.Event()
    typing_thread = threading.Thread(
        target=keep_typing, 
        args=(message.chat.id, stop_typing_event), 
        daemon=True
    )
    typing_thread.start()

    try:
        # ទាញយក file សំឡេងពី Telegram (.ogg)
        file_info = bot.get_file(message.voice.file_id)
        downloaded_voice = bot.download_file(file_info.file_path)

        # បញ្ជូនសំឡេងទៅកាន់ Gemini AI ឱ្យស្តាប់
        reply = asyncio.run(ai_assistant.ask_ai_with_audio(
            user_id=user_id,
            audio_bytes=downloaded_voice,
            mime_type="audio/ogg",
            user_name=user_name
        ))
    except Exception as e:
        logger.error(f"Error handling voice message: {e}")
        reply = messages.AI_GENERIC_ERROR
    finally:
        stop_typing_event.set()

    try:
        bot.reply_to(message, reply, parse_mode="Markdown")
    except Exception:
        bot.reply_to(message, reply)


# =========================================================
# Text Message Handler (AI ឆ្លើយតបអត្ថបទ)
# =========================================================

@registered_message_handler(func=lambda message: True)
def echo_all(message):
    user_id = message.from_user.id
    user_text = message.text
    user_name = message.from_user.first_name

    stop_typing_event = threading.Event()
    typing_thread = threading.Thread(
        target=keep_typing, 
        args=(message.chat.id, stop_typing_event), 
        daemon=True
    )
    typing_thread.start()

    try:
        reply = asyncio.run(ai_assistant.ask_ai(
            user_id=user_id, 
            user_message=user_text, 
            user_name=user_name
        ))
    except Exception as e:
        logger.error(f"Error handling AI message: {e}")
        reply = messages.AI_GENERIC_ERROR
    finally:
        stop_typing_event.set()

    try:
        bot.reply_to(message, reply, parse_mode="Markdown")
    except Exception:
        bot.reply_to(message, reply)


# =========================================================
# Start Polling
# =========================================================

if __name__ == '__main__':
    logger.info("Initializing database...")
    database.init_db()
    database.initialize_dailycare_enabled(config.DAILYCARE_ENABLED)

    logger.info("Configuring Bot Commands Menu...")
    setup_bot_commands()

    dailycare_scheduler = scheduler.start_scheduler(bot)

    logger.info("Bot is starting polling 24/7...")
    try:
        bot.infinity_polling(skip_pending=True, timeout=20, long_polling_timeout=20)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot process interrupted. Shutting down gracefully...")
    finally:
        scheduler.shutdown_scheduler(dailycare_scheduler)
        asyncio.run(ai_assistant.close_shared_http_client())
        logger.info("Cleaned up network connections.")
