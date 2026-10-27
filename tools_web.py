"""🧰 Cooler Daily Tools — the Hub's everyday-helpers web app (hub/tools.html) talks to these endpoints.

Same prompts, same tables as the bot commands (daily_tools.py, knowledge_base.py), so a member sees the
same reminders, cash book and documents in the bot chat and in the app (their private chat = their book).

  POST /tools/me                         → member, AI uses left, categories, document count
  POST /tools/ai   {tool, text, photo, audio, lang}   tool = scam | money | totext | translate | homework
  POST /tools/remind/add {text} · /tools/remind/list · /tools/remind/del {id}
  POST /tools/cash/add {kind, amount, currency, category, note} or {text} · /tools/cash/list · /tools/cash/del {id}
  POST /tools/report {month: "YYYY-MM"}
  POST /tools/kb/list · /tools/kb/add {title, body | file, mime, name} · /tools/kb/del {id} · /tools/kb/ask {q}
  POST /tools/send {title, text}         → the bot sends it to the member's chat (to keep / forward)
Every call carries Telegram's signed initData; members only. AI calls share TOOLS_DAILY_LIMIT (default 40).
"""
import base64
import html
import json
import logging
import os
import re
import time
from datetime import datetime

logger = logging.getLogger(__name__)
HOOKS = {}
MAX_MEDIA = 8_000_000
LANGS = {
    "auto": ("🔄 ខ្មែរ ⇄ English", None), "km": ("🇰🇭 ខ្មែរ", "Khmer"), "en": ("🇬🇧 English", "English"),
    "zh": ("🇨🇳 中文", "Simplified Chinese"), "th": ("🇹🇭 ไทย", "Thai"), "vi": ("🇻🇳 Tiếng Việt", "Vietnamese"),
    "ko": ("🇰🇷 한국어", "Korean"), "ja": ("🇯🇵 日本語", "Japanese"),
}
_rate_cache = {"t": 0.0, "v": None}


def register(bot, config, hooks=None):
    HOOKS.clear()
    HOOKS.update(hooks or {})
    HOOKS["bot"] = bot


class Refused(Exception):
    pass


def ai_limit():
    try:
        return int(os.getenv("TOOLS_DAILY_LIMIT", "40"))
    except ValueError:
        return 40


def _ask(config, parts, prompt, max_tokens=2500):
    fn = HOOKS.get("ai")
    if fn:
        return fn(parts, prompt)
    import knowledge_base
    return knowledge_base.gemini(getattr(config, "GEMINI_API_KEY", ""), list(parts) + [{"text": prompt}],
                                 max_tokens=max_tokens, temperature=0.2)


def khr_rate():
    fn = HOOKS.get("rate")
    if fn:
        return fn()
    if _rate_cache["v"] and time.time() - _rate_cache["t"] < 3600:
        return _rate_cache["v"]
    import daily_tools
    try:
        import market_info
        r = market_info.fetch_rates()
        v = float(r["rates"]["KHR"]) if r else daily_tools.KHR_PER_USD_FALLBACK
    except Exception:
        v = daily_tools.KHR_PER_USD_FALLBACK
    _rate_cache.update(t=time.time(), v=v)
    return v


def _b64(data, field, limit=MAX_MEDIA):
    raw = str(data.get(field) or "")
    mime = ""
    m = re.match(r"data:([\w/+.-]+);base64,", raw[:100])
    if m:
        mime, raw = m.group(1), raw[m.end():]
    if not raw:
        return None, ""
    try:
        blob = base64.b64decode(raw, validate=False)
    except Exception:
        raise Refused("📎 ឯកសារខូច — សាកម្ដងទៀត")
    if len(blob) > limit:
        raise Refused("📎 ឯកសារធំពេក — សាកមួយតូចជាង")
    return blob, mime


def _part(blob, mime):
    return {"inline_data": {"mime_type": mime, "data": base64.b64encode(blob).decode()}}


def run_ai_tool(config, uid, data):
    import daily_tools as dt
    tool = str(data.get("tool") or "")
    text = str(data.get("text") or "").strip()[:4000]
    photo, pmime = _b64(data, "photo")
    parts = [_part(photo, pmime or "image/jpeg")] if photo else []
    if tool == "scam":
        if not text and not parts:
            raise Refused("🔗 ដាក់ Link, សារ ឬ Screenshot ជាមុនសិន")
        prompt, title = dt.SCAM_PROMPT + text, "🚨 លទ្ធផលឆែក Scam"
    elif tool == "money":
        if not parts:
            raise Refused("📸 ថតរូបក្រដាសប្រាក់ជាមុនសិន")
        prompt, title = dt.MONEY_PROMPT + text[:300], "💵 ពិនិត្យលុយ"
    elif tool == "totext":
        audio, amime = _b64(data, "audio")
        if not audio:
            raise Refused("🎤 ថតសំឡេង ឬជ្រើសឯកសារសំឡេងជាមុនសិន")
        parts = [_part(audio, amime or "audio/wav")]
        prompt, title = dt.TOTEXT_PROMPT, "📝 Voice → អក្សរ"
    elif tool == "translate":
        if not text and not parts:
            raise Refused("✍️ សរសេរអក្សរ ឬថតរូបដែលមានអក្សរជាមុនសិន")
        lang = LANGS.get(str(data.get("lang") or "auto"), LANGS["auto"])[1]
        base = dt.TRANSLATE_PROMPT if not lang else (
            f"Translate the text (or all text in the image) into natural {lang}. Keep names, numbers, prices. "
            "Output only the translation, keep line breaks.")
        prompt, title = base + ("\n\nText:\n" + text if text else ""), "🌐 បកប្រែ"
    elif tool == "homework":
        if not text and not parts:
            raise Refused("📚 ថតរូបលំហាត់ ឬសរសេរលំហាត់ជាមុនសិន")
        prompt, title = dt.HOMEWORK_PROMPT + ("\n\nExercise:\n" + text if text else ""), "📚 ពន្យល់លំហាត់"
    else:
        raise Refused("មិនស្គាល់មុខងារនេះ")
    out = (_ask(config, parts, prompt) or "").strip()
    if not out:
        raise RuntimeError("empty answer")
    return {"title": title, "text": out[:6000]}


# ---------------- reminders ----------------

def _db(config):
    import daily_tools
    return daily_tools._db(config)


def reminders(config, uid):
    conn = _db(config)
    try:
        rows = conn.execute("SELECT id, due, repeat, what FROM reminders WHERE chat_id=? ORDER BY due LIMIT 30", (uid,)).fetchall()
        return [{"id": r["id"], "due": r["due"], "repeat": r["repeat"], "what": r["what"]} for r in rows]
    finally:
        conn.close()


def add_reminder(config, uid, text, now=None):
    import daily_tools as dt
    text = " ".join(str(text or "").split())[:300]
    if not text:
        raise Refused("✍️ សរសេរពេល និងរឿងដែលត្រូវរំលឹក")
    if len(reminders(config, uid)) >= 30:
        raise Refused("📋 មានការរំលឹក ៣០ ហើយ — លុបខ្លះសិន")
    now = now or datetime.now(dt.TZ_KH)
    raw = _ask(config, [], dt.REMIND_PROMPT.format(now=now.strftime("%Y-%m-%d %H:%M"), weekday=now.strftime("%A"),
                                                   text=dt.kh_digits_to_ascii(text)))
    rem = dt.parse_reminder_json(raw)
    if not rem:
        raise Refused("🤔 មិនយល់ពេលវេលា — សាកសរសេរឲ្យច្បាស់ ឧ. «ស្អែក ម៉ោង ៩ ព្រឹក ...»")
    when = rem["when"]
    if when <= now:
        nxt = dt.next_time(when, rem["repeat"])
        while nxt is not None and nxt <= now:
            nxt = dt.next_time(nxt, rem["repeat"])
        if nxt is None:
            raise Refused("⌛ ពេលនោះកន្លងហើយ — សូមដាក់ពេលខាងមុខ")
        when = nxt
    what = rem["what"] or text[:200]
    conn = _db(config)
    try:
        with conn:
            rid = conn.execute("INSERT INTO reminders(chat_id, user_id, due, repeat, what, created_at) VALUES (?,?,?,?,?,?)",
                               (uid, uid, int(when.timestamp()), rem["repeat"], what, int(time.time()))).lastrowid
    finally:
        conn.close()
    return {"id": rid, "due": int(when.timestamp()), "repeat": rem["repeat"], "what": what}


def del_row(config, table, uid, rid, owner_col="chat_id"):
    conn = _db(config)
    try:
        with conn:
            return conn.execute(f"DELETE FROM {table} WHERE id=? AND {owner_col}=?", (int(rid), uid)).rowcount
    finally:
        conn.close()


# ---------------- cash book ----------------

def categories():
    import daily_tools as dt
    return list(dt.CATEGORIES) + ["📦 ផ្សេងៗ", "💰 ចំណូល"]


def add_cash(config, uid, data):
    import daily_tools as dt
    if data.get("text"):
        entry = dt.parse_cash(str(data["text"])[:200])
        if not entry:
            raise Refused("🤔 មិនយល់ — ឧ. «ចាយ 5000 រៀល កាហ្វេ»")
    else:
        try:
            amount = float(str(data.get("amount") or "0").replace(",", ""))
        except ValueError:
            amount = 0
        if not 0 < amount < 1e12:
            raise Refused("💵 វាយចំនួនលុយ")
        kind = "in" if data.get("kind") == "in" else "out"
        currency = "USD" if data.get("currency") == "USD" else "KHR"
        cat = str(data.get("category") or "")
        if cat not in categories():
            cat = "💰 ចំណូល" if kind == "in" else "📦 ផ្សេងៗ"
        entry = {"kind": kind, "amount": amount, "currency": currency, "category": cat,
                 "note": " ".join(str(data.get("note") or "").split())[:60]}
    conn = _db(config)
    try:
        with conn:
            rid = conn.execute("INSERT INTO cashbook(chat_id, user_id, kind, amount, currency, category, note, at) "
                               "VALUES (?,?,?,?,?,?,?,?)", (uid, uid, entry["kind"], entry["amount"], entry["currency"],
                                                           entry["category"], entry["note"], int(time.time()))).lastrowid
    finally:
        conn.close()
    return dict(entry, id=rid, at=int(time.time()))


def month_bounds(month):
    import daily_tools as dt
    now = datetime.now(dt.TZ_KH)
    try:
        y, m = (int(x) for x in str(month or "").split("-")[:2])
        start = datetime(y, m, 1, tzinfo=dt.TZ_KH)
    except (ValueError, TypeError):
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(year=start.year + (start.month == 12), month=start.month % 12 + 1)
    return start, end


def cash_rows(config, uid, month=None, limit=None):
    start, end = month_bounds(month)
    conn = _db(config)
    try:
        sql = "SELECT id, kind, amount, currency, category, note, at FROM cashbook WHERE chat_id=? AND at>=? AND at<? ORDER BY at DESC"
        rows = conn.execute(sql + (f" LIMIT {int(limit)}" if limit else ""), (uid, int(start.timestamp()), int(end.timestamp()))).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def report(config, uid, month=None):
    start, _end = month_bounds(month)
    rows = cash_rows(config, uid, month)
    khr = khr_rate()
    usd = lambda r: r["amount"] if r["currency"] == "USD" else r["amount"] / khr
    income = sum(usd(r) for r in rows if r["kind"] == "in")
    spent = sum(usd(r) for r in rows if r["kind"] == "out")
    cats = {}
    for r in rows:
        if r["kind"] == "out":
            cats[r["category"]] = cats.get(r["category"], 0) + usd(r)
    days = {}
    for r in rows:
        if r["kind"] == "out":
            d = datetime.fromtimestamp(r["at"], start.tzinfo).day
            days[d] = days.get(d, 0) + usd(r)
    return {"month": start.strftime("%Y-%m"), "income": round(income, 2), "spent": round(spent, 2),
            "balance": round(income - spent, 2), "khr": khr, "count": len(rows),
            "cats": [[c, round(v, 2), round(100 * v / spent) if spent else 0] for c, v in sorted(cats.items(), key=lambda kv: -kv[1])],
            "days": [[d, round(v, 2)] for d, v in sorted(days.items())]}


# ---------------- documents (AI answers from your own files) ----------------

def kb_list(config, uid):
    import knowledge_base as kb
    kb.init_schema(config)
    return [{"id": d["id"], "title": d["title"], "chars": len(d["body"]), "at": d["created_at"]} for d in kb.docs(config, uid)]


def kb_add(config, uid, data):
    import knowledge_base as kb
    kb.init_schema(config)
    title = str(data.get("title") or "").strip()
    body = str(data.get("body") or "").strip()
    blob, mime = _b64(data, "file", kb.MAX_FILE_BYTES)
    name = str(data.get("name") or "").strip()
    if blob:
        mime = mime or str(data.get("mime") or "")
        if name.lower().endswith(kb.TEXT_EXT) or mime.startswith("text/"):
            body = blob.decode("utf-8", "replace")
        elif mime == "application/pdf" or name.lower().endswith(".pdf") or mime.startswith("image/"):
            extract = HOOKS.get("extract") or (lambda b, m: kb.gemini(getattr(config, "GEMINI_API_KEY", ""), [
                _part(b, m), {"text": kb.EXTRACT_PROMPT}], max_tokens=16000, temperature=0))
            body = extract(blob, mime or "application/pdf")
        else:
            raise Refused("📄 គាំទ្រ PDF, .txt ឬរូបថតប៉ុណ្ណោះ (Word → Save as PDF សិន)")
        title = title or name or "ឯកសារ"
    if not body.strip():
        raise Refused("✍️ សរសេរអត្ថបទ ឬជ្រើសឯកសារ")
    try:
        doc_id = kb.add_doc(config, uid, title or body.split("\n")[0][:40], body, uid)
    except OverflowError:
        raise Refused(f"📚 ទំហំឯកសារពេញហើយ ({kb.MAX_CHAT_CHARS:,} តួអក្សរ) — លុបខ្លះសិន")
    return next(d for d in kb_list(config, uid) if d["id"] == doc_id)


def kb_ask(config, uid, q):
    import knowledge_base as kb
    kb.init_schema(config)
    q = " ".join(str(q or "").split())[:500]
    if not q:
        raise Refused("❓ សរសេរសំណួរ")
    rows = kb.docs(config, uid)
    if not rows:
        raise Refused("📚 មិនទាន់មានឯកសារ — បញ្ចូលឯកសារជាមុនសិន")
    ask = HOOKS.get("kb_ask") or (lambda system, prompt: kb.gemini(getattr(config, "GEMINI_API_KEY", ""),
                                                                   [{"text": prompt}], system=system, max_tokens=1500))
    text = (ask(kb.ANSWER_SYSTEM, f"DOCUMENTS:\n{kb.build_documents(rows)}\n\nQUESTION: {q}") or "").strip()
    if not text or kb.NOT_FOUND in text[:40]:
        return {"found": False, "text": "🤷 រកមិនឃើញចម្លើយក្នុងឯកសាររបស់អ្នកទេ — បន្ថែមឯកសារដែលមានព័ត៌មាននេះ"}
    return {"found": True, "text": text[:4000]}


# ---------------- HTTP ----------------

def _json(code, data):
    return code, json.dumps(data, ensure_ascii=False).encode(), "application/json", {}


def handle_post(path, config, body, bot=None, now=None):
    import studio_web
    import voice_web
    try:
        data = json.loads(body.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError
    except (ValueError, AttributeError):
        return _json(400, {"error": "bad_request"})
    user, owner, member = voice_web._user(config, data, now)
    if not user:
        return _json(401, {"error": "open_in_telegram", "msg": "សូមបើកពី Cooler Hub ក្នុង Telegram"})
    uid = int(user["id"])
    path = path.rstrip("/")
    bot = bot or HOOKS.get("bot")
    studio_web.init_schema(config)
    import daily_tools
    daily_tools.init_schema(config)

    def left():
        return None if owner else max(0, ai_limit() - studio_web.text_used(config, uid, "tools"))

    if path == "/tools/me":
        import knowledge_base as kb
        kb.init_schema(config)
        return _json(200, {"member": member, "owner": owner, "left": left() if member else 0, "limit": ai_limit(),
                           "categories": categories(), "langs": [[k, v[0]] for k, v in LANGS.items()],
                           "docs": len(kb.docs(config, uid)) if member else 0})
    if not member:
        return _json(403, {"error": "members_only", "msg": "🔒 សម្រាប់សមាជិក — វាយ /pay ក្នុង Bot"})
    uses_ai = path in ("/tools/ai", "/tools/remind/add", "/tools/kb/ask") or (path == "/tools/kb/add" and data.get("file"))
    try:
        if uses_ai and not getattr(config, "GEMINI_API_KEY", "") and not HOOKS.get("ai"):
            raise Refused("AI មិនទាន់បើកដំណើរការទេ")
        if uses_ai and not owner:
            if studio_web.text_used(config, uid, "tools") >= ai_limit():
                raise Refused(f"⏳ ថ្ងៃនេះប្រើ AI អស់ {ai_limit()} ដងហើយ — ស្អែកមកម្ដងទៀតណា 🙏")
            studio_web.text_add(config, uid, "tools", 1)
        try:
            if path == "/tools/ai":
                out = run_ai_tool(config, uid, data)
            elif path == "/tools/remind/add":
                out = {"item": add_reminder(config, uid, data.get("text")), "items": reminders(config, uid)}
            elif path == "/tools/remind/list":
                out = {"items": reminders(config, uid)}
            elif path == "/tools/remind/del":
                del_row(config, "reminders", uid, data.get("id") or 0)
                out = {"items": reminders(config, uid)}
            elif path == "/tools/cash/add":
                out = {"item": add_cash(config, uid, data), "items": cash_rows(config, uid, limit=30)}
            elif path == "/tools/cash/list":
                out = {"items": cash_rows(config, uid, data.get("month"), limit=60)}
            elif path == "/tools/cash/del":
                del_row(config, "cashbook", uid, data.get("id") or 0)
                out = {"items": cash_rows(config, uid, limit=30)}
            elif path == "/tools/report":
                out = report(config, uid, data.get("month"))
            elif path == "/tools/kb/list":
                out = {"items": kb_list(config, uid)}
            elif path == "/tools/kb/add":
                out = {"item": kb_add(config, uid, data), "items": kb_list(config, uid)}
            elif path == "/tools/kb/del":
                import knowledge_base as kb
                kb.delete_doc(config, uid, int(data.get("id") or 0))
                out = {"items": kb_list(config, uid)}
            elif path == "/tools/kb/ask":
                out = kb_ask(config, uid, data.get("q"))
            elif path == "/tools/send":
                if bot is None:
                    raise RuntimeError("no bot")
                title = " ".join(str(data.get("title") or "").split())[:80]
                text = str(data.get("text") or "").strip()[:3800]
                if not text:
                    raise Refused("គ្មានអ្វីផ្ញើ")
                bot.send_message(uid, (f"<b>{html.escape(title)}</b>\n\n" if title else "") + html.escape(text), parse_mode="HTML")
                out = {"ok": True}
            else:
                return _json(404, {"error": "not_found"})
        except Exception:
            if uses_ai and not owner:
                studio_web.text_add(config, uid, "tools", -1)
            raise
    except Refused as exc:
        return _json(400, {"error": "refused", "msg": str(exc)})
    except (ValueError, TypeError) as exc:
        logger.info("tools bad input (%s)", type(exc).__name__)
        return _json(400, {"error": "input", "msg": "🤔 ព័ត៌មានមិនត្រឹមត្រូវ — សាកម្ដងទៀត"})
    except Exception as exc:
        logger.warning("tools %s failed (%s: %s)", path, type(exc).__name__, str(exc)[:200])
        msg = "😔 AI រវល់ — សាកម្ដងទៀតបន្តិចទៀត (មិនកាត់ចំនួនទេ)"
        if owner:
            msg += f" · {type(exc).__name__}: {str(exc)[:150]}"
        return _json(502, {"error": "server", "msg": msg})
    if isinstance(out, dict):
        out.setdefault("left", left())
    return _json(200, out)
