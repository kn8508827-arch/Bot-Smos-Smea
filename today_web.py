"""☀️ Cooler Today — the Hub's "life & news" web app (hub/today.html) talks to these endpoints.

One app for: today's date (solar + Khmer lunar, holidays, next ថ្ងៃសីល), weather for 25 provinces,
dollar & other currencies, world gold price, news by topic, ថ្ងៃសីល reminder on/off, personal
anniversaries (solar or lunar), the 7:00 morning brief on/off, trips around Cambodia, and health
(daily exercise, guides, blood-pressure log, AI health questions with an emergency check).

Same data and tables as the bot commands (/calendar /weather /rate /gold /news /sila /anniv /morning
/trip /health /bp /askhealth); the member's private chat id is used, so the app and the chat agree.

  POST /today/me                     → member + static lists (provinces, news topics, trip, health guides)
  POST /today/dash {province}        → date, weather, dollar, gold, top news, quote, brief & sila state
  POST /today/weather {province} · /today/rates · /today/gold · /today/news {topic}
  POST /today/sila · /today/sila/set {on}
  POST /today/anniv/list · /today/anniv/add {text | lunar, month, day, waning, title} · /today/anniv/del {id}
  POST /today/brief/set {on, province}
  POST /today/health/done · /today/bp/list · /today/bp/add {text} · /today/health/ask {q}
Every call carries Telegram's signed initData; members only.
"""
import html
import json
import logging
import time
from datetime import timedelta

logger = logging.getLogger(__name__)
HOOKS = {}


def register(bot, config, hooks=None):
    HOOKS.clear()
    HOOKS.update(hooks or {})
    HOOKS["bot"] = bot


class Refused(Exception):
    pass


def _today():
    import buddhist_days
    return buddhist_days.now_kh().date()


# ---------------- static content ----------------

def static_lists():
    import health_features as hf
    import health_more as hm
    import market_info as mi
    import trip_places as tp
    import weather_service as ws
    return {
        "provinces": [[k, v[0]] for k, v in ws.PROVINCES.items()],
        "newsTopics": [[k, v[0]] for k, v in mi.NEWS_TOPICS.items()],
        "trip": [{"key": p[0], "km": p[1], "en": p[2], "emoji": p[3], "drive": p[4],
                  "places": [{"km": pl[0], "desc": pl[2], "tip": pl[3], "map": tp.map_url(pl[1], p[2])} for pl in p[5]]}
                 for p in tp.PROVINCES],
        "health": {
            "routine": _routine(),
            "balance": hf.BALANCE_TEXT, "warning": hf.WARNING_TEXT, "sos": hf.SOS_TEXT,
            "water": hm.WATER_TEXT, "sleep": hm.SLEEP_TEXT, "stop": hf.STOP_NOTE,
            "topics": [[k, v[0], v[1]] for k, v in hf.TOPICS.items()],
            "points": hf.POINTS_EXERCISE,
        },
        "solarMonths": _kc().SOLAR_MONTHS, "lunarMonths": _kc().LUNAR_MONTHS,
    }


def _kc():
    import khmer_calendar
    return khmer_calendar


def _routine():
    import health_features as hf
    from datetime import datetime
    title, steps = hf.routine_for(datetime.now(hf.TZ).weekday())
    return {"title": title, "steps": steps}


# ---------------- live data ----------------

def weather(province):
    import weather_service as ws
    if province not in ws.PROVINCES:
        province = "phnom_penh"
    data = ws.weather_service.fetch(province)
    cur = data["current"]
    icon, desc = ws.describe_code(cur.get("code"))
    days = []
    for i, d in enumerate(data["days"]):
        di, dd = ws.describe_code(d["code"])
        days.append({"label": "ថ្ងៃនេះ" if i == 0 else "ស្អែក" if i == 1 else ws._day_label(i, d["date"]),
                     "date": d["date"].strftime("%d/%m"), "icon": di, "desc": dd, "tmin": d["tmin"], "tmax": d["tmax"],
                     "rain": d["rain"], "tip": ws._tip(d) if i < 2 else ""})
    return {"province": province, "name": ws.PROVINCES[province][0], "now": {**cur, "icon": icon, "desc": desc},
            "days": days}


def rates():
    import market_info as mi
    r = mi.fetch_rates()
    if not r:
        raise RuntimeError("no rates")
    khr = r["rates"]["KHR"]
    return {"khr": khr, "date": r.get("date", ""),
            "others": [[code, label, r["rates"][code], khr / r["rates"][code]] for code, label in mi.SHOWN_CURRENCIES
                       if r["rates"].get(code)]}


def gold():
    import market_info as mi
    g = mi.fetch_gold()
    if not g:
        raise RuntimeError("no gold")
    u = mi.gold_units(g["oz"])
    r = mi.fetch_rates()
    return {"oz": g["oz"], "at": g["at"], "chi": u["chi"], "damlung": u["damlung"], "gram": u["gram"],
            "khr": (r or {}).get("rates", {}).get("KHR")}


def news(topic):
    import market_info as mi
    if topic not in mi.NEWS_TOPICS:
        topic = "top"
    return {"topic": topic, "items": (mi.fetch_news(topic) or [])[:12]}


def calendar_today():
    import calendar_brief as cb
    kc = _kc()
    d = _today()
    k = kc.gregorian_to_khmer(d)
    sila = cb.next_sila(d)
    return {"weekday": kc.WEEKDAYS[k.weekday], "solar": kc.format_solar(d), "lunar": kc.format_khmer(d, with_weekday=False),
            "animal": f"{kc.ANIMAL_EMOJI[k.animal]} ឆ្នាំ{kc.ANIMALS[k.animal]}", "holiday": cb.holiday_on(d),
            "sila": None if sila is None else {"days": (sila - d).days, "solar": kc.format_solar(sila)},
            "upcoming": [[day.strftime("%d/%m"), name, (day - d).days] for day, name in cb.upcoming(d, 45)[:4]],
            "quote": cb.QUOTES[d.toordinal() % len(cb.QUOTES)]}


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception as exc:
        logger.info("today: %s failed (%s)", getattr(fn, "__name__", "?"), type(exc).__name__)
        return None


# ---------------- sila / anniversaries / brief ----------------

def _bd_db(config):
    import buddhist_days
    buddhist_days.init_schema(config)
    return buddhist_days._db(config)


def sila_state(config, uid):
    kc = _kc()
    d = _today()
    conn = _bd_db(config)
    try:
        on = conn.execute("SELECT 1 FROM sila_subs WHERE chat_id=?", (uid,)).fetchone() is not None
    finally:
        conn.close()
    days = []
    for i in range(0, 60):
        day = d + timedelta(days=i)
        if kc.is_sila_day(day):
            days.append({"days": i, "solar": kc.format_solar(day), "lunar": kc.format_khmer(day, with_weekday=True).split(" ឆ្នាំ")[0]})
            if len(days) >= 4:
                break
    import buddhist_days
    return {"on": on, "upcoming": days, "precepts": buddhist_days.PRECEPTS, "eve": buddhist_days.EVE_HOUR}


def sila_set(config, uid, on):
    conn = _bd_db(config)
    try:
        with conn:
            if on:
                conn.execute("INSERT OR IGNORE INTO sila_subs(chat_id, created_at) VALUES (?, ?)", (uid, int(time.time())))
            else:
                conn.execute("DELETE FROM sila_subs WHERE chat_id=?", (uid,))
    finally:
        conn.close()


def anniv_list(config, uid):
    import buddhist_days as bd
    kc = _kc()
    d = _today()
    conn = _bd_db(config)
    try:
        rows = conn.execute("SELECT * FROM anniversaries WHERE chat_id=? ORDER BY id", (uid,)).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        nd = bd.next_date(r, d)
        out.append({"id": r["id"], "title": r["title"], "when": bd.describe(r), "lunar": bool(r["lunar"]),
                    "next": kc.format_solar(nd) if nd else None, "left": (nd - d).days if nd else None})
    return sorted(out, key=lambda x: (x["left"] is None, x["left"] or 0))


def anniv_add(config, uid, data):
    import buddhist_days as bd
    kc = _kc()
    title = " ".join(str(data.get("title") or "").split())[:80]
    if data.get("text"):
        parsed = bd.parse_anniv(str(data["text"])[:120])
    else:
        try:
            lunar, month, day = int(bool(data.get("lunar"))), int(data.get("month")), int(data.get("day"))
        except (TypeError, ValueError):
            parsed = None
        else:
            parsed = {"lunar": lunar, "month": month, "day": day, "waning": int(bool(data.get("waning"))), "title": title}
            if lunar and not (1 <= day <= 15 and 0 <= month < len(kc.LUNAR_MONTHS)):
                parsed = None
            if not lunar:
                try:
                    from datetime import date
                    date(2024, month, day)
                except ValueError:
                    parsed = None
    if not parsed or not parsed.get("title"):
        raise Refused("🤔 ជ្រើសថ្ងៃ ខែ ហើយសរសេរឈ្មោះខួប")
    if len(anniv_list(config, uid)) >= bd.MAX_ANNIV:
        raise Refused(f"⚠️ មានច្រើនពេកហើយ (អតិបរមា {bd.MAX_ANNIV}) — លុបខ្លះសិន")
    conn = _bd_db(config)
    try:
        with conn:
            conn.execute("INSERT INTO anniversaries(chat_id, lunar, month, day, waning, title, created_at) VALUES (?,?,?,?,?,?,?)",
                         (uid, parsed["lunar"], parsed["month"], parsed["day"], parsed["waning"], parsed["title"], int(time.time())))
    finally:
        conn.close()


def anniv_del(config, uid, rid):
    conn = _bd_db(config)
    try:
        with conn:
            conn.execute("DELETE FROM anniversaries WHERE id=? AND chat_id=?", (int(rid), uid))
    finally:
        conn.close()


def brief_state(config, uid):
    import calendar_brief as cb
    cb.init_schema(config)
    return cb.subscription(config, uid)


# ---------------- health ----------------

def _hm_db(config):
    import health_more
    health_more.init_schema(config)
    return health_more._connect(config)


def bp_list(config, uid):
    import health_more as hm
    with _hm_db(config) as conn:
        rows = conn.execute("SELECT systolic, diastolic, pulse, taken_at FROM health_bp_log WHERE user_id=? ORDER BY id DESC LIMIT 10",
                            (uid,)).fetchall()
    out = []
    for r in rows:
        emoji, label, _adv = hm.bp_level(r["systolic"], r["diastolic"])
        out.append({"s": r["systolic"], "d": r["diastolic"], "p": r["pulse"], "at": r["taken_at"], "emoji": emoji, "label": label})
    return out


def bp_add(config, uid, text):
    import health_more as hm
    from datetime import datetime
    parsed = hm.parse_bp(str(text or "")[:20])
    if not parsed:
        raise Refused("🩸 វាយដូចនេះ៖ 130/85 ឬ 130/85 72")
    s, d, p = parsed
    with _hm_db(config) as conn:
        conn.execute("INSERT INTO health_bp_log(user_id, systolic, diastolic, pulse, taken_at) VALUES (?,?,?,?,?)",
                     (uid, s, d, p, datetime.now(hm.TZ).strftime("%d/%m %H:%M")))
        conn.execute("DELETE FROM health_bp_log WHERE user_id=? AND id NOT IN (SELECT id FROM health_bp_log WHERE user_id=? "
                     "ORDER BY id DESC LIMIT ?)", (uid, uid, hm.MAX_BP_ROWS))
    emoji, label, advice = hm.bp_level(s, d)
    return {"emoji": emoji, "label": label, "advice": advice}


def exercise_done(config, uid):
    import health_features as hf
    import sqlite3
    from datetime import datetime
    today = datetime.now(hf.TZ).date().isoformat()
    with hf._connect(config) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS health_exercise_log (user_id INTEGER NOT NULL, local_date TEXT NOT NULL, "
                     "PRIMARY KEY (user_id, local_date))")
        try:
            conn.execute("INSERT INTO health_exercise_log(user_id, local_date) VALUES (?, ?)", (uid, today))
        except sqlite3.IntegrityError:
            return {"new": False}
        try:
            conn.execute("INSERT OR IGNORE INTO community_points(user_id) VALUES (?)", (uid,))
            conn.execute("UPDATE community_points SET points=points+? WHERE user_id=?", (hf.POINTS_EXERCISE, uid))
            row = conn.execute("SELECT points FROM community_points WHERE user_id=?", (uid,)).fetchone()
            return {"new": True, "points": row["points"] if row else None}
        except sqlite3.OperationalError:
            return {"new": True, "points": None}


def health_ask(config, uid, q, owner):
    import health_more as hm
    import studio_web
    q = " ".join(str(q or "").split())[:300]
    if not q:
        raise Refused("✍️ សរសេរសំណួរ")
    if hm.has_red_flag(q):
        return {"emergency": True, "text": hm.EMERGENCY_REPLY}
    studio_web.init_schema(config)
    if not owner:
        import tools_web
        if studio_web.text_used(config, uid, "tools") >= tools_web.ai_limit():
            raise Refused(f"⏳ ថ្ងៃនេះប្រើ AI អស់ {tools_web.ai_limit()} ដងហើយ — ស្អែកមកម្ដងទៀតណា 🙏")
        studio_web.text_add(config, uid, "tools", 1)
    try:
        ask = HOOKS.get("ask")
        if ask is None:
            import knowledge_base
            ask = lambda prompt: knowledge_base.gemini(getattr(config, "GEMINI_API_KEY", ""), [{"text": prompt}], max_tokens=1200)
        text = (ask(hm.HEALTH_PROMPT.format(q=q)) or "").strip()
        if not text:
            raise RuntimeError("empty")
    except Exception:
        if not owner:
            studio_web.text_add(config, uid, "tools", -1)
        raise
    return {"emergency": False, "text": html.escape(text[:3000]) + hm.DISCLAIMER}


# ---------------- HTTP ----------------

def _json(code, data):
    return code, json.dumps(data, ensure_ascii=False).encode(), "application/json", {}


def handle_post(path, config, body, bot=None, now=None):
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
    if path == "/today/me":
        return _json(200, {"member": member, "owner": owner, **static_lists()})
    if not member:
        return _json(403, {"error": "members_only", "msg": "🔒 សម្រាប់សមាជិក — វាយ /pay ក្នុង Bot"})
    try:
        if path == "/today/dash":
            province = str(data.get("province") or brief_state(config, uid) or "phnom_penh")
            n = _safe(news, "top")
            out = {"cal": calendar_today(), "weather": _safe(weather, province), "rates": _safe(rates), "gold": _safe(gold),
                   "news": (n or {}).get("items", [])[:5], "brief": brief_state(config, uid),
                   "sila": sila_state(config, uid)["on"]}
        elif path == "/today/weather":
            out = weather(str(data.get("province") or "phnom_penh"))
        elif path == "/today/rates":
            out = rates()
        elif path == "/today/gold":
            out = gold()
        elif path == "/today/news":
            out = news(str(data.get("topic") or "top"))
        elif path == "/today/sila":
            out = sila_state(config, uid)
        elif path == "/today/sila/set":
            sila_set(config, uid, bool(data.get("on")))
            out = sila_state(config, uid)
        elif path == "/today/anniv/list":
            out = {"items": anniv_list(config, uid)}
        elif path == "/today/anniv/add":
            anniv_add(config, uid, data)
            out = {"items": anniv_list(config, uid)}
        elif path == "/today/anniv/del":
            anniv_del(config, uid, data.get("id") or 0)
            out = {"items": anniv_list(config, uid)}
        elif path == "/today/brief/set":
            import calendar_brief as cb
            import weather_service as ws
            cb.init_schema(config)
            province = str(data.get("province") or "phnom_penh")
            if province not in ws.PROVINCES:
                province = "phnom_penh"
            if data.get("on"):
                cb.subscribe(config, uid, province)
            else:
                cb.unsubscribe(config, uid)
            out = {"brief": brief_state(config, uid)}
        elif path == "/today/health/done":
            out = exercise_done(config, uid)
        elif path == "/today/bp/list":
            out = {"items": bp_list(config, uid)}
        elif path == "/today/bp/add":
            out = {"result": bp_add(config, uid, data.get("text")), "items": bp_list(config, uid)}
        elif path == "/today/health/ask":
            out = health_ask(config, uid, data.get("q"), owner)
        else:
            return _json(404, {"error": "not_found"})
    except Refused as exc:
        return _json(400, {"error": "refused", "msg": str(exc)})
    except Exception as exc:
        logger.warning("today %s failed (%s: %s)", path, type(exc).__name__, str(exc)[:200])
        msg = "😔 ទាញទិន្នន័យមិនបានពេលនេះ — សាកម្ដងទៀតបន្តិចទៀត"
        if owner:
            msg += f" · {type(exc).__name__}: {str(exc)[:150]}"
        return _json(502, {"error": "server", "msg": msg})
    return _json(200, out)
