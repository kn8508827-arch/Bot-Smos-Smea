"""Cooler Knowledge: AI that answers from your own documents (24/7 helper for shops & groups).

Each chat (a group, or a person's private chat) has its own knowledge:
  • /learn <text>, or reply /learn to a PDF / .txt / photo (menu, price list, rules) → saved
  • /ask <question> → answered ONLY from that chat's documents, with the source name
  • /kb → list documents with 🗑️ delete buttons · /kbclear → delete all
  • /autoanswer on|off (groups) → the bot answers questions ending in "?" by itself
    when the answer is in the documents, and stays quiet when it isn't

In groups only group admins (or the bot owner/admins) can add, delete or switch auto-answer.
PDFs and photos are read by Gemini once, when they are added; questions use the saved text.
"""
import base64
import html
import logging
import os
import re
import sqlite3
import time

import httpx

logger = logging.getLogger(__name__)

API = "https://generativelanguage.googleapis.com/v1beta"
MAX_CHAT_CHARS = int(os.getenv("KB_MAX_CHARS", "150000") or 150000)   # all documents of one chat
MAX_DOC_CHARS = 60000
MAX_FILE_BYTES = 15 * 1024 * 1024
NOT_FOUND = "NOT_FOUND"
TEXT_EXT = (".txt", ".md", ".csv", ".json")

EXTRACT_PROMPT = ("Copy out ALL the text in this file exactly, in its original language (Khmer stays Khmer). "
                  "Keep prices, numbers, names, phone numbers and lists. Describe tables row by row. "
                  "Output plain text only, no comments.")
ANSWER_SYSTEM = ("You are Cooler Bot, a friendly assistant for a Cambodian shop/group. Answer the user's question "
                 "using ONLY the DOCUMENTS below. Reply in the user's language (Khmer by default), short and clear, "
                 "and end with the source like: 📄 <document name>. If the documents do not contain the answer, "
                 f"reply exactly {NOT_FOUND} and nothing else. Never invent prices, dates, phone numbers or rules.")


# ---------------- Gemini ----------------

def _models():
    try:
        import ai_assistant
        chain = list(ai_assistant.audio_models())
    except Exception:
        chain = []
    chain = [os.getenv("KB_AI_MODEL", "").strip(), *chain, "gemini-3.8-flash", "gemini-3.6-flash"]
    seen, out = set(), []
    for m in chain:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def gemini(api_key, parts, system=None, max_tokens=4096, temperature=0.2, client=None):
    """parts: list of Gemini parts. Tries each model until one answers."""
    if not api_key:
        raise RuntimeError("no GEMINI_API_KEY")
    payload = {"contents": [{"role": "user", "parts": parts}],
               "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}}
    if system:
        payload["system_instruction"] = {"parts": [{"text": system}]}
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0))
    errors = []
    try:
        for model in _models():
            try:
                r = client.post(f"{API}/models/{model}:generateContent", json=payload, headers=headers)
            except httpx.HTTPError as exc:
                errors.append(f"{model}: {type(exc).__name__}")
                continue
            if r.status_code == 200:
                cands = r.json().get("candidates") or []
                ps = ((cands[0].get("content") or {}).get("parts") or []) if cands else []
                text = "".join(p.get("text") or "" for p in ps if not p.get("thought")).strip()
                if text:
                    return text
                errors.append(f"{model}: empty")
                continue
            errors.append(f"{model}: HTTP {r.status_code}")
            if r.status_code in (401, 403):
                break
    finally:
        if own:
            client.close()
    raise RuntimeError("; ".join(errors)[:300])


# ---------------- storage ----------------

def _db(config):
    conn = sqlite3.connect(getattr(config, "BOT_DB_PATH", "bot_database.db"), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(config):
    conn = _db(config)
    try:
        with conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS kb_docs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, title TEXT NOT NULL,
                body TEXT NOT NULL, added_by INTEGER, created_at INTEGER NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS kb_docs_chat ON kb_docs(chat_id)")
            conn.execute("CREATE TABLE IF NOT EXISTS kb_settings (chat_id INTEGER PRIMARY KEY, auto INTEGER NOT NULL DEFAULT 0)")
    finally:
        conn.close()


def docs(config, chat_id):
    conn = _db(config)
    try:
        return [dict(r) for r in conn.execute("SELECT id, title, body, created_at FROM kb_docs WHERE chat_id=? "
                                              "ORDER BY id", (chat_id,)).fetchall()]
    finally:
        conn.close()


def used_chars(config, chat_id):
    conn = _db(config)
    try:
        return conn.execute("SELECT COALESCE(SUM(LENGTH(body)), 0) FROM kb_docs WHERE chat_id=?", (chat_id,)).fetchone()[0]
    finally:
        conn.close()


def add_doc(config, chat_id, title, body, added_by=None):
    body = (body or "").strip()[:MAX_DOC_CHARS]
    title = re.sub(r"\s+", " ", title or "").strip()[:80] or "ឯកសារ"
    if not body:
        raise ValueError("empty")
    if used_chars(config, chat_id) + len(body) > MAX_CHAT_CHARS:
        raise OverflowError("full")
    conn = _db(config)
    try:
        with conn:
            return conn.execute("INSERT INTO kb_docs(chat_id, title, body, added_by, created_at) VALUES (?, ?, ?, ?, ?)",
                                (chat_id, title, body, added_by, int(time.time()))).lastrowid
    finally:
        conn.close()


def delete_doc(config, chat_id, doc_id=None):
    conn = _db(config)
    try:
        with conn:
            if doc_id is None:
                return conn.execute("DELETE FROM kb_docs WHERE chat_id=?", (chat_id,)).rowcount
            return conn.execute("DELETE FROM kb_docs WHERE chat_id=? AND id=?", (chat_id, doc_id)).rowcount
    finally:
        conn.close()


def auto_on(config, chat_id):
    conn = _db(config)
    try:
        row = conn.execute("SELECT auto FROM kb_settings WHERE chat_id=?", (chat_id,)).fetchone()
        return bool(row and row["auto"])
    finally:
        conn.close()


def set_auto(config, chat_id, on):
    conn = _db(config)
    try:
        with conn:
            conn.execute("INSERT OR REPLACE INTO kb_settings(chat_id, auto) VALUES (?, ?)", (chat_id, 1 if on else 0))
    finally:
        conn.close()


def build_documents(rows):
    return "\n\n".join(f"=== DOCUMENT: {r['title']} ===\n{r['body']}" for r in rows)


def looks_like_question(text):
    t = (text or "").strip()
    return 4 <= len(t) <= 500 and not t.startswith("/") and (
        t.endswith(("?", "？")) or re.search(r"(ប៉ុន្មាន|យ៉ាងម៉េច|ដូចម្ដេច|អត់|ទេ\s*\?|ឬទេ|នៅណា|ពេលណា|ម៉ោងប៉ុន្មាន)$", t))


# ---------------- bot module ----------------

def register(bot, config, ask=None, extract=None):
    """ask(system, prompt) -> text; extract(bytes, mime) -> text. Both injectable for tests."""
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    init_schema(config)
    key = lambda: getattr(config, "GEMINI_API_KEY", "")
    ask = ask or (lambda system, prompt: gemini(key(), [{"text": prompt}], system=system, max_tokens=1500))
    extract = extract or (lambda data, mime: gemini(key(), [
        {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}},
        {"text": EXTRACT_PROMPT}], max_tokens=16000, temperature=0))
    admin_cache = {}

    def privileged(uid):
        return uid == getattr(config, "OWNER_ID", 0) or uid in getattr(config, "ADMIN_IDS", [])

    def is_group(chat):
        return getattr(chat, "type", None) in ("group", "supergroup")

    def can_edit(message_or_call_chat, uid):
        if privileged(uid) or not is_group(message_or_call_chat):
            return True
        cache_key = (message_or_call_chat.id, uid)
        hit = admin_cache.get(cache_key)
        if hit and hit[1] > time.monotonic():
            return hit[0]
        try:
            ok = bot.get_chat_member(message_or_call_chat.id, uid).status in ("creator", "administrator")
        except Exception:
            ok = False
        admin_cache[cache_key] = (ok, time.monotonic() + 600)
        return ok

    def help_text(chat):
        where = "Group នេះ" if is_group(chat) else "អ្នក"
        n = len(docs(config, chat.id))
        return ("📚 <b>Cooler Knowledge — AI ឆ្លើយតាមឯកសាររបស់អ្នក</b>\n\n"
                f"ឯកសាររបស់{where}៖ <b>{n}</b>\n\n"
                "➕ <b>បញ្ចូល</b>\n"
                "• <code>/learn អត្ថបទ</code> — ឧ. ម៉ោងបើកហាង តម្លៃ ច្បាប់ Group\n"
                "• Reply <code>/learn</code> លើ PDF, .txt ឬរូបថត (ម៉ឺនុយ តារាងតម្លៃ)\n\n"
                "❓ <b>សួរ</b>៖ <code>/ask ហាងបើកម៉ោងប៉ុន្មាន?</code>\n"
                "🤖 <code>/autoanswer on</code> — Bot ឆ្លើយសំណួរក្នុង Group ដោយខ្លួនឯង ២៤/៧\n"
                "📋 /kb — មើល/លុបឯកសារ\n\n"
                "💡 ល្អសម្រាប់ហាង អាជីវកម្ម Team និង Group អតិថិជន")

    def list_markup(chat_id):
        m = InlineKeyboardMarkup()
        for d in docs(config, chat_id)[:20]:
            m.row(InlineKeyboardButton(f"🗑️ {d['title'][:40]} ({len(d['body']):,})", callback_data=f"kb:del:{d['id']}"))
        return m

    def answer(chat_id, question):
        rows = docs(config, chat_id)
        if not rows:
            return None
        prompt = f"DOCUMENTS:\n{build_documents(rows)}\n\nQUESTION: {question}"
        text = ask(ANSWER_SYSTEM, prompt).strip()
        if not text or NOT_FOUND in text[:40]:
            return ""
        return text

    def learn(message, uid):
        chat = message.chat
        if not can_edit(chat, uid):
            bot.reply_to(message, "⛔ មានតែ Admin Group ទេ ដែលអាចបញ្ចូលឯកសារបាន")
            return
        src = getattr(message, "reply_to_message", None)
        own_file = src is message
        parts = (message.text or message.caption or "").split(maxsplit=1)
        body, title = ("" if own_file else (parts[1] if len(parts) > 1 else "")), None
        if own_file and len(parts) > 1:
            title = parts[1].split("\n")[0][:80]
        try:
            if not body and src is not None:
                doc, photo = getattr(src, "document", None), getattr(src, "photo", None)
                if doc is not None:
                    name = (getattr(doc, "file_name", None) or "file").strip()
                    mime = getattr(doc, "mime_type", None) or ""
                    if (getattr(doc, "file_size", 0) or 0) > MAX_FILE_BYTES:
                        bot.reply_to(message, "📦 File ធំពេក (លើស 15MB)")
                        return
                    data = bot.download_file(bot.get_file(doc.file_id).file_path)
                    title = title or name
                    if name.lower().endswith(TEXT_EXT) or mime.startswith("text/"):
                        body = data.decode("utf-8", "replace")
                    elif mime == "application/pdf" or name.lower().endswith(".pdf") or mime.startswith("image/"):
                        bot.send_chat_action(chat.id, "typing")
                        body = extract(data, mime or "application/pdf")
                    else:
                        bot.reply_to(message, "📄 គាំទ្រ PDF, .txt ឬរូបថតប៉ុណ្ណោះ (Word → Save as PDF សិន)")
                        return
                elif photo:
                    bot.send_chat_action(chat.id, "typing")
                    data = bot.download_file(bot.get_file(photo[-1].file_id).file_path)
                    body = extract(data, "image/jpeg")
                    title = title or ("រូបថត" if own_file else (getattr(src, "caption", None) or "រូបថត").split("\n")[0])
                else:
                    body = getattr(src, "text", None) or getattr(src, "caption", None) or ""
            if not body.strip():
                bot.reply_to(message, help_text(chat), parse_mode="HTML")
                return
            title = title or body.strip().split("\n")[0][:40]
            doc_id = add_doc(config, chat.id, title, body, uid)
        except OverflowError:
            bot.reply_to(message, f"📚 ទំហំឯកសារពេញហើយ ({MAX_CHAT_CHARS:,} តួអក្សរ) — លុបខ្លះសិន /kb")
            return
        except Exception as exc:
            logger.warning("KB learn failed (%s: %s)", type(exc).__name__, str(exc)[:200])
            note = f"\n<code>{html.escape(str(exc)[:200])}</code>" if privileged(uid) else ""
            bot.reply_to(message, "😔 អានឯកសារនេះមិនបានទេ សូមសាកម្ដងទៀត" + note, parse_mode="HTML")
            return
        saved = docs(config, chat.id)
        size = next((len(d["body"]) for d in saved if d["id"] == doc_id), 0)
        bot.reply_to(message, f"✅ បានរៀន «{html.escape(title)}» ({size:,} តួអក្សរ)\n"
                     f"📚 ឯកសារសរុប៖ {len(saved)}\n\nសាកសួរ៖ <code>/ask ...</code>", parse_mode="HTML")

    @bot.message_handler(commands=["learn", "teach"], content_types=["text", "photo", "document"])
    def learn_cmd(message):
        learn(message, message.from_user.id)

    def caption_learn(message):
        cap = (getattr(message, "caption", None) or "").strip().lower()
        return cap.split()[0].split("@")[0] in ("/learn", "/teach") if cap else False

    @bot.message_handler(func=caption_learn, content_types=["photo", "document"])
    def learn_caption(message):
        # "/learn" written as the caption of the file itself
        message.reply_to_message = message
        learn(message, message.from_user.id)

    @bot.message_handler(commands=["ask", "kbask"])
    def ask_cmd(message):
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            bot.reply_to(message, help_text(message.chat), parse_mode="HTML")
            return
        if not docs(config, message.chat.id):
            bot.reply_to(message, "📚 មិនទាន់មានឯកសារទេ — បញ្ចូលដោយ <code>/learn ...</code>", parse_mode="HTML")
            return
        try:
            bot.send_chat_action(message.chat.id, "typing")
            text = answer(message.chat.id, parts[1])
        except Exception as exc:
            logger.warning("KB ask failed (%s)", type(exc).__name__)
            bot.reply_to(message, "😔 AI រវល់បន្តិច សូមសួរម្ដងទៀត")
            return
        bot.reply_to(message, text or "🤔 ក្នុងឯកសារមិនមានចម្លើយនេះទេ — សូមសួរ Admin ផ្ទាល់ 🙏")

    @bot.message_handler(commands=["kb", "knowledge", "docs"])
    def kb_cmd(message):
        rows = docs(config, message.chat.id)
        body = help_text(message.chat)
        if rows:
            body += f"\n\n📊 ប្រើ {used_chars(config, message.chat.id):,} / {MAX_CHAT_CHARS:,} តួអក្សរ"
            if is_group(message.chat):
                body += f"\n🤖 ឆ្លើយស្វ័យប្រវត្តិ៖ {'✅ បើក' if auto_on(config, message.chat.id) else '⛔ បិទ'}"
        bot.reply_to(message, body, reply_markup=list_markup(message.chat.id) if rows else None, parse_mode="HTML")

    @bot.message_handler(commands=["kbclear"])
    def kbclear_cmd(message):
        if not can_edit(message.chat, message.from_user.id):
            bot.reply_to(message, "⛔ សម្រាប់ Admin Group ប៉ុណ្ណោះ")
            return
        n = delete_doc(config, message.chat.id)
        bot.reply_to(message, f"🗑️ បានលុបឯកសារ {n}")

    @bot.message_handler(commands=["autoanswer"])
    def auto_cmd(message):
        chat = message.chat
        if not is_group(chat):
            bot.reply_to(message, "ℹ️ /autoanswer ប្រើក្នុង Group — ក្នុង Private ប្រើ /ask")
            return
        if not can_edit(chat, message.from_user.id):
            bot.reply_to(message, "⛔ សម្រាប់ Admin Group ប៉ុណ្ណោះ")
            return
        arg = ((message.text or "").split() + [""])[1].lower()
        on = arg in ("on", "1", "បើក") if arg else not auto_on(config, chat.id)
        set_auto(config, chat.id, on)
        bot.reply_to(message, "✅ បើកហើយ — Bot នឹងឆ្លើយសំណួរ (បញ្ចប់ដោយ ?) ដែលមានក្នុងឯកសារ ហើយស្ងៀមបើមិនដឹង"
                     if on else "⛔ បិទការឆ្លើយស្វ័យប្រវត្តិហើយ")

    def auto_match(message):
        chat = getattr(message, "chat", None)
        return (is_group(chat) and looks_like_question(getattr(message, "text", None))
                and auto_on(config, chat.id) and bool(docs(config, chat.id)))

    @bot.message_handler(func=auto_match)
    def auto_reply(message):
        try:
            text = answer(message.chat.id, message.text)
        except Exception as exc:
            logger.warning("KB auto answer failed (%s)", type(exc).__name__)
            return
        if text:
            bot.reply_to(message, "🤖 " + text)

    @bot.callback_query_handler(func=lambda c: (c.data or "").startswith("kb:"))
    def kb_cb(call):
        parts = (call.data or "").split(":")
        chat = call.message.chat
        if len(parts) == 3 and parts[1] == "del":
            if not can_edit(chat, call.from_user.id):
                bot.answer_callback_query(call.id, "⛔ សម្រាប់ Admin ប៉ុណ្ណោះ", show_alert=True)
                return
            try:
                n = delete_doc(config, chat.id, int(parts[2]))
            except ValueError:
                n = 0
            bot.answer_callback_query(call.id, "🗑️ បានលុប" if n else "មិនមានទៀតទេ")
            try:
                bot.edit_message_reply_markup(chat.id, call.message.message_id, reply_markup=list_markup(chat.id))
            except Exception:
                pass
            return
        if parts[1:2] == ["menu"]:
            bot.answer_callback_query(call.id)
            bot.send_message(chat.id, help_text(chat), parse_mode="HTML")
            return
        bot.answer_callback_query(call.id)

    return answer
