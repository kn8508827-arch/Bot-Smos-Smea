"""
Main Entry Point សម្រាប់ Team ស្មោះស្នេហ៍ Telegram Bot
រួមបញ្ចូល Web App, Gemini AI Assistant, Voice Note Listening, និង SQLite
"""
import os
import sys
import khmer_shaping  # noqa: F401  (load fribidi before any PIL.ImageFont import)
import time
import asyncio
import logging
import threading
import secrets
from functools import wraps
import telebot
from telebot.types import (
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    ReplyKeyboardMarkup,
    KeyboardButton,
    WebAppInfo, 
    BotCommand
)

import config
import database
import messages
import ai_assistant
import async_runner
import scheduler
import location_service
import weather_service
import photo_studio
import style_studio
import community_features
import fan_features
import group_manager
import health_features
import health_more
import trip_places
import prompt_studio
import social_links
import market_info
import family_tips
import subscription
import voice_studio
import record_studio
import music_studio
import khqr_pay
import calendar_brief
import group_guard
import ai_extras
import daily_tools
import hub
import friend_map
import buddhist_days
import rewards
import knowledge_base
import ops_monitor
import backup_tools
import fortune
import game_center

logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s - [%(levelname)s] - %(name)s: %(message)s"
)
logger = logging.getLogger("TeamSmosSneaBot")

_pending_location_modes = {}
_location_result_pages = {}
_studio_waiting = {}   # user_id -> expires_at: waiting for a photo after /studio
_studio_photos = {}    # user_id -> {"file_id": str, "expires_at": float}
_studio_usage = {}     # (user_id, "YYYY-MM-DD") -> number of edits today
_studio_busy = set()   # user_ids with an edit in progress
_STUDIO_TTL_SECONDS = 600
_LOCATION_STATE_TTL = 900
_LOCATION_PAGE_SIZE = 5

TOKEN = getattr(config, "BOT_TOKEN", "") or os.getenv("BOT_TOKEN", "")

if not TOKEN:
    logger.critical("រកមិនឃើញ BOT_TOKEN ទេ! សូមពិនិត្យមើល Environment Variables លើ Railway។")
    sys.exit(1)

# បើក threaded=True សម្រាប់ដំណើរការរលូនពេល User ផ្ញើសារច្រើនដំណាលគ្នា
# Workers: AI answers take seconds, so 2 default threads made everyone wait in line.
BOT_THREADS = max(2, int(os.getenv("BOT_THREADS", "8") or 8))
_exception_handler = ops_monitor.AlertingExceptionHandler(
    config, lambda chat_id, text: bot.send_message(chat_id, text, parse_mode="HTML"))
bot = telebot.TeleBot(TOKEN, threaded=True, num_threads=BOT_THREADS, exception_handler=_exception_handler)

# Paid membership gate (1$/user, 5$/group per 30 days). MUST be registered first so it runs
# before every other handler. SUBSCRIPTION_ENABLED=false turns it off.
_subscription = subscription.register(bot, config)
# 🧭 /imagine /song /translate … typed on their own → clear guide + example buttons, next message is used
import guided
guided.register(bot, config)
# 🕘 Staff attendance (/work · /staff · /setwork): GPS + selfie check-in/out, owner notified at once.
# Registered right after the paywall so its location/selfie steps win over the other photo/location handlers.
import attendance
_attendance = attendance.register(bot, config)
# 🎙️ /live — walkie-talkie interpreter (voice → translated voice); before the generic voice / AI chat handlers
import live_translate
live_translate.register(bot, config)
# 🛡️ Cooler Guard: anti-spam / captcha / warn-ban for paid groups (registered right after the paywall)
def _group_paid(chat_id):
    st = subscription.settings()
    if not st.enabled or subscription.is_active(config, "g", chat_id):
        return True
    deadline = subscription.group_deadline(config, chat_id)
    return deadline is not None and deadline > time.time()


group_guard.register(bot, config, group_allowed=_group_paid)
# 🎨 /imagine · 📝 /summary · 🧠 /memory (+ group message buffer for /summary)
_extras = ai_extras.register(bot, config)
# 🚨 /scam · 🎤 /totext · 🌐 /translate · 📚 /homework · ⏰ /remind · 💵 /cash /report
_daily = daily_tools.register(bot, config)
# 🚀 Cooler Hub mini app: /hub, menu button, and /start go_<feature> from the hub tiles
_hub = hub.register(bot, config)
# 📍 Friend Map: opt-in, time-limited location sharing between members of the same group
_friend_map = friend_map.register(bot, config)
# 🙏 /sila reminders · 🕯️ /anniv personal anniversaries (solar or Khmer lunar)
_buddhist = buddhist_days.register(bot, config)
# 🎁 /redeem: points → VIP days
rewards.register(bot, config)
# Owner: /backup and a daily database copy to OWNER_ID (members and payments).
_start_daily_backup = backup_tools.register(bot, config)

# Register community commands before the catch-all AI handler so commands are not sent to AI.
community_features.register(bot, database, config)
# Owner tools: /groups and /leave (make the bot leave a group).
group_manager.register(bot, config)
# Health & exercise for older people: /health /exercise /balance /warning /sos
health_features.register(bot, config)
# Travel ideas around Cambodia: /trip
trip_places.register(bot, config)
# Vid Cooler's TikTok / YouTube / Facebook / Telegram: /follow
_follow_markup = social_links.register(bot, config)
# Dollar/riel rate, gold price, news: /market /rate /gold /news
market_info.register(bot, config)
# 📰 TPNews live: new posts on the TPNews channel → subscribers (/tpnews) + the Hub
import tpnews_feed
_tpnews = tpnews_feed.register(bot, config)
# 💬 Cooler Bot in Facebook Messenger (webhook server on $PORT) · /msgconnect (owner)
import messenger_bot
_messenger = messenger_bot.register(bot, config, observe=getattr(_extras, "observe", None))


def _fan_ask_gemini(prompt):
    return async_runner.run_sync(ai_assistant.generate_gemini_response(
        api_key=getattr(config, "GEMINI_API_KEY", ""), prompt=prompt, history=[],
        user_name=None, user_id=None, db_path=database.DB_PATH,
    ), timeout=90)


PROMPT_MODEL_CHAIN = ("gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.1-pro-preview")


def _prompt_models():
    """PROMPT_AI_MODEL first, then current Gemini models, then AI_MODEL — without duplicates."""
    chain = [os.getenv("PROMPT_AI_MODEL", "").strip().strip('"').strip("'"), *PROMPT_MODEL_CHAIN,
             getattr(config, "AI_MODEL", "")]
    seen, models = set(), []
    for model in chain:
        if model and model not in seen:
            seen.add(model)
            models.append(model)
    return models


def _prompt_ask(system, prompt):
    # Long, structured answers for Prompt Studio; tries the next model when one is retired or failing.
    errors = []
    for model in _prompt_models():
        try:
            return async_runner.run_sync(ai_assistant.generate_gemini_response(
                api_key=getattr(config, "GEMINI_API_KEY", ""), prompt=prompt, history=[],
                user_name=None, user_id=None, db_path=database.DB_PATH, model_name=model,
                system_instruction=system, max_output_tokens=8192, temperature=0.9, raw=True,
                request_timeout=110,
            ), timeout=150)
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", "")
            errors.append(f"{model}: {type(exc).__name__} {status}".strip())
            logger.warning("Prompt Studio model %s failed (%s %s)", model, type(exc).__name__, status)
            if isinstance(exc, PermissionError) and "Invalid Key" in str(exc) and len(errors) > 2:
                break
    raise RuntimeError("; ".join(errors)[:300])


# Prompt Studio: /prompt /videoprompt /songprompt /imageprompt /story
_prompt_studio = prompt_studio.register(bot, config, _prompt_ask)
# Fan features: /games /music /rap /movie (registered before the catch-all AI handler)
_fan = fan_features.register(bot, config, _fan_ask_gemini)
# More health tools: /bp /water /sleep /askhealth
health_more.register(bot, config, _fan_ask_gemini)
# Parenting (kids 1-6) and dog/cat training tips: /parent /pet /askfamily
family_tips.register(bot, config, _fan_ask_gemini)


def _fortune_vision(prompt, image_bytes):
    return async_runner.run_sync(ai_assistant.generate_gemini_image_response(
        api_key=getattr(config, "GEMINI_API_KEY", ""), prompt=prompt, image_bytes=image_bytes,
        mime_type="image/jpeg", history=[], db_path=database.DB_PATH,
    ), timeout=90)


# Fortune telling for fun: /fortune (12 Khmer years), /number, /palm (photo), /dream
fortune.register(bot, config, _fan_ask_gemini, _fortune_vision)
# Instant Telegram dice games with points and a daily Top: /play /duel /top
game_center.register(bot, config)
# Cooler Voice: /say /voice and the 🔊 button under voice answers
_voice = voice_studio.register(bot, config)
# 🎙️ /record: clean a voice note, add effects, or re-voice it with an AI voice (members 3×30 s a day)
_record = record_studio.register(bot, config)
# Cooler Music: /song /beat — Lyria sings & raps original Khmer lyrics over a beat
_music = music_studio.register(bot, config)
_fan.hooks["rap_markup"] = _music.rap_markup
# ⚡ Automatic KHQR payments via Bakong (only when BAKONG_API_TOKEN + BAKONG_ACCOUNT_ID are set)
_khqr = khqr_pay.register(bot, config)
_subscription.hooks["khqr"] = lambda chat_id, uid, kind, target_id, amount, title: (
    khqr_pay.enabled() and bool(_khqr.new_bill(chat_id, uid, kind, target_id, amount, "USD", title=title)))
# 📅 Khmer calendar /calendar, /goodday and ☀️ /morning brief
_calendar = calendar_brief.register(bot, config, ask=_fan_ask_gemini)
# Cooler Knowledge: /learn /ask /kb /autoanswer — AI answers from a chat's own documents
knowledge_base.register(bot, config)
# Owner health check: /status (uptime, errors, 🩺 test AI)
ops_monitor.register(bot, config, workers=BOT_THREADS, ai_ping=lambda: knowledge_base.gemini(
    getattr(config, "GEMINI_API_KEY", ""), [{"text": "Reply with one word: OK"}], max_tokens=1024))
ops_monitor.register_selftest(bot, config)


def register_interaction(from_user, chat) -> bool:
    """Record only real Telegram updates; private chat type gates DM eligibility."""
    if from_user is None or chat is None or getattr(from_user, "id", None) is None:
        return False
    group_manager.record_chat(config, chat)
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


_COMMAND_OVERRIDE_LANGUAGES = ("en", "km")


def _clear_command_overrides():
    """Remove command lists set for narrower scopes or languages (by older bot
    versions or BotFather). Telegram shows those INSTEAD of the default list,
    which is why "/" showed only 5 old commands. Afterwards every chat falls
    back to the one default list set below."""
    from telebot import types as tg_types

    scopes = [
        None,
        tg_types.BotCommandScopeAllPrivateChats(),
        tg_types.BotCommandScopeAllGroupChats(),
        tg_types.BotCommandScopeAllChatAdministrators(),
    ]
    owner_id = getattr(config, "OWNER_ID", 0)
    for chat_id in {owner_id, *getattr(config, "ADMIN_IDS", [])}:
        if chat_id:
            scopes.append(tg_types.BotCommandScopeChat(chat_id))
    for scope in scopes:
        for language_code in (None, *_COMMAND_OVERRIDE_LANGUAGES):
            if scope is None and language_code is None:
                continue  # the default list itself is replaced by set_my_commands
            try:
                bot.delete_my_commands(scope=scope, language_code=language_code)
            except Exception as exc:
                logger.debug("delete_my_commands skipped (%s)", type(exc).__name__)


OWNER_COMMANDS = (
    ("status", "🩺 ស្ថានភាព Bot (Owner)"),
    ("selftest", "🧪 សាកគ្រប់សេវា ✅/❌ (Owner)"),
    ("stats", "📊 ចំណូល និងសមាជិក (Owner)"),
    ("subgroups", "👥 Group បង់/មិនបង់ · ចេញ (Owner)"),
    ("subs", "💳 បញ្ជីសមាជិកសកម្ម (Owner)"),
    ("grant", "🎁 ផ្ដល់សមាជិក u|g ID ថ្ងៃ (Owner)"),
    ("revoke", "🗑️ ដកសមាជិក (Owner)"),
    ("groups", "📋 Group ទាំងអស់ (Owner)"),
    ("leave", "🚪 ឲ្យ Bot ចេញពី Group (Owner)"),
    ("backup", "💾 Backup Database (Owner)"),
    ("khqrtest", "🧪 សាក KHQR ស្វ័យប្រវត្តិ (Owner)"),
    ("communityadmin", "🌟 គ្រប់គ្រងសហគមន៍ (Owner)"),
    ("setcode", "🔑 កំណត់លេខកូដ (Owner)"),
    ("dailycare", "🛠️ Daily Care (Admin)"),
)
GROUP_ADMIN_COMMANDS = (
    ("guard", "🛡️ ការពារ Group (Admin)"),
    ("warn", "⚠️ Warn (Reply លើសារ)"),
    ("unwarn", "✅ លុប Warn"),
    ("mute", "🔇 បិទមាត់ (នាទី)"),
    ("unmute", "🔊 បើកមាត់វិញ"),
    ("ban", "🚫 Ban (Reply លើសារ)"),
    ("kick", "👢 ដកចេញ (Reply លើសារ)"),
    ("unban", "♻️ Unban USER_ID"),
    ("setwelcome", "👋 កំណត់សារស្វាគមន៍"),
    ("badword", "🤬 ពាក្យហាមឃាត់"),
    ("allowlink", "🔗 អនុញ្ញាត Link"),
)


MENU_COMMANDS = (
    "start", "hub", "icom", "menu", "pay", "nearby", "prompt", "studio", "imagine", "song", "say", "calendar", "morning", "rap", "play", "games", "fortune", "quiz", "card", "checkin",
    "health", "sos", "parent", "pet", "rate", "gold", "news", "weather", "trip", "follow", "contact", "help",
)


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
            BotCommand("fuel", "⛽ ស្វែងរកកន្លែងចាក់សាំង"),
            BotCommand("ev", "⚡ ស្វែងរកស្ថានីយសាក EV"),
            BotCommand("weather", "🌤️ ព្យាករណ៍អាកាសធាតុតាមខេត្ត"),
            BotCommand("studio", "🎨 កែរូបដោយ AI (Photo Studio)"),
            BotCommand("style", "💇 សាកម៉ូដសក់ & ស្ទាយថតរូប"),
            BotCommand("money", "💵 ពិនិត្យក្រដាសប្រាក់"),
            BotCommand("record", "🎙️ ថតសំឡេង សម្អាត Effect & AI Voice"),
            BotCommand("community", "🌟 មឺនុយសហគមន៍"),
            BotCommand("profile", "⭐ ប្រវត្តិរូប និងពិន្ទុ"),
            BotCommand("checkin", "🎁 Check-in ប្រចាំថ្ងៃ"),
            BotCommand("quiz", "🧠 Quiz ប្រចាំថ្ងៃ"),
            BotCommand("leaderboard", "🏆 ចំណាត់ថ្នាក់សមាជិក"),
            BotCommand("invite", "🤝 អញ្ជើញមិត្តភក្តិ"),
            BotCommand("vip", "👑 ស្ថានភាព VIP"),
            BotCommand("games", "🎮 ហ្គេមរបស់ Vid Cooler"),
            BotCommand("music", "🎵 បទរបស់ Vid Cooler"),
            BotCommand("rap", "🎤 AI សរសេរ Rap ជាមួយ Cooler"),
            BotCommand("movie", "🎬 ណែនាំរឿង + កន្លែងមើលផ្លូវការ"),
            BotCommand("myname", "🗣️ AI បង្កើតឈ្មោះ Rapper ឲ្យអ្នក"),
            BotCommand("card", "🃏 Fan Card របស់អ្នក"),
            BotCommand("code", "🎟️ វាយកូដសម្ងាត់ពី MV"),
            BotCommand("pay", "💳 បង់ 1$ ប្រើគ្រប់មុខងារ 30 ថ្ងៃ"),
            BotCommand("myplan", "📅 ស្ថានភាពសមាជិករបស់ខ្ញុំ"),
            BotCommand("play", "🎮 Game Center លេងភ្លាម 🎲🎯🏀⚽🎳🎰"),
            BotCommand("duel", "⚔️ ប្រកួតឡុកឡាក់ជាមួយមិត្ត (Group)"),
            BotCommand("gametop", "🏆 Top Game ថ្ងៃនេះ"),
            BotCommand("fortune", "🔮 ទស្សន៍ទាយ ១២ ឆ្នាំ បាតដៃ សុបិន"),
            BotCommand("number", "🔢 លេខជីវិតតាមថ្ងៃខែឆ្នាំកំណើត"),
            BotCommand("palm", "🖐️ ទាយបាតដៃ (ផ្ញើរូប)"),
            BotCommand("dream", "💭 ទាយសុបិន"),
            BotCommand("parent", "👨‍👩‍👧 មើលកូន ១-៦ ឆ្នាំឲ្យស្ដាប់បង្គាប់"),
            BotCommand("pet", "🐶🐱 បង្ហាត់ឆ្កែ ឆ្មា"),
            BotCommand("rate", "💵 អត្រាប្ដូរប្រាក់ ដុល្លារ ↔ រៀល"),
            BotCommand("gold", "🪙 តម្លៃមាសថ្ងៃនេះ"),
            BotCommand("news", "📰 ព័ត៌មានថ្មីៗ"),
            BotCommand("follow", "📲 តាមដាន Vid Cooler TikTok YouTube Facebook"),
            BotCommand("contact", "💬 ទាក់ទង Vid Cooler"),
            BotCommand("prompt", "✍️ Prompt Studio កម្រិតខ្ពស់"),
            BotCommand("song", "🎧 Bot ច្រៀង & Rap មានភ្លេង Beat"),
            BotCommand("beat", "🥁 បង្កើត Beat ៣០ វិនាទី"),
            BotCommand("hub", "🚀 Cooler Hub — គ្រប់មុខងារ"),
            BotCommand("icom", "🎙️ ICOM — ចូលនិយាយជាមួយក្រុមភ្លាម"),
            BotCommand("nearby", "📍 មិត្តនៅឯណា (ចែករំលែកទីតាំងក្នុង Group)"),
            BotCommand("stopshare", "⛔ ឈប់ចែករំលែកទីតាំង"),
            BotCommand("imagine", "🎨 គូររូបពីអក្សរ"),
            BotCommand("scam", "🚨 ឆែកសារ/Link បោកប្រាស់"),
            BotCommand("totext", "🎤 Voice → អក្សរខ្មែរ"),
            BotCommand("translate", "🌐 បកប្រែរូប/អក្សរ"),
            BotCommand("homework", "📚 ពន្យល់លំហាត់"),
            BotCommand("remind", "⏰ រំលឹកខ្ញុំ"),
            BotCommand("cash", "💵 កត់ចំណាយ/ចំណូល"),
            BotCommand("work", "🕘 ចូល/ចេញការងារ (បុគ្គលិក)"),
            BotCommand("live", "🎙️ បកប្រែ Live ជាសំឡេង"),
            BotCommand("report", "📊 របាយការណ៍លុយខែនេះ"),
            BotCommand("calendar", "📅 ប្រតិទិនខ្មែរ ថ្ងៃសីល ថ្ងៃឈប់"),
            BotCommand("sila", "🙏 រំលឹកថ្ងៃសីល"),
            BotCommand("anniv", "🕯️ ខួប & បុណ្យផ្ទាល់ខ្លួន"),
            BotCommand("redeem", "🎁 ប្ដូរពិន្ទុ → ថ្ងៃ VIP"),
            BotCommand("morning", "☀️ ព័ត៌មានពេលព្រឹក ម៉ោង ៧"),
            BotCommand("guard", "🛡️ ការពារ Group (Admin)"),
            BotCommand("summary", "📝 សង្ខេបសារក្នុង Group"),
            BotCommand("memory", "🧠 អ្វីដែល Bot ចាំពីអ្នក"),
            BotCommand("say", "🎙️ Bot អានអត្ថបទជាសំឡេងខ្មែរ"),
            BotCommand("kb", "📚 AI ឆ្លើយតាមឯកសាររបស់អ្នក"),
            BotCommand("ask", "❓ សួរពីឯកសារដែលបានបញ្ចូល"),
            BotCommand("learn", "➕ បញ្ចូលឯកសារ/ព័ត៌មានឲ្យ AI"),
            BotCommand("voice", "🗣️ ស្ដាប់សំឡេង Cooler Bot"),
            BotCommand("videoprompt", "🎬 Prompt វីដេអូ AI"),
            BotCommand("songprompt", "🎵 Prompt ចម្រៀង Suno"),
            BotCommand("imageprompt", "🖼️ Prompt រូបភាព"),
            BotCommand("story", "🎞️ រៀបចំរឿង Scene និងភាគ"),
            BotCommand("trip", "🧭 កន្លែងដើរលេងល្អៗនៅកម្ពុជា"),
            BotCommand("health", "🧓 សុខភាព និងលំហាត់ប្រាណ"),
            BotCommand("exercise", "🏃 លំហាត់ ១០ នាទីថ្ងៃនេះ"),
            BotCommand("balance", "⚖️ លំហាត់ទប់លំនឹង ការពារការដួល"),
            BotCommand("warning", "🚨 សញ្ញាគ្រោះថ្នាក់ត្រូវទៅពេទ្យ"),
            BotCommand("sos", "🆘 លេខទូរស័ព្ទបន្ទាន់"),
            BotCommand("bp", "🩸 កត់ត្រាសម្ពាធឈាម"),
            BotCommand("water", "💧 ផឹកទឹកឲ្យគ្រប់ពេលក្ដៅ"),
            BotCommand("sleep", "😴 គន្លឹះគេងលក់ស្រួល"),
            BotCommand("askhealth", "🤖 សួរ AI អំពីសុខភាព"),
            BotCommand("reminders", "📋 មើល/លុបការរំលឹក"),
            BotCommand("sale", "➕ កត់ចំណូល (លក់បាន)"),
            BotCommand("undo", "↩️ លុបកំណត់ត្រាលុយចុងក្រោយ"),
            BotCommand("goodday", "✨ ណែនាំថ្ងៃល្អ"),
            BotCommand("autoanswer", "🤖 Group ឆ្លើយស្វ័យប្រវត្តិ (Admin)"),
            BotCommand("forget", "🗑️ លុបអ្វីដែល Bot ចាំ"),
            BotCommand("kbclear", "🗑️ លុបឯកសារ AI ទាំងអស់"),
            BotCommand("dice", "🎲 គប់គ្រាប់ឡុកឡាក់"),
            BotCommand("dart", "🎯 បោះព្រួញ"),
            BotCommand("basket", "🏀 បោះបាល់បោះ"),
            BotCommand("football", "⚽ ទាត់បាល់"),
            BotCommand("bowling", "🎳 Bowling"),
            BotCommand("slot", "🎰 Slot ម៉ាស៊ីន"),
            BotCommand("market", "📈 ដុល្លារ មាស ព័ត៌មាន"),
            BotCommand("askfamily", "👨‍👩‍👧 សួរ AI អំពីកូន និងសត្វ"),
        ]
        # Main commands first (in MENU_COMMANDS order), then every other public command,
        # so typing "/m" shows /myplan, /memory ... too. Telegram allows up to 100.
        by_name = {c.command: c for c in commands}
        hidden = {"dailycare", "register", "code"}            # admin / internal ones stay out of the public menu
        ordered = [n for n in MENU_COMMANDS if n in by_name]
        ordered += [n for n in by_name if n not in ordered and n not in hidden]
        public = [by_name[n] for n in ordered][:100]
        _clear_command_overrides()
        bot.set_my_commands(public)
        # Group admins see the moderation commands too; the owner/admins see owner tools.
        from telebot import types as tg_types
        minor = {"exercise", "balance", "warning", "bp", "water", "sleep", "videoprompt", "songprompt", "imageprompt"}
        admin_names = {c for c, _d in GROUP_ADMIN_COMMANDS} | {c for c, _d in OWNER_COMMANDS}
        trimmed = [c for c in public if c.command not in minor and c.command not in admin_names]
        group_admin = [BotCommand(c, d) for c, d in GROUP_ADMIN_COMMANDS] + trimmed
        bot.set_my_commands(group_admin[:100], scope=tg_types.BotCommandScopeAllChatAdministrators())
        # owner: owner tools + admin tools + the main public list (the few that don't fit still work when typed)
        rare = {"register", "team", "events", "notifications", "ev", "fuel", "duel", "gametop", "code", "vip",
                "number", "palm", "dream", "community", "profile", "leaderboard", "movie", "myname", "music"}
        owner_list = ([BotCommand(c, d) for c, d in OWNER_COMMANDS] + [BotCommand(c, d) for c, d in GROUP_ADMIN_COMMANDS]
                      + [c for c in trimmed if c.command not in rare] + [c for c in trimmed if c.command in rare]
                      + [c for c in public if c.command in minor])[:100]
        for chat_id in {getattr(config, "OWNER_ID", 0), *getattr(config, "ADMIN_IDS", [])}:
            if chat_id:
                try:
                    bot.set_my_commands(owner_list, scope=tg_types.BotCommandScopeChat(chat_id))
                except Exception as exc:
                    logger.info("owner command list skipped (%s)", type(exc).__name__)
        logger.info("Bot commands menu registered successfully.")
    except Exception as e:
        logger.warning(f"Could not register bot commands menu: {e}")


def _location_menu_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row_width = 2
    markup.add(
        InlineKeyboardButton("⛽ រកកន្លែងចាក់សាំង", callback_data="location:fuel"),
        InlineKeyboardButton("⚡ រកកន្លែងសាក EV", callback_data="location:ev"),
        InlineKeyboardButton("📍 ស្វែងរកជិតខ្ញុំ", callback_data="location:nearby"),
        InlineKeyboardButton("🗺️ មើលផែនទី", callback_data="location:map"),
        InlineKeyboardButton("🔎 ស្វែងរកតាមខេត្ត", callback_data="location:province"),
        InlineKeyboardButton("🌤️ អាកាសធាតុ", callback_data="weather:menu"),
    )
    markup.row(InlineKeyboardButton("🏠 មឺនុយមេ", callback_data="nav:main"))
    return markup


def _location_request_keyboard():
    markup = ReplyKeyboardMarkup(row_width=2, resize_keyboard=True, one_time_keyboard=True)
    markup.add(
        KeyboardButton("📍 ចែករំលែកទីតាំង", request_location=True),
        KeyboardButton("🔎 ស្វែងរកតាមខេត្ត"),
    )
    return markup


def _set_pending_location_mode(user_id, kind):
    if user_id is not None:
        _pending_location_modes[user_id] = (kind, time.monotonic() + _LOCATION_STATE_TTL)


def _take_pending_location_mode(user_id):
    pending = _pending_location_modes.pop(user_id, None)
    if pending and pending[1] > time.monotonic():
        return True, pending[0]
    return False, None


def _location_prompt(message, kind=None):
    _set_pending_location_mode(getattr(getattr(message, "from_user", None), "id", None), kind)
    if getattr(getattr(message, "chat", None), "type", None) == "private":
        bot.reply_to(
            message,
            "សូមចែករំលែកទីតាំង ឬវាយឈ្មោះខេត្ត/ទីតាំងជាភាសាខ្មែរ ឬអង់គ្លេស។",
            reply_markup=_location_request_keyboard(),
        )
    else:
        bot.reply_to(
            message,
            "សូមវាយឈ្មោះខេត្ត/ទីតាំង។ ដើម្បីស្វែងរកតាម GPS សូមប្រើ Bot ក្នុង Private Chat។",
        )


_LOCATION_WIDE_RADIUS = 60000


def _format_location_page(results, token, page, maps=None):
    start = page * _LOCATION_PAGE_SIZE
    current = results[start:start + _LOCATION_PAGE_SIZE]
    chunks = []
    keyboard = InlineKeyboardMarkup()
    for offset, station in enumerate(current, start=start + 1):
        lines = [f"{offset}. {station['name']}", f"📍 {station['address']}"]
        distance = station.get("distance_km")
        if distance is not None:
            lines.append(f"📏 ចម្ងាយពីទីតាំងស្វែងរក៖ {distance:.1f} គម")
        if station.get("operator"):
            lines.append(f"🏢 ប្រតិបត្តិករ៖ {station['operator']}")
        if station.get("connectors"):
            lines.append(f"🔌 Connector៖ {', '.join(station['connectors'])}")
        if station.get("power"):
            lines.append(f"⚡ កម្លាំងសាកដែលបានដាក់ស្លាក៖ {station['power']}")
        if station.get("hours"):
            lines.append(f"🕒 ម៉ោង (តាម OpenStreetMap; សូមផ្ទៀងផ្ទាត់)៖ {station['hours']}")
        if station.get("details"):
            lines.append(f"ℹ️ {station['details']}")
        if station.get("phone"):
            lines.append(f"☎️ {station['phone']}")
        chunks.append("\n".join(lines))
        keyboard.add(InlineKeyboardButton(f"🗺️ ផ្លូវទៅ {offset}", url=station["maps_url"]))
    nav_buttons = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton("⬅️ ថយក្រោយ", callback_data=f"location:next:{token}:{page - 1}")
        )
    if start + _LOCATION_PAGE_SIZE < len(results):
        nav_buttons.append(
            InlineKeyboardButton("បន្ទាប់ ➡️", callback_data=f"location:next:{token}:{page + 1}")
        )
    if nav_buttons:
        keyboard.row(*nav_buttons)
    from_google = bool(results) and results[0].get("source") == "google"
    footer = ("\n\nប្រភព៖ Google Maps ✅ — ចុច 🗺️ ផ្លូវទៅ ដើម្បីបើកផ្លូវ។" if from_google else
              "\n\nប្រភព៖ OpenStreetMap។ ទិន្នន័យអាចមិនពេញលេញ; មិនមានស្ថានភាព charger ផ្ទាល់ពេលទេ។")
    if maps:
        kind, lat, lon, query = maps
        for row in _maps_fallback_markup(kind, lat, lon, query, label="🌐 មើលទាំងអស់ជិតអ្នក (Google Maps)").keyboard:
            keyboard.row(*row)
        if len(results) < 3 and not from_google:
            footer = ("\n\n💡 OpenStreetMap មានទិន្នន័យ" + ("ស្ថានីយ EV" if kind == "ev" else "ស្ថានីយ") +
                      " នៅកម្ពុជាតិចនៅឡើយ — ចុច 🌐 Google Maps ខាងក្រោម ដើម្បីឃើញកន្លែងច្រើនជាង និងជិតជាងនេះ។")
    return (
        "\n\n".join(chunks)
        + footer,
        keyboard,
    )


def _maps_fallback_markup(kind, lat=None, lon=None, query="", label="🗺️ បើកក្នុង Google Maps"):
    """Google Maps search buttons — always work, even when OpenStreetMap is down."""
    from urllib.parse import quote
    what = "EV charging station" if kind == "ev" else "gas station"
    if lat is not None and lon is not None:
        url = f"https://www.google.com/maps/search/{quote(what)}/@{lat:.5f},{lon:.5f},14z"
    else:
        url = f"https://www.google.com/maps/search/{quote(what + ' ' + (query or 'Cambodia'))}"
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton(label, url=url))
    return markup


def _google_places_result(query, kind, lat, lon):
    """Google Maps (via Gemini grounding) first — far more EV chargers than OpenStreetMap. None = use OSM."""
    import maps_places
    key = getattr(config, "GEMINI_API_KEY", "")
    if not maps_places.enabled(key):
        return None
    parsed = location_service.normalize_query(query or "")
    kind = kind or parsed.get("kind")
    place = parsed.get("location") or ""
    if lat is None and not place:
        return None
    try:
        stations = maps_places.search(key, kind, lat=lat, lon=lon, place=place)
    except Exception as exc:
        logger.warning("Google Maps grounding failed (%s)", str(exc)[:200])
        return None
    if len(stations) < 2:
        return None
    return {"status": "ok", "stations": stations, "origin": "google",
            "center": (lat, lon) if lat is not None else (None, None), "radius_m": None}


def _send_location_results(message, query="", kind=None, lat=None, lon=None):
    try:
        bot.send_chat_action(message.chat.id, "find_location")
    except Exception:
        pass
    waiting = None
    try:
        waiting = bot.reply_to(message, "🔎 កំពុងស្វែងរក" + (" ស្ថានីយសាក EV ⚡" if kind == "ev" else " ស្ថានីយប្រេង ⛽") + " ...")
    except Exception:
        pass
    result = _google_places_result(query, kind, lat, lon)
    try:
        if result is None:
            result = location_service.location_service.search(
                query,
                lat=lat,
                lon=lon,
                kind=kind,
            )
        # OpenStreetMap is thin in Cambodia (especially EV chargers): look further before giving up.
        if (result.get("status") in ("ok", "empty") and result.get("origin") != "google" and len(result["stations"]) < 3
                and result.get("radius_m") == location_service.SEARCH_RADIUS_METERS):
            try:
                wider = location_service.location_service.search(query, lat=lat, lon=lon, kind=kind,
                                                                  radius_m=_LOCATION_WIDE_RADIUS)
                if len(wider["stations"]) > len(result["stations"]):
                    result = wider
            except location_service.LocationSearchError:
                pass
    except location_service.LocationSearchError as exc:
        cause = exc.__cause__
        logger.warning(
            "Location search provider request failed: %s (%s)",
            exc, location_service._describe_http_error(cause) if cause else "no cause",
        )
        bot.reply_to(
            message,
            "⚠️ ផែនទី OpenStreetMap រវល់ពេលនេះ — ចុចប៊ូតុងខាងក្រោម ដើម្បីមើលក្នុង Google Maps ភ្លាម 👇",
            reply_markup=_maps_fallback_markup(kind, lat, lon, query),
        )
        return
    finally:
        if waiting is not None:
            try:
                bot.delete_message(message.chat.id, waiting.message_id)
            except Exception:
                pass

    if result["status"] == "missing_location":
        _location_prompt(message, kind)
        return
    if result["status"] == "not_found":
        bot.reply_to(message, "រកមិនឃើញទីតាំងនោះក្នុង Cambodia ទេ។ សូមពិនិត្យឈ្មោះខេត្ត ឬទីតាំង។")
        return
    if not result["stations"]:
        bot.reply_to(
            message,
            "មិនមានលទ្ធផលក្នុង OpenStreetMap ក្នុងតំបន់នេះទេ — សាកមើលក្នុង Google Maps 👇",
            reply_markup=_maps_fallback_markup(kind, lat, lon, query),
        )
        return

    stations = result["stations"][:50]
    now = time.monotonic()
    for old_token, state in list(_location_result_pages.items()):
        if state["expires_at"] <= now:
            _location_result_pages.pop(old_token, None)
    while len(_location_result_pages) >= 100:
        _location_result_pages.pop(next(iter(_location_result_pages)))

    token = secrets.token_urlsafe(6)
    _location_result_pages[token] = {
        "owner_id": getattr(getattr(message, "from_user", None), "id", None),
        "expires_at": now + _LOCATION_STATE_TTL,
        "stations": stations,
        "maps": (kind or stations[0].get("kind"), *(result.get("center") or (lat, lon)), query),
    }
    text, markup = _format_location_page(stations, token, 0, _location_result_pages[token]["maps"])
    bot.reply_to(message, text, reply_markup=markup)


_WEATHER_MENU_TEXT = "🌤️ សូមជ្រើសរើសខេត្តដែលចង់ដឹងអាកាសធាតុ៖"
_WEATHER_ERROR_TEXT = "មិនអាចទាញទិន្នន័យអាកាសធាតុបានទេឥឡូវនេះ។ សូមសាកល្បងម្ដងទៀតពេលក្រោយ។"


def _weather_menu_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row_width = 3
    markup.add(*[
        InlineKeyboardButton(info[0], callback_data=f"weather:{slug}")
        for slug, info in weather_service.PROVINCES.items()
    ])
    markup.row(InlineKeyboardButton("🏠 មឺនុយមេ", callback_data="nav:main"))
    return markup


def _weather_result_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🔙 ជ្រើសខេត្តផ្សេង", callback_data="weather:menu"),
        InlineKeyboardButton("🏠 មឺនុយមេ", callback_data="nav:main"),
    )
    return markup


def _send_weather(message, slug):
    try:
        text = weather_service.get_weather_text(slug)
    except weather_service.WeatherError:
        bot.reply_to(message, _WEATHER_ERROR_TEXT)
        return
    bot.reply_to(message, text, reply_markup=_weather_result_keyboard())


def _send_weather_menu(message):
    bot.reply_to(message, _WEATHER_MENU_TEXT, reply_markup=_weather_menu_keyboard())


def _handle_weather_callback(call, chat, data):
    key = data.split(":", 1)[1]
    message = getattr(call, "message", None)
    if chat is None or message is None:
        bot.answer_callback_query(call.id)
        return

    def show(text, markup):
        try:
            bot.edit_message_text(text, chat.id, message.message_id, reply_markup=markup)
        except Exception:
            bot.send_message(chat.id, text, reply_markup=markup)

    if key == "menu":
        bot.answer_callback_query(call.id)
        show(_WEATHER_MENU_TEXT, _weather_menu_keyboard())
        return
    if key not in weather_service.PROVINCES:
        bot.answer_callback_query(call.id)
        return
    bot.answer_callback_query(call.id, "កំពុងទាញទិន្នន័យ...")
    try:
        text = weather_service.get_weather_text(key)
    except weather_service.WeatherError:
        bot.send_message(chat.id, _WEATHER_ERROR_TEXT)
        return
    show(text, _weather_result_keyboard())



# =========================================================
# AI Photo Studio (កែរូបដោយ AI)
# =========================================================

_STUDIO_INTRO_TEXT = (
    "🎨 *AI Photo Studio*\n\n"
    "សូមផ្ញើរូបថតមួយសន្លឹកមក (មុខច្បាស់ ពន្លឺល្អ) ហើយខ្ញុំនឹងឲ្យអ្នកជ្រើសម៉ូដ។\n"
    "មុខដើមរបស់អ្នកនឹងត្រូវរក្សាទុក មានតែសម្លៀកបំពាក់ និងផ្ទៃខាងក្រោយដែលប្ដូរ។\n\n"
    "💡 ឬផ្ញើរូប + សរសេរ Prompt នៅ Caption (ឧ. Prompt ពី /imageprompt) ខ្ញុំបង្កើតរូបភ្លាម។"
)
_STUDIO_CHOOSE_TEXT = "🎨 សូមជ្រើសម៉ូដដែលចង់បាន៖"
_STUDIO_NOT_CONFIGURED_TEXT = "🎨 Photo Studio មិនទាន់បើកដំណើរការនៅឡើយទេ។"
_STUDIO_BILLING_HINT = "\n\n🔧 (Admin) Gemini key ត្រូវបើក Billing នៅ Google AI Studio ទើបកែរូបបាន។"
_STUDIO_BLOCKED_TEXT = "AI មិនអាចកែរូបនេះបានទេ (អាចជារូបមិនច្បាស់ ឬខុសគោលការណ៍)។ សូមសាករូបផ្សេង។"
_STUDIO_BUSY_TEXT = "ប្រព័ន្ធកែរូបរវល់ពេកឥឡូវនេះ។ សូមសាកម្ដងទៀតក្នុងមួយភ្លែតទៀត។"
_STUDIO_EXPIRED_TEXT = "រូបនេះផុតកំណត់ហើយ។ សូមវាយ /studio ហើយផ្ញើរូបម្ដងទៀត។"
_STUDIO_ERROR_TEXT = "សុំទោស មិនអាចកែរូបបានទេឥឡូវនេះ។ សូមសាកល្បងម្ដងទៀតពេលក្រោយ។"
_STUDIO_CAPTION_TRIGGERS = ("/studio", "studio", "កែរូប")


def _studio_enabled() -> bool:
    return bool(getattr(config, "STUDIO_ENABLED", False) and getattr(config, "GEMINI_API_KEY", ""))


def _studio_is_privileged(user_id) -> bool:
    return user_id is not None and (
        user_id == getattr(config, "OWNER_ID", 0) or user_id in getattr(config, "ADMIN_IDS", [])
    )


def _studio_today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _studio_remaining(user_id) -> int:
    if _studio_is_privileged(user_id):
        return 10**6
    limit = max(0, int(getattr(config, "STUDIO_DAILY_LIMIT", 3)))
    return max(0, limit - _studio_usage.get((user_id, _studio_today()), 0))


def _studio_record_use(user_id) -> None:
    today = _studio_today()
    for key in [k for k in _studio_usage if k[1] != today]:
        _studio_usage.pop(key, None)
    _studio_usage[(user_id, today)] = _studio_usage.get((user_id, today), 0) + 1


def _studio_take_waiting(user_id) -> bool:
    expires_at = _studio_waiting.pop(user_id, None)
    return expires_at is not None and expires_at > time.monotonic()


def _studio_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row_width = 2
    markup.add(*[
        InlineKeyboardButton(style["name_kh"], callback_data=f"studio:{key}")
        for key, style in photo_studio.STYLES.items()
        if key != "custom"
    ])
    markup.row(InlineKeyboardButton("❌ បោះបង់", callback_data="studio:cancel"))
    return markup


def _start_studio(message):
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if not _studio_enabled():
        bot.reply_to(message, _STUDIO_NOT_CONFIGURED_TEXT)
        return
    if _studio_remaining(user_id) <= 0:
        if int(getattr(config, "STUDIO_DAILY_LIMIT", 3)) <= 0:
            bot.reply_to(message, "🎨 Photo Studio បើកសម្រាប់តែ Admin ប៉ុណ្ណោះពេលនេះ។")
            return
        bot.reply_to(message, "អ្នកបានប្រើ Photo Studio អស់សម្រាប់ថ្ងៃនេះហើយ។ សូមត្រឡប់មកវិញថ្ងៃស្អែក 🙏")
        return
    _studio_waiting[user_id] = time.monotonic() + _STUDIO_TTL_SECONDS
    try:
        bot.reply_to(message, _STUDIO_INTRO_TEXT, parse_mode="Markdown")
    except Exception:
        bot.reply_to(message, _STUDIO_INTRO_TEXT.replace("*", ""))


def _studio_accept_photo(message, user_id, file_id) -> None:
    _studio_photos[user_id] = {"file_id": file_id, "expires_at": time.monotonic() + _STUDIO_TTL_SECONDS}
    bot.reply_to(message, _STUDIO_CHOOSE_TEXT, reply_markup=_studio_keyboard())


def _handle_studio_callback(call, chat, data):
    key = data.split(":", 1)[1]
    message = getattr(call, "message", None)
    user_id = getattr(getattr(call, "from_user", None), "id", None)
    if chat is None or message is None or user_id is None:
        bot.answer_callback_query(call.id)
        return

    def show(text):
        try:
            bot.edit_message_text(text, chat.id, message.message_id)
        except Exception:
            bot.send_message(chat.id, text)

    if key == "cancel":
        _studio_photos.pop(user_id, None)
        bot.answer_callback_query(call.id)
        show("បានបោះបង់ Photo Studio ហើយ។")
        return
    if key not in photo_studio.STYLES or key == "custom":
        bot.answer_callback_query(call.id)
        return

    state = _studio_photos.get(user_id)
    if state is None or state["expires_at"] <= time.monotonic():
        _studio_photos.pop(user_id, None)
        bot.answer_callback_query(call.id, _STUDIO_EXPIRED_TEXT, show_alert=True)
        return
    if not _studio_enabled():
        bot.answer_callback_query(call.id, _STUDIO_NOT_CONFIGURED_TEXT, show_alert=True)
        return
    if _studio_remaining(user_id) <= 0:
        bot.answer_callback_query(call.id, "អ្នកបានប្រើអស់សម្រាប់ថ្ងៃនេះហើយ។", show_alert=True)
        return
    if user_id in _studio_busy:
        bot.answer_callback_query(call.id, "កំពុងកែរូបមុនរបស់អ្នក សូមរង់ចាំបន្តិច...", show_alert=True)
        return

    _studio_busy.add(user_id)
    style_name = photo_studio.STYLES[key]["name_kh"]
    bot.answer_callback_query(call.id, "កំពុងកែរូប...")
    show(f"⏳ កំពុងកែរូបជាម៉ូដ {style_name} ... (ប្រហែល 10-40 វិនាទី)")

    try:
        try:
            bot.send_chat_action(chat.id, "upload_photo")
        except Exception:
            pass
        file_info = bot.get_file(state["file_id"])
        photo_bytes = bot.download_file(file_info.file_path)
        result = async_runner.run_sync(
            photo_studio.transform_photo(photo_bytes, style_key=key),
            timeout=180,
        )
        _studio_record_use(user_id)
        _studio_photos.pop(user_id, None)
        caption = f"✨ រួចរាល់! ម៉ូដ៖ {style_name}\nវាយ /studio ដើម្បីកែរូបផ្សេងទៀត។"
        bot.send_photo(chat.id, result, caption=caption)
        show(f"✅ កែរូបជាម៉ូដ {style_name} រួចរាល់។")
    except photo_studio.StudioError as exc:
        logger.error("Photo studio error (%s)", exc.code)
        if exc.code == "not_configured":
            show(_STUDIO_NOT_CONFIGURED_TEXT)
        elif exc.code == "billing":
            show(_STUDIO_ERROR_TEXT + (_STUDIO_BILLING_HINT if _studio_is_privileged(user_id) else ""))
        elif exc.code == "rate_limited":
            show(_STUDIO_BUSY_TEXT)
        elif exc.code in ("blocked", "no_image"):
            show(_STUDIO_BLOCKED_TEXT)
        else:
            show(_STUDIO_ERROR_TEXT)
    except Exception as exc:
        logger.error("Photo studio error (%s): %s", type(exc).__name__, exc)
        show(_STUDIO_ERROR_TEXT)
    finally:
        _studio_busy.discard(user_id)


_QUESTION_STARTS = ("what", "who", "why", "how", "where", "when", "which", "is ", "are ", "can ", "could ",
                    "does ", "do ", "explain", "describe", "តើ", "អ្វី", "ពន្យល់", "ពិពណ៌នា", "មើល", "សួរ")


def _caption_is_prompt(caption: str) -> bool:
    """A photo caption that reads like an edit/generation prompt (not a question about the photo)."""
    text = " ".join((caption or "").split())
    if len(text) < 15 or text.startswith("/"):
        return False
    low = text.lower().lstrip("> ").strip()
    if low.endswith(("?", "？")) or low.startswith(_QUESTION_STARTS):
        return False
    return True


def _studio_error_text(exc, user_id) -> str:
    code = getattr(exc, "code", "")
    if code == "not_configured":
        return _STUDIO_NOT_CONFIGURED_TEXT
    if code == "billing":
        return _STUDIO_ERROR_TEXT + (_STUDIO_BILLING_HINT if _studio_is_privileged(user_id) else "")
    if code == "rate_limited":
        return _STUDIO_BUSY_TEXT
    if code in ("blocked", "no_image"):
        return _STUDIO_BLOCKED_TEXT
    return _STUDIO_ERROR_TEXT


# 💇 Style Studio: /style — try a haircut or a photo pose on your own selfie (shares the Studio daily limit)
_style = style_studio.register(bot, config, {
    "enabled": _studio_enabled, "remaining": _studio_remaining, "record": _studio_record_use,
    "busy": _studio_busy, "error_text": _studio_error_text,
    "run": lambda data, prompt: async_runner.run_sync(photo_studio.transform_photo(data, full_prompt=prompt), timeout=180),
})


# ✨ AI Studio web app in the Hub (hub/ai.html): same engines and daily limits as the commands above
import studio_web  # noqa: E402
studio_web.register(bot, config, {
    "photo_remaining": _studio_remaining, "photo_record": _studio_record_use, "photo_busy": _studio_busy,
    "photo_run": lambda data, prompt: async_runner.run_sync(photo_studio.transform_photo(data, full_prompt=prompt), timeout=180),
    "prompt_ask": _prompt_ask, "ask": _fan_ask_gemini,
})


def _studio_from_caption(message, user_id, file_id, prompt) -> None:
    """Photo + prompt caption → edit the photo with that prompt right away (keeps the person's face)."""
    if _studio_remaining(user_id) <= 0:
        bot.reply_to(message, "អ្នកបានប្រើ Photo Studio អស់សម្រាប់ថ្ងៃនេះហើយ។ សូមត្រឡប់មកវិញថ្ងៃស្អែក 🙏")
        return
    if user_id in _studio_busy:
        bot.reply_to(message, "កំពុងបង្កើតរូបមុនរបស់អ្នក សូមរង់ចាំបន្តិច...")
        return
    _studio_busy.add(user_id)
    status = None
    try:
        status = bot.reply_to(message, "🎨 កំពុងបង្កើតរូបតាម Prompt របស់អ្នក... (ប្រហែល 10-40 វិនាទី)")
    except Exception:
        pass
    try:
        try:
            bot.send_chat_action(message.chat.id, "upload_photo")
        except Exception:
            pass
        file_info = bot.get_file(file_id)
        photo_bytes = bot.download_file(file_info.file_path)
        result = async_runner.run_sync(
            photo_studio.transform_photo(photo_bytes, style_key="custom", custom_prompt=prompt[:900]),
            timeout=180,
        )
        _studio_record_use(user_id)
        bot.send_photo(message.chat.id, result,
                       caption="✨ រួចរាល់! បង្កើតតាម Prompt របស់អ្នក\n"
                               "💡 ចង់ឲ្យ AI ពិពណ៌នារូបវិញ សរសេរសំណួរ ហើយបញ្ចប់ដោយ ?",
                       reply_to_message_id=message.message_id)
    except photo_studio.StudioError as exc:
        logger.error("Photo studio caption error (%s)", exc.code)
        bot.send_message(message.chat.id, _studio_error_text(exc, user_id))
    except Exception as exc:
        logger.error("Photo studio caption error (%s): %s", type(exc).__name__, exc)
        bot.send_message(message.chat.id, _STUDIO_ERROR_TEXT)
    finally:
        _studio_busy.discard(user_id)
        if status is not None:
            try:
                bot.delete_message(message.chat.id, status.message_id)
            except Exception:
                pass


def keep_typing(chat_id: int, stop_event: threading.Event):
    """បញ្ជូនសញ្ញា typing រៀងរាល់ ៤ វិនាទីម្តង រហូតដល់ AI រៀបចំចម្លើយរួចរាល់"""
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, 'typing')
        except Exception:
            pass
        time.sleep(4)


_TELEGRAM_MAX_CHARS = 4000  # Telegram hard limit is 4096


def _split_text(text, limit=_TELEGRAM_MAX_CHARS):
    """Split long text on paragraph/line/space boundaries so each part fits Telegram."""
    text = text or ""
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts or [""]


def send_long_reply(message, text, reply_markup=None):
    """Reply with text, splitting into several messages when it is over the limit.
    Answers that contain copy-ready prompts/code (``` fences) go out as separate, labelled code blocks
    with Telegram's one-tap Copy button (same look as Prompt Studio)."""
    if text and ("```" in text or "<<<" in text):
        try:
            import prompt_studio
            rich = prompt_studio.render_chunks(text)
            for index, chunk in enumerate(rich):
                kwargs = {"parse_mode": "HTML", "disable_web_page_preview": True}
                if reply_markup is not None and index == len(rich) - 1:
                    kwargs["reply_markup"] = reply_markup
                if index == 0:
                    bot.reply_to(message, chunk, **kwargs)
                else:
                    bot.send_message(message.chat.id, chunk, **kwargs)
            if rich:
                return
        except Exception as exc:
            logger.info("rich reply fell back to plain (%s)", type(exc).__name__)
    chunks = _split_text(text)
    for index, chunk in enumerate(chunks):
        def _send(**kwargs):
            if reply_markup is not None and index == len(chunks) - 1:
                kwargs["reply_markup"] = reply_markup
            if index == 0:
                return bot.reply_to(message, chunk, **kwargs)
            return bot.send_message(message.chat.id, chunk, **kwargs)
        try:
            _send(parse_mode="Markdown")
        except Exception:
            _send()


# =========================================================
# Main Menu (inline buttons, edited in place)
# =========================================================

WEB_APP_URL = os.getenv("WEB_APP_URL", "https://team-smos-snea.lovable.app")

_MAIN_MENU_TEXT = (
    "🏠 <b>មឺនុយមេ — Bot-Smos-Snea</b>\n"
    "ជំនួយការ AI ផ្លូវការរបស់ Team ស្មោះស្នេហ៍ 🇰🇭\n\n"
    "👇 ចុចលើប៊ូតុងខាងក្រោម ឬវាយសួរ AI ដោយផ្ទាល់ (អក្សរ សំឡេង ឬរូបភាព)"
)
_CHAT_HELP_TEXT = (
    "🤖 <b>ជជែកជាមួយ AI</b>\n\n"
    "• ✍️ វាយសំណួរអ្វីក៏បាន\n"
    "• 🎙️ ផ្ញើ Voice Note — AI ស្ដាប់ ហើយឆ្លើយ (ចុច 🔊 ស្ដាប់ចម្លើយជាសំឡេង)\n"
    "• 🗣️ /say អត្ថបទ — Bot អានជាសំឡេងខ្មែរ\n"
    "• 🖼️ ផ្ញើរូបភាព — AI មើល និងពន្យល់ (សួរសំណួរបញ្ចប់ដោយ ?)\n"
    "• 🎨 ផ្ញើរូប + Prompt នៅ Caption — AI បង្កើតរូបថ្មីភ្លាម\n"
    "• 🧹 /reset — សម្អាតប្រវត្តិសន្ទនា"
)
_ABOUT_TEXT = (
    "ℹ️ <b>អំពី Bot-Smos-Snea</b>\n\n"
    "ជំនួយការ AI ផ្លូវការរបស់ <b>Team ស្មោះស្នេហ៍</b> — ជជែក AI, អាកាសធាតុ, "
    "ស្ថានីយសាំង/EV, កែរូប AI និងសហគមន៍ពិន្ទុ ទាំងអស់នៅកន្លែងតែមួយ។"
)


def _md_to_html(text: str) -> str:
    """Convert the **bold** / *italic* used in messages.py to Telegram HTML."""
    import html as _html
    import re as _re
    out = _html.escape(text, quote=False)
    out = _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out, flags=_re.S)
    out = _re.sub(r"\*(.+?)\*", r"<i>\1</i>", out, flags=_re.S)
    return out


def _home_button():
    return InlineKeyboardButton("🏠 មឺនុយមេ", callback_data="nav:main")


def _back_markup():
    markup = InlineKeyboardMarkup()
    markup.row(_home_button())
    return markup


def _main_menu_keyboard():
    """Main menu grouped by category (each category opens a sub-menu in place)."""
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🚀 Cooler Hub — គ្រប់មុខងារ ✨", callback_data="hub:open"))
    markup.row(
        InlineKeyboardButton("🤖 ជជែកជាមួយ AI", callback_data="nav:chat"),
        InlineKeyboardButton("🎨 កែរូប AI", callback_data="nav:studio"),
    )
    markup.row(
        InlineKeyboardButton("✍️ AI បង្កើត", callback_data="nav:cat:create"),
        InlineKeyboardButton("🎮 កម្សាន្ត & ហ្វេន", callback_data="nav:cat:fun"),
    )
    markup.row(
        InlineKeyboardButton("🧓 សុខភាព & គ្រួសារ", callback_data="nav:cat:health"),
        InlineKeyboardButton("📈 ព័ត៌មាន & ជីវិត", callback_data="nav:cat:life"),
    )
    markup.row(
        InlineKeyboardButton("💳 សមាជិក", callback_data="nav:cat:member"),
        InlineKeyboardButton("📲 Vid Cooler", callback_data="follow:menu"),
    )
    markup.row(
        InlineKeyboardButton("🆘 លេខបន្ទាន់", callback_data="health:sos"),
        InlineKeyboardButton("ℹ️ អំពី & ជំនួយ", callback_data="nav:cat:info"),
    )
    return markup


def _category_view(key, user_id=None):
    """(text, markup) for a main-menu category, or None for an unknown key."""
    B = InlineKeyboardButton
    m = InlineKeyboardMarkup()
    if key == "create":
        text = ("✍️ <b>AI បង្កើត</b>\nសរសេរ Prompt វីដេអូ ចម្រៀង រូបភាព រៀបចំរឿង កែរូប សរសេរ Rap និងអានជាសំឡេង 🔥")
        m.row(B("✍️ Prompt Studio", callback_data="pstudio:menu"))
        m.row(B("🎬 វីដេអូ AI", callback_data="pstudio:m:video"), B("🎵 ចម្រៀង Suno", callback_data="pstudio:m:song"))
        m.row(B("🖼️ រូបភាព", callback_data="pstudio:m:image"), B("🎞️ រឿង & Scene", callback_data="pstudio:m:story"))
        m.row(B("🎨 កែរូប AI", callback_data="nav:studio"), B("🎤 AI Rap", callback_data="nav:rap"))
        m.row(B("🎧 Bot ច្រៀង & Rap មាន Beat", callback_data="song:menu"), B("🎨 គូររូប AI", callback_data="x:img"))
        m.row(B("🎙️ Bot និយាយខ្មែរ (Voice-over)", callback_data="tts:menu"))
        m.row(B("📚 AI ឆ្លើយតាមឯកសារ (ហាង & Group)", callback_data="kb:menu"))
    elif key == "fun":
        text = "🎮 <b>កម្សាន្ត & ហ្វេន</b>\nលេងហ្គេម ឆ្លើយ Quiz ប្រមូលពិន្ទុ យក Fan Card 🃏"
        m.row(B("🎮 Game Center — លេងភ្លាម 🎲🎯🏀⚽🎳🎰", callback_data="gc:menu"))
        m.row(B(f"🕹️ ហ្គេមទាំងអស់ ({fan_features.GAME_COUNT})", callback_data="nav:games"), B("🧠 Quiz", callback_data="community:menu:quiz:0"))
        m.row(B("🃏 Fan Card", callback_data="community:menu:card:0"), B("🎁 Check-in", callback_data="community:menu:checkin:0"))
        m.row(B("🔮 ទស្សន៍ទាយ · បាតដៃ · សុបិន", callback_data="fort:menu"))
        m.row(B("🌟 សហគមន៍ & ពិន្ទុ", callback_data="nav:community"))
    elif key == "health":
        text = "🧓 <b>សុខភាព & គ្រួសារ</b>\nលំហាត់ប្រាណ សម្ពាធឈាម មើលកូន និងបង្ហាត់សត្វ ❤️"
        m.row(B("🧓 សុខភាព & លំហាត់", callback_data="health:menu"), B("🆘 លេខបន្ទាន់", callback_data="health:sos"))
        m.row(B("👨‍👩‍👧 មើលកូន", callback_data="fam:kids"), B("🐶🐱 បង្ហាត់សត្វ", callback_data="fam:pet"))
    elif key == "life":
        text = "📈 <b>ព័ត៌មាន & ជីវិតប្រចាំថ្ងៃ</b>\nដុល្លារ មាស ព័ត៌មាន អាកាសធាតុ សាំង និងកន្លែងដើរលេង 🇰🇭"
        m.row(B("💵 ដុល្លារ · 🪙 មាស · 📰 ព័ត៌មាន", callback_data="mkt:menu"))
        m.row(B("🌤️ អាកាសធាតុ", callback_data="nav:weather"), B("⛽ សាំង & ⚡ EV", callback_data="nav:location"))
        m.row(B("📅 ប្រតិទិនខ្មែរ", callback_data="cal:today"), B("☀️ ព័ត៌មានព្រឹក", callback_data="cal:brief"))
        m.row(B("🚨 ឆែក Scam", callback_data="x:scam"), B("⏰ រំលឹក", callback_data="x:remind"))
        m.row(B("💵 កត់លុយ", callback_data="x:cash"), B("🎤 Voice→អក្សរ · 🌐 បកប្រែ · 📚", callback_data="x:tools"))
        m.row(B("🧭 Trip ដើរលេង", callback_data="trip:menu"),
              B("🚀 Team App", web_app=WebAppInfo(url=WEB_APP_URL)))
    elif key == "member":
        until = subscription.paid_until(config, "u", user_id) if user_id else 0
        st = subscription.settings()
        if _is_owner_or_admin(user_id):
            status = "👑 អ្នកជា Owner/Admin — ប្រើគ្រប់មុខងារ"
        elif until > time.time():
            status = f"✅ សមាជិករហូតដល់ <b>{subscription.fmt_date(until)}</b>"
        else:
            status = "🔒 មិនទាន់ជាសមាជិក"
        text = (f"💳 <b>សមាជិក Cooler Bot</b>\n\n{status}\n\n"
                f"👤 ម្នាក់៖ {st.user_price} / {st.days} ថ្ងៃ · 👥 Group៖ {st.group_price} / {st.days} ថ្ងៃ\n"
                "🙏 អរគុណដែលគាំទ្រ Vid Cooler ❤️")
        m.row(B(f"💳 បង់ / បន្ត {st.user_price}", callback_data="sub:pay:u"), B("🆓 មុខងារឥតគិតថ្លៃ", callback_data="sub:free"))
        m.row(B("💬 ទាក់ទង Vid Cooler", callback_data="follow:menu"))
    elif key == "info":
        text = "ℹ️ <b>អំពី & ជំនួយ</b>"
        m.row(B("👥 ក្រុមយើង", callback_data="nav:team"), B("📅 ព្រឹត្តិការណ៍", callback_data="nav:events"))
        m.row(B("🔔 ការជូនដំណឹង", callback_data="nav:notifications"), B("ℹ️ អំពី Bot", callback_data="nav:about"))
        m.row(B("🛡️ ការពារ Group · 📝 សង្ខេប", callback_data="x:guard"), B("🧠 Bot ចាំអ្នក", callback_data="x:mem"))
    else:
        return None
    m.row(B("🏠 ម៉ឺនុយមេ", callback_data="nav:main"))
    return text, m


def _all_menu_rows():
    """Main-menu rows plus every category's rows (handy for checks and tests)."""
    rows = list(_main_menu_keyboard().keyboard)
    for key in ("create", "fun", "health", "life", "member", "info"):
        rows += _category_view(key)[1].keyboard
    return rows


def _is_owner_or_admin(user_id):
    return user_id is not None and (user_id == getattr(config, "OWNER_ID", 0) or user_id in getattr(config, "ADMIN_IDS", []))


def _main_menu_text(user_id=None):
    """Main-menu text with the person's membership status on top."""
    status = ""
    if user_id is not None and subscription.settings().enabled:
        if _is_owner_or_admin(user_id):
            status = "👑 Owner/Admin\n"
        else:
            until = subscription.paid_until(config, "u", user_id)
            status = (f"✅ សមាជិក · ដល់ {subscription.fmt_date(until)[:10]}\n" if until > time.time()
                      else "🔒 មិនទាន់ជាសមាជិក — ចុច 💳 សមាជិក\n")
    return status + _MAIN_MENU_TEXT


def _community_nav_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🎁 Check-in", callback_data="community:menu:checkin:0"),
        InlineKeyboardButton("🧠 Quiz", callback_data="community:menu:quiz:0"),
    )
    markup.row(
        InlineKeyboardButton("🏆 Leaderboard", callback_data="community:menu:top:0"),
        InlineKeyboardButton("⭐ Profile", callback_data="community:menu:profile:0"),
    )
    markup.row(
        InlineKeyboardButton("🤝 Invite", callback_data="community:menu:invite:0"),
        InlineKeyboardButton("👑 VIP", callback_data="community:menu:vip:0"),
    )
    markup.row(_home_button())
    return markup


def _send_main_menu(message):
    bot.reply_to(message, _main_menu_text(getattr(getattr(message, "from_user", None), "id", None)),
                 reply_markup=_main_menu_keyboard(), parse_mode="HTML")


def _show_in_place(call, text, markup, parse_mode="HTML"):
    """Edit the menu message in place; fall back to a new message."""
    message = call.message
    try:
        bot.edit_message_text(text, message.chat.id, message.message_id,
                              reply_markup=markup, parse_mode=parse_mode)
    except Exception as exc:
        if "message is not modified" in str(exc):
            return
        try:
            bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode=parse_mode)
        except Exception:
            bot.send_message(message.chat.id, text, reply_markup=markup)


def _handle_nav_callback(call, data):
    from types import SimpleNamespace
    key = data.split(":", 1)[1]
    message = getattr(call, "message", None)
    if message is None:
        bot.answer_callback_query(call.id)
        return
    bot.answer_callback_query(call.id)
    if key == "main":
        _show_in_place(call, _main_menu_text(getattr(call.from_user, "id", None)), _main_menu_keyboard())
    elif key.startswith("cat:"):
        view = _category_view(key[4:], getattr(call.from_user, "id", None))
        if view:
            _show_in_place(call, *view)
    elif key == "chat":
        _show_in_place(call, _CHAT_HELP_TEXT, _back_markup())
    elif key == "about":
        _show_in_place(call, _ABOUT_TEXT, _back_markup())
    elif key == "team":
        _show_in_place(call, _md_to_html(messages.TEAM_MESSAGE), _back_markup())
    elif key == "events":
        _show_in_place(call, _md_to_html(messages.EVENTS_MESSAGE), _back_markup())
    elif key == "weather":
        _show_in_place(call, _WEATHER_MENU_TEXT, _weather_menu_keyboard(), parse_mode=None)
    elif key == "location":
        _show_in_place(call, "⛽ <b>សាំង & ⚡ EV នៅកម្ពុជា</b>\nជ្រើសសេវាកម្ម ឬវាយឈ្មោះខេត្ត៖", _location_menu_keyboard())
    elif key == "community":
        _show_in_place(call, "🌟 <b>សហគមន៍ VidCooler</b>\nប្រមូលពិន្ទុ ឡើងកម្រិត ហើយប្រកួតលើ Leaderboard!", _community_nav_keyboard())
    elif key == "games":
        games_kb = _fan.games_markup(getattr(message.chat, "type", None) == "private")
        games_kb.row(InlineKeyboardButton("🎮 Game Center — លេងភ្លាមក្នុង Telegram", callback_data="gc:menu"))
        games_kb.row(_home_button())
        _show_in_place(call, f"🎮 <b>ហ្គេម Vid Cooler — {fan_features.GAME_COUNT} ហ្គេម</b>\n👨‍👩‍👧 មនុស្សធំ · 🚀 3D · ⚡ ល្បឿន\nចុចដើម្បីលេងភ្លាម 👇", games_kb)
    elif key == "rap":
        proxy = SimpleNamespace(from_user=call.from_user, chat=message.chat,
                                message_id=message.message_id, text="/rap")
        _fan.ask(proxy, "rap")
    elif key in ("studio", "notifications"):
        proxy = SimpleNamespace(from_user=call.from_user, chat=message.chat,
                                message_id=message.message_id, text=f"/{key}")
        if key == "studio":
            _start_studio(proxy)
        else:
            notifications_command(proxy)


# =========================================================
# Commands Handlers
# =========================================================

@registered_message_handler(commands=['start'])
def send_welcome(message):
    import html as _html
    raw_name = getattr(getattr(message, "from_user", None), "first_name", None) or "ប្រិយមិត្ត"
    name = _html.escape(raw_name)
    welcome_text = (
        f"🙏 សួស្ដី <b>{name}</b>! សូមស្វាគមន៍មកកាន់ <b>Bot-Smos-Snea</b> 🤖✨\n"
        "ជំនួយការ AI ផ្លូវការរបស់ Team ស្មោះស្នេហ៍ 🇰🇭\n\n"
        "👇 ជ្រើសសេវាកម្មខាងក្រោម ឬវាយសួរ AI ដោយផ្ទាល់"
    )
    mascot = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "coolerbot_avatar.jpg")
    if os.path.exists(mascot):
        try:
            with open(mascot, "rb") as photo:
                bot.send_photo(
                    message.chat.id, photo,
                    caption=f"😎 សួស្ដី {raw_name}! ខ្ញុំគឺ Cooler Bot 🤖✨\nជំនួយការ AI របស់ Vid Cooler 🎤🇰🇭",
                    reply_to_message_id=getattr(message, "message_id", None),
                    reply_markup=_follow_markup(with_home=False),
                )
        except Exception as exc:
            logger.warning("Could not send mascot photo (%s)", type(exc).__name__)
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    bot.send_message(message.chat.id, _main_menu_text(user_id).split("\n")[0] + "\n\n" + welcome_text
                     if user_id is not None and subscription.settings().enabled else welcome_text,
                     reply_markup=_main_menu_keyboard(), parse_mode="HTML")


@registered_message_handler(commands=['menu'])
def send_menu(message):
    _send_main_menu(message)


@registered_message_handler(commands=['fuel'])
def fuel_search_command(message):
    _location_prompt(message, "fuel")


@registered_message_handler(commands=['ev'])
def ev_search_command(message):
    _location_prompt(message, "ev")


@registered_message_handler(commands=['weather'])
def weather_command(message):
    parts = (message.text or "").split(maxsplit=1)
    arg = parts[1] if len(parts) > 1 else ""
    slug = weather_service.find_province(arg)
    if arg and slug is None:
        bot.reply_to(message, "រកមិនឃើញខេត្តនោះទេ។ សូមជ្រើសរើសពីបញ្ជីខាងក្រោម។")
        _send_weather_menu(message)
    elif slug:
        _send_weather(message, slug)
    else:
        _send_weather_menu(message)


@registered_message_handler(commands=['about'])
def send_about(message):
    bot.reply_to(message, _ABOUT_TEXT, reply_markup=_back_markup(), parse_mode="HTML")


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


@registered_message_handler(commands=['studio'])
def studio_command(message):
    _start_studio(message)


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
        "• /notifications - បើក ឬបិទសារ Daily Care\n"
        "• /weather - ព្យាករណ៍អាកាសធាតុតាមខេត្ត\n"
        "• /studio - កែរូបដោយ AI (Photo Studio)\n"
        "• /community - មឺនុយសហគមន៍\n"
        "• /profile - ពិន្ទុ និងកម្រិត\n"
        "• /checkin - ពិន្ទុប្រចាំថ្ងៃ\n"
        "• /quiz - Quiz ប្រចាំថ្ងៃ\n"
        "• /leaderboard - ចំណាត់ថ្នាក់\n"
        "• /invite - អញ្ជើញមិត្តភក្តិ\n"
        "• /vip - ស្ថានភាព VIP\n"
        "• /games - ហ្គេមរបស់ Vid Cooler\n"
        "• /music - បទរបស់ Vid Cooler\n"
        "• /rap - AI សរសេរ Rap ជាមួយ Cooler\n"
        "• /movie - ណែនាំរឿង + កន្លែងមើលផ្លូវការ\n"
        "• /myname - AI បង្កើតឈ្មោះ Rapper\n"
        "• /card - Fan Card សម្រាប់ Share\n"
        "• /code - វាយកូដសម្ងាត់ពី MV/Live\n"
        "• /trip - កន្លែងដើរលេងល្អៗនៅកម្ពុជា\n"
        "• /pay - បង់ 1$ ប្រើគ្រប់មុខងារ 30 ថ្ងៃ (Group 5$)\n"
        "• /myplan - ស្ថានភាពសមាជិក\n"
        "• /play - 🎮 Game Center 🎲🎯🏀⚽🎳🎰 · /duel - ប្រកួតមិត្ត · /gametop - Top ថ្ងៃនេះ\n"
        "• /fortune - ទស្សន៍ទាយ ១២ ឆ្នាំខ្មែរ\n"
        "• /number - លេខជីវិត · /palm - ទាយបាតដៃ · /dream - ទាយសុបិន\n"
        "• /parent - មើលកូន ១-៦ ឆ្នាំ\n"
        "• /pet - បង្ហាត់ឆ្កែ ឆ្មា\n"
        "• /rate - ដុល្លារ ↔ រៀល (ឧ. /rate 100)\n"
        "• /gold - តម្លៃមាស ជី / ដំឡឹង\n"
        "• /news - ព័ត៌មានថ្មីៗ\n"
        "• /follow - តាមដាន Vid Cooler (TikTok, YouTube, Facebook)\n"
        "• /contact - ទាក់ទង Vid Cooler តាម Telegram\n\n"
        "✍️ **Prompt Studio**\n"
        "• /prompt - ម៉ឺនុយ Prompt Studio\n"
        "• /videoprompt - Prompt វីដេអូ AI\n"
        "• /songprompt - Prompt ចម្រៀង (Suno)\n"
        "• /imageprompt - Prompt រូបភាព\n"
        "• /story - រៀបចំរឿង Scene និងភាគ\n\n"
        "🧓 **សុខភាព**\n"
        "• /health - សុខភាព និងលំហាត់ប្រាណ\n"
        "• /exercise - លំហាត់ ១០ នាទីថ្ងៃនេះ\n"
        "• /balance - ទប់លំនឹង ការពារការដួល\n"
        "• /warning - សញ្ញាគ្រោះថ្នាក់\n"
        "• /sos - លេខទូរស័ព្ទបន្ទាន់ (119)\n"
        "• /bp - កត់ត្រាសម្ពាធឈាម\n"
        "• /water - ផឹកទឹកពេលក្ដៅ\n"
        "• /sleep - គេងលក់ស្រួល\n"
        "• /askhealth - សួរ AI អំពីសុខភាព\n\n"
        "💡 *អ្នកអាចវាយសំណួរ ឬផ្ញើសារជាសំឡេង (Voice Note) ឬរូបភាពមកជជែកជាមួយ AI បានគ្រប់ពេល!*"
    )
    try:
        bot.reply_to(message, help_text, parse_mode="Markdown")
    except Exception:
        bot.reply_to(message, help_text)


@registered_message_handler(commands=['register'])
def send_register(message):
    markup = InlineKeyboardMarkup()
    web_app = WebAppInfo(url=WEB_APP_URL)
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

        if data.startswith("nav:"):
            _handle_nav_callback(call, data)
            return

        if data.startswith("weather:"):
            _handle_weather_callback(call, chat, data)
            return

        if data.startswith("studio:"):
            _handle_studio_callback(call, chat, data)
            return

        if data.startswith("location:"):
            if data.startswith("location:next:"):
                _, _, token, page_text = data.split(":", 3)
                state = _location_result_pages.get(token)
                if (
                    state is None
                    or state["expires_at"] <= time.monotonic()
                    or state["owner_id"] != user_id
                ):
                    _location_result_pages.pop(token, None)
                    bot.answer_callback_query(call.id, "លទ្ធផលនេះផុតកំណត់ ឬមិនមែនជារបស់អ្នកទេ។", show_alert=True)
                    return
                last_page = max(0, (len(state["stations"]) - 1) // _LOCATION_PAGE_SIZE)
                page = min(max(0, int(page_text)), last_page)
                text, markup = _format_location_page(state["stations"], token, page, state.get("maps"))
                bot.answer_callback_query(call.id)
                try:
                    bot.edit_message_text(
                        text, chat.id, message.message_id, reply_markup=markup
                    )
                except Exception:
                    bot.send_message(chat.id, text, reply_markup=markup)
                return

            mode = data.split(":", 1)[1]
            if mode == "fuel":
                kind = "fuel"
            elif mode == "ev":
                kind = "ev"
            elif mode in ("nearby", "map", "province"):
                kind = None
            else:
                bot.answer_callback_query(call.id)
                return
            bot.answer_callback_query(call.id)
            _set_pending_location_mode(user_id, kind)
            if mode == "province":
                prompt = "សូមវាយឈ្មោះខេត្ត ឬទីតាំងជាភាសាខ្មែរ ឬអង់គ្លេស។"
            else:
                prompt = "ចែករំលែកទីតាំង ឬវាយឈ្មោះខេត្ត/ទីតាំង ដើម្បីស្វែងរក។"
            markup = _location_request_keyboard() if getattr(chat, "type", None) == "private" else None
            if getattr(chat, "type", None) != "private":
                prompt += " ដើម្បីប្រើទីតាំង GPS សូមបើក Private Chat ជាមួយ Bot។"
            bot.send_message(chat.id, prompt, reply_markup=markup)
            return

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

        if data in ("menu", "about"):
            # buttons on old /start messages
            _handle_nav_callback(call, "nav:main" if data == "menu" else "nav:about")
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
        "🛠️ Daily Care Admin\n"
        f"ស្ថានភាពសកល៖ {status_text}\n"
        f"សមាជិកបានជ្រើសរើសទទួល៖ {eligible_count}\n"
        f"ម៉ោងកម្ពុជា៖\n{messages.DAILYCARE_SCHEDULE_TEXT}\n"
        f"កំណត់ត្រាចុងក្រោយ៖ {last_run}\n"
        f"ថ្ងៃនេះ៖ ផ្ញើបាន {stats.get('sent', 0)} · បរាជ័យ {stats.get('failed', 0)} · បិទ/រកមិនឃើញ {stats.get('blocked', 0)}"
    )
    if not storage_ready:
        text += f"\n\n{messages.DAILYCARE_STORAGE_REQUIRED}"
    bot.reply_to(message, text, reply_markup=_dailycare_admin_keyboard())


# =========================================================
# Voice Note Handler (AI ស្តាប់សំឡេង Voice)
# =========================================================

@registered_message_handler(content_types=['voice', 'audio'])
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
        media = getattr(message, "voice", None) or getattr(message, "audio", None)
        if getattr(media, "file_size", 0) and media.file_size > 19 * 1024 * 1024:
            stop_typing_event.set()
            bot.reply_to(message, "📦 File សំឡេងធំពេក (លើស 19MB) — សូមផ្ញើខ្លីជាងនេះ 🙏")
            return
        file_info = bot.get_file(media.file_id)
        downloaded_voice = bot.download_file(file_info.file_path)
        mime = getattr(media, "mime_type", None) or "audio/ogg"

        # បញ្ជូនសំឡេងទៅកាន់ Gemini AI ឱ្យស្តាប់
        reply = async_runner.run_sync(ai_assistant.ask_ai_with_audio(
            user_id=user_id,
            audio_bytes=downloaded_voice,
            mime_type=mime,
            user_name=user_name
        ))
    except Exception as e:
        logger.error("Error handling voice message (%s)", type(e).__name__)
        reply = messages.AI_GENERIC_ERROR
    finally:
        stop_typing_event.set()

    answer = reply.split("\n\n", 1)[1] if reply.startswith("🎧") and "\n\n" in reply else reply
    ok = reply not in (getattr(messages, "AI_GENERIC_ERROR", None), getattr(messages, "AI_NOT_CONFIGURED", None))
    send_long_reply(message, reply, reply_markup=_voice.listen_markup(answer) if ok else None)


# =========================================================
# Photo Handler (AI វិភាគរូបភាព)
# =========================================================

@registered_message_handler(content_types=['photo'])
def handle_photo(message):
    photo_sizes = getattr(message, "photo", None) or []
    if not photo_sizes:
        bot.reply_to(message, "មិនអាចអានរូបភាពបានទេ។ សូមផ្ញើម្ដងទៀត។")
        return

    largest_photo = max(
        photo_sizes,
        key=lambda photo: (
            (getattr(photo, "width", 0) or 0) * (getattr(photo, "height", 0) or 0),
            getattr(photo, "file_size", 0) or 0,
        ),
    )
    max_image_bytes = ai_assistant.MAX_INLINE_IMAGE_BYTES
    declared_size = getattr(largest_photo, "file_size", None)
    if declared_size and declared_size > max_image_bytes:
        bot.reply_to(message, "រូបភាពមានទំហំធំពេក។ សូមផ្ញើរូបភាពតូចជាង 10 MB។")
        return

    user = getattr(message, "from_user", None)
    user_id = getattr(user, "id", None)
    if user_id is None:
        bot.reply_to(message, messages.AI_GENERIC_ERROR)
        return

    # Photo Studio: after /studio, or a photo captioned "studio"/"កែរូប"
    caption_text = (getattr(message, "caption", None) or "").strip().lower()
    wants_studio = _studio_take_waiting(user_id) or caption_text.startswith(_STUDIO_CAPTION_TRIGGERS)
    if wants_studio:
        if not _studio_enabled():
            bot.reply_to(message, _STUDIO_NOT_CONFIGURED_TEXT)
        else:
            _studio_accept_photo(message, user_id, largest_photo.file_id)
        return

    # Photo + a prompt in the caption (not a question) → make the image straight away
    raw_caption = (getattr(message, "caption", None) or "").strip()
    if _studio_enabled() and _caption_is_prompt(raw_caption):
        _studio_from_caption(message, user_id, largest_photo.file_id, raw_caption.lstrip("> ").strip())
        return

    stop_typing_event = threading.Event()
    typing_thread = threading.Thread(
        target=keep_typing,
        args=(message.chat.id, stop_typing_event),
        daemon=True
    )
    typing_thread.start()

    try:
        file_info = bot.get_file(largest_photo.file_id)
        remote_size = getattr(file_info, "file_size", None)
        if remote_size and remote_size > max_image_bytes:
            reply = "រូបភាពមានទំហំធំពេក។ សូមផ្ញើរូបភាពតូចជាង 10 MB។"
        else:
            downloaded_photo = bot.download_file(file_info.file_path)
            if not downloaded_photo:
                reply = "មិនអាចទាញយករូបភាពបានទេ។ សូមផ្ញើម្ដងទៀត។"
            elif len(downloaded_photo) > max_image_bytes:
                reply = "រូបភាពមានទំហំធំពេក។ សូមផ្ញើរូបភាពតូចជាង 10 MB។"
            else:
                reply = async_runner.run_sync(ai_assistant.ask_ai_with_image(
                    user_id=user_id,
                    image_bytes=downloaded_photo,
                    caption=getattr(message, "caption", None),
                    mime_type="image/jpeg",
                    user_name=getattr(user, "first_name", None)
                ))
    except Exception as exc:
        logger.error("Error handling photo message (%s)", type(exc).__name__)
        reply = messages.AI_GENERIC_ERROR
    finally:
        stop_typing_event.set()

    send_long_reply(message, reply)


# =========================================================
# Text Message Handler (AI ឆ្លើយតបអត្ថបទ)
# =========================================================

@registered_message_handler(content_types=['location'])
def handle_shared_location(message):
    if getattr(getattr(message, "chat", None), "type", None) != "private":
        bot.reply_to(message, "ដើម្បីរក្សាឯកជនភាព សូមចែករំលែកទីតាំងជាមួយ Bot ក្នុង Private Chat។")
        return
    location = message.location
    if location is None:
        bot.reply_to(message, "មិនអាចអានទីតាំងបានទេ។ សូមសាកល្បងម្ដងទៀត។")
        return
    _has_pending, kind = _take_pending_location_mode(
        getattr(getattr(message, "from_user", None), "id", None)
    )
    _send_location_results(
        message,
        kind=kind,
        lat=getattr(location, "latitude", None),
        lon=getattr(location, "longitude", None),
    )


def _is_bare_province_name(text: str) -> bool:
    cleaned = " ".join((text or "").replace("ខេត្ត", " ").replace("រាជធានី", " ").split()).strip(" .!?។៖")
    if not cleaned:
        return False
    folded = cleaned.casefold()
    return any(
        cleaned == khmer or folded == english.casefold()
        for khmer, english in location_service.PROVINCES.items()
    )


@registered_message_handler(func=lambda message: True)
def echo_all(message):
    user_id = message.from_user.id
    user_text = message.text
    if not user_text:
        return
    # 🕘 "QR ចូលធ្វើការ?", "ចុចម៉ោងចូលធ្វើការម៉េច?" → the check-in menu / buttons (not the payment QR)
    try:
        if attendance.work_intent(user_text):
            _attendance["intent_reply"](message)
            return
    except Exception as exc:
        logger.warning("work intent failed (%s)", type(exc).__name__)
    # 💳 "QR?", "បង់ម៉េច?", "សមាជិកប៉ុន្មាន?" → show the QR / prices right away (no need to know /pay)
    try:
        if _subscription.text_intent(message):
            return
    except Exception as exc:
        logger.warning("pay intent failed (%s)", type(exc).__name__)
    # 🔒 non-members: plain AI chat only (set by the subscription gate) — no weather/location extras
    text_only = getattr(message, "_cooler_text_only", False)
    if not text_only and weather_service.has_weather_intent(user_text):
        weather_slug = weather_service.find_province(user_text)
        if weather_slug:
            _send_weather(message, weather_slug)
        else:
            _send_weather_menu(message)
        return
    pending, pending_kind = _take_pending_location_mode(user_id)
    if user_text.strip() == "🔎 ស្វែងរកតាមខេត្ត":
        _location_prompt(message, pending_kind)
        return
    parsed = location_service.normalize_query(user_text)
    lowered = user_text.casefold()
    location_intent = (
        parsed["kind"] is not None
        or pending
        # A message that is ONLY a province name (e.g. the user typed "កំពត" after
        # "🔎 ស្វែងរកតាមខេត្ត" and the pending state was lost on restart) is a
        # location search. A province mentioned inside a normal sentence goes to AI.
        or _is_bare_province_name(user_text)
        or any(
            term in lowered
            for term in (
                "ជិតខ្ញុំ", "near me", "nearby", "near ", "មើលផែនទី",
                "ស្វែងរកជិត", "fuel station", "gas station", "charging station",
            )
        )
    )
    if location_intent and not text_only:
        _send_location_results(
            message,
            query=user_text,
            kind=pending_kind if pending else None,
        )
        return

    user_name = message.from_user.first_name

    stop_typing_event = threading.Event()
    typing_thread = threading.Thread(
        target=keep_typing, 
        args=(message.chat.id, stop_typing_event), 
        daemon=True
    )
    typing_thread.start()

    try:
        reply = async_runner.run_sync(ai_assistant.ask_ai(
            user_id=user_id, 
            user_message=user_text, 
            user_name=user_name
        ))
    except Exception as e:
        logger.error(f"Error handling AI message: {e}")
        reply = messages.AI_GENERIC_ERROR
    finally:
        stop_typing_event.set()

    send_long_reply(message, reply)
    if getattr(message.chat, "type", None) == "private" and reply != messages.AI_GENERIC_ERROR:
        _extras.observe(user_id, user_text)   # 🧠 remember harmless facts the user shares


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
    _subscription.start_loop()
    _tpnews["start_loop"]()
    _messenger["start_server"]()
    _khqr.start_loop()
    _calendar.start_loop()
    _buddhist.start_loop()
    _daily.start_loop()
    _hub.setup_menu_button()
    try:  # warn the owner once per start if the database would be wiped by the next deploy
        _db_ok, _db_text = ops_monitor.db_storage_status(config)
        if not _db_ok and getattr(config, "OWNER_ID", 0):
            bot.send_message(config.OWNER_ID, "⚠️ <b>Database មិននៅលើ Volume</b>\n" + _db_text, parse_mode="HTML")
    except Exception as exc:
        logger.warning("storage check failed (%s)", type(exc).__name__)
    _start_daily_backup()

    logger.info("Bot is starting polling 24/7...")
    try:
        bot.infinity_polling(
            # keep messages sent while the bot was restarting (payment receipts!) unless told otherwise
            skip_pending=os.getenv("SKIP_PENDING_UPDATES", "false").strip().lower() in ("1", "true", "yes"),
            timeout=20, long_polling_timeout=20,
            # my_chat_member = bot added to / removed from a group (for /groups)
            allowed_updates=["message", "edited_message", "callback_query", "my_chat_member", "channel_post"],
        )
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot process interrupted. Shutting down gracefully...")
    finally:
        scheduler.shutdown_scheduler(dailycare_scheduler)
        async_runner.run_sync(ai_assistant.close_shared_http_client(), timeout=10)
        async_runner.shutdown_loop()
        logger.info("Cleaned up network connections.")
