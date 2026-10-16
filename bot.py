import os
import asyncio
import logging
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

# Import ម៉ូឌុល AI ដែលយើងបានរៀបចំ
import ai_assistant

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ទាញយក Token ពី Environment Variables
TOKEN = os.getenv('BOT_TOKEN')

if not TOKEN:
    raise ValueError("រកមិនឃើញ BOT_TOKEN ទេ! សូមពិនិត្យមើលការកំណត់ Environment Variables នៅក្នុង Railway/Render។")

bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = InlineKeyboardMarkup()
    markup.row_width = 2
    markup.add(
        InlineKeyboardButton("📋 មឺនុយ", callback_data="menu"),
        InlineKeyboardButton("ℹ️ អំពីយើង", callback_data="about"),
        InlineKeyboardButton("🚀 បើក Web App", web_app=WebAppInfo(url="https://team-smos-snea.lovable.app"))
    )
    bot.reply_to(message, "សួស្ដី! សូមស្វាគមន៍មកកាន់ Bot-Smos-Snea ។ តើមានអ្វីឱ្យខ្ញុំជួយទេ?", reply_markup=markup)

@bot.message_handler(commands=['menu'])
def send_menu(message):
    bot.reply_to(message, "សូមជ្រើសរើសជម្រើសខាងក្រោម៖\n1. សេវាកម្មរបស់យើង\n2. ទំនាក់ទំនងមកយើងខ្ញុំ")

@bot.message_handler(commands=['about'])
def send_about(message):
    bot.reply_to(message, "នេះគឺជា Bot-Smos-Snea 🤖 ដែលបង្កើតឡើងដើម្បីជួយសម្រួលការងារ និងទំនាក់ទំនងរបស់អ្នក។")

@bot.message_handler(commands=['help'])
def send_help(message):
    bot.reply_to(message, "បញ្ជីពាក្យបញ្ជាដែលមាន:\n/start - ចាប់ផ្ដើម\n/menu - មឺនុយ\n/about - អំពី Bot\n/register - ចុះឈ្មោះ (Web App)")

@bot.message_handler(commands=['register'])
def send_register(message):
    markup = InlineKeyboardMarkup()
    web_app = WebAppInfo(url="https://team-smos-snea.lovable.app")
    markup.add(InlineKeyboardButton("🚀 បើកទម្រង់ចុះឈ្មោះ (Register)", web_app=web_app))
    bot.reply_to(message, "សូមចុចប៊ូតុងខាងក្រោមដើម្បីបើកមើលទម្រង់ចុះឈ្មោះក្នុង Mini App:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    if call.data == "menu":
        bot.answer_callback_query(call.id, "កំពុងបើកមឺនុយ...")
        bot.send_message(call.message.chat.id, "នេះជាបញ្ជីមឺនុយរបស់អ្នក!")
    elif call.data == "about":
        bot.answer_callback_query(call.id, "ព័ត៌មាន Bot")
        bot.send_message(call.message.chat.id, "Bot-Smos-Snea ត្រូវបានបង្កើតឡើងយ៉ាងពិសេសសម្រាប់អ្នក!")

# ផ្នែកឆ្លើយតបសំណួរស្វ័យប្រវត្តិតាមរយៈ AI (Gemini)
@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    user_id = message.from_user.id
    user_text = message.text
    user_name = message.from_user.first_name

    bot.send_chat_action(message.chat.id, 'typing')
    
    try:
        # ហៅដំណើរការ ask_ai ពី ai_assistant.py
        reply = asyncio.run(ai_assistant.ask_ai(user_id=user_id, user_message=user_text, user_name=user_name))
    except Exception as e:
        logger.error(f"Error handling AI message: {e}")
        reply = "សុំទោស មានបញ្ហាក្នុងការភ្ជាប់ទៅកាន់ប្រព័ន្ធ AI។"

    bot.reply_to(message, reply)

if __name__ == '__main__':
    logger.info("Bot is starting...")
    bot.infinity_polling(skip_pending=True)
