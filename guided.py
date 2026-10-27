"""🧭 Guided commands: typing a command on its own never leaves people stuck.

When someone sends /imagine, /song, /translate … with nothing after it, they get a clear card:
what the feature does, 3 short steps, and two example buttons that run it right away. The bot then
waits 10 minutes: whatever they send next (text — or a photo for /translate and /homework) is handed
to that feature, so they don't have to retype the command. /cancel (or ❌) stops waiting.

Registered right after the membership gate, so non-members still see the join message.
"""
import copy
import html
import logging
import time

logger = logging.getLogger(__name__)
WAIT_SECONDS = 600

# cmd: (title, what it does, [steps], [(button label, example argument)], accepts photo)
GUIDES = {
    "imagine": ("🎨 គូររូបពីអក្សរ",
                "សរសេររូបដែលចង់បាន — AI គូរជារូបភាពថ្មីឲ្យភ្លាម",
                ["សរសេរពិពណ៌នារូប (មនុស្ស/សត្វ · កន្លែង · ពេលវេលា · ស្ទាយ)", "ផ្ញើមកខ្ញុំ", "រង់ចាំ ១០–៣០ វិនាទី → ទទួលរូប 🖼️"],
                [("🐱 ឆ្មាពាក់វ៉ែនតា នៅអង្គរវត្ត", "ឆ្មាពាក់វ៉ែនតាខ្មៅ រាំ Hip-hop នៅអង្គរវត្ត ពេលថ្ងៃលិច បែប 3D Pixar"),
                 ("🏙️ ភ្នំពេញ ឆ្នាំ 2050", "ទីក្រុងភ្នំពេញឆ្នាំ 2050 អគារភ្លឺ Neon ឡានហោះ ពេលយប់ ភ្លៀងស្រិចៗ")], False),
    "song": ("🎧 Bot ច្រៀង & Rap",
             "ប្រាប់ប្រធានបទ — AI សរសេរទំនុក ហើយច្រៀងជាមួយភ្លេងពេញ (~២ នាទី)",
             ["សរសេរប្រធានបទបទចម្រៀង", "ជ្រើសស្ទីល (Rap · Pop · Ballad …)", "រង់ចាំ ១–២ នាទី → ស្ដាប់បទ 🎶"],
             [("💗 ស្រលាញ់ម៉ាក់", "ស្រលាញ់ម៉ាក់"), ("🚗 Team ដើរលេងកំពត", "Team ស្មោះស្នេហ៍ដើរលេងកំពត")], False),
    "beat": ("🥁 បង្កើត Beat",
             "ភ្លេងឥតសំឡេងច្រៀង ៣០ វិនាទី សម្រាប់ Rap ឬវីដេអូ",
             ["សរសេរស្ទីលភ្លេង", "ផ្ញើមក", "ទាញយក Beat 🎧"],
             [("🌙 Trap ងងឹត", "trap ងងឹត 140 BPM"), ("☀️ Chill ស្រាល", "lofi chill ស្រាលៗ ពេលព្រឹក")], False),
    "translate": ("🌐 បកប្រែ",
                  "បកប្រែអក្សរ ឬរូប (ម៉ឺនុយ ផ្លាក ឯកសារ) ជាភាសាខ្មែរ/អង់គ្លេស/ចិន …",
                  ["សរសេរអក្សរ ឬ ថតរូបដែលមានអក្សរ", "ផ្ញើមកខ្ញុំ", "ទទួលការបកប្រែភ្លាម ✅"],
                  [("🇬🇧 Hello, how much?", "Hello, how much is this?"), ("🇨🇳 你好，多少钱？", "你好，这个多少钱？")], True),
    "homework": ("📚 ជួយលំហាត់",
                 "ពន្យល់លំហាត់ជាជំហានៗ (គណិត រូប គីមី ភាសា …) — មិនមែនផ្ដល់តែចម្លើយទេ",
                 ["ថតរូបលំហាត់ឲ្យច្បាស់ ឬ សរសេរលំហាត់", "ផ្ញើមកខ្ញុំ", "ទទួលការពន្យល់មួយជំហានៗ 📖"],
                 [("➗ 2x + 5 = 15", "2x + 5 = 15"), ("📐 ក្រឡាផ្ទៃរង្វង់ r=7", "រកក្រឡាផ្ទៃរង្វង់ដែលមានកាំ 7 cm")], True),
    "cash": ("💵 កត់ចំណាយ / ចំណូល",
             "កត់លុយចូល-ចេញរាល់ថ្ងៃ ហើយមើលសរុបប្រចាំខែ (/report)",
             ["សរសេរ ចាយ/ចូល · ចំនួន · រៀល ឬ $ · ចំណាំ", "ផ្ញើមក", "មើលសរុប៖ /report 📊"],
             [("☕ ចាយ 5000 រៀល កាហ្វេ", "ចាយ 5000 រៀល កាហ្វេ"), ("💰 ចូល 50$ ប្រាក់ខែ", "ចូល 50$ ប្រាក់ខែ")], False),
    "remind": ("⏰ រំលឹក",
               "ប្រាប់ពេល និងរឿង — Bot ផ្ញើសាររំលឹកដល់ពេល",
               ["សរសេរ ពេល + រឿង (ភាសាធម្មតា)", "ផ្ញើមក", "ដល់ពេល Bot រំលឹក 🔔 · មើលបញ្ជី /reminders"],
               [("🤝 ស្អែកម៉ោង ២ ណាត់ភ្ញៀវ", "ស្អែកម៉ោង ២ រសៀល ណាត់ជួបភ្ញៀវ"), ("💊 ៣០ នាទីទៀត លេបថ្នាំ", "៣០ នាទីទៀត លេបថ្នាំ")], False),
}


def guide_text(cmd):
    title, what, steps, _ex, photo = GUIDES[cmd]
    nums = ["1️⃣", "2️⃣", "3️⃣"]
    lines = [f"<b>{title}</b>", what, ""]
    lines += [f"{nums[i]} {html.escape(s)}" for i, s in enumerate(steps)]
    lines += ["", "👇 <b>ផ្ញើមកខ្ញុំឥឡូវបានភ្លាម</b>" + (" (អក្សរ ឬរូប)" if photo else "") + " — ឬចុចឧទាហរណ៍សាកមើល",
              f"<i>💡 លើកក្រោយវាយមួយដង៖ /{cmd} អត្ថបទ</i>"]
    return "\n".join(lines)


def register(bot, config):
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup
    waiting = {}          # user_id -> (cmd, expires)

    def bare_cmd(message):
        text = (getattr(message, "text", None) or "").strip()
        if not text.startswith("/") or getattr(message, "reply_to_message", None) is not None:
            return None
        parts = text.split()
        if len(parts) != 1:
            return None
        cmd = parts[0][1:].split("@")[0].lower()
        return cmd if cmd in GUIDES else None

    def markup(cmd):
        m = InlineKeyboardMarkup()
        for i, (label, _arg) in enumerate(GUIDES[cmd][3]):
            m.row(InlineKeyboardButton("🧪 " + label, callback_data=f"gd:{cmd}:{i}"))
        m.row(InlineKeyboardButton("❌ បោះបង់", callback_data="gd:x"))
        return m

    def forget_on_other_command(message):
        """Any other command cancels the wait (never handles the message itself)."""
        text = getattr(message, "text", None) or ""
        uid = getattr(getattr(message, "from_user", None), "id", None)
        if text.startswith("/") and uid in waiting and bare_cmd(message) is None:
            waiting.pop(uid, None)
        return False

    @bot.message_handler(func=forget_on_other_command)
    def _never(message):   # pragma: no cover — func always returns False
        pass

    @bot.message_handler(func=lambda m: bare_cmd(m) is not None)
    def guide(message):
        cmd = bare_cmd(message)
        text = guide_text(cmd)
        if getattr(message.chat, "type", "") == "private":
            waiting[message.from_user.id] = (cmd, time.monotonic() + WAIT_SECONDS)
        else:   # in groups other people talk too: don't grab the next message, ask for the command with text
            text = text.replace("👇 <b>ផ្ញើមកខ្ញុំឥឡូវបានភ្លាម</b>", f"👇 <b>ក្នុង Group វាយ</b> <code>/{cmd} អត្ថបទ</code>")
        bot.reply_to(message, text, reply_markup=markup(cmd), parse_mode="HTML")

    def run(base, user, cmd, arg=None, photo_msg=None):
        """Hand the request to the real feature (goes through the membership gate as usual)."""
        fake = copy.copy(photo_msg if photo_msg is not None else base)
        fake.from_user = user
        if photo_msg is not None:
            fake.caption = f"/{cmd}"
            fake.text = None
        else:
            fake.text = f"/{cmd} {arg}"
            fake.entities = None
            fake.reply_to_message = None
        try:
            bot.process_new_messages([fake])
        except Exception as exc:
            logger.warning("guided run failed (%s)", type(exc).__name__)

    def wants(message):
        hit = waiting.get(getattr(getattr(message, "from_user", None), "id", None))
        if not hit or hit[1] < time.monotonic():
            return False
        text = getattr(message, "text", None)
        if text is not None:
            return not text.startswith("/")
        return bool(getattr(message, "photo", None)) and GUIDES[hit[0]][4] and not getattr(message, "caption", None)

    @bot.message_handler(func=wants, content_types=["text", "photo"])
    def follow_up(message):
        cmd, _exp = waiting.pop(message.from_user.id)
        if getattr(message, "photo", None):
            run(message, message.from_user, cmd, photo_msg=message)
        else:
            run(message, message.from_user, cmd, arg=message.text.strip()[:1500])

    @bot.callback_query_handler(func=lambda c: (c.data or "").startswith("gd:"))
    def example(call):
        bot.answer_callback_query(call.id)
        parts = call.data.split(":")
        if parts[1] == "x":
            waiting.pop(call.from_user.id, None)
            bot.send_message(call.message.chat.id, "✅ បោះបង់ហើយ")
            return
        cmd = parts[1]
        if cmd not in GUIDES or len(parts) != 3 or not parts[2].isdigit() or int(parts[2]) >= len(GUIDES[cmd][3]):
            return
        waiting.pop(call.from_user.id, None)
        label, arg = GUIDES[cmd][3][int(parts[2])]
        bot.send_message(call.message.chat.id, f"🧪 សាក៖ <code>/{cmd} {html.escape(arg)}</code>", parse_mode="HTML")
        run(call.message, call.from_user, cmd, arg=arg)

    return waiting
