"""Family tips (Khmer): raising kids aged 1-6 to listen, and training dogs and cats.

  /parent   tips by age (1-2, 3-4, 5-6), today's tip, apps, ask AI
  /pet      dog and cat training basics, today's tip, apps, ask AI
  /askfamily  AI answer about parenting or pet training (5 per day)

Positive methods only: praise, routines, rewards — never hitting, shouting or
frightening a child or an animal. Medical or safety issues go to a doctor or vet.
"""
import html
import logging
import os
import threading
import time
from datetime import date
from types import SimpleNamespace

logger = logging.getLogger(__name__)

AI_DAILY_LIMIT = int(os.getenv("FAMILY_AI_DAILY_LIMIT", "5"))

KIDS = {
    "k12": ("👶 ១-២ ឆ្នាំ", (
        "🗣️ និយាយខ្លីៗ ១-២ ពាក្យ ហើយចង្អុលបង្ហាញ ឧ. «ដាក់ — នេះ» ក្មេងតូចយល់សកម្មភាពច្រើនជាងពាក្យ",
        "🙌 សរសើរភ្លាមៗ ពេលកូនធ្វើល្អ ឧ. «ពូកែណាស់ កូនឲ្យនំម៉ាក់!» ការសរសើរធ្វើឲ្យកូនចង់ធ្វើម្ដងទៀត",
        "🔄 ប្ដូរការចាប់អារម្មណ៍ ពេលកូនចាប់របស់គ្រោះថ្នាក់ យករបស់ក្មេងលេងមកជំនួស ជាជាងស្រែក «កុំ!»",
        "⏰ ធ្វើទម្លាប់ដដែលៗរាល់ថ្ងៃ ម៉ោងញ៉ាំ ម៉ោងងូតទឹក ម៉ោងគេង — ក្មេងមានអារម្មណ៍សុវត្ថិភាព ហើយយំតិច",
        "📵 មិនគួរឲ្យក្មេងក្រោម ២ ឆ្នាំមើលទូរស័ព្ទទេ (WHO) ជំនួសដោយអានសៀវភៅរូបភាព ច្រៀង និងលេងជាមួយគ្នា",
        "🤗 ពេលកូនយំខ្លាំង ឱបលួងសិន រួចទើបនិយាយ — ក្មេងតូចមិនទាន់ចេះគ្រប់គ្រងអារម្មណ៍ខ្លួនឯងទេ",
    )),
    "k34": ("🧒 ៣-៤ ឆ្នាំ", (
        "✌️ ផ្ដល់ជម្រើស ២ ឧ. «ចង់ពាក់អាវក្រហម ឬខៀវ?» កូនមានអារម្មណ៍ថាបានសម្រេចចិត្ត ហើយប្រឆាំងតិច",
        "👀 ចុះអង្គុយឲ្យស្មើភ្នែកកូន ហៅឈ្មោះ ហើយនិយាយទន់ៗ ជាជាងស្រែកពីឆ្ងាយ",
        "✅ ប្រាប់អ្វីដែលត្រូវធ្វើ មិនមែនតែ «កុំ» ឧ. «ដើរយឺតៗ» ជំនួស «កុំរត់!»",
        "⏳ ព្រមានមុន ៥ នាទី ឧ. «៥ នាទីទៀតយើងឈប់លេង ទៅងូតទឹក» ក្មេងមិនចូលចិត្តការផ្លាស់ប្ដូរភ្លាមៗ",
        "😤 ពេលកូនខឹង ជួយដាក់ឈ្មោះអារម្មណ៍ ឧ. «កូនខឹងព្រោះចង់លេងទៀត ម៉ាក់យល់» រួចណែនាំឲ្យដកដង្ហើមវែងៗ",
        "⭐ តារាងផ្កាយ៖ ធ្វើកិច្ចការល្អបានផ្កាយមួយ ប្រមូលបាន ៥ ផ្កាយ ទទួលរង្វាន់តូចមួយ ឬដើរលេងជាមួយម៉ែឪ",
    )),
    "k56": ("👦 ៥-៦ ឆ្នាំ", (
        "📋 ធ្វើច្បាប់គ្រួសារ ៣-៤ ខ ជាមួយកូន ឧ. «ទុករបស់ក្មេងលេងវិញ» ហើយបិទលើជញ្ជាំងជារូបភាព",
        "🧹 ឲ្យកិច្ចការតូចៗ ដូចជារៀបស្បែកជើង ដាក់ចានក្នុងលិច ហើយអរគុណកូន — កូនរៀនទទួលខុសត្រូវ",
        "🔗 លទ្ធផលធម្មជាតិ ឧ. «បើមិនទុករបស់ក្មេងលេង ថ្ងៃស្អែកវានៅក្នុងប្រអប់ម៉ាក់» ហើយធ្វើតាមដែលបាននិយាយ",
        "💬 សួរកូនពីថ្ងៃនេះរាល់ល្ងាច ហើយស្ដាប់ឲ្យចប់ — កូនដែលត្រូវគេស្ដាប់ ក៏ចេះស្ដាប់គេវិញដែរ",
        "🙏 បង្រៀនពាក្យ «អរគុណ» «សុំទោស» «សូម» ហើយម៉ែឪប្រើពាក្យទាំងនេះជាមួយកូនផងដែរ",
        "⏱️ អេក្រង់មិនលើស ១ ម៉ោង/ថ្ងៃ មើលជាមួយកូន ហើយបិទទូរស័ព្ទ ១ ម៉ោងមុនគេង",
    )),
}

PETS = {
    "dog": ("🐶 ឆ្កែ", (
        "🍖 ប្រើរង្វាន់៖ ពេលឆ្កែធ្វើត្រូវ ឲ្យនំតូចមួយ និងសរសើរក្នុងរយៈពេល ១-២ វិនាទី ទើបវាយល់ថាធ្វើត្រូវ",
        "🪑 «អង្គុយ»៖ កាន់នំពីលើច្រមុះ រួចលើកបន្តិចទៅក្រោយ ពេលគូទប៉ះដី និយាយ «អង្គុយ» ហើយឲ្យនំ",
        "🏃 «មក»៖ ហៅឈ្មោះ + «មក» ដោយសំឡេងរីករាយ ពេលវាមកដល់ ឲ្យនំ — កុំហៅមកដើម្បីស្ដីឲ្យ",
        "⏱️ បង្ហាត់ម្ដង ៥-១០ នាទី ២-៣ ដងក្នុងមួយថ្ងៃ ហើយបញ្ចប់ដោយអ្វីដែលវាធ្វើបានល្អ",
        "🚽 បង្ហាត់បន្ទោរបង់៖ ដាក់ទៅកន្លែងដដែលក្រោយញ៉ាំ ក្រោយគេង និងក្រោយលេង ហើយសរសើរពេលវាធ្វើត្រូវកន្លែង",
        "🚫 កុំវាយ ឬស្រែកដាក់ — ឆ្កែនឹងខ្លាច ហើយអាចខាំ។ បើវាធ្វើខុស និយាយ «ទេ» ស្ងាត់ៗ ហើយបង្ហាញអ្វីដែលត្រូវធ្វើ",
        "💉 ចាក់វ៉ាក់សាំងឆ្កែឆ្កួតឲ្យគ្រប់ ហើយបើឆ្កែខាំមនុស្ស ត្រូវលាងរបួសនឹងសាប៊ូ ហើយទៅពេទ្យភ្លាម",
    )),
    "cat": ("🐱 ឆ្មា", (
        "🔔 ឆ្មារៀនបានតាម Clicker + នំ៖ ចុច «ក្លិក» ភ្លាមពេលវាធ្វើត្រូវ រួចឲ្យនំ — វានឹងចងចាំសំឡេងនោះ",
        "⏱️ បង្ហាត់ម្ដង ៣-៥ នាទីប៉ុណ្ណោះ ឆ្មាធុញលឿនជាងឆ្កែ ហើយបង្ហាត់មុនពេលឲ្យបាយ ពេលវាឃ្លាន",
        "🏃 បង្ហាត់ឲ្យមក៖ ហៅឈ្មោះ ហើយអង្រួនប្រអប់នំ ពេលវាមក ឲ្យនំ — ធ្វើរាល់ថ្ងៃ",
        "🪵 កុំឲ្យកោសកៅអី៖ ដាក់បង្គោលកោសក្បែរនោះ ជូតក្លិនឆ្មា ឬ Catnip ហើយសរសើរពេលវាកោសត្រូវ",
        "🚽 ប្រអប់ខ្សាច់៖ ទុកកន្លែងស្ងាត់ សម្អាតរាល់ថ្ងៃ ឆ្មាមិនប្រើប្រអប់កខ្វក់ទេ",
        "🚫 កុំវាយ ឬបាញ់ទឹក — ឆ្មានឹងខ្លាចម្ចាស់។ បើវាធ្វើខុស ប្ដូរការចាប់អារម្មណ៍ទៅរបស់លេងវិញ",
        "💉 ចាក់វ៉ាក់សាំង និងពិនិត្យពេទ្យសត្វ បើឆ្មាប្ដូរទម្លាប់ភ្លាមៗ (មិនញ៉ាំ លាក់ខ្លួន) ព្រោះវាអាចឈឺ",
    )),
}

KIDS_APPS = (
    "📱 <b>កម្មវិធីដែលល្អ</b>\n\n"
    "👶 <b>Khan Academy Kids</b> — ឥតគិតថ្លៃ គ្មានពាណិជ្ជកម្ម រឿង និងល្បែងបង្រៀនចិត្តល្អ ការចែករំលែក (អង់គ្លេស)\n"
    "🐯 <b>Daniel Tiger (PBS Kids)</b> — ចម្រៀងខ្លីៗបង្រៀនពីអារម្មណ៍ ការរង់ចាំ ការស្ដាប់ម៉ែឪ\n"
    "👨‍👩‍👧 <b>Daniel Tiger for Parents</b> — ឥតគិតថ្លៃ សម្រាប់ម៉ែឪ ចម្រៀងប្រើពេលកូនខឹង មិនចង់គេង មិនចង់ងូតទឹក\n\n"
    "⏰ ក្រោម ២ ឆ្នាំ មិនគួរមើលអេក្រង់ · ២-៥ ឆ្នាំ មិនលើស ១ ម៉ោង/ថ្ងៃ ហើយមើលជាមួយម៉ែឪ (WHO)\n"
    "💡 កម្មវិធីជួយបានខ្លះ ប៉ុន្តែកូនរៀនពីម៉ែឪច្រើនជាងគេ ❤️"
)
PET_APPS = (
    "📱 <b>កម្មវិធីបង្ហាត់សត្វ</b>\n\n"
    "🐶 <b>Dogo</b> — មេរៀនជំហានៗ និងប៊ូតុង Clicker ក្នុងទូរស័ព្ទ (ប្រើជាមួយឆ្មាក៏បាន)\n"
    "🐶 <b>Woofz</b> — កម្មវិធីបង្ហាត់ឆ្កែ និងកូនឆ្កែ\n"
    "🐶 <b>Puppr</b> — វីដេអូបង្ហាត់ល្បិចឆ្កែ\n\n"
    "ℹ️ មេរៀនខ្លះឥតគិតថ្លៃ ភាគច្រើនត្រូវបង់ប្រចាំខែ — សាកកំណែឥតគិតថ្លៃសិន"
)

AI_PROMPT = (
    "You are Cooler Bot, a warm family helper for Cambodian parents and pet owners. Answer this question in "
    "natural, simple modern Khmer, short (max ~12 lines), with practical steps: \"{q}\".\n"
    "Rules: only positive, evidence-based methods (praise, routines, rewards, calm limits, redirection); never "
    "suggest hitting, shouting, frightening, shaming, locking up or starving a child or an animal. For a child's "
    "illness, injury, developmental delay or possible abuse, say to see a doctor (Cambodia emergency 119). For a "
    "pet bite, aggression or illness, say to see a vet, and after a dog or cat bite wash with soap and water and "
    "see a doctor about rabies. Plain text, emoji allowed, no markdown."
)

WAIT_TEXT = ("🤖 <b>សួរ AI ពីការមើលកូន ឬបង្ហាត់សត្វ</b>\n"
             "សរសេរសំណួររបស់អ្នកមក ឧ. <i>កូន ៣ ឆ្នាំមិនព្រមគេងពេលយប់ ធ្វើម៉េច?</i> ឬ <i>ឆ្កែខ្ញុំព្រុសពេលយប់</i>")


def tip_of_day(groups, today=None):
    tips = [t for _k, (_label, items) in groups.items() for t in items]
    day = (today or date.today()).toordinal()
    return tips[day % len(tips)]


class _DailyLimit:
    def __init__(self, limit):
        self.limit, self.used, self.lock = limit, {}, threading.Lock()

    def take(self, uid, unlimited=False):
        if unlimited:
            return True
        today = time.strftime("%Y-%m-%d")
        with self.lock:
            for k in [k for k in self.used if k[1] != today]:
                self.used.pop(k, None)
            n = self.used.get((uid, today), 0)
            if n >= self.limit:
                return False
            self.used[(uid, today)] = n + 1
            return True


def register(bot, config, ask_gemini=None):
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    limits = _DailyLimit(AI_DAILY_LIMIT)
    waiting = {}

    def privileged(uid):
        return uid is not None and (uid == getattr(config, "OWNER_ID", 0) or uid in getattr(config, "ADMIN_IDS", []))

    def kids_menu():
        m = InlineKeyboardMarkup()
        m.row(*[InlineKeyboardButton(label, callback_data=f"fam:k:{key}") for key, (label, _t) in KIDS.items()])
        m.row(InlineKeyboardButton("💡 គន្លឹះថ្ងៃនេះ", callback_data="fam:tip:kids"),
              InlineKeyboardButton("📱 កម្មវិធីល្អ", callback_data="fam:apps:kids"))
        m.row(InlineKeyboardButton("🤖 សួរ AI", callback_data="fam:ask"),
              InlineKeyboardButton("🐶🐱 បង្ហាត់សត្វ", callback_data="fam:pet"))
        m.row(InlineKeyboardButton("🏠 ម៉ឺនុយមេ", callback_data="nav:main"))
        return m

    def pets_menu():
        m = InlineKeyboardMarkup()
        m.row(*[InlineKeyboardButton(label, callback_data=f"fam:p:{key}") for key, (label, _t) in PETS.items()])
        m.row(InlineKeyboardButton("💡 គន្លឹះថ្ងៃនេះ", callback_data="fam:tip:pets"),
              InlineKeyboardButton("📱 កម្មវិធីល្អ", callback_data="fam:apps:pets"))
        m.row(InlineKeyboardButton("🤖 សួរ AI", callback_data="fam:ask"),
              InlineKeyboardButton("👨‍👩‍👧 មើលកូន", callback_data="fam:kids"))
        m.row(InlineKeyboardButton("🏠 ម៉ឺនុយមេ", callback_data="nav:main"))
        return m

    KIDS_TEXT = ("👨‍👩‍👧 <b>មើលកូនឲ្យល្អ ស្ដាប់បង្គាប់ (១-៦ ឆ្នាំ)</b>\n\n"
                 "គន្លឹះសាមញ្ញ ប្រើបានរាល់ថ្ងៃ — ប្រើការសរសើរ និងទម្លាប់ល្អ មិនប្រើការវាយ ឬស្រែក ❤️\n\n"
                 "ជ្រើសអាយុកូន 👇")
    PETS_TEXT = ("🐶🐱 <b>បង្ហាត់ឆ្កែ ឆ្មា ឲ្យស្ដាប់បង្គាប់</b>\n\n"
                 "ប្រើនំរង្វាន់ ការសរសើរ និងការអត់ធ្មត់ — មិនវាយ មិនស្រែក 🐾\n\nជ្រើសសត្វ 👇")

    def back(target):
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("⬅️ ត្រឡប់", callback_data=f"fam:{target}"),
              InlineKeyboardButton("🤖 សួរ AI", callback_data="fam:ask"))
        return m

    def list_text(title, tips):
        return f"<b>{title}</b>\n\n" + "\n\n".join(html.escape(t) for t in tips)

    def view(parts):
        kind = parts[1] if len(parts) > 1 else "kids"
        key = parts[2] if len(parts) > 2 else ""
        if kind == "k" and key in KIDS:
            label, tips = KIDS[key]
            return list_text(f"👨‍👩‍👧 គន្លឹះសម្រាប់កូន {label}", tips), back("kids")
        if kind == "p" and key in PETS:
            label, tips = PETS[key]
            return list_text(f"{label} របៀបបង្ហាត់", tips), back("pet")
        if kind == "tip":
            groups, target = (PETS, "pet") if key == "pets" else (KIDS, "kids")
            return "💡 <b>គន្លឹះថ្ងៃនេះ</b>\n\n" + html.escape(tip_of_day(groups)), back(target)
        if kind == "apps":
            return (PET_APPS, back("pet")) if key == "pets" else (KIDS_APPS, back("kids"))
        if kind == "pet":
            return PETS_TEXT, pets_menu()
        return KIDS_TEXT, kids_menu()

    def answer(message, question):
        uid = message.from_user.id
        q = " ".join((question or "").split())[:400]
        if not q:
            waiting[uid] = time.monotonic() + 600
            bot.reply_to(message, WAIT_TEXT, parse_mode="HTML")
            return
        if ask_gemini is None or not getattr(config, "GEMINI_API_KEY", ""):
            bot.reply_to(message, "AI មិនទាន់បើកដំណើរការនៅឡើយទេ។ សាកមើលគន្លឹះក្នុង /parent ឬ /pet សិន 🙏")
            return
        if not limits.take(uid, privileged(uid)):
            bot.reply_to(message, f"អ្នកសួរ AI អស់ {AI_DAILY_LIMIT} ដងសម្រាប់ថ្ងៃនេះហើយ។ ត្រឡប់មកថ្ងៃស្អែក 🙏")
            return
        try:
            bot.send_chat_action(message.chat.id, "typing")
        except Exception:
            pass
        try:
            reply = (ask_gemini(AI_PROMPT.format(q=q)) or "").strip()
        except Exception as exc:
            logger.warning("family AI failed (%s)", type(exc).__name__)
            reply = ""
        if not reply:
            bot.reply_to(message, "សុំទោស AI មិនអាចឆ្លើយបានទេឥឡូវនេះ។ សូមសាកម្ដងទៀត 🙏")
            return
        bot.reply_to(message, "🤖 <b>Cooler AI ឆ្លើយ</b>\n\n" + html.escape(reply[:3500])
                     + "\n\n<i>ℹ️ ព័ត៌មានទូទៅ — បញ្ហាសុខភាព សូមជួបពេទ្យ ឬពេទ្យសត្វ</i>",
                     parse_mode="HTML", reply_markup=back("kids"))

    @bot.message_handler(commands=["parent", "parents", "kids", "family"])
    def parent_cmd(message):
        bot.reply_to(message, KIDS_TEXT, reply_markup=kids_menu(), parse_mode="HTML")

    @bot.message_handler(commands=["pet", "pets", "dog", "cat"])
    def pet_cmd(message):
        bot.reply_to(message, PETS_TEXT, reply_markup=pets_menu(), parse_mode="HTML")

    @bot.message_handler(commands=["askfamily"])
    def ask_cmd(message):
        parts = (message.text or "").split(maxsplit=1)
        answer(message, parts[1] if len(parts) > 1 else "")

    def is_waiting(message):
        user = getattr(message, "from_user", None)
        if user is None or not getattr(message, "text", None) or message.text.startswith("/"):
            return False
        exp = waiting.get(user.id)
        return bool(exp and exp > time.monotonic())

    @bot.message_handler(func=is_waiting, content_types=["text"])
    def ask_follow_up(message):
        waiting.pop(message.from_user.id, None)
        answer(message, message.text)

    @bot.callback_query_handler(func=lambda call: (call.data or "").startswith("fam:"))
    def family_cb(call):
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        message = getattr(call, "message", None)
        if message is None:
            return
        parts = (call.data or "").split(":")
        if parts[1:2] == ["ask"]:
            waiting[call.from_user.id] = time.monotonic() + 600
            bot.send_message(message.chat.id, WAIT_TEXT, parse_mode="HTML")
            return
        text, markup = view(parts)
        try:
            bot.edit_message_text(text, message.chat.id, message.message_id, reply_markup=markup, parse_mode="HTML")
        except Exception as exc:
            if "message is not modified" not in str(exc):
                bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")

    return SimpleNamespace(limits=limits, waiting=waiting)
