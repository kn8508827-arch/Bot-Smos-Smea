"""/fortune — Khmer zodiac daily fortune, lucky stick, life number, palm and dream reading. For fun.

Daily readings are picked from phrase pools with a seed of (date, animal), so everyone with
the same animal sees the same reading all day and it changes tomorrow. No AI cost.
  /fortune            menu: 12 animals, my reading, find by birth year, lucky stick, ask AI
  /fortune 1995       reading for the animal of that birth year
  /number 15/08/1995  life number (numerology), birth weekday and animal year — no AI
  /palm               send a palm photo → AI palm reading (vision)
  /dream ...          AI dream interpretation
AI uses (ask, palm, dream) share FORTUNE_AI_DAILY_LIMIT (3) per person per day.
"""
import hashlib
import html
import logging
import os
import random
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

logger = logging.getLogger(__name__)
TZ_KH = timezone(timedelta(hours=7))
AI_DAILY_LIMIT = int(os.getenv("FORTUNE_AI_DAILY_LIMIT", "3"))

ANIMALS = (  # index 0 = Rat; (year - 4) % 12
    ("rat", "🐀", "ជូត"), ("ox", "🐂", "ឆ្លូវ"), ("tiger", "🐅", "ខាល"), ("rabbit", "🐇", "ថោះ"),
    ("dragon", "🐉", "រោង"), ("snake", "🐍", "ម្សាញ់"), ("horse", "🐎", "មមី"), ("goat", "🐐", "មមែ"),
    ("monkey", "🐒", "វក"), ("rooster", "🐓", "រកា"), ("dog", "🐕", "ច"), ("pig", "🐖", "កុរ"),
)
_KEYS = [a[0] for a in ANIMALS]

LOVE = (
    "ស្នេហាផ្អែមល្ហែម មានអ្នកនឹកអ្នកស្ងាត់ៗ 💕", "គួរនិយាយពាក្យល្អៗដាក់មនុស្សជាទីស្រឡាញ់ ថ្ងៃនេះគេត្រូវការអ្នក",
    "អ្នកនៅលីវ អាចជួបមនុស្សថ្មីដែលនិយាយគ្នាត្រូវចិត្ត", "កុំខឹងរឿងតូចតាច ស្ងប់ចិត្តនឹងល្អជាង",
    "មានដំណឹងល្អពីមនុស្សដែលអ្នកចាំយូរ 📩", "ពេលល្ងាចល្អសម្រាប់ញ៉ាំបាយ ឬដើរលេងជាមួយគ្នា",
    "ស្មោះត្រង់ និងយកចិត្តទុកដាក់ នឹងធ្វើឲ្យស្នេហារឹងមាំ", "ចាស់ៗក្នុងផ្ទះនឹងជួយផ្ដល់យោបល់ល្អពីស្នេហា",
)
MONEY = (
    "លុយចូលបន្តិចៗ តែជាប់លាប់ 💰", "កុំខ្ចីលុយគេ ឬឲ្យគេខ្ចីថ្ងៃនេះ", "មានឱកាសរកចំណូលបន្ថែមពីមិត្តភក្តិ",
    "ចាយតែរបស់ចាំបាច់ សន្សំទុកសម្រាប់ថ្ងៃក្រោយ", "លក់ដូរមានអតិថិជនច្រើនជាងធម្មតា 🛒",
    "គិតឲ្យច្បាស់មុនទិញរបស់ថ្លៃៗ", "អាចទទួលបានកាដូ ឬរង្វាន់ដែលមិននឹកស្មាន 🎁", "សន្សំបន្តិចៗ យូរទៅក្លាយជាច្រើន",
)
WORK = (
    "ការងាររលូន ចៅហ្វាយសរសើរ 👍", "ធ្វើការជាក្រុមនឹងទទួលបានលទ្ធផលល្អជាងធ្វើម្នាក់ឯង",
    "មានការងារច្រើន តែអ្នកអាចបញ្ចប់បាន ប្រសិនបើរៀបចំពេលល្អ", "ល្អសម្រាប់ចាប់ផ្ដើមគម្រោងថ្មី 🚀",
    "ពិនិត្យឯកសារម្ដងទៀតមុនផ្ញើ ដើម្បីជៀសវាងកំហុស", "គំនិតថ្មីរបស់អ្នកនឹងត្រូវគេទទួលយក",
    "សិស្សរៀនចូលចិត្ត ចងចាំបានល្អ 📚", "អត់ធ្មត់បន្តិច លទ្ធផលល្អនឹងមកដល់",
)
HEALTH = (
    "សុខភាពល្អ ផឹកទឹកឲ្យបានច្រើន 💧", "សម្រាកឲ្យគ្រប់ កុំលេងទូរស័ព្ទយប់ជ្រៅ 😴", "ដើរលំហាត់ ២០ នាទី នឹងធ្វើឲ្យស្រស់ស្រាយ",
    "ប្រយ័ត្នពេលបើកបរ ពាក់មួកសុវត្ថិភាព 🪖", "ញ៉ាំបន្លែ ផ្លែឈើបន្ថែម", "ចិត្តស្ងប់ សុខភាពក៏ល្អ ដកដង្ហើមវែងៗ",
    "កុំញ៉ាំហឹរ ឬជូរពេក ថ្ងៃនេះ", "រាងកាយរឹងមាំ ល្អសម្រាប់ធ្វើសកម្មភាពក្រៅផ្ទះ ☀️",
)
TIPS = (
    "ញញឹមច្រើន សំណាងនឹងមកច្រើន 😊", "ជួយមនុស្សម្នាក់ថ្ងៃនេះ អំពើល្អនឹងវិលមកវិញ", "ទូរស័ព្ទទៅឪពុកម្ដាយបន្តិច ❤️",
    "សរសេររបស់ ៣ ដែលអ្នកដឹងគុណថ្ងៃនេះ", "រៀបចំបន្ទប់ឲ្យស្អាត ចិត្តនឹងស្រឡះ", "ស្ដាប់បទ Vid Cooler មួយបទ បង្កើនកម្លាំង 🎤",
    "ក្រោកពីព្រលឹម ធ្វើអ្វីៗបានច្រើនជាងមុន", "កុំប្រកែកជាមួយអ្នកដទៃ ស្ងៀមល្អជាង",
)
COLORS = ("🔴 ក្រហម", "🟠 ទឹកក្រូច", "🟡 លឿង", "🟢 បៃតង", "🔵 ខៀវ", "🟣 ស្វាយ", "⚪ ស", "🩷 ផ្កាឈូក", "🟤 ត្នោត", "⚫ ខ្មៅ")
TIMES = ("ព្រឹកព្រលឹម (៥-៧)", "ព្រឹក (៨-១០)", "ថ្ងៃត្រង់ (១១-១)", "រសៀល (២-៤)", "ល្ងាច (៥-៧)", "យប់ (៨-១០)")
STICKS = (
    "🎋 ឆ្នោតល្អបំផុត៖ អ្វីដែលអ្នកប្រាថ្នា នឹងសម្រេចបាន ដោយការព្យាយាម",
    "🎋 ឆ្នោតល្អ៖ មិត្តល្អនឹងជួយអ្នកនៅពេលត្រូវការ",
    "🎋 ឆ្នោតល្អ៖ ផ្លូវឆ្ងាយ មានសុវត្ថិភាព ធ្វើដំណើរបានរលូន",
    "🎋 ឆ្នោតមធ្យម៖ អត់ធ្មត់សិន កុំប្រញាប់សម្រេចចិត្ត",
    "🎋 ឆ្នោតល្អ៖ គ្រួសារសុខដុមរមនា មានរឿងរីករាយ",
    "🎋 ឆ្នោតល្អបំផុត៖ ឱកាសថ្មីកំពុងមក ត្រៀមខ្លួនឲ្យរួច",
    "🎋 ឆ្នោតមធ្យម៖ ថែរក្សាសុខភាព មុនការងារ",
    "🎋 ឆ្នោតល្អ៖ អ្នកដែលអ្នកជួយ នឹងតបស្នងគុណវិញ",
    "🎋 ឆ្នោតល្អ៖ ការសិក្សា និងការងារមានវឌ្ឍនភាព",
    "🎋 ឆ្នោតមធ្យម៖ ប្រយ័ត្នពាក្យសម្ដី កុំនិយាយពេលខឹង",
    "🎋 ឆ្នោតល្អបំផុត៖ ស្នេហាផ្អែមល្ហែម ចិត្តត្រូវគ្នា",
    "🎋 ឆ្នោតល្អ៖ លុយកាក់ចូលជាបន្តបន្ទាប់ បើសន្សំត្រឹមត្រូវ",
    "🎋 ឆ្នោតមធ្យម៖ រឿងពិបាកនឹងកន្លងផុត ដូចភ្លៀងហើយមេឃស្រឡះ",
    "🎋 ឆ្នោតល្អ៖ គំនិតច្នៃប្រឌិតរបស់អ្នកមានតម្លៃ",
    "🎋 ឆ្នោតល្អបំផុត៖ សេចក្ដីសុខ និងសំណាងនៅជាមួយអ្នក",
    "🎋 ឆ្នោតល្អ៖ ចាប់ផ្ដើមតូចៗ ថ្ងៃមួយនឹងធំ",
)

WEEKDAYS = ("ចន្ទ", "អង្គារ", "ពុធ", "ព្រហស្បតិ៍", "សុក្រ", "សៅរ៍", "អាទិត្យ")  # Monday = 0
LIFE = {
    1: ("អ្នកដឹកនាំ 👑", "ក្លាហាន ចេះសម្រេចចិត្ត ចូលចិត្តចាប់ផ្ដើមរឿងថ្មី", "ចេះស្ដាប់អ្នកដទៃបន្ថែម"),
    2: ("អ្នកសម្របសម្រួល 🤝", "ចិត្តល្អ ទន់ភ្លន់ ពូកែធ្វើការជាក្រុម", "ជឿជាក់លើខ្លួនឯងឲ្យច្រើន"),
    3: ("អ្នកច្នៃប្រឌិត 🎨", "រួសរាយ និយាយពូកែ ចូលចិត្តសិល្បៈ តន្ត្រី", "ផ្ដោតលើគោលដៅមួយឲ្យចប់"),
    4: ("អ្នកកសាង 🧱", "ស្មោះត្រង់ ខិតខំ មានរបៀប ទុកចិត្តបាន", "សម្រាកខ្លះ កុំធ្វើការហួសកម្លាំង"),
    5: ("អ្នកស្វែងរក ✈️", "ចូលចិត្តសេរីភាព ធ្វើដំណើរ រៀនរឿងថ្មីលឿន", "ចេះសន្សំ និងរក្សាពាក្យសន្យា"),
    6: ("អ្នកថែទាំ ❤️", "ស្រឡាញ់គ្រួសារ ទទួលខុសត្រូវ ចូលចិត្តជួយគេ", "កុំភ្លេចថែរក្សាខ្លួនឯងផង"),
    7: ("អ្នកគិតជ្រៅ 🔭", "ឆ្លាត ចូលចិត្តស្វែងយល់ រៀនសូត្រ", "បើកចិត្តចែករំលែកអារម្មណ៍"),
    8: ("អ្នកជំនួញ 💼", "មានមហិច្ឆតា ពូកែគ្រប់គ្រង រកលុយបាន", "ឲ្យពេលគ្រួសារ និងសុខភាព"),
    9: ("អ្នកមេត្តា 🌏", "ចិត្តធំ ចូលចិត្តជួយសង្គម មានទេពកោសល្យសិល្បៈ", "ចេះបដិសេធពេលចាំបាច់"),
    11: ("អ្នកបំផុសគំនិត ✨ (លេខមេ)", "មានវិចារណញាណខ្ពស់ បំផុសទឹកចិត្តអ្នកដទៃ", "គ្រប់គ្រងភាពតានតឹង"),
    22: ("អ្នកកសាងធំ 🏗️ (លេខមេ)", "មានក្ដីសុបិនធំ ហើយធ្វើឲ្យក្លាយជាការពិតបាន", "កុំដាក់សម្ពាធខ្លួនឯងខ្លាំងពេក"),
    33: ("គ្រូនៃមេត្តា 🕊️ (លេខមេ)", "ចិត្តល្អខ្លាំង ចូលចិត្តបង្រៀន និងថែទាំ", "ចែករំលែកការងារឲ្យអ្នកដទៃ"),
}

PALM_PROMPT = (
    "You are Cooler Bot, a playful palm reader for entertainment. Look at this photo. If it does not clearly show "
    "the inside of a human hand (palm), say kindly in Khmer that you need a clear photo of an open palm in good light, "
    "and stop. Otherwise describe in warm modern Khmer what you see (heart line, head line, life line, fate line, hand "
    "shape) and give a fun, positive reading for love, work/money and personality, max 12 short lines with emoji. "
    "Never predict death, illness, accidents or lifespan, even for the life line; say it reflects energy and "
    "enthusiasm. Say it is for entertainment. Plain text, no markdown."
)
DREAM_PROMPT = (
    "You are Cooler Bot, a gentle Khmer dream interpreter for entertainment. Dream: \"{q}\". In warm modern Khmer, "
    "max 10 short lines with emoji: what the main symbols traditionally mean in Khmer/Asian folk belief, a positive "
    "message, and one small practical tip. If the dream is frightening, reassure the person that dreams often reflect "
    "stress and are not predictions. Never predict death, illness or disasters; never give lottery numbers. Say it is "
    "for entertainment. Plain text, no markdown."
)


def life_number(d, m, y):
    """Numerology life path: add every digit, reduce to 1-9 but keep the master numbers 11, 22, 33."""
    n = sum(int(ch) for ch in f"{d:02d}{m:02d}{y:04d}")
    while n > 9 and n not in (11, 22, 33):
        n = sum(int(ch) for ch in str(n))
    return n


def parse_birth_date(text):
    import re
    m = re.search(r"(\d{1,2})\s*[/\-.\s]\s*(\d{1,2})\s*[/\-.\s]\s*(\d{4})", text or "")
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return datetime(y, mo, d).date()
    except ValueError:
        return None


def number_text(born):
    n = life_number(born.day, born.month, born.year)
    title, good, grow = LIFE[n]
    a = animal_for_year(born.year)
    return (f"🔢 <b>លេខជីវិតរបស់អ្នក៖ {n}</b> — {title}\n\n"
            f"🎂 កើត {born.strftime('%d/%m/%Y')} · ថ្ងៃ<b>{WEEKDAYS[born.weekday()]}</b> · ឆ្នាំ{a[2]} {a[1]}\n\n"
            f"🌟 <b>ចំណុចខ្លាំង៖</b> {good}\n🌱 <b>គួរបន្ថែម៖</b> {grow}\n\n"
            f"🧮 របៀបគិត៖ បូកលេខទាំងអស់ក្នុងថ្ងៃខែឆ្នាំកំណើត រហូតនៅសល់ ១ ខ្ទង់ (លើកលែង ១១ ២២ ៣៣)\n\n"
            "<i>✨ សម្រាប់កម្សាន្ត</i>")


AI_PROMPT = (
    "You are Cooler Bot, a playful Khmer fortune teller for fun. The person was born in the year of the {animal}. "
    "Their question: \"{q}\". Answer in warm, natural modern Khmer, max 10 short lines, with emoji, encouraging and "
    "positive. Make clear it is for entertainment. Never predict death, illness, accidents or disasters; never give "
    "lottery/gambling numbers, investment advice or medical advice; for health or money problems suggest talking to a "
    "doctor or a trusted person. Plain text, no markdown."
)


def animal_for_year(year):
    return ANIMALS[(int(year) - 4) % 12]


def _rng(*parts):
    seed = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return random.Random(int(seed[:16], 16))


def today():
    return datetime.now(TZ_KH).date()


def reading(key, day=None):
    day = day or today()
    r = _rng("fortune", day.isoformat(), key)
    stars = lambda: "⭐" * r.randint(3, 5) if r.random() < 0.8 else "⭐" * 2
    return {
        "overall": stars(), "love": (stars(), r.choice(LOVE)), "money": (stars(), r.choice(MONEY)),
        "work": (stars(), r.choice(WORK)), "health": (stars(), r.choice(HEALTH)),
        "number": r.randint(1, 99), "color": r.choice(COLORS), "time": r.choice(TIMES),
        "friend": ANIMALS[r.randrange(12)], "tip": r.choice(TIPS),
    }


def reading_text(key, day=None):
    day = day or today()
    a = ANIMALS[_KEYS.index(key)]
    x = reading(key, day)
    f = x["friend"]
    return (f"🔮 <b>ទស្សន៍ទាយឆ្នាំ{a[2]} {a[1]}</b>\n📅 {day.strftime('%d/%m/%Y')}\n\n"
            f"🌟 រួម៖ {x['overall']}\n\n"
            f"💕 <b>ស្នេហា</b> {x['love'][0]}\n{html.escape(x['love'][1])}\n\n"
            f"💰 <b>លុយកាក់</b> {x['money'][0]}\n{html.escape(x['money'][1])}\n\n"
            f"💼 <b>ការងារ/ការសិក្សា</b> {x['work'][0]}\n{html.escape(x['work'][1])}\n\n"
            f"🩺 <b>សុខភាព</b> {x['health'][0]}\n{html.escape(x['health'][1])}\n\n"
            f"🍀 លេខសំណាង <b>{x['number']}</b> · ពណ៌ {x['color']}\n⏰ ពេលល្អ៖ {x['time']}\n"
            f"🤝 ឆ្នាំត្រូវគ្នាថ្ងៃនេះ៖ {f[1]} {f[2]}\n\n💡 {html.escape(x['tip'])}\n\n"
            "<i>✨ សម្រាប់កម្សាន្ត — ជោគវាសនាពិតនៅក្នុងដៃអ្នក 💪</i>")


def stick_for(user_id, day=None):
    day = day or today()
    r = _rng("stick", day.isoformat(), user_id)
    n = r.randrange(len(STICKS))
    return n + 1, STICKS[n]


@contextmanager
def _connect(config):
    conn = sqlite3.connect(getattr(config, "BOT_DB_PATH", "bot_database.db"), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def register(bot, config, ask_gemini=None, ask_vision=None):
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    with _connect(config) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS fortune_users (user_id INTEGER PRIMARY KEY, animal TEXT NOT NULL)")

    waiting = {}   # user_id -> ("year"|"ask", expires)
    used, lock = {}, threading.Lock()

    def my_animal(uid):
        with _connect(config) as conn:
            row = conn.execute("SELECT animal FROM fortune_users WHERE user_id=?", (uid,)).fetchone()
        return row["animal"] if row and row["animal"] in _KEYS else None

    def save_animal(uid, key):
        with _connect(config) as conn:
            conn.execute("INSERT OR REPLACE INTO fortune_users(user_id, animal) VALUES (?, ?)", (uid, key))

    def privileged(uid):
        return uid is not None and (uid == getattr(config, "OWNER_ID", 0) or uid in getattr(config, "ADMIN_IDS", []))

    def take(uid):
        if privileged(uid):
            return True
        day = today().isoformat()
        with lock:
            for k in [k for k in used if k[1] != day]:
                used.pop(k, None)
            n = used.get((uid, day), 0)
            if n >= AI_DAILY_LIMIT:
                return False
            used[(uid, day)] = n + 1
            return True

    def menu(uid):
        m = InlineKeyboardMarkup()
        mine = my_animal(uid)
        if mine:
            a = ANIMALS[_KEYS.index(mine)]
            m.row(InlineKeyboardButton(f"🔮 ថ្ងៃនេះរបស់ខ្ញុំ ({a[1]} {a[2]})", callback_data=f"fort:a:{mine}"))
        row = []
        for key, emoji, km in ANIMALS:
            row.append(InlineKeyboardButton(f"{emoji} {km}", callback_data=f"fort:a:{key}"))
            if len(row) == 4:
                m.row(*row)
                row = []
        m.row(InlineKeyboardButton("🔢 រកតាមឆ្នាំកំណើត", callback_data="fort:year"),
              InlineKeyboardButton("🎋 ចាប់ឆ្នោត", callback_data="fort:stick"))
        m.row(InlineKeyboardButton("🔢 លេខជីវិត (ថ្ងៃខែឆ្នាំកំណើត)", callback_data="fort:num"))
        m.row(InlineKeyboardButton("🖐️ ទាយបាតដៃ", callback_data="fort:palm"),
              InlineKeyboardButton("💭 ទាយសុបិន", callback_data="fort:dream"))
        m.row(InlineKeyboardButton("🤖 សួរ AI ទស្សន៍ទាយ", callback_data="fort:ask"))
        m.row(InlineKeyboardButton("🏠 ម៉ឺនុយមេ", callback_data="nav:main"))
        return m

    PALM_ASK = ("🖐️ <b>ទាយបាតដៃ</b>\nថតរូប <b>បាតដៃស្ដាំ</b> (ឬដៃដែលអ្នកប្រើច្រើន) បើកឲ្យត្រង់ ពន្លឺល្អ ហើយផ្ញើមកទីនេះ 📸\n"
                f"<i>({AI_DAILY_LIMIT} ដង/ថ្ងៃ · សម្រាប់កម្សាន្ត)</i>")
    DREAM_ASK = ("💭 <b>ទាយសុបិន</b>\nរៀបរាប់សុបិនរបស់អ្នក ឧ. <i>សុបិនឃើញពស់ធំពណ៌មាស ចូលផ្ទះ</i>\n"
                 f"<i>({AI_DAILY_LIMIT} ដង/ថ្ងៃ · សម្រាប់កម្សាន្ត)</i>")
    MENU_TEXT = ("🔮 <b>ទស្សន៍ទាយ</b>\n\nជ្រើសឆ្នាំកំណើតរបស់អ្នក (១២ ឆ្នាំខ្មែរ) 👇\n"
                 "ឬសាក 🔢 លេខជីវិត · 🖐️ ទាយបាតដៃ · 💭 ទាយសុបិន\n"
                 "មិនដឹងឆ្នាំខ្លួនឯង? ចុច 🔢 ហើយវាយឆ្នាំកំណើត\n\n<i>✨ សម្រាប់កម្សាន្ត</i>")

    def reading_markup(key):
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("🎋 ចាប់ឆ្នោត", callback_data="fort:stick"),
              InlineKeyboardButton("🤖 សួរ AI", callback_data="fort:ask"))
        m.row(InlineKeyboardButton("⬅️ ឆ្នាំផ្សេង", callback_data="fort:menu"))
        return m

    def show(chat_id, message_id, text, markup):
        try:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode="HTML")
        except Exception as exc:
            if "message is not modified" not in str(exc):
                bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")

    def year_reply(message, text):
        try:
            year = int("".join(ch for ch in text if ch.isdigit())[:4])
        except ValueError:
            year = 0
        if not 1900 <= year <= today().year:
            bot.reply_to(message, "❓ សូមវាយឆ្នាំកំណើត ៤ ខ្ទង់ ឧ. <code>1995</code>", parse_mode="HTML")
            return
        a = animal_for_year(year)
        save_animal(message.from_user.id, a[0])
        bot.reply_to(message, f"🎉 កើតឆ្នាំ {year} = ឆ្នាំ<b>{a[2]}</b> {a[1]}\n"
                     "<i>(កើតមុនបុណ្យចូលឆ្នាំខ្មែរ ខែមេសា អាចជាឆ្នាំមុន)</i>\n\n" + reading_text(a[0]),
                     reply_markup=reading_markup(a[0]), parse_mode="HTML")

    def ai_reply(message, question, kind="ask"):
        uid = message.from_user.id
        q = " ".join((question or "").split())[:300]
        mine = my_animal(uid)
        if ask_gemini is None or not getattr(config, "GEMINI_API_KEY", ""):
            bot.reply_to(message, "AI មិនទាន់បើកដំណើរការទេ។ សាកមើលទស្សន៍ទាយប្រចាំថ្ងៃសិន 🔮")
            return
        if not take(uid):
            bot.reply_to(message, f"អ្នកសួរ AI ទស្សន៍ទាយអស់ {AI_DAILY_LIMIT} ដងថ្ងៃនេះហើយ។ ត្រឡប់មកថ្ងៃស្អែក 🙏")
            return
        try:
            bot.send_chat_action(message.chat.id, "typing")
        except Exception:
            pass
        animal = f"{ANIMALS[_KEYS.index(mine)][2]} ({mine})" if mine else "unknown"
        prompt = DREAM_PROMPT.format(q=q) if kind == "dream" else AI_PROMPT.format(animal=animal, q=q)
        try:
            answer = (ask_gemini(prompt) or "").strip()
        except Exception as exc:
            logger.warning("fortune AI failed (%s)", type(exc).__name__)
            answer = ""
        if not answer:
            bot.reply_to(message, "សុំទោស គ្រូទាយ AI រវល់បន្តិច 😅 សូមសាកម្ដងទៀត")
            return
        head = "💭 <b>ទាយសុបិន</b>" if kind == "dream" else "🔮 <b>គ្រូទាយ Cooler AI</b>"
        bot.reply_to(message, head + "\n\n" + html.escape(answer[:3000])
                     + "\n\n<i>✨ សម្រាប់កម្សាន្ត</i>", parse_mode="HTML", reply_markup=reading_markup(mine or "rat"))

    @bot.message_handler(commands=["fortune", "horoscope", "tumneay", "zodiac"])
    def fortune_cmd(message):
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) > 1 and any(ch.isdigit() for ch in parts[1]):
            year_reply(message, parts[1])
            return
        bot.reply_to(message, MENU_TEXT, reply_markup=menu(message.from_user.id), parse_mode="HTML")

    def number_reply(message, text):
        born = parse_birth_date(text)
        if born is None or not 1900 <= born.year <= today().year:
            bot.reply_to(message, "❓ វាយថ្ងៃខែឆ្នាំកំណើតបែបនេះ៖ <code>15/08/1995</code>", parse_mode="HTML")
            return
        save_animal(message.from_user.id, animal_for_year(born.year)[0])
        bot.reply_to(message, number_text(born), parse_mode="HTML", reply_markup=reading_markup(None))

    def palm_reply(message):
        uid = message.from_user.id
        if ask_vision is None or not getattr(config, "GEMINI_API_KEY", ""):
            bot.reply_to(message, "AI មើលរូបមិនទាន់បើកដំណើរការទេ 🙏")
            return
        if not take(uid):
            bot.reply_to(message, f"អ្នកប្រើ AI ទស្សន៍ទាយអស់ {AI_DAILY_LIMIT} ដងថ្ងៃនេះហើយ។ ត្រឡប់មកថ្ងៃស្អែក 🙏")
            return
        try:
            bot.send_chat_action(message.chat.id, "typing")
            info = bot.get_file(message.photo[-1].file_id)
            data = bot.download_file(info.file_path)
            answer = (ask_vision(PALM_PROMPT, data) or "").strip()
        except Exception as exc:
            logger.warning("palm reading failed (%s)", type(exc).__name__)
            answer = ""
        if not answer:
            bot.reply_to(message, "សុំទោស មើលបាតដៃមិនទាន់បាន 😅 សូមថតម្ដងទៀតឲ្យច្បាស់ ពន្លឺល្អ")
            return
        bot.reply_to(message, "🖐️ <b>ទាយបាតដៃ</b>\n\n" + html.escape(answer[:3000]) + "\n\n<i>✨ សម្រាប់កម្សាន្ត</i>",
                     parse_mode="HTML", reply_markup=reading_markup(None))

    @bot.message_handler(commands=["number", "lifenumber", "numerology"])
    def number_cmd(message):
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) > 1:
            number_reply(message, parts[1])
        else:
            waiting[message.from_user.id] = ("num", time.monotonic() + 600)
            bot.reply_to(message, "🔢 វាយថ្ងៃខែឆ្នាំកំណើត ឧ. <code>15/08/1995</code>", parse_mode="HTML")

    @bot.message_handler(commands=["dream"])
    def dream_cmd(message):
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) > 1:
            ai_reply(message, parts[1], "dream")
        else:
            waiting[message.from_user.id] = ("dream", time.monotonic() + 600)
            bot.reply_to(message, DREAM_ASK, parse_mode="HTML")

    @bot.message_handler(commands=["palm"])
    def palm_cmd(message):
        waiting[message.from_user.id] = ("palm", time.monotonic() + 600)
        bot.reply_to(message, PALM_ASK, parse_mode="HTML")

    def wants_palm(message):
        st = waiting.get(getattr(getattr(message, "from_user", None), "id", None))
        return bool(st and st[0] == "palm" and st[1] > time.monotonic())

    @bot.message_handler(func=wants_palm, content_types=["photo"])
    def palm_photo(message):
        waiting.pop(message.from_user.id, None)
        palm_reply(message)

    def is_waiting(message):
        user = getattr(message, "from_user", None)
        if user is None or not getattr(message, "text", None) or message.text.startswith("/"):
            return False
        st = waiting.get(user.id)
        return bool(st and st[1] > time.monotonic())

    @bot.message_handler(func=is_waiting, content_types=["text"])
    def fortune_follow(message):
        kind, _ = waiting.pop(message.from_user.id)
        if kind == "year":
            year_reply(message, message.text)
        elif kind == "num":
            number_reply(message, message.text)
        elif kind == "palm":
            waiting[message.from_user.id] = ("palm", time.monotonic() + 600)
            bot.reply_to(message, "📸 សូមផ្ញើ <b>រូបថត</b> បាតដៃ (មិនមែនអក្សរ) 🖐️", parse_mode="HTML")
        else:
            ai_reply(message, message.text, kind)

    @bot.callback_query_handler(func=lambda call: (call.data or "").startswith("fort:"))
    def fortune_cb(call):
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        message = getattr(call, "message", None)
        if message is None:
            return
        uid = call.from_user.id
        parts = (call.data or "").split(":")
        action = parts[1] if len(parts) > 1 else "menu"
        if action == "a" and len(parts) > 2 and parts[2] in _KEYS:
            save_animal(uid, parts[2])
            show(message.chat.id, message.message_id, reading_text(parts[2]), reading_markup(parts[2]))
        elif action == "stick":
            n, text = stick_for(uid)
            bot.send_message(message.chat.id, f"🙏 អធិស្ឋាន… អង្រួនបំពង់ឆ្នោត… 🎋\n\n<b>ឆ្នោតលេខ {n}</b>\n{html.escape(text)}\n\n"
                             "<i>ម្នាក់បាន ១ ឆ្នោត/ថ្ងៃ · សម្រាប់កម្សាន្ត</i>", parse_mode="HTML")
        elif action == "year":
            waiting[uid] = ("year", time.monotonic() + 600)
            bot.send_message(message.chat.id, "🔢 វាយឆ្នាំកំណើតរបស់អ្នក ឧ. <code>1995</code>", parse_mode="HTML")
        elif action == "num":
            waiting[uid] = ("num", time.monotonic() + 600)
            bot.send_message(message.chat.id, "🔢 វាយថ្ងៃខែឆ្នាំកំណើត ឧ. <code>15/08/1995</code>", parse_mode="HTML")
        elif action == "palm":
            waiting[uid] = ("palm", time.monotonic() + 600)
            bot.send_message(message.chat.id, PALM_ASK, parse_mode="HTML")
        elif action == "dream":
            waiting[uid] = ("dream", time.monotonic() + 600)
            bot.send_message(message.chat.id, DREAM_ASK, parse_mode="HTML")
        elif action == "ask":
            waiting[uid] = ("ask", time.monotonic() + 600)
            bot.send_message(message.chat.id, "🤖 <b>សួរគ្រូទាយ AI</b>\nសរសេរសំណួរ ឧ. <i>ខែនេះការងារខ្ញុំយ៉ាងម៉េច?</i> "
                             f"({AI_DAILY_LIMIT} ដង/ថ្ងៃ)", parse_mode="HTML")
        else:
            show(message.chat.id, message.message_id, MENU_TEXT, menu(uid))

    return SimpleNamespace(waiting=waiting, menu=menu)
