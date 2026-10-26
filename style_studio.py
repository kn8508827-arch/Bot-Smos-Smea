"""💇 Style Studio — try a haircut or a photo pose on your own selfie.

/style → 👨 ប្រុស · 👩 ស្រី · 👴 ចាស់ · 👶 ក្មេង → 💇 ម៉ូដសក់ or 📸 ស្ទាយថតរូប → pick one → send a selfie.
The Hub's 💇 sheet opens t.me/<bot>?start=go_sty_<key> → "/style <key>" → straight to "send a selfie".
The last selfie is kept for STYLE_TTL seconds, so a person can try several styles without sending it again.
Uses the Photo Studio pipeline (Gemini image edit) and shares its daily limit.
"""
import html
import logging
import time

logger = logging.getLogger(__name__)
STYLE_TTL = 15 * 60

GROUPS = {   # code: (button, who — for the prompt)
    "m": ("👨 ប្រុស", "man"), "w": ("👩 ស្រី", "woman"),
    "e": ("👴 មនុស្សចាស់", "older person"), "k": ("👶 ក្មេង", "child"),
}
KINDS = {"hair": "💇 ម៉ូដសក់", "pose": "📸 ស្ទាយថតរូប"}

# key: (group, kind, Khmer label, what to make — English for the image model)
ITEMS = {
    # 👨 men · hair
    "m_twoblock": ("m", "hair", "✂️ Two Block កូរ៉េ", "a Korean two-block cut: short clipped sides, longer soft textured top falling forward"),
    "m_fade": ("m", "hair", "💈 Skin Fade", "a clean skin fade on the sides and back with a short textured crop on top"),
    "m_undercut": ("m", "hair", "🔥 Undercut", "a slick undercut: shaved sides, longer top styled back with a natural shine"),
    "m_crop": ("m", "hair", "🧢 French Crop", "a French crop: short faded sides and a short textured top with a blunt fringe"),
    "m_curtain": ("m", "hair", "🎭 Curtain ចែកកណ្ដាល", "curtain hair: medium length, parted in the middle, falling softly on both sides of the forehead"),
    "m_quiff": ("m", "hair", "🕺 Quiff", "a modern quiff: short sides, voluminous top brushed up and back"),
    "m_perm": ("m", "hair", "🌀 Perm រួញកូរ៉េ", "a Korean wavy perm: soft loose curls on top with neat shorter sides"),
    "m_buzz": ("m", "hair", "⚡ Buzz Cut", "a very short even buzz cut with a sharp hairline"),
    # 👩 women · hair
    "w_bob": ("w", "hair", "💁‍♀️ Bob ខ្លី", "a sleek chin-length bob with a soft inward curve"),
    "w_layer": ("w", "hair", "🌊 Layer វែង", "long layered hair with face-framing layers and soft volume"),
    "w_bangs": ("w", "hair", "🎀 Curtain Bangs", "long hair with airy curtain bangs parted in the middle"),
    "w_wavy": ("w", "hair", "🌸 រលកធំ", "long glossy hair styled in big loose waves"),
    "w_hush": ("w", "hair", "✨ Hush Cut", "a Korean hush cut: shoulder-length with wispy feathered layers"),
    "w_pony": ("w", "hair", "🎗️ Ponytail ខ្ពស់", "a sleek high ponytail with a few soft face-framing strands"),
    "w_bun": ("w", "hair", "🍡 Bun សក់ចង", "a neat elegant low bun with soft loose strands"),
    "w_pixie": ("w", "hair", "🧚 Pixie", "a chic short pixie cut with soft textured top"),
    # 👴 older people · hair
    "e_classic": ("e", "hair", "🎩 ចែកចំហៀងបុរាណ", "a neat classic side-part, short and well combed, natural grey hair"),
    "e_silver": ("e", "hair", "🤍 Silver ខ្លីស្អាត", "a short tidy silver haircut with gentle volume, modern and elegant"),
    "e_crew": ("e", "hair", "💈 Crew Cut", "a tidy short crew cut, natural grey"),
    "e_bobsilver": ("e", "hair", "👵 Bob ពណ៌ប្រាក់", "an elegant silver bob with soft volume"),
    "e_curls": ("e", "hair", "🌼 រួញខ្លីទន់", "soft short curls, neat and voluminous, natural grey"),
    "e_bun": ("e", "hair", "🪷 Bun បុរាណ", "a classic neat low bun, tidy and dignified"),
    # 👶 children · hair
    "k_mushroom": ("k", "hair", "🍄 សក់ផ្សិត", "a cute rounded mushroom cut with a straight fringe"),
    "k_spiky": ("k", "hair", "🦔 ស្រួចឡើងលើ", "short spiky hair styled up, playful"),
    "k_side": ("k", "hair", "👦 ចែកចំហៀង", "a neat short side-part for a boy"),
    "k_bob": ("k", "hair", "👧 Bob មានកន្ទុយ", "a cute bob with straight bangs"),
    "k_pigtails": ("k", "hair", "🎀 ចងពីរ", "two cute pigtails with small ribbons"),
    "k_braids": ("k", "hair", "🌸 ក្រងពីរ", "two neat braids with small hair clips"),
    # 📸 poses (the same person, a new photo)
    "m_lean": ("m", "pose", "🧱 ផ្អែកជញ្ជាំង", "leaning on a textured city wall, one foot up, arms crossed, relaxed confident look, street-style photo"),
    "m_walk": ("m", "pose", "🚶 ដើរ Candid", "walking toward the camera on a city street, mid-step, looking slightly off camera, candid street photo"),
    "m_pocket": ("m", "pose", "😎 ដៃក្នុងហោប៉ៅ", "standing straight with hands in pockets, slight head tilt, half smile, clean modern background"),
    "m_stairs": ("m", "pose", "🪜 អង្គុយជណ្ដើរ", "sitting on outdoor stairs, elbows on knees, looking at the camera, warm afternoon light"),
    "m_golden": ("m", "pose", "🌅 Side Profile ថ្ងៃលិច", "side profile looking into the distance at golden hour, cinematic backlight"),
    "w_hairtouch": ("w", "pose", "💫 ដៃប៉ះសក់", "one hand gently touching her hair, soft smile, dreamy soft light, outdoor garden"),
    "w_shoulder": ("w", "pose", "👀 ងាកមើលក្រោយ", "glancing back over the shoulder with a soft smile, city lights bokeh behind"),
    "w_walk": ("w", "pose", "👗 ដើររំលាយ", "walking gracefully with the outfit flowing, mid-step, golden hour street"),
    "w_cafe": ("w", "pose", "☕ ហាងកាហ្វេ", "sitting by a cafe window holding a coffee cup, natural window light, cozy"),
    "w_flowers": ("w", "pose", "💐 កាន់ផ្កា", "holding a bouquet of flowers near the chest, gentle smile, pastel background"),
    "e_garden": ("e", "pose", "🌳 សួនច្បារ", "sitting on a garden bench smiling warmly, soft morning light, green trees"),
    "e_temple": ("e", "pose", "🛕 ប្រាសាទខ្មែរ", "standing peacefully in front of an Angkor-style Khmer temple, hands together, warm light"),
    "e_chair": ("e", "pose", "🪑 រូបផ្លូវការលើកៅអី", "a dignified formal portrait seated in an elegant wooden chair, classic studio light"),
    "e_tea": ("e", "pose", "🍵 ផឹកតែ", "holding a cup of tea at home, relaxed happy smile, warm cozy light"),
    "k_jump": ("k", "pose", "🤸 លោតសប្បាយ", "jumping happily outdoors in a park, big smile, bright sunny day"),
    "k_balloon": ("k", "pose", "🎈 កាន់ប៉េងប៉ោង", "holding colorful balloons, laughing, bright cheerful background"),
    "k_school": ("k", "pose", "🎒 ទៅសាលា", "wearing a school backpack, waving at the camera, in front of a school gate"),
    "k_park": ("k", "pose", "🛝 លេងនៅសួន", "playing at a playground slide, joyful, sunny afternoon"),
}


def build_prompt(key):
    group, kind, _label, what = ITEMS[key]
    who = GROUPS[group][1]
    if kind == "hair":
        return ("Edit this photo of a real person. Keep the face, facial features, skin tone, age, expression, "
                "clothes, pose and background exactly the same — it must still clearly be the same person. "
                f"Change ONLY the hairstyle to: {what}. Realistic hair texture and hairline, natural colour unless "
                "the style says otherwise, lit like the rest of the photo so it looks like a real salon result. "
                "No text, no watermark.")
    return (f"Make a new realistic photograph of this same real {who}. Keep the face, facial features, skin tone, "
            "age, hairstyle and body exactly the same — it must clearly be the same person. "
            f"Pose and scene: {what}. Tasteful, modest everyday clothing that suits the scene. "
            "Professional photography, natural lighting, sharp focus, vertical portrait framing. No text, no watermark.")


def register(bot, config, studio):
    """studio: dict(enabled, remaining, record, privileged, busy, run) from bot.py's Photo Studio."""
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    pending = {}   # uid -> (item key, expires)
    photos = {}    # uid -> (file_id, expires)

    def group_menu():
        m = InlineKeyboardMarkup()
        g = [InlineKeyboardButton(label, callback_data=f"sty:g:{code}") for code, (label, _w) in GROUPS.items()]
        m.row(*g[:2])
        m.row(*g[2:])
        return m

    def kind_menu(group):
        m = InlineKeyboardMarkup()
        m.row(*[InlineKeyboardButton(label, callback_data=f"sty:k:{group}:{kind}") for kind, label in KINDS.items()])
        m.row(InlineKeyboardButton("⬅️ ត្រឡប់", callback_data="sty:menu"))
        return m

    def items_menu(group, kind):
        m = InlineKeyboardMarkup()
        btns = [InlineKeyboardButton(v[2], callback_data=f"sty:i:{k}") for k, v in ITEMS.items()
                if v[0] == group and v[1] == kind]
        for i in range(0, len(btns), 2):
            m.row(*btns[i:i + 2])
        other = "pose" if kind == "hair" else "hair"
        m.row(InlineKeyboardButton(KINDS[other], callback_data=f"sty:k:{group}:{other}"),
              InlineKeyboardButton("⬅️ ក្រុមផ្សេង", callback_data="sty:menu"))
        return m

    INTRO = ("💇 <b>Style Studio</b> — សាកម៉ូដសក់ និងស្ទាយថតរូបលើរូបខ្លួនឯង ✨\n"
             "ជ្រើសក្រុម 👇 រួចជ្រើសម៉ូដ ហើយផ្ញើ Selfie មួយសន្លឹក")

    def has_photo(uid):
        p = photos.get(uid)
        return bool(p and p[1] > time.monotonic())

    def choose(chat_id, uid, key):
        group, kind, label, _w = ITEMS[key]
        if not studio["enabled"]():
            bot.send_message(chat_id, "💇 Style Studio មិនទាន់បើកនៅឡើយទេ។")
            return
        if has_photo(uid):
            run(chat_id, uid, key, photos[uid][0])
            return
        pending[uid] = (key, time.monotonic() + STYLE_TTL)
        tip = ("មុខត្រង់ ឃើញសក់ទាំងមូល ពន្លឺល្អ" if kind == "hair" else "មុខច្បាស់ ពន្លឺល្អ (រូបពាក់កណ្ដាលខ្លួនកាន់តែល្អ)")
        bot.send_message(chat_id, f"{label} ✅\n📸 ឥឡូវផ្ញើ <b>Selfie មួយសន្លឹក</b> មក — {tip}",
                         parse_mode="HTML")

    def result_markup(key):
        group, kind = ITEMS[key][0], ITEMS[key][1]
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("🔁 សាកម៉ូដផ្សេង (រូបដដែល)", callback_data=f"sty:k:{group}:{kind}"))
        other = "pose" if kind == "hair" else "hair"
        m.row(InlineKeyboardButton(KINDS[other], callback_data=f"sty:k:{group}:{other}"),
              InlineKeyboardButton("📷 រូបថ្មី", callback_data="sty:new"))
        return m

    def run(chat_id, uid, key, file_id):
        label = ITEMS[key][2]
        if studio["remaining"](uid) <= 0:
            bot.send_message(chat_id, "ថ្ងៃនេះប្រើ Studio អស់ហើយ — ស្អែកមកសាកម្ដងទៀត 🙏")
            return
        if uid in studio["busy"]:
            bot.send_message(chat_id, "⏳ កំពុងធ្វើរូបមុន សូមរង់ចាំបន្តិច...")
            return
        studio["busy"].add(uid)
        status = None
        try:
            status = bot.send_message(chat_id, f"⏳ កំពុងសាក {label} ... (10–40 វិនាទី)")
            try:
                bot.send_chat_action(chat_id, "upload_photo")
            except Exception:
                pass
            data = bot.download_file(bot.get_file(file_id).file_path)
            out = studio["run"](data, build_prompt(key))
            studio["record"](uid)
            bot.send_photo(chat_id, out, caption=f"✨ {html.escape(label)} — សាកម៉ូដផ្សេងទៀតបាន 👇",
                           reply_markup=result_markup(key))
        except Exception as exc:
            logger.warning("style studio failed (%s)", type(exc).__name__)
            bot.send_message(chat_id, studio["error_text"](exc, uid))
        finally:
            studio["busy"].discard(uid)
            if status is not None:
                try:
                    bot.delete_message(chat_id, status.message_id)
                except Exception:
                    pass

    @bot.message_handler(commands=["style", "hair", "pose"])
    def style_cmd(message):
        parts = (message.text or "").split()
        key = parts[1] if len(parts) > 1 else ""
        if key.startswith("sty_"):
            key = key[4:]
        if key in ITEMS:
            choose(message.chat.id, message.from_user.id, key)
            return
        bot.reply_to(message, INTRO, reply_markup=group_menu(), parse_mode="HTML")

    @bot.callback_query_handler(func=lambda c: (c.data or "").startswith("sty:"))
    def style_cb(call):
        parts = (call.data or "").split(":")
        uid, chat_id = call.from_user.id, call.message.chat.id
        bot.answer_callback_query(call.id)

        def show(text, markup):
            try:
                bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=markup, parse_mode="HTML")
            except Exception:
                bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")

        if parts[1] == "menu":
            show(INTRO, group_menu())
        elif parts[1] == "g" and len(parts) > 2 and parts[2] in GROUPS:
            show(f"{GROUPS[parts[2]][0]} — ចង់សាកអ្វី? 👇", kind_menu(parts[2]))
        elif parts[1] == "k" and len(parts) > 3 and parts[2] in GROUPS and parts[3] in KINDS:
            text = f"{GROUPS[parts[2]][0]} · {KINDS[parts[3]]} — ជ្រើសម៉ូដ 👇"
            if call.message.content_type == "photo":   # under a result photo → new message, keep the photo
                bot.send_message(chat_id, text, reply_markup=items_menu(parts[2], parts[3]))
            else:
                show(text, items_menu(parts[2], parts[3]))
        elif parts[1] == "i" and len(parts) > 2 and parts[2] in ITEMS:
            choose(chat_id, uid, parts[2])
        elif parts[1] == "new":
            photos.pop(uid, None)
            bot.send_message(chat_id, "📷 ជ្រើសម៉ូដម្ដងទៀត ហើយផ្ញើរូបថ្មី 👇", reply_markup=group_menu())

    def waiting_photo(message):
        p = pending.get(getattr(message.from_user, "id", None))
        return bool(p and p[1] > time.monotonic() and getattr(message.chat, "type", "") == "private")

    @bot.message_handler(func=waiting_photo, content_types=["photo"])
    def style_photo(message):
        uid = message.from_user.id
        key = pending.pop(uid)[0]
        file_id = max(message.photo, key=lambda p: (p.width or 0) * (p.height or 0)).file_id
        photos[uid] = (file_id, time.monotonic() + STYLE_TTL)
        run(message.chat.id, uid, key, file_id)

    return {"pending": pending, "photos": photos, "choose": choose}
