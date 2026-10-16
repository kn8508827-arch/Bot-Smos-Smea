"""
Main Entry Point សម្រាប់ Team ស្មោះស្នេហ៍ Telegram Bot
រៀបចំឡើងយ៉ាងប្រណិត ដំណើរការ Smooth និងមានស្ថេរភាពខ្ពស់លើ Railway
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

TOKEN = getattr(config, "BOT_TOKEN", "")

if not TOKEN:
    logger.critical("រកមិនឃើញ BOT_TOKEN ទេ! សូមពិនិត្យមើល Environment Variables លើ Railway។")
    sys.exit(1)

# កំណត់ threaded=True សម្រាប់ pyTelegramBotAPI ដើម្បីឆ្លើយតបសំណើច្រើនក្នុងពេលតែមួយ
bot = telebot.TeleBot(TOKEN, threaded=True, parse_mode=None)


def setup_bot_commands():
    """កំណត់ Menu Button លើ Telegram UI ឱ្យបង្ហាញជានិច្ច"""
    try:
        commands = [
            BotCommand("start", "🌟 ចាប់ផ្តើម និងបើក Menu ស្វាគមន៍"),
            BotCommand("team", "👥 មើលព័ត៌មាន និងបេសកកម្មក្រុម"),
            BotCommand("events", "📅 កាលវិភាគព្រឹត្តិការណ៍"),
            BotCommand("help", "📖 សៀវភៅណែនាំរបៀបប្រើប្រាស់"),
            BotCommand("reset", "🧹 សម្អាតប្រវត្តិសន្ទនាជាមួយ AI"),
        ]
        bot.set_my_commands(commands)
        logger.info("Bot commands menu registered successfully.")
    except Exception as e:
        logger.warning(f"Could not register bot commands menu: {e}")


def keep_typing(chat_id: int, stop_event: threading.Event):
    """បញ្ជូនសញ្ញា typing រៀងរាល់ ៤ វិនាទីម្តង រហូតដល់ AI គិតចប់"""
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, 'typing')
        except Exception:
            pass
        time.sleep(4)


# =========================================================
# Handlers សម្រាប់ Commands
# =========================================================

@bot.message_handler(commands=['start'])
def handle_start(message):
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

    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("📋 មឺនុយសេវាកម្ម", callback_data="menu"),
        InlineKeyboardButton("ℹ️ អំពីយើង", callback_data="about"),
        InlineKeyboardButton("🚀 បើក Web App", web_app=WebAppInfo(url="https://team-smos-snea.lovable.app"))
    )
    
    try:
        bot.reply_to(message, messages.START_MESSAGE, reply_markup=markup, parse_mode="Markdown")
    except Exception:
        bot.reply_to(message, messages.START_MESSAGE, reply_markup=markup)


@bot.message_handler(commands=['team'])
def handle_team(message):
    try:
        bot.reply_to(message, messages.TEAM_MESSAGE, parse_mode="Markdown")
    except Exception:
        bot.reply_to(message, messages.TEAM_MESSAGE)


@bot.message_handler(commands=['events'])
def handle_events(message):
    try:
        bot.reply_to(message, messages.EVENTS_MESSAGE, parse_mode="Markdown")
    except Exception:
        bot.reply_to(message, messages.EVENTS_MESSAGE)


@bot.message_handler(commands=['help'])
def handle_help(message):
    try:
        bot.reply_to(message, messages.HELP_MESSAGE, parse_mode="Markdown")
    except Exception:
        bot.reply_to(message, messages.HELP_MESSAGE)


@bot.message_handler(commands=['reset'])
def handle_reset(message):
    try:
        database.clear_chat_history(message.from_user.id)
        bot.reply_to(message, messages.RESET_SUCCESS, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error resetting chat context: {e}")
        bot.reply_to(message, messages.RESET_SUCCESS)


# =========================================================
# Callback Queries (Inline Buttons)
# =========================================================

@bot.callback_query_handler(func=lambda call: True)
def handle_callback_query(call):
    try:
        if call.data == "menu":
            bot.answer_callback_query(call.id, "កំពុងបង្ហាញមឺនុយ...")
            menu_text = (
                "⚜️ **មឺនុយពាក្យបញ្ជារហ័ស៖**\n\n"
                "• /team - ព័ត៌មានក្រុម Team ស្មោះស្នេហ៍\n"
                "• /events - កាលវិភាគព្រឹត្តិការណ៍\n"
                "• /help - សៀវភៅណែនាំ Bot\n"
                "• /reset - ចាប់ផ្តើមប្រធានបទថ្មីជាមួយ AI\n\n"
                "💬 ឬអ្នកអាចវាយសំណួរជជែកជាមួយ AI បានគ្រប់ពេលវេលា!"
            )
            bot.send_message(call.message.chat.id, menu_text, parse_mode="Markdown")
        elif call.data == "about":
            bot.answer_callback_query(call.id, "ព័ត៌មាន Bot")
            bot.send_message(call.message.chat.id, messages.TEAM_MESSAGE, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error handling callback {call.data}: {e}")


# =========================================================
# Handle Voice Notes (ណែនាំសមាជិកពេលផ្ញើសំឡេង)
# =========================================================

@bot.message_handler(content_types=['voice', 'audio'])
def handle_voice(message):
    reply = (
        "🎙️ **ខ្ញុំបានទទួលសារជាសំឡេងរបស់អ្នកហើយ!**\n\n"
        "ដើម្បីទទួលបានចម្លើយច្បាស់លាស់ និងលម្អិតបំផុតពី Cooler Bot "
        "សូមមេត្តាវាយជាអត្ថបទ (Text Message) មកកាន់ខ្ញុំវិញណា
