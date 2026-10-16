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

@bot.message_handler(commands=['start'])
def send_welcome(message):
    try:
        database.register_or_update_user(
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name
        )
    except Exception as e:
        logger.error(f"Error registering user on /start: {e}")

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


@bot.message_handler(commands=['menu'])
def send_menu(message):
    bot.reply_to(message, "សូមជ្រើសរើសជម្រើសខាងក្រោម៖\n1. សេវាកម្មរបស់យើង\n2. ទំនាក់ទំនងមកយើងខ្ញុំ\n3. សួរសំណួរ AI ដោយផ្ទាល់")


@bot.message_handler(commands=['about'])
def send_about(message):
    bot.reply_to(message, "នេះគឺជា Bot-Smos-Snea 🤖 ដែលបង្កើតឡើងដើម្បីជួយសម្រួលការងារ និងទំនាក់ទំនងរបស់អ្នក។")


@bot.message_handler(commands=['team'])
def send_team(message):
    try:
        bot.reply_to(message, messages.TEAM_MESSAGE, parse_mode="Markdown")
    except Exception:
        bot.reply_to(message, messages.TEAM_MESSAGE)


@bot.message_handler(commands=['events'])
def send_events(message):
    try:
        bot.reply_to(message, messages.EVENTS_MESSAGE, parse_mode="Markdown")
    except Exception:
        bot.reply_to(message, messages.EVENTS_MESSAGE)


@bot.message_handler(commands=['help'])
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
        "💡 *អ្នកអាចវាយសំណួរ ឬផ្ញើសារជាសំឡេង (Voice Note) មកជជែកជាមួយ AI បានគ្រប់ពេល!*"
    )
    try:
        bot.reply_to(message, help_text, parse_mode="Markdown")
    except Exception:
        bot.reply_to(message, help_text)


@bot.message_handler(commands=['register'])
def send_register(message):
    markup = InlineKeyboardMarkup()
    web_app = WebAppInfo(url="https://team-smos-snea.lovable.app")
    markup.add(InlineKeyboardButton("🚀 បើកទម្រង់ចុះឈ្មោះ (Register)", web_app=web_app))
    bot.reply_to(message, "សូមចុចប៊ូតុងខាងក្រោមដើម្បីបើកមើលទម្រង់ចុះឈ្មោះក្នុង Mini App:", reply_markup=markup)


@bot.message_handler(commands=['reset'])
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

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    try:
        if call.data == "menu":
            bot.answer_callback_query(call.id, "កំពុងបើកមឺនុយ...")
            bot.send_message(call.message.chat.id, "នេះជាបញ្ជីមឺនុយរបស់អ្នក! អ្នកអាចសាកល្បងចុច /team, /events ឬសួរ AI ដោយសេរី។")
        elif call.data == "about":
            bot.answer_callback_query(call.id, "ព័ត៌មាន Bot")
            bot.send_message(call.message.chat.id, "Bot-Smos-Snea ត្រូវបានបង្កើតឡើងយ៉ាងពិសេសសម្រាប់អ្នក!")
    except Exception as e:
        logger.error(f"Error handling callback {call.data}: {e}")


# =========================================================
# Voice Note Handler (AI ស្តាប់សំឡេង Voice)
# =========================================================

@bot.message_handler(content_types=['voice'])
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
            user_name=user_name,
            last_name=message.from_user.last_name,
            username=message.from_user.username
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

@bot.message_handler(func=lambda message: True)
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
            user_name=user_name,
            last_name=message.from_user.last_name,
            username=message.from_user.username
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

    logger.info("Configuring Bot Commands Menu...")
    setup_bot_commands()

    logger.info("Bot is starting polling 24/7...")
    try:
        bot.infinity_polling(skip_pending=True, timeout=20, long_polling_timeout=20)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot process interrupted. Shutting down gracefully...")
    finally:
        asyncio.run(ai_assistant.close_shared_http_client())
        logger.info("Cleaned up network connections.")
