"""Fan features for Cooler Bot (Vid Cooler, rapper in Cambodia).

  /games  — Vid Cooler's games (Arcade hub + 4 games) as Telegram Mini App buttons
  /music  — Vid Cooler's official music channel (MUSIC_YOUTUBE_URL)
  /rap    — AI writes a short, ORIGINAL Khmer rap verse from the fan's words
  /movie  — AI recommends dramas/movies and where to watch them OFFICIALLY

Only legal content is shared: Vid Cooler's own work, official platforms, and new
text written by the AI. No links to full movies uploaded without rights.
"""
import html
import logging
import os
import threading
import time
from types import SimpleNamespace

logger = logging.getLogger(__name__)

GAMES = (
    # (key, button label, env var, default URL)
    ("arcade", "🕹️ Cooler Arcade — ៦ ហ្គេម (ថ្មី!)", "GAME_ARCADE_URL", "https://kn8508827-arch.github.io/Blue-Gold-Rush/arcade/"),
    ("fly", "🚀 Cooler Fly 3D", "GAME_COOLER_FLY_URL", "https://kn8508827-arch.github.io/Blue-Gold-Rush/cooler-fly/"),
    ("bgr", "⚡ Blue Gold Rush", "GAME_BLUE_GOLD_RUSH_URL", "https://kn8508827-arch.github.io/Blue-Gold-Rush/"),
    ("logo", "🧩 Logo Crush 3D", "GAME_LOGO_CRUSH_URL", "https://logo-crush-3d-production.up.railway.app/"),
    ("spin", "🎡 Spin & Drink", "GAME_SPIN_AND_DRINK_URL", "https://spin-and-drink-production.up.railway.app/"),
)

AI_DAILY_LIMIT = int(os.getenv("FAN_AI_DAILY_LIMIT", "5"))  # /rap + /movie per fan per day
_MAX_INPUT = 120

RAP_PROMPT = (
    "You are Cooler Bot, the AI sidekick of Vid Cooler, a Cambodian rapper. Write ONE short, "
    "ORIGINAL rap verse in natural modern Khmer (8 short lines max) inspired by these words from a fan: "
    "\"{words}\". Make it fun, positive and rhythmic with end rhymes, suitable for all ages: no insults, "
    "no hate, no sexual content, no drugs or violence. Never copy or imitate the lyrics of any existing "
    "song or artist — the verse must be brand new. You may mention 'Cooler' or 'Team ស្មោះស្នេហ៍' once. "
    "Return only the verse, no title, no explanation, no markdown."
)

NAME_PROMPT = (
    "You are Cooler Bot. Invent 3 fun, original rapper stage names for a Cambodian fan whose vibe is: "
    "\"{vibe}\". Mix Khmer and English like Cambodian hip-hop culture, keep them positive and suitable "
    "for all ages, and never reuse the name of a real, existing artist. For each name give one short Khmer "
    "line explaining it. Plain text, no markdown, numbered 1-3."
)

MOVIE_PROMPT = (
    "You are Cooler Bot. Recommend exactly 3 movies or dramas (Chinese dramas are popular with our fans) "
    "matching this request: \"{taste}\". For each give: the title (original + Khmer if known), year, one "
    "line in Khmer on why it fits, and WHERE TO WATCH IT LEGALLY (official platforms such as iQIYI, WeTV, "
    "Youku, Tencent Video, Netflix, Viu, an official YouTube channel of the studio, or a Cambodian TV "
    "channel that bought the rights). Do NOT give any URL, and never suggest free full-movie, download or "
    "re-upload sites. If unsure of the platform, say 'ពិនិត្យលើកម្មវិធីផ្លូវការ'. Answer in Khmer, "
    "plain text, short, no markdown."
)


class _DailyLimit:
    def __init__(self, limit):
        self.limit = limit
        self.used = {}
        self.lock = threading.Lock()

    def take(self, user_id, unlimited=False):
        if unlimited:
            return True
        today = time.strftime("%Y-%m-%d")
        with self.lock:
            for key in [k for k in self.used if k[1] != today]:
                self.used.pop(key, None)
            n = self.used.get((user_id, today), 0)
            if n >= self.limit:
                return False
            self.used[(user_id, today)] = n + 1
            return True


def game_links():
    return [(key, label, os.getenv(env, default).strip() or default) for key, label, env, default in GAMES]


def register(bot, config, ask_gemini):
    """ask_gemini(prompt) -> str. Register before the catch-all AI handler."""
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

    limits = _DailyLimit(AI_DAILY_LIMIT)
    waiting = {}  # user_id -> ("rap"|"movie", expires_at)

    def privileged(user_id):
        return user_id is not None and (
            user_id == getattr(config, "OWNER_ID", 0) or user_id in getattr(config, "ADMIN_IDS", [])
        )

    def games_markup(private):
        markup = InlineKeyboardMarkup()
        for _key, label, url in game_links():
            if private:  # Mini App buttons only work in private chats
                markup.row(InlineKeyboardButton(label, web_app=WebAppInfo(url=url)))
            else:
                markup.row(InlineKeyboardButton(label, url=url))
        return markup

    def is_private(message):
        return getattr(getattr(message, "chat", None), "type", None) == "private"

    @bot.message_handler(commands=["games", "game"])
    def games(message):
        bot.reply_to(
            message,
            "🎮 <b>ហ្គេមរបស់ Vid Cooler</b> — ១០+ ហ្គេម\n\n"
            "🕹️ <b>Cooler Arcade</b> — 2048 · ពស់ · ផ្គូផ្គងកាត · វាយ Cooler · XO · បំបែកឥដ្ឋ (ថ្មី!)\n"
            "🚀 <b>Cooler Fly 3D</b> — ហោះកាត់ភ្នំពេញ 2050 អង្គរ Hologram ដល់អវកាស 🌌\n"
            "⚡ <b>Blue Gold Rush</b> — រត់ ប្រមូលកាក់ ជិះ Sky Bike 🚲\n"
            "🧩 <b>Logo Crush 3D</b> — ផ្គូផ្គង logo ៣ ឡើងកម្រិត\n"
            "🎡 <b>Spin &amp; Drink</b> — កង់សំណាងលេងជាក្រុម\n\n"
            "🎲 + Game Center ក្នុង Telegram: /play\n\n"
            "ចុចប៊ូតុងខាងក្រោមដើម្បីលេងភ្លាម 👇",
            reply_markup=games_markup(is_private(message)),
            parse_mode="HTML",
        )

    @bot.message_handler(commands=["music"])
    def music(message):
        url = os.getenv("MUSIC_YOUTUBE_URL", "").strip()
        if not url:
            bot.reply_to(message, "🎵 Channel តន្ត្រីផ្លូវការរបស់ Vid Cooler នឹងមកដល់ឆាប់ៗនេះ! 🎤")
            return
        markup = InlineKeyboardMarkup()
        markup.row(InlineKeyboardButton("▶️ ស្ដាប់បទ Vid Cooler", url=url))
        bot.reply_to(
            message,
            "🎵 <b>បទរបស់ Vid Cooler</b>\nស្ដាប់ Subscribe ហើយ Share ទៅមិត្តភក្តិផង 🔥",
            reply_markup=markup,
            parse_mode="HTML",
        )

    def ask(message, kind):
        waiting[message.from_user.id] = (kind, time.monotonic() + 300)
        if kind == "name":
            text = "🗣️ <b>Rapper Name Generator</b>\nប្រាប់ខ្ញុំពីខ្លួនអ្នកបន្តិច (ឧ. <i>ចូលចិត្តបាល់ទាត់ ម៉ូតូ ញ៉ាំមី</i>) ខ្ញុំបង្កើតឈ្មោះ rapper ៣ ឲ្យ 😎"
        elif kind == "rap":
            text = "🎤 <b>AI Rap ជាមួយ Cooler</b>\nផ្ញើពាក្យ ១-៣ ម៉ាត់មក (ឧ. <i>មិត្តភាព ភ្នំពេញ សុបិន</i>) ខ្ញុំនឹងសរសេរ verse ថ្មីឲ្យ 🔥"
        else:
            text = "🎬 <b>ណែនាំរឿង</b>\nប្រាប់ខ្ញុំថាចង់មើលរឿងបែបណា (ឧ. <i>រឿងចិន ស្នេហា + ប្រយុទ្ធ</i>) ខ្ញុំណែនាំ ៣ រឿង និងកន្លែងមើលផ្លូវការ 📺"
        bot.reply_to(message, text, parse_mode="HTML")

    def run(message, kind, text):
        user_id = message.from_user.id
        text = " ".join(text.split())[:_MAX_INPUT]
        if not text:
            ask(message, kind)
            return
        if not getattr(config, "GEMINI_API_KEY", ""):
            bot.reply_to(message, "AI មិនទាន់បើកដំណើរការនៅឡើយទេ។")
            return
        if not limits.take(user_id, privileged(user_id)):
            bot.reply_to(message, f"អ្នកប្រើ /rap /movie /myname អស់ {AI_DAILY_LIMIT} ដងសម្រាប់ថ្ងៃនេះហើយ។ ត្រឡប់មកថ្ងៃស្អែក 🙏")
            return
        try:
            bot.send_chat_action(message.chat.id, "typing")
        except Exception:
            pass
        prompt = {"rap": RAP_PROMPT.format(words=text), "movie": MOVIE_PROMPT.format(taste=text),
                  "name": NAME_PROMPT.format(vibe=text)}[kind]
        try:
            answer = (ask_gemini(prompt) or "").strip()
        except Exception as exc:
            logger.warning("fan %s failed (%s)", kind, type(exc).__name__)
            answer = ""
        if not answer:
            bot.reply_to(message, "សុំទោស AI មិនអាចឆ្លើយបានទេឥឡូវនេះ។ សូមសាកម្ដងទៀត។")
            return
        header = {"rap": "🎤 <b>Cooler Rap</b>\n\n", "movie": "🎬 <b>រឿងណែនាំ</b>\n\n",
                  "name": "🗣️ <b>ឈ្មោះ Rapper របស់អ្នក</b>\n\n"}[kind]
        footer = {"rap": "\n\n— ✍️ សរសេរថ្មីដោយ Cooler AI · /rap ម្ដងទៀត",
                  "movie": "\n\n📺 សូមមើលតាមកម្មវិធីផ្លូវការ ដើម្បីគាំទ្រអ្នកផលិត 🙏",
                  "name": "\n\n🎤 យកឈ្មោះមួយទៅសាក /rap ហើយ Share ទៅមិត្តផង!"}[kind]
        bot.reply_to(message, header + html.escape(answer[:3500]) + footer, parse_mode="HTML")

    @bot.message_handler(commands=["rap"])
    def rap(message):
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) > 1:
            run(message, "rap", parts[1])
        else:
            ask(message, "rap")

    @bot.message_handler(commands=["myname", "rapname"])
    def myname(message):
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) > 1:
            run(message, "name", parts[1])
        else:
            ask(message, "name")

    @bot.message_handler(commands=["movie", "movies"])
    def movie(message):
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) > 1:
            run(message, "movie", parts[1])
        else:
            ask(message, "movie")

    def is_waiting(message):
        user = getattr(message, "from_user", None)
        if user is None or not getattr(message, "text", None) or message.text.startswith("/"):
            return False
        state = waiting.get(user.id)
        return bool(state and state[1] > time.monotonic())

    @bot.message_handler(func=is_waiting, content_types=["text"])
    def follow_up(message):
        kind, _ = waiting.pop(message.from_user.id)
        run(message, kind, message.text)

    return SimpleNamespace(games_markup=games_markup, limits=limits, waiting=waiting, ask=ask)
