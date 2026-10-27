"""🎨 /imagine · 📝 /summary · 🧠 memory · 🎁 referral rewards

  /imagine <description> (/draw) → a new picture from text (Gemini image model)
  /summary (groups) → AI summary of the last ~150 messages the bot could see (kept in RAM only)
  🧠 Memory: every few chats the AI updates a short list of harmless facts the user shared
     (name they like, interests, projects). /memory shows it, /forget deletes it.
  🎁 Referral: every 3 invited friends who PAY → the inviter gets 7 free days.
"""
import base64
import html
import io
import logging
import os
import re
import sqlite3
import threading
import time
from collections import defaultdict, deque

import httpx

logger = logging.getLogger(__name__)
API = "https://generativelanguage.googleapis.com/v1beta"
REF_NEEDED, REF_DAYS = 3, 7


def _db(config):
    conn = sqlite3.connect(getattr(config, "BOT_DB_PATH", "bot_database.db"), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(config):
    conn = _db(config)
    try:
        with conn:
            conn.execute("CREATE TABLE IF NOT EXISTS user_notes (user_id INTEGER PRIMARY KEY, notes TEXT NOT NULL, "
                         "chats INTEGER NOT NULL DEFAULT 0, updated_at INTEGER)")
            conn.execute("CREATE TABLE IF NOT EXISTS ref_paid (referred_user_id INTEGER PRIMARY KEY, referrer_user_id "
                         "INTEGER NOT NULL, paid_at INTEGER NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS image_usage (user_id INTEGER, day TEXT, n INTEGER NOT NULL, "
                         "PRIMARY KEY (user_id, day))")
    finally:
        conn.close()


# ---------------- 🎨 image ----------------

def image_models():
    chain = [os.getenv("IMAGINE_MODEL", "").strip(), "gemini-nano-banana-2.1", "gemini-3.1-flash-lite-image",
             "gemini-3.1-flash-image"]
    seen, out = set(), []
    for m in chain:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def find_image(data):
    def walk(node):
        if isinstance(node, dict):
            inline = node.get("inlineData") or node.get("inline_data")
            if isinstance(inline, dict) and inline.get("data") and "image" in (inline.get("mimeType") or inline.get("mime_type") or "image"):
                return inline["data"]
            if node.get("type") == "image" and isinstance(node.get("data"), str):
                return node["data"]
            for v in node.values():
                found = walk(v)
                if found:
                    return found
        elif isinstance(node, list):
            for v in node:
                found = walk(v)
                if found:
                    return found
        return None
    b64 = walk(data)
    return base64.b64decode(b64) if b64 else None


def generate_image(api_key, prompt, aspect="1:1", client=None):
    if not api_key:
        raise RuntimeError("no GEMINI_API_KEY")
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0))
    errors = []
    try:
        for model in image_models():
            payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                       "generationConfig": {"responseModalities": ["TEXT", "IMAGE"],
                                            "imageConfig": {"aspectRatio": aspect}}}
            try:
                r = client.post(f"{API}/models/{model}:generateContent", json=payload, headers=headers)
            except httpx.HTTPError as exc:
                errors.append(f"{model}: {type(exc).__name__}")
                continue
            if r.status_code == 200:
                img = find_image(r.json())
                if img:
                    return img
                errors.append(f"{model}: no image (blocked?)")
                continue
            errors.append(f"{model}: HTTP {r.status_code}")
            if r.status_code in (401, 403):
                break
    finally:
        if own:
            client.close()
    raise RuntimeError("; ".join(errors)[:300])


def image_limit():
    try:
        return int(os.getenv("IMAGINE_DAILY_LIMIT", "5"))
    except ValueError:
        return 5


def _kh_day():
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 7 * 3600))


def images_used(config, uid):
    conn = _db(config)
    try:
        row = conn.execute("SELECT n FROM image_usage WHERE user_id=? AND day=?", (uid, _kh_day())).fetchone()
        return int(row["n"]) if row else 0
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def take_image(config, uid, unlimited=False):
    """Count one /imagine picture (shared by the bot and the Hub's AI Studio); False when over today's limit."""
    if unlimited or image_limit() <= 0:
        return True
    import vip
    lim = vip.cap(uid, image_limit(), config)
    day = _kh_day()
    conn = _db(config)
    try:
        with conn:
            row = conn.execute("SELECT n FROM image_usage WHERE user_id=? AND day=?", (uid, day)).fetchone()
            if row and row["n"] >= lim:
                return False
            conn.execute("INSERT INTO image_usage(user_id, day, n) VALUES (?, ?, 1) ON CONFLICT(user_id, day) "
                         "DO UPDATE SET n=n+1", (uid, day))
        return True
    finally:
        conn.close()


def give_image_back(config, uid):
    conn = _db(config)
    try:
        with conn:
            conn.execute("UPDATE image_usage SET n=MAX(0, n-1) WHERE user_id=? AND day=?", (uid, _kh_day()))
    finally:
        conn.close()


IMAGINE_STYLE = ("Create one high-quality, original image. If people appear they must be fictional (not real "
                 "celebrities). No text in the image unless asked. Request: ")


# ---------------- 🧠 memory ----------------

MEMORY_PROMPT = """You keep a SHORT memory about a chat user so a friendly Khmer assistant can feel personal.
Current notes (may be empty):
{notes}

New messages from the user:
{messages}

Update the notes: max 8 bullet lines, each under 15 words, in Khmer. Keep only harmless, useful, stable facts the
user said about themself: preferred name, interests, hobbies, projects, favourite music, goals, city.
NEVER keep: health, money/salary/debts, religion, politics, sexuality, ID/bank/phone numbers, passwords, addresses,
anything about children's private details, or anything the user asked to forget. If nothing worth keeping, return the
current notes unchanged. Output only the bullet lines starting with "- "."""


def get_notes(config, user_id):
    conn = _db(config)
    try:
        row = conn.execute("SELECT notes FROM user_notes WHERE user_id=?", (user_id,)).fetchone()
        return row["notes"] if row else ""
    finally:
        conn.close()


def set_notes(config, user_id, notes):
    conn = _db(config)
    try:
        with conn:
            conn.execute("INSERT INTO user_notes(user_id, notes, chats, updated_at) VALUES (?, ?, 0, ?) "
                         "ON CONFLICT(user_id) DO UPDATE SET notes=excluded.notes, updated_at=excluded.updated_at",
                         (user_id, notes, int(time.time())))
    finally:
        conn.close()


def forget(config, user_id):
    conn = _db(config)
    try:
        with conn:
            conn.execute("DELETE FROM user_notes WHERE user_id=?", (user_id,))
    finally:
        conn.close()


def bump_chats(config, user_id):
    conn = _db(config)
    try:
        with conn:
            conn.execute("INSERT INTO user_notes(user_id, notes, chats, updated_at) VALUES (?, '', 1, NULL) "
                         "ON CONFLICT(user_id) DO UPDATE SET chats=chats+1", (user_id,))
            return conn.execute("SELECT chats FROM user_notes WHERE user_id=?", (user_id,)).fetchone()["chats"]
    finally:
        conn.close()


def clean_notes(text):
    lines = [l.strip() for l in (text or "").splitlines() if l.strip().startswith("- ")]
    lines = [l for l in lines if not re.search(r"\d{6,}", l)][:8]   # never keep long numbers
    return "\n".join(l[:120] for l in lines)


def memory_block(config, user_id):
    """Text for the system instruction (empty when nothing is known)."""
    if user_id is None:
        return ""
    try:
        notes = get_notes(config, user_id)
    except Exception:
        return ""
    if not notes:
        return ""
    return ("\n\nThings this user told you before (use naturally when relevant, never list them back, never mention "
            "that you keep notes unless asked):\n" + notes + "\n")


# ---------------- 🎁 referral rewards ----------------

def referral_paid(bot, config, payer_id):
    """Call after a person's payment is approved. Rewards the inviter every 3 paying friends."""
    import subscription as sub
    conn = _db(config)
    try:
        try:
            row = conn.execute("SELECT referrer_user_id FROM community_referrals WHERE referred_user_id=?",
                               (payer_id,)).fetchone()
        except sqlite3.OperationalError:
            return None
        if not row:
            return None
        referrer = row["referrer_user_id"]
        with conn:
            cur = conn.execute("INSERT OR IGNORE INTO ref_paid(referred_user_id, referrer_user_id, paid_at) VALUES (?, ?, ?)",
                               (payer_id, referrer, int(time.time())))
        if cur.rowcount != 1:
            return None      # only the friend's first payment counts
        count = conn.execute("SELECT COUNT(*) FROM ref_paid WHERE referrer_user_id=?", (referrer,)).fetchone()[0]
    finally:
        conn.close()
    try:
        if count % REF_NEEDED == 0:
            until = sub.extend(config, "u", referrer, REF_DAYS)
            bot.send_message(referrer, f"🎁 <b>អរគុណដែលណែនាំមិត្ត!</b> មិត្ត {count} នាក់បានក្លាយជាសមាជិក\n"
                                       f"✅ អ្នកទទួលបាន <b>{REF_DAYS} ថ្ងៃឥតគិតថ្លៃ</b> — រហូតដល់ {sub.fmt_date(until)} 🎉",
                             parse_mode="HTML")
        else:
            left = REF_NEEDED - count % REF_NEEDED
            bot.send_message(referrer, f"🤝 មិត្តដែលអ្នកណែនាំបានបង់ហើយ! ({count % REF_NEEDED}/{REF_NEEDED})\n"
                                       f"🎁 នៅ {left} នាក់ទៀត ទទួលបាន {REF_DAYS} ថ្ងៃឥតគិតថ្លៃ · /invite")
    except Exception as exc:
        logger.info("referral notice failed (%s)", type(exc).__name__)
    return count


# ---------------- bot module ----------------

def register(bot, config, ask=None, make_image=None):
    """ask(prompt) -> text; make_image(prompt, aspect) -> bytes (injectable for tests)."""
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    init_schema(config)
    key = lambda: getattr(config, "GEMINI_API_KEY", "")
    make_image = make_image or (lambda prompt, aspect: generate_image(key(), IMAGINE_STYLE + prompt, aspect))
    if ask is None:
        def ask(prompt):
            import knowledge_base
            return knowledge_base.gemini(key(), [{"text": prompt}], max_tokens=1500, temperature=0.3)
    history = defaultdict(lambda: deque(maxlen=150))   # chat_id -> (time, name, text)
    busy = set()
    summary_at = {}

    def privileged(uid):
        return uid == getattr(config, "OWNER_ID", 0) or uid in getattr(config, "ADMIN_IDS", [])

    def img_limit():
        return image_limit()

    def take_image_quota(uid):
        return take_image(config, uid, privileged(uid))

    def give_back(uid):
        give_image_back(config, uid)

    ASPECTS = {"ឈរ": "9:16", "portrait": "9:16", "tiktok": "9:16", "ដេក": "16:9", "wide": "16:9", "youtube": "16:9"}

    @bot.message_handler(commands=["imagine", "draw", "img"])
    def imagine_cmd(message):
        parts = (message.text or "").split(maxsplit=1)
        prompt = parts[1].strip()[:600] if len(parts) > 1 else ""
        uid = message.from_user.id
        if not prompt:
            bot.reply_to(message, "🎨 <b>គូររូបពីអក្សរ</b>\n\nឧ. <code>/imagine ឆ្មាពាក់វ៉ែនតាខ្មៅ រាំ Hip-hop នៅអង្គរវត្ត ពេលថ្ងៃលិច</code>\n"
                                  "💡 បន្ថែមពាក្យ <code>tiktok</code> (រូបឈរ) ឬ <code>youtube</code> (រូបដេក)\n"
                                  f"⏳ {img_limit()} រូប/ថ្ងៃ", parse_mode="HTML")
            return
        if uid in busy:
            bot.reply_to(message, "⏳ រូបមុនកំពុងគូរ — រង់ចាំបន្តិច")
            return
        if not take_image_quota(uid):
            bot.reply_to(message, f"🎨 ថ្ងៃនេះគូរអស់ {img_limit()} រូបហើយ — ស្អែកមកម្ដងទៀតណា 🙏")
            return
        aspect = next((a for w, a in ASPECTS.items() if w in prompt.lower()), "1:1")
        busy.add(uid)
        try:
            bot.send_chat_action(message.chat.id, "upload_photo")
            img = make_image(prompt, aspect)
            f = io.BytesIO(img)
            f.name = "cooler.png"
            bot.send_photo(message.chat.id, f, caption=f"🎨 {html.escape(prompt[:200])}\n— Cooler Bot × AI",
                           reply_to_message_id=message.message_id, parse_mode="HTML")
        except Exception as exc:
            logger.warning("imagine failed (%s: %s)", type(exc).__name__, str(exc)[:200])
            if not privileged(uid):
                give_back(uid)
            note = f"\n<code>{html.escape(str(exc)[:250])}</code>" if privileged(uid) else ""
            bot.reply_to(message, "😔 គូរមិនបានទេពេលនេះ (មិនកាត់ចំនួន) — សាកពាក្យផ្សេង ឬម្ដងទៀត" + note, parse_mode="HTML")
        finally:
            busy.discard(uid)

    # group messages seen by the bot → RAM only, for /summary
    def remember_group(message):
        chat = getattr(message, "chat", None)
        if getattr(chat, "type", None) in ("group", "supergroup"):
            text = getattr(message, "text", None) or getattr(message, "caption", None)
            if text and not text.startswith("/"):
                user = getattr(message, "from_user", None)
                name = (getattr(user, "first_name", None) or "?")[:20]
                history[chat.id].append((time.time(), name, text[:400]))
        return False   # never consume the message

    @bot.message_handler(func=remember_group, content_types=["text", "photo", "video"])
    def _never(message):   # pragma: no cover - func always returns False
        pass

    @bot.message_handler(commands=["summary", "tldr", "sangkhep"])
    def summary_cmd(message):
        chat = message.chat
        if getattr(chat, "type", None) not in ("group", "supergroup"):
            bot.reply_to(message, "📝 /summary ប្រើក្នុង Group — សង្ខេបសារចុងក្រោយ")
            return
        items = list(history[chat.id])
        if len(items) < 5:
            bot.reply_to(message, "📝 សារនៅតិចពេក សម្រាប់សង្ខេប (Bot ត្រូវជា Admin ទើបឃើញសារទាំងអស់)")
            return
        last = summary_at.get(chat.id)
        if last is not None and time.monotonic() - last < 120 and not privileged(message.from_user.id):
            bot.reply_to(message, "⏳ ទើបតែសង្ខេបមិញ — រង់ចាំ ២ នាទី")
            return
        summary_at[chat.id] = time.monotonic()
        convo = "\n".join(f"{name}: {text}" for _t, name, text in items)
        prompt = ("Summarize this Telegram group conversation in Khmer for someone who missed it. 4-7 short bullet "
                  "points with the main topics, decisions, questions still open and who asked, then one line of "
                  "upcoming plans/dates if any. Be neutral; don't add facts.\n\n" + convo[-12000:])
        try:
            bot.send_chat_action(chat.id, "typing")
            text = ask(prompt)
        except Exception:
            text = ""
        bot.reply_to(message, (f"📝 <b>សង្ខេបសារ {len(items)} ចុងក្រោយ</b>\n\n" + html.escape(text[:3500]))
                     if text else "😔 AI រវល់ — សាកម្ដងទៀត", parse_mode="HTML")

    # 🧠 memory
    def observe(user_id, user_text):
        """Call after each private AI chat; updates notes in the background every 6 chats."""
        try:
            n = bump_chats(config, user_id)
        except Exception:
            return
        if n % 6 != 0:
            return

        def work():
            try:
                import database
                hist = database.get_chat_history(user_id=user_id, limit=16)
                msgs = "\n".join("- " + h["content"][:300] for h in hist if h["role"] == "user")[-3000:]
                if user_text:
                    msgs += "\n- " + user_text[:300]
                notes = clean_notes(ask(MEMORY_PROMPT.format(notes=get_notes(config, user_id) or "(none)", messages=msgs)))
                if notes:
                    set_notes(config, user_id, notes)
            except Exception as exc:
                logger.info("memory update skipped (%s)", type(exc).__name__)
        threading.Thread(target=work, daemon=True).start()

    @bot.message_handler(commands=["memory", "mymemory"])
    def memory_cmd(message):
        notes = get_notes(config, message.from_user.id)
        m = InlineKeyboardMarkup()
        if notes:
            m.row(InlineKeyboardButton("🗑️ លុបទាំងអស់", callback_data="mem:forget"))
        bot.reply_to(message, ("🧠 <b>អ្វីដែល Cooler Bot ចាំពីអ្នក</b>\n\n" + html.escape(notes) +
                               "\n\n🔒 មិនរក្សាលេខសម្ងាត់ លេខធនាគារ សុខភាព ឬលុយកាក់ · /forget ដើម្បីលុប")
                     if notes else "🧠 Bot មិនទាន់ចាំអ្វីពីអ្នកទេ — ជជែកច្រើនទៀត Bot នឹងស្គាល់អ្នកកាន់តែច្បាស់ 😊",
                     reply_markup=m if notes else None, parse_mode="HTML")

    @bot.message_handler(commands=["forget"])
    def forget_cmd(message):
        forget(config, message.from_user.id)
        bot.reply_to(message, "🗑️ បានលុបការចងចាំទាំងអស់ហើយ")

    HELP = {
        "img": "🎨 <b>គូររូបពីអក្សរ</b>\nវាយ <code>/imagine</code> + អ្វីដែលចង់បាន ឧ. <code>/imagine ឆ្មាពាក់វ៉ែនតា រាំនៅអង្គរវត្ត</code>",
        "guard": ("🛡️ <b>ការពារ Group</b>\n1️⃣ Add Bot ចូល Group ហើយដាក់ជា <b>Admin</b>\n2️⃣ វាយ /guard ក្នុង Group → បើក\n"
                  "លុប Link បោក Spam Flood · ផ្ទៀងផ្ទាត់សមាជិកថ្មី · /warn /mute /ban\n\n"
                  "📝 <b>សង្ខេប</b>៖ វាយ /summary ក្នុង Group ដើម្បីសង្ខេបសារចុងក្រោយ"),
        "scam": ("🚨 <b>ឆែក Scam</b>\nForward/Reply សារ Link ឬរូបដែលសង្ស័យ ហើយវាយ <code>/scam</code> — AI ប្រាប់ថាគ្រោះថ្នាក់ប៉ុណ្ណា "
                 "និងត្រូវធ្វើអ្វី · ឥតគិតថ្លៃ 🛡️"),
        "remind": ("⏰ <b>រំលឹក</b>\n<code>/remind ស្អែកម៉ោង ២ ណាត់ភ្ញៀវ</code>\n<code>/remind ថ្ងៃទី ៥ រៀងរាល់ខែ បង់ថ្លៃភ្លើង</code>\n"
                   "📋 /reminders មើល/លុប"),
        "cash": ("💵 <b>កត់លុយ</b>\n<code>/cash ចាយ 5000 រៀល កាហ្វេ</code> · <code>/cash លក់បាន $20</code>\n"
                 "📊 /report សរុបខែនេះ · ↩️ /undo"),
        "tools": ("🎤 <b>Voice→អក្សរ</b>៖ Reply <code>/totext</code> លើ Voice\n🌐 <b>បកប្រែ</b>៖ Reply <code>/translate</code> លើរូប/សារ\n"
                  "📚 <b>លំហាត់</b>៖ ថតរូប ដាក់ Caption <code>/homework</code>"),
        "mem": "🧠 Bot ចាំឈ្មោះ និងចំណូលចិត្តដែលអ្នកប្រាប់ ដើម្បីជជែកកាន់តែស្និទ្ធ · /memory មើល · /forget លុប",
    }

    @bot.callback_query_handler(func=lambda c: (c.data or "").startswith("x:"))
    def extras_cb(call):
        bot.answer_callback_query(call.id)
        text = HELP.get((call.data or "")[2:])
        if text:
            bot.send_message(call.message.chat.id, text, parse_mode="HTML")

    @bot.callback_query_handler(func=lambda c: (c.data or "") == "mem:forget")
    def mem_cb(call):
        forget(config, call.from_user.id)
        bot.answer_callback_query(call.id, "🗑️ លុបហើយ")
        try:
            bot.edit_message_text("🗑️ បានលុបការចងចាំទាំងអស់ហើយ", call.message.chat.id, call.message.message_id)
        except Exception:
            pass

    class Api:
        pass
    api = Api()
    api.observe = observe
    api.history = history
    return api
