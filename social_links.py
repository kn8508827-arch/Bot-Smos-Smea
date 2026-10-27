"""/follow — Vid Cooler's official social accounts (TikTok, YouTube, Facebook, Telegram).

Each link can be changed on Railway with a variable; an empty link hides its button:
  SOCIAL_TIKTOK_URL, SOCIAL_YOUTUBE_URL (falls back to MUSIC_YOUTUBE_URL),
  SOCIAL_FACEBOOK_URL, SOCIAL_TELEGRAM_URL
"""
import os
from urllib.parse import quote

SOCIALS = (
    # (key, button label, env var, default)
    ("tiktok", "🎵 TikTok @vidcooler52", "SOCIAL_TIKTOK_URL", "https://www.tiktok.com/@vidcooler52"),
    ("youtube", "▶️ YouTube", "SOCIAL_YOUTUBE_URL", ""),
    ("facebook", "📘 Facebook", "SOCIAL_FACEBOOK_URL", ""),
    ("telegram", "💬 Telegram @VidCooler52", "SOCIAL_TELEGRAM_URL", "https://t.me/VidCooler52"),
)

FOLLOW_TEXT = (
    "📲 <b>តាមដាន & ទាក់ទង Vid Cooler</b> 🎤🇰🇭\n\n"
    "ចុច Follow / Subscribe ដើម្បីមិនឲ្យខកខានបទថ្មី វីដេអូថ្មី និង Live ពី Vid Cooler 🔥\n"
    "💬 ចង់ទាក់ទង Vid Cooler ផ្ទាល់ ចុចប៊ូតុង Telegram\n\n"
    "🙏 អរគុណដែលគាំទ្រ Team ស្មោះស្នេហ៍!\n"
    "📤 Share Bot នេះទៅមិត្តភក្តិផង ❤️"
)


def _url(env, default):
    value = os.getenv(env, "").strip()
    if not value and env == "SOCIAL_YOUTUBE_URL":
        value = os.getenv("MUSIC_YOUTUBE_URL", "").strip()
    value = value or default
    return value if value.startswith("https://") else ""


def links():
    """[(key, label, url)] for the links that are set."""
    return [(key, label, url) for key, label, env, default in SOCIALS if (url := _url(env, default))]


def register(bot, config=None):
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    def share_url():
        try:
            username = bot.get_me().username
        except Exception:
            username = None
        if not username:
            return None
        text = "😎 សាក Cooler Bot — AI ជំនួយការរបស់ Vid Cooler 🎤🇰🇭 ហ្គេម Quiz Fan Card និងច្រើនទៀត!"
        return f"https://t.me/share/url?url={quote('https://t.me/' + username)}&text={quote(text)}"

    def follow_markup(with_home=True):
        m = InlineKeyboardMarkup()
        found = links()
        items = [InlineKeyboardButton(label, url=url) for key, label, url in found if key != "telegram"]
        for i in range(0, len(items), 2):
            m.row(*items[i:i + 2])
        for key, label, url in found:  # the contact button gets its own full-width row
            if key == "telegram":
                m.row(InlineKeyboardButton(label, url=url))
        share = share_url()
        if share:
            m.row(InlineKeyboardButton("📤 Share Bot ទៅមិត្តភក្តិ", url=share))
        if with_home:
            m.row(InlineKeyboardButton("🏠 ម៉ឺនុយមេ", callback_data="nav:main"))
        return m

    @bot.message_handler(commands=["follow", "social", "tiktok", "contact", "vidcooler"])
    def follow(message):
        bot.reply_to(message, FOLLOW_TEXT, reply_markup=follow_markup(), parse_mode="HTML",
                     disable_web_page_preview=True)

    @bot.callback_query_handler(func=lambda call: (call.data or "") == "follow:menu")
    def follow_cb(call):
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        message = getattr(call, "message", None)
        if message is None:
            return
        try:
            bot.edit_message_text(FOLLOW_TEXT, message.chat.id, message.message_id,
                                  reply_markup=follow_markup(), parse_mode="HTML",
                                  disable_web_page_preview=True)
        except Exception as exc:
            if "message is not modified" not in str(exc):
                bot.send_message(message.chat.id, FOLLOW_TEXT, reply_markup=follow_markup(), parse_mode="HTML")

    return follow_markup
