"""Prompt Studio — expert prompt writer for AI video, songs, images and story scenes (Khmer UI).

  /prompt        menu
  /videoprompt   AI video prompt (Veo / Kling / Sora / Runway / Hailuo)
  /songprompt    song prompt for Suno / Udio: style tags + structured original lyrics
  /imageprompt   image prompt (Nano Banana / Midjourney / Flux / SDXL)
  /story         story → structure, pacing, character sheet, scene-by-scene prompts, episodes

Explanations are in Khmer; the prompts themselves are in English (AI tools follow
English best) and are sent in <pre> blocks so they are easy to copy.
"""
import html
import logging
import os
import re
import threading
import time
from types import SimpleNamespace

logger = logging.getLogger(__name__)

DAILY_LIMIT = int(os.getenv("PROMPT_DAILY_LIMIT", "10"))
MAX_IDEA = 600
MSG_LIMIT = 3900

COMMON = """
OUTPUT FORMAT (strict, Telegram plain text):
- Write all explanations, labels and tips in natural, friendly modern Khmer.
- Write every copy-ready prompt in English and wrap EACH one exactly like this on its own lines:
<<<
the prompt text
>>>
- No markdown tables, no # headings, no ** bold. Use emoji + short Khmer labels as section titles.
- Never copy existing copyrighted lyrics, scripts or characters; never use the names of real artists,
  directors or living people as a style reference (describe the style instead); never make prompts that
  show a real, identifiable person, sexual content, minors in unsafe situations, or graphic gore.
- Fill in smart creative details the user did not give, but stay faithful to their idea.
- Put each section title on its OWN line right above its prompt (e.g. "🎨 Midjourney"), then the <<< >>> block.
- Inside prompts: perfect English spelling and grammar, proper capitalisation (Sentence case, proper nouns
  capitalised, parameters lower-case like --ar 9:16 --v 7), no typos, no Khmer inside English prompts
  (except song lyrics), no trailing explanations inside the block — the block must be paste-ready.
"""

SYSTEMS = {
    "video": """You are a world-class AI video director and prompt engineer (Google Veo 3, Kling, Sora,
Runway, Hailuo). Turn the user's idea into cinematic, production-ready prompts.
Use this order inside every prompt: [shot type + camera movement] + [subject with a fixed, detailed visual
description] + [ONE clear main action for an 8-second clip] + [setting, time of day, weather] +
[lighting + mood + colour grade] + [style: lens, film look, realism level] + [audio: SFX, ambience, music
mood, and any dialogue in quotes, max ~15 words per 8 s]. Physically plausible motion, no scene cuts inside
one prompt, consistent wardrobe.
Sections:
🎯 គំនិត (1-2 Khmer lines) · 👤 Character Sheet (fixed English description per character, in <<< >>>,
to reuse word-for-word) · 🎬 Prompt មេ (one <<< >>>) · 🔁 ជម្រើស ២ ទៀត (two variations: different camera/mood,
each <<< >>>) · ⛔ Negative (one <<< >>>: things to avoid) · 💡 គន្លឹះ (3 short Khmer tips: aspect ratio
9:16 for TikTok/Reels or 16:9 for YouTube, how to keep characters consistent, Khmer dialogue may be
pronounced imperfectly so voice-over can be added later).""" + COMMON,

    "song": """You are a hit songwriter, rap lyricist and Suno/Udio prompt expert who knows Khmer
hip-hop and Cambodian pop. Write an ORIGINAL song from the user's idea.
Sections:
🎵 ចំណងជើង (3 title ideas) · 🎛️ Style of Music (one <<< >>>: comma-separated tags, max 200 characters:
genre, sub-genre, BPM, key/mood, main instruments, vocal type and gender, production texture — no artist
names) · ⛔ Exclude Styles (one <<< >>>) · 📝 Lyrics (one <<< >>>, lyrics in Khmer script unless the user asks
otherwise, with Suno section tags on their own lines: [Intro] [Verse 1] [Pre-Chorus] [Chorus] [Verse 2]
[Bridge] [Final Chorus] [Outro]; add performance cues in brackets like [rap, fast flow] [whisper]
[beat drop] [crowd chant]; 4-8 lines per section, balanced syllables per line, clear end rhymes, a short
memorable hook repeated in every chorus; for rap use internal rhymes and punchlines) ·
🧭 រចនាសម្ព័ន្ធ (one Khmer line explaining the song structure and where the energy rises) ·
💡 គន្លឹះ (3 Khmer tips: generate 2-4 versions, use Extend/Replace section, fix Khmer pronunciation by
respelling a word or splitting syllables).""" + COMMON,

    "image": """You are an award-winning photographer, art director and image prompt engineer
(Gemini Nano Banana, Midjourney v7, Flux, SDXL, GPT image).
Build prompts from: subject (specific details) + action/pose + composition & framing + setting/background +
lighting (direction, quality, time) + camera & lens (e.g. 85mm f/1.8) or art medium + style & colour palette
+ mood + fine details & textures + quality cues. Any text that must appear in the image goes in "quotes"
with font style and position.
Sections:
🎯 គំនិត (1-2 Khmer lines) · 🍌 Nano Banana / Gemini (one <<< >>>: rich natural-language paragraph) ·
🎨 Midjourney (one <<< >>>: comma phrases ending with suitable --ar, --stylize, --v 7) · ⚙️ Flux / SDXL
(one <<< >>>: positive) + Negative (one <<< >>>) · 🔁 ជម្រើស ២ ទៀត (two variations with different style or
mood, each <<< >>>) · 💡 គន្លឹះ (aspect ratio by platform: 9:16 TikTok/Story, 1:1 or 4:5 Facebook post,
16:9 YouTube thumbnail; plus 2 more tips).""" + COMMON,

    "story": """You are a senior screenwriter, story editor and AI film director. Turn the user's idea into
a well-structured, well-paced story ready to generate with AI video tools (8-second clips).
1) Decide format from the request (default: 60-second vertical short). If the user asks for episodes, plan
the whole series.
2) Structure rules — Short (≤90 s): HOOK in the first 3 s (question, conflict or striking image) → setup →
turn/conflict → climax → payoff or twist → ending that loops or calls to action. Long: three acts
(Act 1 ≈25% setup + inciting incident; Act 2 ≈50% rising stakes, midpoint, lowest point; Act 3 ≈25% climax +
resolution). Series: each episode has its own mini-arc and ends on a cliffhanger; episode 1 hooks in the
first scene; tension rises across episodes.
3) Pacing rules: scene durations must add up to the total; 5-8 s per AI clip; vary shots (establishing wide
→ medium → close-up for emotion → insert/detail); faster cuts in action, longer holds for emotion; keep
continuity (same wardrobe, props, light, time of day, screen direction / 180° rule); clear cause → effect
between scenes; plan transitions (match cut, whip pan, sound bridge) and music energy.
Sections:
🎯 Logline (1 Khmer sentence) · 🎭 ប្រភេទ & អារម្មណ៍ · ⏱️ ទម្រង់ (platform, aspect ratio, total length,
number of scenes or episodes) · 🧭 រចនាសម្ព័ន្ធ (beats with time ranges, Khmer) · 👤 Character Bible
(one <<< >>> per character: fixed English visual description to reuse word-for-word) · 🎬 Scenes — for each
scene: "Scene N | 0:00-0:08 | location" then Khmer lines: 📷 shot + camera, 🎬 action, 🗣️ dialogue or
voice-over (Khmer, short), 🔊 sound/music, ✂️ transition; then the full English video prompt for that clip in
<<< >>> (include the character description and style each time). Max 12 scenes per answer.
📺 If episodes were requested: list ALL episodes (title + 2-line Khmer summary + cliffhanger), then give the
full scene breakdown for the requested episode only (default episode 1) and end with "👉 ចុច ▶️ បន្ត
ដើម្បីបានភាគបន្ទាប់".
💡 គន្លឹះ (3 Khmer tips on editing, music sync on beats, and keeping faces consistent).""" + COMMON,
}

CONTINUE_NOTE = (
    "\n\nCONTINUE the same project. Here is your previous answer (keep the same characters, style, "
    "structure and episode plan):\n---\n{prev}\n---\nNow write the NEXT part: if there are episodes, the full "
    "scene breakdown of the next episode; otherwise continue with the next scenes. Same output format."
)

MODES = {
    # key: (button label, intro text, example)
    "video": ("🎬 វីដេអូ AI", "🎬 <b>Prompt វីដេអូ AI</b>\nប្រាប់គំនិតឈុតដែលចង់បាន ខ្ញុំសរសេរ Prompt កម្រិតភាពយន្តសម្រាប់ Veo / Kling / Sora / Runway។",
              "ក្មេងប្រុសជិះកង់ហោះលើភ្នំពេញពេលថ្ងៃលិច បែប Pixar"),
    "song": ("🎵 ចម្រៀង (Suno)", "🎵 <b>Prompt ចម្រៀង</b>\nប្រាប់ប្រធានបទ អារម្មណ៍ និងប្រភេទបទ ខ្ញុំសរសេរ Style + ទំនុកច្រៀងថ្មីមានរចនាសម្ព័ន្ធត្រឹមត្រូវសម្រាប់ Suno / Udio។",
             "បទ Rap លើកទឹកចិត្តអ្នកធ្វើការឆ្ងាយផ្ទះ បែប Boom bap 90 BPM"),
    "image": ("🖼️ រូបភាព", "🖼️ <b>Prompt រូបភាព</b>\nប្រាប់រូបដែលចង់បាន ខ្ញុំសរសេរ Prompt សម្រាប់ Nano Banana / Midjourney / Flux។",
              "Poster Concert Hip-hop នៅអង្គរវត្តពេលយប់ ពន្លឺខៀវមាស"),
    "story": ("🎞️ រឿង & Scene", "🎞️ <b>រៀបចំរឿង & Scene</b>\nប្រាប់គំនិតរឿង ប្រវែង (ឧ. 60 វិនាទី) ឬចំនួនភាគ ខ្ញុំរៀបចំរចនាសម្ព័ន្ធ ចង្វាក់ Scene និង Prompt វីដេអូគ្រប់ Scene។",
              "រឿងស្នេហាកម្លោះអ្នកលក់នំបញ្ចុក និងនារីអ្នកក្រុង 5 ភាគ ភាគនីមួយៗ 1 នាទី"),
}
COMMANDS = {"videoprompt": "video", "songprompt": "song", "imageprompt": "image", "story": "story"}

_PROMPT_RE = re.compile(r"<<<\s*\n?(.*?)\n?\s*>>>|```[ \t]*([\w+.-]*)[ \t]*\n(.*?)```", re.S)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_EMOJI_START = re.compile(r"^\s*(?:[^\w\s<>\"'(\[]|\d+[.)]|[①-⑳])", re.U)


def _label(header):
    """Telegram shows the code-block language as a header with a copy button: use the section name."""
    latin = re.findall(r"[A-Za-z][A-Za-z0-9+./-]*", header or "")
    words = [w.strip("./-") for w in latin if w.lower() not in ("one", "the", "and")][:3]
    lab = "-".join(w for w in words if w)
    return lab[:28] or "Prompt"


def render_chunks(text, limit=MSG_LIMIT):
    """AI text → Telegram-HTML messages.
    Every copy-ready prompt (<<< >>> or ``` fences) becomes its own message: bold section title + a code block
    labelled with the tool name (Telegram adds a one-tap 📋 Copy button). Explanations stay together."""
    pieces = []          # ("text", html) | ("prompt", html)
    pos = 0
    for m in _PROMPT_RE.finditer(text):
        plain = text[pos:m.start()]
        if plain.strip():
            pieces.extend(("text", p) for p in _plain_pieces(plain))
        body = (m.group(1) if m.group(1) is not None else (m.group(3) or "")).strip()
        if body:
            pieces.append(("prompt", body))
        pos = m.end()
    tail = text[pos:].replace("<<<", "").replace(">>>", "").replace("```", "")
    if tail.strip():
        pieces.extend(("text", p) for p in _plain_pieces(tail))
    chunks, cur, count = [], "", 0

    def flush():
        nonlocal cur
        if cur.strip():
            chunks.append(cur)
        cur = ""

    for i, (kind, p) in enumerate(pieces):
        if kind == "text":
            if cur and len(cur) + len(p) + 2 > limit:
                flush()
            cur = (cur + "\n\n" + p) if cur else p
            continue
        # a short paragraph right before the prompt is its title: move it into the prompt's own message
        header = ""
        if cur:
            last = cur.rsplit("\n\n", 1)
            if len(last[-1]) <= 160:
                header = last[-1]
                cur = last[0] if len(last) == 2 else ""
        flush()
        count += 1
        plain_header = re.sub(r"<[^>]+>", "", header)
        lab = _label(plain_header)
        title = header if header.startswith("<b>") else (f"<b>{header}</b>" if header else f"<b>📋 Prompt {count}</b>")
        code = html.escape(p[: limit - 300])
        chunks.append(f"{title}\n<pre><code class=\"language-{lab}\">{code}</code></pre>")
    flush()
    return chunks


def _plain_pieces(text, limit=MSG_LIMIT - 200):
    text = re.sub(r"^#+\s*", "", text.strip("\n"), flags=re.M)
    out = []
    for para in re.split(r"\n{2,}", text):
        para = para.strip("\n")
        if not para.strip():
            continue
        esc = _BOLD_RE.sub(r"<b>\1</b>", html.escape(para)).replace("**", "")
        # short lines that start with an emoji / number are section titles → bold
        lines = []
        for line in esc.split("\n"):
            raw = line.strip()
            if raw and len(raw) <= 70 and "<b>" not in raw and _EMOJI_START.match(raw) and not raw.startswith(("•", "-")):
                line = f"<b>{raw}</b>"
            lines.append(line)
        esc = "\n".join(lines)
        while len(esc) > limit:  # very long paragraph: cut on a line break
            cut = esc.rfind("\n", 0, limit)
            cut = cut if cut > 0 else limit
            out.append(_close_bold(esc[:cut]))
            esc = esc[cut:].lstrip("\n")
        out.append(_close_bold(esc))
    return out


def _close_bold(s):
    return s + "</b>" * (s.count("<b>") - s.count("</b>"))


def _vip_cap(user_id, limit):
    try:
        import vip
        return vip.cap(user_id, limit)
    except Exception:
        return limit


class _DailyLimit:
    def __init__(self, limit):
        self.limit, self.used, self.lock = limit, {}, threading.Lock()

    def take(self, user_id, unlimited=False):
        if unlimited:
            return True
        today = time.strftime("%Y-%m-%d")
        with self.lock:
            for k in [k for k in self.used if k[1] != today]:
                self.used.pop(k, None)
            n = self.used.get((user_id, today), 0)
            if n >= _vip_cap(user_id, self.limit):
                return False
            self.used[(user_id, today)] = n + 1
            return True

    def left(self, user_id):
        return max(0, _vip_cap(user_id, self.limit) - self.used.get((user_id, time.strftime("%Y-%m-%d")), 0))


def register(bot, config, ask_ai):
    """ask_ai(system, prompt) -> str. Register before the catch-all AI handler."""
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    limits = _DailyLimit(DAILY_LIMIT)
    waiting = {}   # user_id -> (mode, expires)
    last = {}      # user_id -> {"mode", "idea", "answer"}

    def privileged(uid):
        return uid is not None and (uid == getattr(config, "OWNER_ID", 0) or uid in getattr(config, "ADMIN_IDS", []))

    def menu_markup():
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton(MODES["video"][0], callback_data="pstudio:m:video"),
              InlineKeyboardButton(MODES["song"][0], callback_data="pstudio:m:song"))
        m.row(InlineKeyboardButton(MODES["image"][0], callback_data="pstudio:m:image"),
              InlineKeyboardButton(MODES["story"][0], callback_data="pstudio:m:story"))
        m.row(InlineKeyboardButton("🏠 ម៉ឺនុយមេ", callback_data="nav:main"))
        return m

    MENU_TEXT = (
        "✍️ <b>Prompt Studio — Cooler AI</b>\n\n"
        "ខ្ញុំសរសេរ Prompt កម្រិតខ្ពស់ឲ្យ៖\n"
        "🎬 វីដេអូ AI (Veo, Kling, Sora, Runway)\n"
        "🎵 ចម្រៀង (Suno, Udio) — Style + ទំនុកច្រៀងមានរចនាសម្ព័ន្ធ\n"
        "🖼️ រូបភាព (Nano Banana, Midjourney, Flux)\n"
        "🎞️ រឿង & Scene — រៀបចំរចនាសម្ព័ន្ធ ចង្វាក់ ភាគ និង Prompt គ្រប់ Scene\n\n"
        f"📌 ប្រើបាន {DAILY_LIMIT} ដង/ថ្ងៃ · Prompt ជាភាសាអង់គ្លេស (AI ស្ដាប់បានល្អជាង) ចុចលើប្រអប់ដើម្បី Copy"
    )

    def ask_idea(chat_id, uid, mode, reply_to=None):
        waiting[uid] = (mode, time.monotonic() + 600)
        _label, intro, example = MODES[mode]
        text = f"{intro}\n\n✍️ សរសេរគំនិតរបស់អ្នកមកឥឡូវ\nឧ. <i>{html.escape(example)}</i>"
        if reply_to is not None:
            bot.reply_to(reply_to, text, parse_mode="HTML")
        else:
            bot.send_message(chat_id, text, parse_mode="HTML")

    def after_markup(mode):
        m = InlineKeyboardMarkup()
        row = [InlineKeyboardButton("🔁 សរសេរថ្មី", callback_data="pstudio:again")]
        if mode == "story":
            row.append(InlineKeyboardButton("▶️ បន្ត", callback_data="pstudio:next"))
        m.row(*row)
        m.row(InlineKeyboardButton("✍️ Prompt ផ្សេង", callback_data="pstudio:menu"))
        return m

    def generate(chat_id, uid, mode, idea, reply_to=None, cont=False):
        idea = " ".join((idea or "").split())[:MAX_IDEA]
        if not idea:
            ask_idea(chat_id, uid, mode, reply_to)
            return
        if not getattr(config, "GEMINI_API_KEY", ""):
            bot.send_message(chat_id, "AI មិនទាន់បើកដំណើរការនៅឡើយទេ។")
            return
        if not limits.take(uid, privileged(uid)):
            bot.send_message(chat_id, f"អ្នកប្រើ Prompt Studio អស់ {_vip_cap(uid, DAILY_LIMIT)} ដងសម្រាប់ថ្ងៃនេះហើយ។ ត្រឡប់មកថ្ងៃស្អែក 🙏")
            return
        try:
            bot.send_chat_action(chat_id, "typing")
        except Exception:
            pass
        status = None
        try:
            status = bot.send_message(chat_id, "⏳ កំពុងសរសេរ Prompt កម្រិតខ្ពស់... (≈ 20-60 វិនាទី)")
        except Exception:
            pass
        user_prompt = f"User idea (may be Khmer): {idea}"
        if cont and last.get(uid, {}).get("answer"):
            user_prompt += CONTINUE_NOTE.format(prev=last[uid]["answer"][:6000])
        error = ""
        try:
            answer = (ask_ai(SYSTEMS[mode], user_prompt) or "").strip()
        except Exception as exc:
            logger.warning("prompt studio %s failed (%s: %s)", mode, type(exc).__name__, exc)
            answer, error = "", f"{type(exc).__name__}: {exc}"
        if status is not None:
            try:
                bot.delete_message(chat_id, status.message_id)
            except Exception:
                pass
        if not answer:
            text = "សុំទោស AI មិនអាចសរសេរបានទេឥឡូវនេះ។ សូមសាកម្ដងទៀត 🙏"
            if privileged(uid) and error:
                text += f"\n\n🔧 (Admin) {error[:300]}"
            bot.send_message(chat_id, text)
            return
        last[uid] = {"mode": mode, "idea": idea, "answer": answer}
        chunks = render_chunks(answer) or [html.escape(answer[:MSG_LIMIT])]
        header = f"{MODES[mode][0]} <b>Prompt Studio</b>\n💭 <i>{html.escape(idea[:150])}</i>\n"
        for i, chunk in enumerate(chunks):
            text = (header + "\n" + chunk) if i == 0 else chunk
            markup = after_markup(mode) if i == len(chunks) - 1 else None
            try:
                bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup,
                                 disable_web_page_preview=True)
            except Exception as exc:  # broken HTML from odd AI output → send as plain text
                logger.warning("prompt studio send failed (%s)", type(exc).__name__)
                bot.send_message(chat_id, html.unescape(re.sub(r"<[^>]+>", "", text)), reply_markup=markup)

    @bot.message_handler(commands=["prompt", "prompts"])
    def prompt_menu(message):
        bot.reply_to(message, MENU_TEXT, reply_markup=menu_markup(), parse_mode="HTML")

    @bot.message_handler(commands=list(COMMANDS))
    def prompt_cmd(message):
        parts = (message.text or "").split(maxsplit=1)
        cmd = parts[0][1:].split("@")[0].lower()
        mode = COMMANDS.get(cmd, "video")
        if len(parts) > 1:
            generate(message.chat.id, message.from_user.id, mode, parts[1])
        else:
            ask_idea(message.chat.id, message.from_user.id, mode, reply_to=message)

    def is_waiting(message):
        user = getattr(message, "from_user", None)
        if user is None or not getattr(message, "text", None) or message.text.startswith("/"):
            return False
        state = waiting.get(user.id)
        return bool(state and state[1] > time.monotonic())

    @bot.message_handler(func=is_waiting, content_types=["text"])
    def prompt_follow_up(message):
        mode, _ = waiting.pop(message.from_user.id)
        generate(message.chat.id, message.from_user.id, mode, message.text)

    @bot.callback_query_handler(func=lambda call: (call.data or "").startswith("pstudio:"))
    def prompt_cb(call):
        message = getattr(call, "message", None)
        uid = call.from_user.id
        parts = (call.data or "").split(":")
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        if message is None:
            return
        chat_id = message.chat.id
        if parts[1:2] == ["m"] and len(parts) > 2 and parts[2] in MODES:
            ask_idea(chat_id, uid, parts[2])
        elif parts[1:2] in (["again"], ["next"]):
            prev = last.get(uid)
            if not prev:
                bot.send_message(chat_id, MENU_TEXT, reply_markup=menu_markup(), parse_mode="HTML")
                return
            generate(chat_id, uid, prev["mode"], prev["idea"], cont=parts[1] == "next")
        else:
            try:
                bot.edit_message_text(MENU_TEXT, chat_id, message.message_id,
                                      reply_markup=menu_markup(), parse_mode="HTML")
            except Exception as exc:
                if "message is not modified" not in str(exc):
                    bot.send_message(chat_id, MENU_TEXT, reply_markup=menu_markup(), parse_mode="HTML")

    return SimpleNamespace(limits=limits, waiting=waiting, last=last, menu_markup=menu_markup,
                           generate=generate)
