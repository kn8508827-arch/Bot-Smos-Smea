"""📅 Khmer calendar + ☀️ morning brief.

  /calendar (/today, /khmerdate) → today's solar + Khmer lunar date, ថ្ងៃសីល, next holidays
  📅 button → this month day by day with lunar dates, holidays and ថ្ងៃសីល
  /goodday <what> → for-fun "good day" ideas for the next 30 days (AI, with a "ask a monk/elder" note)
  /morning → subscribe this chat to a 7:00 brief: date, holiday, weather, dollar, gold, news, a word from Cooler
    (in groups only admins can switch it). /morning off to stop.

Public holidays follow Cambodia's 2026 sub-decree; the lunar ones (Visak Bochea, Royal Ploughing,
Pchum Ben, Water Festival) are computed from the Khmer calendar so later years work too.
"""
import html
import logging
import os
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta, timezone

import khmer_calendar as kc

logger = logging.getLogger(__name__)
TZ_KH = timezone(timedelta(hours=7))

FIXED_HOLIDAYS = {
    (1, 1): "ទិវាចូលឆ្នាំសកល",
    (1, 7): "ទិវាជ័យជម្នះលើរបបប្រល័យពូជសាសន៍",
    (3, 8): "ទិវានារីអន្តរជាតិ",
    (4, 14): "ពិធីបុណ្យចូលឆ្នាំថ្មីប្រពៃណីជាតិ",
    (4, 15): "ពិធីបុណ្យចូលឆ្នាំថ្មីប្រពៃណីជាតិ",
    (4, 16): "ពិធីបុណ្យចូលឆ្នាំថ្មីប្រពៃណីជាតិ",
    (5, 1): "ទិវាពលកម្មអន្តរជាតិ",
    (5, 14): "ព្រះរាជពិធីបុណ្យចម្រើនព្រះជន្ម ព្រះមហាក្សត្រ",
    (6, 18): "ព្រះរាជពិធីបុណ្យចម្រើនព្រះជន្ម សម្ដេចព្រះមហាក្សត្រីយានីម៉ូនិច",
    (9, 24): "ទិវាប្រកាសរដ្ឋធម្មនុញ្ញ",
    (10, 15): "ទិវាប្រារព្ធពិធីគោរពព្រះវិញ្ញាណក្ខន្ធ ព្រះបរមរតនកោដ្ឋ",
    (10, 29): "ព្រះរាជពិធីគ្រងព្រះបរមរាជសម្បត្តិ",
    (11, 9): "ពិធីបុណ្យឯករាជ្យជាតិ",
    (12, 29): "ទិវាសន្តិភាពនៅកម្ពុជា",
}

QUOTES = [
    "ថ្ងៃនេះជាឱកាសថ្មី — ញញឹម ហើយចាប់ផ្ដើម! 😎",
    "ជោគជ័យធំ ចាប់ផ្ដើមពីជំហានតូចៗរាល់ថ្ងៃ 💪",
    "កុំភ្លេចផឹកទឹក និងប្រាប់មនុស្សជាទីស្រលាញ់ថាស្រលាញ់គេ ❤️",
    "Keep cool, stay humble, work hard — Cooler style 🎤",
    "ឈឺក្បាលនឹងការងារ? ដកដង្ហើមវែងៗ ៣ ដង ហើយបន្តទៀត 🌿",
    "គ្រួសារ និងមិត្តល្អ ជាទ្រព្យសម្បត្តិធំបំផុត 🏡",
    "រៀនរឿងថ្មីមួយថ្ងៃនេះ ទោះតូចក៏ដោយ 📚",
    "ជួយគេមួយដងថ្ងៃនេះ — កម្មល្អនឹងត្រឡប់មកវិញ 🙏",
    "ថ្ងៃនេះត្រជាក់ចិត្ត ធ្វើការឲ្យរលូន ✨",
    "កុំប្រៀបធៀបខ្លួនឯងជាមួយគេ — ប្រៀបជាមួយខ្លួនឯងម្សិលមិញ 🚀",
]


def holidays(year):
    """{date: name} for a year: fixed dates + lunar festivals computed from the Khmer calendar."""
    out = {date(year, m, d): name for (m, d), name in FIXED_HOLIDAYS.items()}
    d = date(year, 1, 1)
    lunar_names = {}
    while d.year == year:
        k = kc.gregorian_to_khmer(d)
        if k.month == kc.PISAKH and not k.waning and k.day == 15:
            lunar_names[d] = "ពិធីបុណ្យវិសាខបូជា"
        elif k.month == kc.PISAKH and k.waning and k.day == 4:
            lunar_names[d] = "ព្រះរាជពិធីច្រត់ព្រះនង្គ័ល"
        elif k.month == 9 and k.waning and k.day >= 14:            # ភទ្របទ 14-15 រោច
            lunar_names[d] = "ពិធីបុណ្យភ្ជុំបិណ្ឌ"
        elif k.month == 10 and not k.waning and k.day == 1:        # ១កើត អស្សុជ
            lunar_names[d] = "ពិធីបុណ្យភ្ជុំបិណ្ឌ"
        elif k.month == kc.KADEUK and ((not k.waning and k.day >= 14) or (k.waning and k.day == 1)):
            lunar_names[d] = "ព្រះរាជពិធីបុណ្យអុំទូក បណ្ដែតប្រទីប និងសំពះព្រះខែ"
        d += timedelta(days=1)
    for day, name in lunar_names.items():
        out[day] = out[day] + " · " + name if day in out else name
    return out


def holiday_on(d):
    return holidays(d.year).get(d)


def upcoming(d, days=30):
    found = []
    end = d + timedelta(days=days)
    for year in sorted({d.year, end.year}):
        for day, name in holidays(year).items():
            if d <= day <= end:
                found.append((day, name))
    return sorted(found)


def next_sila(d):
    for i in range(0, 16):
        day = d + timedelta(days=i)
        if kc.is_sila_day(day):
            return day
    return None


def today_text(d):
    k = kc.gregorian_to_khmer(d)
    lines = [f"📅 <b>ថ្ងៃ{kc.WEEKDAYS[k.weekday]} {kc.format_solar(d)}</b>",
             f"🌙 {kc.format_khmer(d, with_weekday=False)}",
             f"{kc.ANIMAL_EMOJI[k.animal]} ឆ្នាំ{kc.ANIMALS[k.animal]}"]
    hol = holiday_on(d)
    if hol:
        lines.append(f"🎉 <b>ថ្ងៃឈប់សម្រាក៖ {html.escape(hol)}</b>")
    sila = next_sila(d)
    if sila == d:
        lines.append("🙏 <b>ថ្ងៃនេះជាថ្ងៃសីល</b>")
    elif sila:
        lines.append(f"🙏 ថ្ងៃសីលបន្ទាប់៖ {kc.format_solar(sila)} ({kc.format_khmer(sila, False).split(' ឆ្នាំ')[0]})")
    nxt = [(day, name) for day, name in upcoming(d + timedelta(days=1), 45)][:4]
    if nxt:
        lines.append("\n🗓️ <b>ថ្ងៃឈប់សម្រាកខាងមុខ</b>")
        for day, name in nxt:
            left = (day - d).days
            lines.append(f"• {kc.format_solar(day)} — {html.escape(name)} <i>(នៅ {kc.kh_num(left)} ថ្ងៃ)</i>")
    return "\n".join(lines)


def month_text(year, month):
    d = date(year, month, 1)
    hol = holidays(year)
    lines = [f"🗓️ <b>ខែ{kc.SOLAR_MONTHS[month - 1]} {kc.kh_num(year)}</b>  (🙏 ថ្ងៃសីល · 🎉 ឈប់សម្រាក)\n"]
    while d.month == month:
        k = kc.gregorian_to_khmer(d)
        mark = ("🎉" if d in hol else "") + ("🙏" if kc.is_sila_day(d) else "")
        lunar = f"{kc.kh_num(k.day)}{kc.PHASES[int(k.waning)]} {kc.LUNAR_MONTHS[k.month]}"
        lines.append(f"<code>{d.day:>2}</code> {kc.WEEKDAYS[k.weekday][:3]} · {lunar} {mark}")
        d += timedelta(days=1)
    return "\n".join(lines)


# ---------------- morning brief ----------------

def _db(config):
    conn = sqlite3.connect(getattr(config, "BOT_DB_PATH", "bot_database.db"), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(config):
    conn = _db(config)
    try:
        with conn:
            conn.execute("CREATE TABLE IF NOT EXISTS brief_subs (chat_id INTEGER PRIMARY KEY, province TEXT NOT NULL, "
                         "last_sent TEXT, created_at INTEGER)")
    finally:
        conn.close()


def subscribe(config, chat_id, province="phnom_penh"):
    conn = _db(config)
    try:
        with conn:
            conn.execute("INSERT INTO brief_subs(chat_id, province, last_sent, created_at) VALUES (?, ?, NULL, ?) "
                         "ON CONFLICT(chat_id) DO UPDATE SET province=excluded.province",
                         (chat_id, province, int(time.time())))
    finally:
        conn.close()


def unsubscribe(config, chat_id):
    conn = _db(config)
    try:
        with conn:
            return conn.execute("DELETE FROM brief_subs WHERE chat_id=?", (chat_id,)).rowcount
    finally:
        conn.close()


def subscription(config, chat_id):
    conn = _db(config)
    try:
        row = conn.execute("SELECT province FROM brief_subs WHERE chat_id=?", (chat_id,)).fetchone()
        return row["province"] if row else None
    finally:
        conn.close()


def brief_text(d, province="phnom_penh", weather=None, rates=None, gold=None, news=None):
    """Build the brief; the data arguments are fetched when None (injectable for tests)."""
    import market_info
    import weather_service as ws
    lines = [f"☀️ <b>អរុណសួស្ដី! — Cooler Bot</b>", today_text(d).split("\n\n🗓️")[0], ""]
    try:
        data = weather if weather is not None else ws.weather_service.fetch(province)
        day0 = data["days"][0]
        icon, desc = ws.describe_code(day0["code"])
        name = ws.PROVINCES.get(province, ("ភ្នំពេញ",))[0]
        rain = f" · ភ្លៀង {day0['rain']}%" if day0.get("rain") is not None else ""
        lines.append(f"{icon} <b>{html.escape(name)}</b>៖ {desc} {day0['tmin']}–{day0['tmax']}°C{rain}")
    except Exception as exc:
        logger.info("brief weather skipped (%s)", type(exc).__name__)
    try:
        r = rates if rates is not None else market_info.fetch_rates()
        if r:
            lines.append(f"💵 ១ ដុល្លារ ≈ <b>{r['rates']['KHR']:,.0f}</b> រៀល")
    except Exception:
        pass
    try:
        g = gold if gold is not None else market_info.fetch_gold()
        if g:
            chi = market_info.gold_units(g["oz"])["chi"]
            lines.append(f"🪙 មាសពិភពលោក ≈ <b>${chi:,.0f}</b> / ជី")
    except Exception:
        pass
    try:
        items = news if news is not None else market_info.fetch_news("top")
        if items:
            lines.append("\n📰 <b>ព័ត៌មាន</b>")
            for it in items[:3]:
                lines.append(f"• <a href=\"{html.escape(it['link'])}\">{html.escape(it['title'][:90])}</a>")
    except Exception:
        pass
    lines.append(f"\n💬 {QUOTES[d.toordinal() % len(QUOTES)]}")
    lines.append("<i>/morning off — ឈប់ទទួល</i>")
    return "\n".join(lines)


def register(bot, config, ask=None):
    """ask(prompt) -> text (for /goodday)."""
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    init_schema(config)

    def now_kh():
        return datetime.now(TZ_KH)

    def privileged(uid):
        return uid == getattr(config, "OWNER_ID", 0) or uid in getattr(config, "ADMIN_IDS", [])

    def is_group(chat):
        return getattr(chat, "type", None) in ("group", "supergroup")

    def can_manage(chat, uid):
        if privileged(uid) or not is_group(chat):
            return True
        try:
            return bot.get_chat_member(chat.id, uid).status in ("creator", "administrator")
        except Exception:
            return False

    def cal_markup(d):
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("🗓️ ខែនេះ", callback_data=f"cal:m:{d.year}:{d.month}"),
              InlineKeyboardButton("☀️ ព័ត៌មានព្រឹក", callback_data="cal:brief"))
        return m

    @bot.message_handler(commands=["calendar", "today", "khmerdate", "lunar"])
    def calendar_cmd(message):
        d = now_kh().date()
        bot.reply_to(message, today_text(d), reply_markup=cal_markup(d), parse_mode="HTML")

    @bot.message_handler(commands=["goodday", "thngaiyl"])
    def goodday_cmd(message):
        parts = (message.text or "").split(maxsplit=1)
        what = parts[1].strip()[:80] if len(parts) > 1 else ""
        if not what:
            bot.reply_to(message, "✨ សរសេរអ្វីដែលចង់ធ្វើ ឧ. <code>/goodday រៀបការ</code>, <code>/goodday បើកហាង</code>, "
                                  "<code>/goodday ឡើងផ្ទះថ្មី</code>", parse_mode="HTML")
            return
        if ask is None:
            bot.reply_to(message, "AI មិនទាន់បើក")
            return
        d = now_kh().date()
        days = "\n".join(f"{(d + timedelta(days=i)).isoformat()}: {kc.format_khmer(d + timedelta(days=i))}"
                         + (" (holiday)" if holiday_on(d + timedelta(days=i)) else "")
                         for i in range(1, 31))
        prompt = (f"You are a friendly Khmer culture assistant. Using traditional Khmer beliefs about auspicious days "
                  f"(weekday + lunar day), suggest 3 good days in the next 30 days for: {what}. Days:\n{days}\n"
                  "Answer in Khmer, short, list each date with its Khmer lunar date and one-line reason. End by "
                  "reminding gently that this is for fun and important dates should be confirmed with a monk (លោកសង្ឃ) "
                  "or elder (អាចារ្យ).")
        try:
            bot.send_chat_action(message.chat.id, "typing")
            text = ask(prompt)
        except Exception:
            text = ""
        bot.reply_to(message, ("✨ <b>ថ្ងៃល្អសម្រាប់ " + html.escape(what) + "</b>\n\n" + html.escape(text[:3000]))
                     if text else "😔 AI រវល់ — សាកម្ដងទៀត", parse_mode="HTML")

    def brief_markup(chat_id):
        import weather_service as ws
        current = subscription(config, chat_id)
        m = InlineKeyboardMarkup()
        picks = ["phnom_penh", "siem_reap", "battambang", "kampot", "sihanoukville", "kampong_cham"]
        btns = [InlineKeyboardButton(("✅ " if s == current else "") + ws.PROVINCES[s][0], callback_data=f"cal:sub:{s}")
                for s in picks if s in ws.PROVINCES]
        for i in range(0, len(btns), 3):
            m.row(*btns[i:i + 3])
        if current:
            m.row(InlineKeyboardButton("🔕 ឈប់ទទួល", callback_data="cal:off"),
                  InlineKeyboardButton("👀 មើលឥឡូវ", callback_data="cal:preview"))
        return m

    def brief_menu_text(chat_id):
        current = subscription(config, chat_id)
        hour = int(os.getenv("BRIEF_HOUR", "7") or 7)
        state = "✅ កំពុងទទួល" if current else "⛔ មិនទាន់ទទួល"
        return (f"☀️ <b>ព័ត៌មានពេលព្រឹក ម៉ោង {kc.kh_num(hour)}:០០</b> — {state}\n\n"
                "📅 ថ្ងៃខ្មែរ ថ្ងៃសីល ថ្ងៃឈប់សម្រាក · 🌤️ អាកាសធាតុ · 💵 ដុល្លារ · 🪙 មាស · 📰 ព័ត៌មាន · 💬 ពាក្យលើកទឹកចិត្ត\n\n"
                "👇 ជ្រើសខេត្ត ដើម្បីចាប់ផ្ដើមទទួល")

    @bot.message_handler(commands=["morning", "brief"])
    def morning_cmd(message):
        arg = ((message.text or "").split() + [""])[1].lower()
        chat = message.chat
        if arg in ("off", "stop", "ឈប់"):
            if not can_manage(chat, message.from_user.id):
                bot.reply_to(message, "⛔ សម្រាប់ Admin Group ប៉ុណ្ណោះ")
                return
            unsubscribe(config, chat.id)
            bot.reply_to(message, "🔕 ឈប់ផ្ញើព័ត៌មានពេលព្រឹកហើយ")
            return
        bot.reply_to(message, brief_menu_text(chat.id), reply_markup=brief_markup(chat.id), parse_mode="HTML")

    @bot.callback_query_handler(func=lambda c: (c.data or "").startswith("cal:"))
    def cal_cb(call):
        parts = (call.data or "").split(":")
        chat = call.message.chat
        uid = call.from_user.id
        if parts[1] == "m" and len(parts) == 4:
            bot.answer_callback_query(call.id)
            bot.send_message(chat.id, month_text(int(parts[2]), int(parts[3])), parse_mode="HTML")
            return
        if parts[1] == "today":
            bot.answer_callback_query(call.id)
            d = now_kh().date()
            bot.send_message(chat.id, today_text(d), reply_markup=cal_markup(d), parse_mode="HTML")
            return
        if parts[1] == "brief":
            bot.answer_callback_query(call.id)
            bot.send_message(chat.id, brief_menu_text(chat.id), reply_markup=brief_markup(chat.id), parse_mode="HTML")
            return
        if parts[1] in ("sub", "off") and not can_manage(chat, uid):
            bot.answer_callback_query(call.id, "⛔ សម្រាប់ Admin Group ប៉ុណ្ណោះ", show_alert=True)
            return
        if parts[1] == "sub" and len(parts) == 3:
            subscribe(config, chat.id, parts[2])
            bot.answer_callback_query(call.id, "✅ នឹងផ្ញើរៀងរាល់ព្រឹក")
        elif parts[1] == "off":
            unsubscribe(config, chat.id)
            bot.answer_callback_query(call.id, "🔕 ឈប់ហើយ")
        elif parts[1] == "preview":
            bot.answer_callback_query(call.id)
            bot.send_message(chat.id, brief_text(now_kh().date(), subscription(config, chat.id) or "phnom_penh"),
                             parse_mode="HTML", disable_web_page_preview=True)
            return
        else:
            bot.answer_callback_query(call.id)
            return
        try:
            bot.edit_message_text(brief_menu_text(chat.id), chat.id, call.message.message_id,
                                  reply_markup=brief_markup(chat.id), parse_mode="HTML")
        except Exception:
            pass

    def tick(now=None):
        now = now or now_kh()
        hour = int(os.getenv("BRIEF_HOUR", "7") or 7)
        if now.hour < hour:
            return 0
        today = now.date().isoformat()
        conn = _db(config)
        try:
            rows = conn.execute("SELECT chat_id, province FROM brief_subs WHERE last_sent IS NULL OR last_sent != ?",
                                (today,)).fetchall()
        finally:
            conn.close()
        cache, sent = {}, 0
        for r in rows:
            conn = _db(config)
            try:
                with conn:
                    claimed = conn.execute("UPDATE brief_subs SET last_sent=? WHERE chat_id=? AND "
                                           "(last_sent IS NULL OR last_sent != ?)", (today, r["chat_id"], today)).rowcount
            finally:
                conn.close()
            if not claimed:
                continue
            if r["province"] not in cache:
                cache[r["province"]] = brief_text(now.date(), r["province"])
            try:
                bot.send_message(r["chat_id"], cache[r["province"]], parse_mode="HTML", disable_web_page_preview=True)
                sent += 1
            except Exception as exc:
                text = str(exc).lower()
                if "blocked" in text or "kicked" in text or "not found" in text or "deactivated" in text:
                    unsubscribe(config, r["chat_id"])
            time.sleep(0.05)
        return sent

    def start_loop(interval=60):
        def run():
            time.sleep(30)
            while True:
                try:
                    tick()
                except Exception as exc:
                    logger.warning("brief tick failed (%s)", type(exc).__name__)
                time.sleep(interval)
        threading.Thread(target=run, name="morning-brief", daemon=True).start()

    class Api:
        pass
    api = Api()
    api.tick = tick
    api.start_loop = start_loop
    return api
