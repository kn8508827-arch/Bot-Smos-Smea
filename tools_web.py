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
from datetime import datetime, timedelta

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

INCOME_CATS = ["💼 ប្រាក់ខែ", "🛒 លក់ដូរ", "🎁 កាដូ / ផ្សេងៗ", "💰 ចំណូល"]
TIPS_PROMPT = """You are a kind Cambodian money coach. Here is one person's money for {month} (USD; 1$ = {khr:,.0f} riel):
income {income}, spent {spent}, left {balance}, monthly budget {budget}, last month spent {prev_spent}.
Spending by category: {cats}. Biggest single expenses: {top}.
In simple, warm modern Khmer give: one short line on how the month looks, then exactly 3 practical saving tips that
fit THESE numbers (concrete, e.g. "កាហ្វេ ៥ ដង/សប្ដាហ៍ → ៣ ដង សន្សំបាន ~$X/ខែ"), then one encouraging line.
Max 9 short lines, emoji ok, no markdown, no investment or loan advice."""


def categories():
    import daily_tools as dt
    return list(dt.CATEGORIES) + ["📦 ផ្សេងៗ"] + INCOME_CATS


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
        if kind == "in" and cat not in INCOME_CATS:
            cat = "💰 ចំណូល"
        elif kind == "out" and (cat not in categories() or cat in INCOME_CATS):
            cat = "📦 ផ្សេងៗ"
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


def _usd(r, khr):
    return r["amount"] if r["currency"] == "USD" else r["amount"] / khr


def budget(config, uid):
    conn = _db(config)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS cash_budget (user_id INTEGER PRIMARY KEY, usd REAL NOT NULL)")
        row = conn.execute("SELECT usd FROM cash_budget WHERE user_id=?", (uid,)).fetchone()
        return float(row["usd"]) if row else None
    finally:
        conn.close()


def set_budget(config, uid, usd):
    try:
        usd = float(str(usd or "0").replace(",", ""))
    except ValueError:
        raise Refused("💵 វាយថវិកាជាដុល្លារ ឧ. 300")
    conn = _db(config)
    try:
        with conn:
            conn.execute("CREATE TABLE IF NOT EXISTS cash_budget (user_id INTEGER PRIMARY KEY, usd REAL NOT NULL)")
            if usd > 0:
                conn.execute("INSERT OR REPLACE INTO cash_budget(user_id, usd) VALUES (?, ?)", (uid, min(usd, 1e9)))
            else:
                conn.execute("DELETE FROM cash_budget WHERE user_id=?", (uid,))
    finally:
        conn.close()
    return budget(config, uid)


def _prev_month(start):
    return (start - timedelta(days=1)).strftime("%Y-%m")


def report(config, uid, month=None):
    import calendar as _cal
    import daily_tools as dt
    start, end = month_bounds(month)
    rows = cash_rows(config, uid, start.strftime("%Y-%m"))
    khr = khr_rate()
    usd = lambda r: _usd(r, khr)
    income = sum(usd(r) for r in rows if r["kind"] == "in")
    spent = sum(usd(r) for r in rows if r["kind"] == "out")
    cats, icats, days = {}, {}, {}
    for r in rows:
        if r["kind"] == "out":
            cats[r["category"]] = cats.get(r["category"], 0) + usd(r)
            d = datetime.fromtimestamp(r["at"], start.tzinfo).day
            days[d] = days.get(d, 0) + usd(r)
        else:
            icats[r["category"] or "💰 ចំណូល"] = icats.get(r["category"] or "💰 ចំណូល", 0) + usd(r)
    prev = cash_rows(config, uid, _prev_month(start))
    prev_spent = sum(usd(r) for r in prev if r["kind"] == "out")
    prev_income = sum(usd(r) for r in prev if r["kind"] == "in")
    now = datetime.now(dt.TZ_KH)
    month_days = _cal.monthrange(start.year, start.month)[1]
    current = start.year == now.year and start.month == now.month
    elapsed = now.day if current else month_days
    avg = spent / elapsed if elapsed else 0
    bud = budget(config, uid)
    top = sorted((r for r in rows if r["kind"] == "out"), key=lambda r: -usd(r))[:5]
    pct = lambda a, b: None if not b else round(100 * (a - b) / b)
    return {"month": start.strftime("%Y-%m"), "income": round(income, 2), "spent": round(spent, 2),
            "balance": round(income - spent, 2), "khr": khr, "count": len(rows),
            "cats": [[c, round(v, 2), round(100 * v / spent) if spent else 0] for c, v in sorted(cats.items(), key=lambda kv: -kv[1])],
            "incomeCats": [[c, round(v, 2), round(100 * v / income) if income else 0] for c, v in sorted(icats.items(), key=lambda kv: -kv[1])],
            "days": [[d, round(v, 2)] for d, v in sorted(days.items())],
            "prev": {"month": _prev_month(start), "spent": round(prev_spent, 2), "income": round(prev_income, 2),
                     "spentPct": pct(spent, prev_spent), "incomePct": pct(income, prev_income)},
            "avgDay": round(avg, 2), "current": current, "elapsed": elapsed, "monthDays": month_days,
            "projected": round(avg * month_days, 2) if current else round(spent, 2),
            "budget": bud, "budgetPct": round(100 * spent / bud) if bud else None,
            "savingRate": round(100 * (income - spent) / income) if income else None,
            "top": [{"note": r["note"] or (r["category"] or "").split(" ", 1)[-1], "cat": r["category"], "usd": round(usd(r), 2),
                     "amount": r["amount"], "currency": r["currency"], "at": r["at"]} for r in top]}


def year_report(config, uid, year=None):
    import daily_tools as dt
    try:
        year = int(year)
    except (TypeError, ValueError):
        year = datetime.now(dt.TZ_KH).year
    khr = khr_rate()
    months = []
    for m in range(1, 13):
        rows = cash_rows(config, uid, f"{year}-{m:02d}")
        months.append([m, round(sum(_usd(r, khr) for r in rows if r["kind"] == "in"), 2),
                       round(sum(_usd(r, khr) for r in rows if r["kind"] == "out"), 2)])
    inc, sp = sum(x[1] for x in months), sum(x[2] for x in months)
    return {"year": year, "months": months, "income": round(inc, 2), "spent": round(sp, 2), "balance": round(inc - sp, 2), "khr": khr}


def report_tips(config, uid, month=None):
    r = report(config, uid, month)
    if not r["count"]:
        raise Refused("📊 ខែនេះមិនទាន់មានកំណត់ត្រា — កត់លុយជាមុនសិន")
    prompt = TIPS_PROMPT.format(month=r["month"], khr=r["khr"], income=r["income"], spent=r["spent"], balance=r["balance"],
                                budget=r["budget"] or "not set", prev_spent=r["prev"]["spent"],
                                cats=", ".join(f"{c} ${v}" for c, v, _p in r["cats"][:6]) or "none",
                                top=", ".join(f"{t['note']} ${t['usd']}" for t in r["top"][:3]) or "none")
    out = (_ask(config, [], prompt) or "").strip()
    if not out:
        raise RuntimeError("empty answer")
    return {"text": out[:2500]}


def report_send(config, uid, bot, month=None):
    """📤 The report as a picture card + the month's records as a CSV file (opens in Excel / Google Sheets)."""
    import csv
    import io
    import card_render
    r = report(config, uid, month)
    if not r["count"]:
        raise Refused("📊 ខែនេះមិនទាន់មានកំណត់ត្រា")
    caption = (f"📊 របាយការណ៍ខែ {r['month']}\n➕ ចំណូល ${r['income']:,.2f} · ➖ ចំណាយ ${r['spent']:,.2f} · "
               f"សល់ ${r['balance']:,.2f}")
    try:
        png = card_render.report_card(r["month"], r["income"], r["spent"], [(c, v) for c, v, _p in r["cats"]], r["khr"])
        bot.send_photo(uid, png, caption=caption)
    except Exception as exc:
        logger.info("report card failed (%s)", type(exc).__name__)
        bot.send_message(uid, caption)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "type", "category", "note", "amount", "currency", "usd"])
    for row in reversed(cash_rows(config, uid, r["month"])):
        w.writerow([datetime.fromtimestamp(row["at"], __import__("daily_tools").TZ_KH).strftime("%Y-%m-%d %H:%M"),
                    "income" if row["kind"] == "in" else "expense", row["category"], row["note"], row["amount"],
                    row["currency"], round(_usd(row, r["khr"]), 2)])
    f = io.BytesIO(("\ufeff" + buf.getvalue()).encode("utf-8"))
    f.name = f"cooler-cash-{r['month']}.csv"
    bot.send_document(uid, f, caption="📄 កំណត់ត្រាទាំងអស់ — បើកក្នុង Excel / Google Sheets")
    return {"ok": True}


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
    uses_ai = path in ("/tools/ai", "/tools/remind/add", "/tools/kb/ask", "/tools/report/tips") or (path == "/tools/kb/add" and data.get("file"))
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
                out = {"items": cash_rows(config, uid, data.get("month"), limit=max(1, min(int(data.get("limit") or 60), 2000)))}
            elif path == "/tools/cash/del":
                del_row(config, "cashbook", uid, data.get("id") or 0)
                out = {"items": cash_rows(config, uid, limit=30)}
            elif path == "/tools/report":
                out = report(config, uid, data.get("month"))
            elif path == "/tools/report/year":
                out = year_report(config, uid, data.get("year"))
            elif path == "/tools/report/tips":
                out = report_tips(config, uid, data.get("month"))
            elif path == "/tools/report/send":
                if bot is None:
                    raise RuntimeError("no bot")
                out = report_send(config, uid, bot, data.get("month"))
            elif path == "/tools/budget/set":
                out = {"budget": set_budget(config, uid, data.get("usd"))}
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
