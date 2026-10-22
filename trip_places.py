"""/trip — good places to visit in Cambodia, grouped by province (Khmer).

Each place has a Google Maps search button (no API key needed). Drive times are
rough estimates from Phnom Penh by car and change with road and traffic.
"""
import html
import random
from urllib.parse import quote_plus

# (key, Khmer name, English name, emoji, rough drive time from Phnom Penh, places)
# place = (Khmer name, English name for the map search, Khmer description, tip)
PROVINCES = (
    ("sr", "សៀមរាប", "Siem Reap", "🛕", "≈ ៥-៦ ម៉ោង", (
        ("ប្រាសាទអង្គរវត្ត", "Angkor Wat", "បេតិកភណ្ឌពិភពលោក UNESCO មើលថ្ងៃរះស្អាតបំផុត", "ទៅដល់ម៉ោង ៥ ព្រឹក ដើម្បីមើលថ្ងៃរះ"),
        ("ប្រាសាទបាយ័ន", "Bayon Temple", "ប្រាសាទមុខបួន នៅកណ្ដាលអង្គរធំ", "ថតរូបល្អពេលព្រឹក"),
        ("ប្រាសាទតាព្រហ្ម", "Ta Prohm", "ប្រាសាទដើមឈើធំឱបជញ្ជាំង", "ទៅពេលព្រឹកមុនភ្ញៀវច្រើន"),
        ("ប្រាសាទបន្ទាយស្រី", "Banteay Srei", "ចម្លាក់ថ្មភក់ពណ៌ផ្កាឈូក ល្អិតល្អន់បំផុត", "ចម្ងាយ ≈ ៣៥ គ.ម ពីក្រុង"),
        ("ភ្នំគូលែន", "Phnom Kulen National Park", "ទឹកធ្លាក់ ព្រះសយនៈ និងលិង្គមួយពាន់", "រដូវភ្លៀងទឹកធ្លាក់ខ្លាំងស្អាត"),
        ("ភូមិបណ្ដែតទឹកកំពង់ភ្លុក", "Kampong Phluk", "ផ្ទះជើងសសរខ្ពស់ ជិះទូកកាត់ព្រៃលិចទឹក", "ល្អបំផុតខែ ៨-១២"),
    )),
    ("pp", "ភ្នំពេញ", "Phnom Penh", "🏙️", "—", (
        ("ព្រះបរមរាជវាំង", "Royal Palace Phnom Penh", "វាំង និងវត្តព្រះកែវមរកត", "ស្លៀកពាក់សមរម្យ គ្របស្មា និងជង្គង់"),
        ("សារមន្ទីរជាតិ", "National Museum of Cambodia", "រូបចម្លាក់បុរាណខ្មែររាប់ពាន់", "នៅជាប់វាំង ដើរទៅបាន"),
        ("វត្តភ្នំ", "Wat Phnom", "និមិត្តរូបនៃទីក្រុងភ្នំពេញ", "ល្អសម្រាប់ដើរលេងពេលល្ងាច"),
        ("មាត់ទន្លេ ព្រះស៊ីសុវត្ថិ", "Sisowath Quay Riverside", "ដើរលេងតាមមាត់ទន្លេ មើលថ្ងៃលិច", "ជិះទូកលេងពេលល្ងាចក៏បាន"),
        ("ផ្សារធំថ្មី", "Central Market Phnom Penh", "ផ្សារអគារ Art Deco លំដាប់ពិភពលោក", "ទៅពេលព្រឹក មិនសូវក្ដៅ"),
        ("កោះដាច់", "Koh Dach", "កោះសូត្រ ភូមិត្បាញ និងឆ្នេរទន្លេ", "ជិះកង់ ឬម៉ូតូលេងពេញមួយថ្ងៃ"),
    )),
    ("kp", "កំពត", "Kampot", "🌶️", "≈ ៣ ម៉ោង", (
        ("ឧទ្យានជាតិបូកគោ", "Bokor National Park", "ភ្នំត្រជាក់ អគារបុរាណ និងទេសភាពសមុទ្រ", "យកអាវក្រៅ ព្រោះលើភ្នំត្រជាក់"),
        ("មាត់ទន្លេកំពត", "Kampot River", "ជិះទូកមើលថ្ងៃលិច និងអំពិលអំពែក", "ពេលល្ងាចម៉ោង ៥ ល្អបំផុត"),
        ("ចំការម្រេចកំពត", "Kampot pepper farm", "ម្រេចកំពតល្បីពិភពលោក ភ្លក់ និងទិញផ្ទាល់", "ទិញធ្វើកាដូល្អណាស់"),
    )),
    ("kep", "កែប", "Kep", "🦀", "≈ ៣ ម៉ោងកន្លះ", (
        ("ផ្សារក្ដាមកែប", "Kep Crab Market", "ក្ដាមស្រស់ឆាម្រេចខ្ចី ឆ្ងាញ់ណាស់", "ទៅញ៉ាំថ្ងៃត្រង់ ឬល្ងាច"),
        ("កោះទន្សាយ", "Koh Tonsay Rabbit Island", "ឆ្នេរស្ងាត់ ជិះទូកពីកែប ≈ ២០ នាទី", "ទូកចុងក្រោយត្រឡប់មកវិញរសៀល"),
        ("ឧទ្យានជាតិកែប", "Kep National Park", "ផ្លូវដើរព្រៃ មើលទេសភាពសមុទ្រ", "ពាក់ស្បែកជើងដើរស្រួល"),
    )),
    ("shv", "ព្រះសីហនុ", "Sihanoukville", "🏖️", "≈ ២-៣ ម៉ោង (ផ្លូវល្បឿនលឿន)", (
        ("កោះរ៉ុង", "Koh Rong", "ឆ្នេរខ្សាច់ស ទឹកថ្លា និងប្លង់តុនភ្លឺពេលយប់", "ជិះកាណូតលឿនពីក្រុង"),
        ("កោះរ៉ុងសន្លឹម", "Koh Rong Samloem", "ឆ្នេរស្ងាត់ សម្រាកបែបធម្មជាតិ", "រដូវប្រាំង (ខែ ១១-៥) សមុទ្ររលកតិច"),
        ("ឆ្នេរអូរត្រេះ", "Otres Beach", "ឆ្នេរវែង ស្ងប់ស្ងាត់ជាងកណ្ដាលក្រុង", "មើលថ្ងៃលិចស្អាត"),
        ("ឧទ្យានជាតិរាម", "Ream National Park", "ព្រៃកោងកាង ឆ្នេរ និងជិះទូក", "ជិតព្រលានយន្តហោះ"),
    )),
    ("kk", "កោះកុង", "Koh Kong", "🌿", "≈ ៥ ម៉ោង", (
        ("ទឹកធ្លាក់តាតៃ", "Tatai Waterfall", "ទឹកធ្លាក់ធំ ហែលទឹកបាន (រដូវប្រាំង)", "រដូវភ្លៀងទឹកខ្លាំង ប្រយ័ត្ន"),
        ("ឈីផាត់", "Chi Phat", "ទេសចរណ៍សហគមន៍ ដើរព្រៃ ជិះកង់ភ្នំក្រវាញ", "ស្នាក់នៅផ្ទះអ្នកភូមិ"),
        ("ព្រៃកោងកាងពាមក្រសោប", "Peam Krasop Mangrove", "ស្ពានឈើដើរកាត់ព្រៃកោងកាង", "ទៅពេលល្ងាចមិនសូវក្ដៅ"),
    )),
    ("btb", "បាត់ដំបង", "Battambang", "🚂", "≈ ៥ ម៉ោង", (
        ("រថភ្លើងឫស្សី", "Bamboo Train Battambang", "ជិះណូរីលើផ្លូវដែក សប្បាយៗ", "ល្អសម្រាប់ក្រុម"),
        ("ភ្នំសំពៅ", "Phnom Sampov", "មើលប្រចៀវរាប់លានហើរចេញពីល្អាង", "ទៅដល់ម៉ោង ៥ ល្ងាច"),
        ("ប្រាសាទឯកភ្នំ", "Wat Ek Phnom", "ប្រាសាទបុរាណសតវត្សទី ១១", "ជិះម៉ូតូតាមផ្លូវជនបទស្អាត"),
        ("ប្រាសាទបាណន់", "Phnom Banan", "ប្រាសាទលើកំពូលភ្នំ ឡើងជណ្ដើរ ៣៥៨", "យកទឹកទៅផឹកផង"),
    )),
    ("mdk", "មណ្ឌលគិរី", "Mondulkiri", "⛰️", "≈ ៦ ម៉ោង", (
        ("ទឹកធ្លាក់ប៊ូស្រា", "Bousra Waterfall", "ទឹកធ្លាក់ពីរជាន់ធំ ល្បីបំផុតនៅមណ្ឌលគិរី", "ល្អបំផុតខែ ៨-១១"),
        ("ទេសភាពភ្នំ សែនមនោរម្យ", "Sen Monorom viewpoint", "ភ្នំស្មៅបៃតង អាកាសធាតុត្រជាក់ និងសមុទ្រអ័ព្ទពេលព្រឹក", "ក្រោកពីព្រលឹមមើលអ័ព្ទ"),
        ("ជ្រលងដំរី", "Elephant Valley Project", "មើលដំរីរស់នៅក្នុងព្រៃដោយសេរី (មិនជិះដំរី)", "ត្រូវកក់មុន"),
    )),
    ("rtk", "រតនគិរី", "Ratanakiri", "🌋", "≈ ៩ ម៉ោង", (
        ("បឹងយក្សឡោម", "Yeak Laom Lake", "បឹងរង្វង់មូលក្នុងរណ្ដៅភ្នំភ្លើងបុរាណ ទឹកថ្លា", "ហែលទឹកបាន"),
        ("ទឹកធ្លាក់កាចាញ", "Ka Chanh Waterfall", "ទឹកធ្លាក់ក្នុងព្រៃ ជិតក្រុងបានលុង", "ផ្លូវដីក្រហម ប្រយ័ត្នរដូវភ្លៀង"),
        ("ទឹកធ្លាក់ចាអុង", "Cha Ong Waterfall", "ដើរចូលក្រោយខ្នងទឹកធ្លាក់បាន", "យកខោអាវប្ដូរផង"),
    )),
    ("krt", "ក្រចេះ", "Kratie", "🐬", "≈ ៥ ម៉ោង", (
        ("ផ្សោតទឹកសាបកំពី", "Kampi Dolphin Pool", "មើលផ្សោតអ៊ីរ៉ាវ៉ាឌីដ៏កម្រ ក្នុងទន្លេមេគង្គ", "ពេលព្រឹក ឬល្ងាចឃើញច្រើន"),
        ("កោះទ្រង់", "Koh Trong", "កោះស្ងប់ស្ងាត់ ជិះកង់ជុំវិញកោះ", "ជិះទូកឆ្លងពីក្រុងក្រចេះ"),
    )),
    ("pvh", "ព្រះវិហារ", "Preah Vihear", "🏯", "≈ ៧ ម៉ោង", (
        ("ប្រាសាទព្រះវិហារ", "Preah Vihear Temple", "ប្រាសាទលើច្រាំងភ្នំដងរែក បេតិកភណ្ឌ UNESCO", "ទេសភាពពីលើស្អាតខ្លាំង"),
        ("ប្រាសាទកោះកេរ", "Koh Ker", "ប្រាសាទពីរ៉ាមីត ៧ ជាន់ បេតិកភណ្ឌ UNESCO", "ទៅពីសៀមរាប ≈ ២ ម៉ោងកន្លះ"),
    )),
    ("kt", "កំពង់ធំ", "Kampong Thom", "🧱", "≈ ៣ ម៉ោង", (
        ("ប្រាសាទសំបូរព្រៃគុក", "Sambor Prei Kuk", "ប្រាសាទឥដ្ឋមុនសម័យអង្គរ បេតិកភណ្ឌ UNESCO", "ឈប់លេងពេលធ្វើដំណើរទៅសៀមរាប"),
    )),
    ("kc", "កំពង់ចាម", "Kampong Cham", "🌉", "≈ ២ ម៉ោងកន្លះ", (
        ("ស្ពានឫស្សីកោះប៉ែន", "Koh Paen Bamboo Bridge", "ស្ពានឫស្សីវែង (មានតែរដូវប្រាំង)", "ពិនិត្យមុនទៅ ព្រោះរដូវភ្លៀងរុះចេញ"),
        ("វត្តនគរបាជ័យ", "Wat Nokor Bachey", "ប្រាសាទបុរាណនៅក្នុងវត្ត", "ជិតក្រុងកំពង់ចាម"),
        ("ភ្នំប្រុស ភ្នំស្រី", "Phnom Pros Phnom Srei", "ភ្នំពីរ មានរឿងព្រេង និងស្វាច្រើន", "ឡើងមើលថ្ងៃលិច"),
    )),
    ("tk", "តាកែវ", "Takeo", "⛩️", "≈ ១ ម៉ោងកន្លះ", (
        ("ប្រាសាទភ្នំជីសូរ", "Phnom Chisor", "ប្រាសាទលើភ្នំ មើលវាលស្រែជុំវិញ", "ឡើងពេលព្រឹកមិនសូវក្ដៅ"),
        ("ភ្នំដា", "Phnom Da Angkor Borei", "ប្រាសាទសម័យហ្វូណន ជិះទូកតាមព្រែក", "រដូវទឹកឡើងជិះទូកទៅដល់"),
    )),
    ("ksp", "កំពង់ស្ពឺ", "Kampong Speu", "🌲", "≈ ២ ម៉ោងកន្លះ", (
        ("ឧទ្យានជាតិគិរីរម្យ", "Kirirom National Park", "ព្រៃស្រល់ អាកាសត្រជាក់ ទឹកធ្លាក់ និងបោះតង់", "ល្អសម្រាប់ Trip ក្រុមចុងសប្ដាហ៍"),
    )),
)

_BY_KEY = {p[0]: p for p in PROVINCES}

INTRO = (
    "🧭 <b>Trip ដើរលេងកម្ពុជា</b>\n\n"
    "ជ្រើសខេត្តខាងក្រោម ដើម្បីមើលកន្លែងល្អៗ ហើយចុច 📍 ដើម្បីបើក Google Maps។\n"
    "🎲 ចុច <b>Trip ចៃដន្យ</b> បើមិនទាន់ដឹងទៅណា!\n\n"
    "<i>ម៉ោងធ្វើដំណើរគិតពីភ្នំពេញ ជាការប៉ាន់ស្មានប៉ុណ្ណោះ</i>"
)


def map_url(place_en, province_en):
    return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(f"{place_en}, {province_en}, Cambodia")


def province_text(key):
    _k, km, en, emoji, drive, places = _BY_KEY[key]
    lines = [f"{emoji} <b>ខេត្ត{km}</b>" if key != "pp" else f"{emoji} <b>រាជធានី{km}</b>"]
    if drive != "—":
        lines.append(f"🚗 ពីភ្នំពេញ {drive}")
    lines.append("")
    for i, (pkm, _pen, desc, tip) in enumerate(places, 1):
        lines.append(f"{i}. <b>{html.escape(pkm)}</b>\n   {html.escape(desc)}\n   💡 {html.escape(tip)}")
    return "\n".join(lines)


def random_place(rng=random):
    prov = rng.choice(PROVINCES)
    return prov, rng.choice(prov[5])


def place_count():
    return sum(len(p[5]) for p in PROVINCES)


def register(bot, config=None):
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    def menu_markup():
        m = InlineKeyboardMarkup()
        row = []
        for key, km, _en, emoji, _d, _p in PROVINCES:
            row.append(InlineKeyboardButton(f"{emoji} {km}", callback_data=f"trip:p:{key}"))
            if len(row) == 3:
                m.row(*row)
                row = []
        if row:
            m.row(*row)
        m.row(InlineKeyboardButton("🎲 Trip ចៃដន្យ", callback_data="trip:rand"))
        m.row(InlineKeyboardButton("🏠 ម៉ឺនុយមេ", callback_data="nav:main"))
        return m

    def province_markup(key):
        _k, _km, en, _e, _d, places = _BY_KEY[key]
        m = InlineKeyboardMarkup()
        for _pkm, pen, _desc, _tip in places:
            m.row(InlineKeyboardButton(f"📍 {pen}", url=map_url(pen, en)))
        m.row(InlineKeyboardButton("⬅️ ខេត្តផ្សេង", callback_data="trip:menu"),
              InlineKeyboardButton("🎲 ចៃដន្យ", callback_data="trip:rand"))
        return m

    def random_view():
        prov, (pkm, pen, desc, tip) = random_place()
        text = (f"🎲 <b>Trip ចៃដន្យសម្រាប់ Team!</b>\n\n{prov[3]} ខេត្ត{prov[1]}"
                + (f" · 🚗 {prov[4]}" if prov[4] != "—" else "")
                + f"\n\n<b>{html.escape(pkm)}</b>\n{html.escape(desc)}\n💡 {html.escape(tip)}\n\n"
                "ចូលចិត្តទេ? បើមិនចូលចិត្ត ចុច 🎲 ម្ដងទៀត 😄")
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton(f"📍 {pen}", url=map_url(pen, prov[2])))
        m.row(InlineKeyboardButton("🎲 ម្ដងទៀត", callback_data="trip:rand"),
              InlineKeyboardButton(f"{prov[3]} មើលខេត្តនេះ", callback_data=f"trip:p:{prov[0]}"))
        m.row(InlineKeyboardButton("⬅️ ខេត្តទាំងអស់", callback_data="trip:menu"))
        return text, m

    @bot.message_handler(commands=["trip", "travel"])
    def trip(message):
        bot.reply_to(message, INTRO, reply_markup=menu_markup(), parse_mode="HTML")

    @bot.callback_query_handler(func=lambda call: (call.data or "").startswith("trip:"))
    def trip_cb(call):
        message = getattr(call, "message", None)
        parts = (call.data or "").split(":")
        if parts[1:2] == ["p"] and len(parts) > 2 and parts[2] in _BY_KEY:
            text, markup = province_text(parts[2]), province_markup(parts[2])
        elif parts[1:2] == ["rand"]:
            text, markup = random_view()
        else:
            text, markup = INTRO, menu_markup()
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        if message is None:
            return
        try:
            bot.edit_message_text(text, message.chat.id, message.message_id,
                                  reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)
        except Exception as exc:
            if "message is not modified" not in str(exc):
                bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")

    return menu_markup
