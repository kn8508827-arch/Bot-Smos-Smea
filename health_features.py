"""Health & exercise for older people (Khmer). General information only — never a diagnosis.

  /exercise  daily 10-minute routine (rotates by weekday) + "done" check-in (+5 points/day)
  /balance   balance exercises to prevent falls
  /health    simple guides: blood pressure, diabetes, joints, bones, healthy food
  /warning   danger signs that need a doctor or 119 now
  /sos       Cambodia emergency numbers
"""
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Phnom_Penh")
POINTS_EXERCISE = 5

DISCLAIMER = "\n\n⚕️ <i>ព័ត៌មានទូទៅប៉ុណ្ណោះ មិនមែនជំនួសគ្រូពេទ្យទេ។ បើមានជំងឺ ឬលេបថ្នាំ សូមពិគ្រោះគ្រូពេទ្យមុនហាត់ប្រាណ។</i>"
STOP_NOTE = "⛔ <b>ឈប់ភ្លាម</b> បើវិលមុខ ឈឺទ្រូង ដកដង្ហើមមិនរួច ឬឈឺខ្លាំង។"

SOS_TEXT = (
    "🆘 <b>លេខទូរស័ព្ទបន្ទាន់ កម្ពុជា</b>\n\n"
    "🚑 <b>119</b> — ឡានពេទ្យ\n"
    "👮 <b>117</b> — ប៉ូលិស\n"
    "🚒 <b>118</b> — អគ្គិភ័យ\n\n"
    "📌 ពេលទូរស័ព្ទ៖ និយាយ <b>ទីតាំង</b> ឲ្យច្បាស់ មានអ្នកជំងឺប៉ុន្មាននាក់ ហើយ <b>កុំបិទទូរស័ព្ទ</b> រហូតដល់គេប្រាប់។\n"
    "💡 សរសេរលេខទាំងនេះ និងលេខពេទ្យជិតផ្ទះ បិទនៅជិតទូរស័ព្ទ ឬទូទឹកកក ឲ្យលោកតាលោកយាយមើលឃើញងាយ។"
)

ROUTINES = [
    ("🌅 លំហាត់ព្រឹក — ដាស់រាងកាយ", [
        "🚶 <b>ដើរនៅនឹងកន្លែង</b> ១ នាទី — លើកជង្គង់ស្រាលៗ ដៃយោលតាម",
        "🔄 <b>រង្វិលស្មា</b> ទៅមុខ ១០ ដង ថយក្រោយ ១០ ដង",
        "🪑 <b>អង្គុយ-ក្រោកពីកៅអី</b> ៥-១០ ដង (ដៃកាន់កៅអីបាន)",
        "🧱 <b>រុញជញ្ជាំង</b> (wall push-up) ៥-១០ ដង",
        "🦶 <b>ឈរលើចុងជើង</b> ដៃកាន់កៅអី ១០ ដង",
        "🧘 <b>លាតសាច់ដុំកំភួនជើង</b> ឈម ១៥ វិនាទី ម្ខាងៗ",
    ]),
    ("🚶 ថ្ងៃដើរ — បេះដូងរឹងមាំ", [
        "🚶 <b>ដើរលឿនបន្តិច</b> ១០-១៥ នាទី (អាចនិយាយបាន តែច្រៀងមិនរួច = ល្មម)",
        "🔄 <b>រង្វិលកដៃ និងកជើង</b> ១០ ដង ម្ខាងៗ",
        "🙆 <b>លាតដៃឡើងលើ</b> ដកដង្ហើមចូលវែងៗ ៥ ដង",
        "🧘 <b>លាតសាច់ដុំខាងក្រោយភ្លៅ</b> អង្គុយលើកៅអី ជើងលាតត្រង់ ឱនទៅមុខស្រាលៗ ១៥ វិនាទី",
    ]),
    ("💪 ថ្ងៃកម្លាំង — សាច់ដុំ និងឆ្អឹង", [
        "🚶 <b>ដើរនៅនឹងកន្លែង</b> ១ នាទី (កម្ដៅខ្លួន)",
        "🪑 <b>អង្គុយ-ក្រោកពីកៅអី</b> ២ ជុំ × ៨ ដង",
        "🦵 <b>លើកជើងទៅចំហៀង</b> ដៃកាន់កៅអី ៨ ដង ម្ខាងៗ",
        "🍶 <b>លើកដបទឹក</b> (ដបទឹក ០.៥ លីត្រ) ពត់កែងដៃ ១០ ដង",
        "🧱 <b>រុញជញ្ជាំង</b> ៨-១០ ដង",
        "🧘 <b>ដកដង្ហើមយឺតៗ</b> ១ នាទី ដើម្បីសម្រាក",
    ]),
]

BALANCE_TEXT = (
    "⚖️ <b>លំហាត់ទប់លំនឹង — ការពារការដួល</b>\n"
    "ការដួល ជាមូលហេតុធំនៃរបួសចំពោះមនុស្សចាស់។ ហាត់ ៣ ថ្ងៃ/សប្ដាហ៍ ជួយបានច្រើន។\n"
    "🪑 <b>ត្រូវមានកៅអីរឹង ឬជញ្ជាំង នៅក្បែរខ្លួនជានិច្ច</b>\n\n"
    "1️⃣ <b>ឈរជើងម្ខាង</b> — ដៃកាន់កៅអី លើកជើងម្ខាង ១០ វិនាទី រួចប្ដូរ (៣ ដង ម្ខាងៗ)\n"
    "2️⃣ <b>ដើរកែងទល់ម្រាមជើង</b> — ដើរតាមបណ្ដោយជញ្ជាំង ដាក់កែងជើងមុខទល់ម្រាមជើងក្រោយ ១០ ជំហាន\n"
    "3️⃣ <b>លើកជើងទៅក្រោយ</b> — កាន់កៅអី ពត់ជង្គង់លើកកែងជើងទៅក្រោយ ៨ ដង ម្ខាងៗ\n"
    "4️⃣ <b>ក្រោកពីកៅអីដោយមិនប្រើដៃ</b> — បើពិបាក ប្រើដៃជួយបាន ៥ ដង\n"
    "5️⃣ <b>ងាកក្បាលយឺតៗ</b> ពេលឈរ ឆ្វេង-ស្ដាំ ៥ ដង (ឈប់បើវិលមុខ)\n\n"
    "🏠 <b>ធ្វើឲ្យផ្ទះមានសុវត្ថិភាព</b>៖ ភ្លើងបំភ្លឺនៅជណ្ដើរ និងបន្ទប់ទឹក, កម្រាលកុំឲ្យរអិល, "
    "ដាក់ដៃកាន់ក្នុងបន្ទប់ទឹក, ពាក់ស្បែកជើងមិនរអិល។"
)

TOPICS = {
    "bp": ("❤️ លើសសម្ពាធឈាម", (
        "❤️ <b>លើសសម្ពាធឈាម</b>\n"
        "ជារឿយៗ <b>គ្មានរោគសញ្ញា</b> ទេ ដូច្នេះត្រូវ <b>វាស់ជាប្រចាំ</b> (យ៉ាងតិចម្ដង/ខែ ឬតាមពេទ្យប្រាប់)។\n\n"
        "📏 ធម្មតាប្រហែល <b>ក្រោម 120/80</b>។ បើវាស់ច្រើនដងហើយ <b>140/90 ឬលើស</b> គួរជួបពេទ្យ។\n\n"
        "✅ <b>អ្វីដែលជួយ</b>\n"
        "• 🧂 កាត់បន្ថយអំបិល — ប្រហុក ទឹកត្រី ស៊ីអ៊ីវ ត្រីងៀត ប៊ីចេង មានអំបិលច្រើន\n"
        "• 🥬 ញ៉ាំបន្លែ ផ្លែឈើ ច្រើនជាងមុន\n"
        "• 🚶 ដើរ ៣០ នាទី/ថ្ងៃ (បំបែកជា ៣ ដង ១០ នាទីក៏បាន)\n"
        "• 🚭 ឈប់ជក់បារី កាត់បន្ថយស្រា\n"
        "• 💊 លេបថ្នាំតាមពេទ្យប្រាប់ <b>កុំឈប់ខ្លួនឯង</b> ទោះមានអារម្មណ៍ថាស្រួលហើយ"
    )),
    "sugar": ("🍬 ទឹកនោមផ្អែម", (
        "🍬 <b>ទឹកនោមផ្អែម</b>\n"
        "⚠️ <b>សញ្ញាគួរពិនិត្យ</b>៖ ស្រេកទឹកខ្លាំង នោមញឹក អស់កម្លាំង ស្រកទម្ងន់ មុខរបួសជាយឺត ភ្នែកព្រិល។\n"
        "👉 ពិនិត្យជាតិស្ករក្នុងឈាមនៅមណ្ឌលសុខភាព ជាពិសេសបើមានគ្រួសារធ្លាប់កើត។\n\n"
        "✅ <b>អ្វីដែលជួយ</b>\n"
        "• 🥤 ជៀសភេសជ្ជៈផ្អែម ទឹកកំប៉ុង តែទឹកដោះគោផ្អែម\n"
        "• 🍚 ញ៉ាំបាយល្មមៗ បន្ថែមបន្លែ — ចាន ½ បន្លែ, ¼ បាយ, ¼ ត្រី/សាច់\n"
        "• 🚶 ដើរក្រោយញ៉ាំបាយ ១០-១៥ នាទី\n"
        "• 🦶 ពិនិត្យជើងរាល់ថ្ងៃ (របួស ពង ក្រហម) ព្រោះអាចមិនដឹងថាឈឺ\n"
        "• 💊 លេបថ្នាំ/ចាក់ថ្នាំតាមពេទ្យប្រាប់"
    )),
    "joint": ("🦵 ឈឺសន្លាក់", (
        "🦵 <b>ឈឺសន្លាក់ (ជង្គង់ ចង្កេះ ស្មា)</b>\n"
        "✅ <b>អ្វីដែលជួយ</b>\n"
        "• 🚶 <b>បន្តធ្វើចលនាស្រាលៗ</b> — អង្គុយស្ងៀមយូរ ធ្វើឲ្យរឹងជាងមុន\n"
        "• 🪑 លំហាត់ក្នុង /exercise និង /balance ជួយពង្រឹងសាច់ដុំជុំវិញសន្លាក់\n"
        "• ⚖️ រក្សាទម្ងន់ល្មម — ទម្ងន់ធ្ងន់ សង្កត់ជង្គង់ខ្លាំង\n"
        "• ♨️ ប្រើក្រណាត់ក្ដៅឧណ្ហៗ ពេលរឹងសន្លាក់ពេលព្រឹក\n\n"
        "👨‍⚕️ <b>ជួបពេទ្យ</b> បើសន្លាក់ហើម ក្រហម ក្ដៅ ឬឈឺខ្លាំងរហូតដើរមិនរួច។ "
        "កុំប្រើថ្នាំបំបាត់ការឈឺរយៈពេលយូរដោយគ្មានពេទ្យណែនាំ។"
    )),
    "bone": ("🦴 ពុកឆ្អឹង", (
        "🦴 <b>ពុកឆ្អឹង — ឆ្អឹងស្រួយ ងាយបាក់</b>\n"
        "កើតច្រើនចំពោះមនុស្សចាស់ ជាពិសេសស្ត្រីក្រោយអស់រដូវ។ ច្រើនតែ <b>គ្មានរោគសញ្ញា</b> រហូតដល់បាក់ឆ្អឹង។\n\n"
        "✅ <b>អ្វីដែលជួយ</b>\n"
        "• 🐟 ញ៉ាំអាហារមានកាល់ស្យូម៖ ត្រីតូចៗញ៉ាំទាំងឆ្អឹង សណ្ដែក តៅហ៊ូ បន្លែស្លឹកបៃតង ទឹកដោះគោ\n"
        "• ☀️ ត្រូវពន្លឺថ្ងៃពេលព្រឹកស្រាលៗ ១០-១៥ នាទី (វីតាមីន D)\n"
        "• 💪 ហាត់កម្លាំង និងដើរ ជួយឲ្យឆ្អឹងរឹង\n"
        "• ⚖️ <b>ការពារការដួល</b> — មើល /balance"
    )),
    "food": ("🥗 អាហារល្អ", (
        "🥗 <b>អាហារល្អសម្រាប់មនុស្សចាស់</b>\n"
        "• 💧 ផឹកទឹកឲ្យបានគ្រប់ ទោះមិនស្រេក — មនុស្សចាស់ច្រើនមានអារម្មណ៍ស្រេកតិច (ថ្ងៃក្ដៅ ផឹកច្រើនជាងមុន)\n"
        "• 🥬 បន្លែ និងផ្លែឈើ រៀងរាល់ពេលបាយ\n"
        "• 🐟 ប្រូតេអ៊ីន៖ ត្រី ពងមាន់ សណ្ដែក តៅហ៊ូ ជួយរក្សាសាច់ដុំ\n"
        "• 🧂 អំបិល ស្ករ ខ្លាញ់ ត្រឹមតិច — ប្រយ័ត្នម្ហូបចៀន និងនំផ្អែម\n"
        "• 🍽️ បើញ៉ាំតិច ញ៉ាំតិចៗ តែញឹកជាងមុន\n"
        "• 🦷 ធ្មេញ/ធ្មេញដាក់ មានបញ្ហា ធ្វើឲ្យញ៉ាំមិនបាន — ពិនិត្យធ្មេញផង"
    )),
}

WARNING_TEXT = (
    "🚨 <b>សញ្ញាគ្រោះថ្នាក់ — ទូរស័ព្ទ 119 ភ្លាម</b>\n\n"
    "🧠 <b>ស្ទះ/ដាច់សរសៃឈាមខួរក្បាល</b> (ចាំពាក្យ <b>BE FAST</b>)\n"
    "• <b>B</b> ដើរទ្រេត បាត់លំនឹងភ្លាមៗ\n"
    "• <b>E</b> មើលមិនឃើញ ឬព្រិលភ្លាមៗ\n"
    "• <b>F</b> មុខវៀច — ឲ្យញញឹម មុខម្ខាងធ្លាក់\n"
    "• <b>A</b> ដៃ/ជើងម្ខាង ស្ពឹក ខ្សោយ លើកមិនរួច\n"
    "• <b>S</b> និយាយមិនច្បាស់ ឬមិនយល់សម្ដី\n"
    "• <b>T</b> ពេលវេលា! ⏱️ ទៅពេទ្យភ្លាម — កុំរង់ចាំមើល កុំឲ្យញ៉ាំអ្វី\n\n"
    "❤️ <b>គាំងបេះដូង</b>\n"
    "ឈឺ/តឹងទ្រូង រាលទៅដៃ ក ថ្គាម ឬខ្នង, បែកញើសត្រជាក់, ដកដង្ហើមមិនរួច, ចង្អោរ\n\n"
    "🍬 <b>ជាតិស្ករធ្លាក់ចុះ</b> (អ្នកទឹកនោមផ្អែម)\n"
    "ញ័រ បែកញើស វិលមុខ ច្របូកច្របល់ → បើនៅដឹងខ្លួន លេបបាន ឲ្យផឹកទឹកស្ករ ឬទឹកផ្លែឈើ រួចហៅពេទ្យ\n\n"
    "☀️ <b>ពុលកម្ដៅ</b>\n"
    "ខ្លួនក្ដៅខ្លាំង ស្បែកស្ងួត ច្របូកច្របល់ សន្លប់ → នាំទៅម្លប់ ជូតទឹកត្រជាក់ ហើយទូរស័ព្ទ 119\n\n"
    "🤕 <b>ដួល</b> ប៉ះក្បាល ហើយក្រោយមកឈឺក្បាលខ្លាំង ក្អួត ងងុយខ្លាំង → ទៅពេទ្យ\n\n"
    "🆘 /sos — លេខទូរស័ព្ទបន្ទាន់"
)


@contextmanager
def _connect(config):
    conn = sqlite3.connect(getattr(config, "BOT_DB_PATH", "bot_database.db"), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def routine_for(day_index: int):
    return ROUTINES[day_index % len(ROUTINES)]


def register(bot, config):
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    with _connect(config) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS health_exercise_log (
            user_id INTEGER NOT NULL,
            local_date TEXT NOT NULL,
            PRIMARY KEY (user_id, local_date)
        )""")

    def menu_markup():
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("🏃 លំហាត់ថ្ងៃនេះ", callback_data="health:exercise"),
              InlineKeyboardButton("⚖️ ទប់លំនឹង", callback_data="health:balance"))
        m.row(*[InlineKeyboardButton(TOPICS[k][0], callback_data=f"health:topic:{k}") for k in ("bp", "sugar")])
        m.row(*[InlineKeyboardButton(TOPICS[k][0], callback_data=f"health:topic:{k}") for k in ("joint", "bone")])
        m.row(InlineKeyboardButton(TOPICS["food"][0], callback_data="health:topic:food"),
              InlineKeyboardButton("🚨 សញ្ញាគ្រោះថ្នាក់", callback_data="health:warning"))
        m.row(InlineKeyboardButton("🆘 លេខបន្ទាន់", callback_data="health:sos"))
        return m

    def back_markup(extra=None):
        m = InlineKeyboardMarkup()
        if extra:
            m.row(extra)
        m.row(InlineKeyboardButton("🔙 សុខភាព", callback_data="health:menu"),
              InlineKeyboardButton("🏠 មឺនុយមេ", callback_data="nav:main"))
        return m

    menu_text = ("🧓💪 <b>សុខភាព និងលំហាត់ប្រាណ</b>\n"
                 "សម្រាប់លោកតា លោកយាយ ឪពុកម្ដាយ និងគ្រប់គ្នា ❤️\nជ្រើសប្រធានបទ៖")

    def exercise_view():
        title, steps = routine_for(datetime.now(TZ).weekday())
        text = (f"🏃 <b>លំហាត់ ១០ នាទីថ្ងៃនេះ</b>\n{title}\n\n" + "\n".join(steps)
                + f"\n\n{STOP_NOTE}" + DISCLAIMER)
        done = InlineKeyboardButton(f"✅ ខ្ញុំធ្វើរួចហើយ (+{POINTS_EXERCISE} ពិន្ទុ)", callback_data="health:done")
        return text, back_markup(done)

    def send(message, text, markup):
        bot.reply_to(message, text, reply_markup=markup, parse_mode="HTML")

    @bot.message_handler(commands=["health"])
    def health_cmd(message):
        send(message, menu_text, menu_markup())

    @bot.message_handler(commands=["exercise"])
    def exercise_cmd(message):
        send(message, *exercise_view())

    @bot.message_handler(commands=["balance"])
    def balance_cmd(message):
        send(message, BALANCE_TEXT + f"\n\n{STOP_NOTE}" + DISCLAIMER, back_markup())

    @bot.message_handler(commands=["warning"])
    def warning_cmd(message):
        send(message, WARNING_TEXT, back_markup())

    @bot.message_handler(commands=["sos"])
    def sos_cmd(message):
        send(message, SOS_TEXT, back_markup())

    def mark_done(user_id):
        """Record today's exercise; returns (new, points) — points=None if no community table."""
        today = datetime.now(TZ).date().isoformat()
        with _connect(config) as conn:
            try:
                conn.execute("INSERT INTO health_exercise_log(user_id, local_date) VALUES (?, ?)", (user_id, today))
            except sqlite3.IntegrityError:
                return False, None
            try:
                conn.execute("INSERT OR IGNORE INTO community_points(user_id) VALUES (?)", (user_id,))
                conn.execute("UPDATE community_points SET points=points+? WHERE user_id=?", (POINTS_EXERCISE, user_id))
                row = conn.execute("SELECT points FROM community_points WHERE user_id=?", (user_id,)).fetchone()
                return True, row["points"] if row else None
            except sqlite3.OperationalError:
                return True, None

    @bot.callback_query_handler(func=lambda call: (call.data or "").startswith("health:"))
    def health_cb(call):
        message = getattr(call, "message", None)
        if message is None:
            bot.answer_callback_query(call.id)
            return
        key = (call.data or "").split(":")[1:]

        def show(text, markup):
            try:
                bot.edit_message_text(text, message.chat.id, message.message_id,
                                      reply_markup=markup, parse_mode="HTML")
            except Exception as exc:
                if "message is not modified" not in str(exc):
                    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")

        if key[:1] == ["done"]:
            new, points = mark_done(call.from_user.id)
            if not new:
                bot.answer_callback_query(call.id, "✅ ថ្ងៃនេះអ្នកបានហាត់រួចហើយ! ល្អណាស់ 💪", show_alert=True)
            else:
                extra = f"\n⭐ ពិន្ទុសរុប៖ {points}" if points is not None else ""
                bot.answer_callback_query(call.id, f"🎉 ពូកែណាស់! +{POINTS_EXERCISE} ពិន្ទុ{extra}", show_alert=True)
            return
        bot.answer_callback_query(call.id)
        if key[:1] == ["menu"]:
            show(menu_text, menu_markup())
        elif key[:1] == ["exercise"]:
            show(*exercise_view())
        elif key[:1] == ["balance"]:
            show(BALANCE_TEXT + f"\n\n{STOP_NOTE}" + DISCLAIMER, back_markup())
        elif key[:1] == ["warning"]:
            show(WARNING_TEXT, back_markup())
        elif key[:1] == ["sos"]:
            show(SOS_TEXT, back_markup())
        elif key[:1] == ["topic"] and len(key) > 1 and key[1] in TOPICS:
            show(TOPICS[key[1]][1] + DISCLAIMER, back_markup())
        else:
            show(menu_text, menu_markup())

    return menu_markup
