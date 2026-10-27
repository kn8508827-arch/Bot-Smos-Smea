"""🕘 Staff attendance: check in / check out with GPS location + a face photo.

Staff (private chat with the bot):
  /work — menu · 🟢 ចូលធ្វើការ / 🔴 ចេញពីការងារ → 1) send current location (must be within
  ATT_RADIUS metres of the workplace) → 2) take a selfie → saved, owner notified at once.
  /joinstaff — ask the owner to be added as staff (owner taps ✅ / ❌).
  📲 Scan the workplace QR (/workscan) → the bot picks 🟢 in or 🔴 out by itself → location → selfie.
  In a group, /work shows buttons that open the private chat straight at check-in / check-out.

Owner:
  /setwork — send the workplace location once (centre of the allowed circle)
  /workqr  — a printable QR poster for the door: staff scan it with the phone camera to check in/out
  /staff   — today's board: who came, late, left, still missing; remove staff
Work hours: ATT_START (08:00) – ATT_END (18:00), ATT_GRACE_MIN (5) minutes before "late".
Location and photo are recorded only at check-in / check-out, never in between.
"""
import copy
import html
import logging
import math
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)
TZ_KH = timezone(timedelta(hours=7))
STEP_SECONDS = 300           # time allowed between the location and the selfie
FREE_COMMANDS = {"work", "workin", "workout", "workscan", "workqr", "joinstaff", "staff", "setwork"}


def settings():
    e = os.getenv

    def hm(v, d):
        try:
            h, m = (v or d).split(":")
            return int(h), int(m)
        except ValueError:
            h, m = d.split(":")
            return int(h), int(m)
    return {"start": hm(e("ATT_START"), "08:00"), "end": hm(e("ATT_END"), "18:00"),
            "grace": int(e("ATT_GRACE_MIN", "5") or 5), "radius": int(e("ATT_RADIUS", "150") or 150)}


@contextmanager
def _db(config):
    conn = sqlite3.connect(getattr(config, "BOT_DB_PATH", "bot_database.db"), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_schema(config):
    with _db(config) as c:
        c.execute("CREATE TABLE IF NOT EXISTS att_place (id INTEGER PRIMARY KEY CHECK (id=1), lat REAL, lon REAL)")
        c.execute("""CREATE TABLE IF NOT EXISTS att_staff (user_id INTEGER PRIMARY KEY, name TEXT,
            status TEXT NOT NULL DEFAULT 'pending', added_at INTEGER)""")
        c.execute("""CREATE TABLE IF NOT EXISTS att_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            kind TEXT NOT NULL, ts INTEGER NOT NULL, day TEXT NOT NULL, lat REAL, lon REAL, dist INTEGER,
            photo TEXT, late_min INTEGER DEFAULT 0)""")
        c.execute("CREATE INDEX IF NOT EXISTS att_log_day ON att_log(day, user_id)")


def distance_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return int(2 * r * math.asin(math.sqrt(a)))


def now_kh():
    return datetime.now(TZ_KH)


def minutes_late(ts_dt, start, grace):
    begin = ts_dt.replace(hour=start[0], minute=start[1], second=0, microsecond=0)
    late = int((ts_dt - begin).total_seconds() // 60)
    return late if late > grace else 0


def minutes_early(ts_dt, end):
    stop = ts_dt.replace(hour=end[0], minute=end[1], second=0, microsecond=0)
    early = int((stop - ts_dt).total_seconds() // 60)
    return early if early > 0 else 0


def place(config):
    with _db(config) as c:
        r = c.execute("SELECT lat, lon FROM att_place WHERE id=1").fetchone()
    return (r["lat"], r["lon"]) if r and r["lat"] is not None else None


def set_place(config, lat, lon):
    with _db(config) as c:
        c.execute("INSERT OR REPLACE INTO att_place(id, lat, lon) VALUES (1, ?, ?)", (lat, lon))


def staff_status(config, uid):
    with _db(config) as c:
        r = c.execute("SELECT status FROM att_staff WHERE user_id=?", (uid,)).fetchone()
    return r["status"] if r else None


def today_logs(config, day):
    with _db(config) as c:
        return [dict(r) for r in c.execute("SELECT * FROM att_log WHERE day=? ORDER BY ts", (day,)).fetchall()]


def fmt_hm(dt):
    return dt.strftime("%H:%M")


def fmt_dur(minutes):
    h, m = divmod(max(0, int(minutes)), 60)
    return f"{h}ម៉ោង {m:02d}នាទី" if h else f"{m}នាទី"


WORK_WORDS = ("ធ្វើការ", "ម៉ោងចូល", "ម៉ោងចេញ", "វត្តមាន", "check in", "check-in", "checkin", "check out", "checkout",
              "attendance", "បុគ្គលិក", "ការងារ", "ស្កេនចូល", "scan ចូល")
_QR = ("qr", "ឃ្យូអា", "គ្យូអា", "ស្កេន", "scan")
_HOW = ("ម៉េច", "របៀប", "យ៉ាងម៉េច", "how", "where", "នៅណា", "មានទេ", "មាន", "ចុច", "ណា", "?", "？", "ឯណា", "អត់")


def work_intent(text):
    """True for questions like «QR ចូលធ្វើការនៅណា?» / «ចុចម៉ោងចូលធ្វើការម៉េច» (not for /commands)."""
    t = (text or "").casefold()
    if not t or t.startswith("/") or len(t) > 300:
        return False
    if not any(w in t for w in WORK_WORDS):
        return False
    return any(w in t for w in _QR) or (any(w in t for w in ("ម៉ោងចូល", "ម៉ោងចេញ", "វត្តមាន", "check", "attendance",
                                                              "ចូលធ្វើការ", "ចេញពីការងារ", "ចេញការងារ"))
                                         and any(w in t for w in _HOW))


def next_kind(config, uid, day=None):
    """What a QR scan means right now: 'in' if not checked in today (or already checked out), else 'out'."""
    day = day or now_kh().strftime("%Y-%m-%d")
    mine = [r for r in today_logs(config, day) if r["user_id"] == uid]
    return "out" if mine and mine[-1]["kind"] == "in" else "in"


def qr_poster(link, start, end):
    """PNG poster to print and stick at the workplace door (QR → t.me/<bot>?start=go_workscan)."""
    import io
    import qrcode
    from PIL import Image, ImageDraw
    import card_render as cr
    base = cr.aurora("ocean", seed=7)
    cr.glass(base, (90, 120, cr.W - 90, cr.H - 130), radius=56, alpha=70)
    d = ImageDraw.Draw(base)
    d.text((cr.W // 2, 220), cr.L("ស្កេនចូល / ចេញការងារ", "Scan to check in / out"), font=cr.font(66, 800),
           fill=(255, 255, 255), anchor="mm")
    d.text((cr.W // 2, 300), cr.L(f"ម៉ោងធ្វើការ {start} – {end}", f"Work hours {start} – {end}"), font=cr.font(40, 500),
           fill=(200, 245, 255), anchor="mm")
    q = qrcode.QRCode(box_size=14, border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(link)
    q.make(fit=True)
    qr = q.make_image(fill_color="black", back_color="white").convert("RGB").resize((560, 560), Image.NEAREST)
    card = Image.new("RGB", (620, 620), "white")
    card.paste(qr, (30, 30))
    mask = Image.new("L", card.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 619, 619), radius=40, fill=255)
    base.paste(card, ((cr.W - 620) // 2, 370), mask)
    steps = [cr.L("1. បើក Camera ស្កេន QR", "1. Scan with the camera"),
             cr.L("2. ចុច «ផ្ញើទីតាំង»", "2. Send your location"),
             cr.L("3. ថត Selfie — រួចរាល់!", "3. Take a selfie — done!")]
    for i, line in enumerate(steps):
        d.text((cr.W // 2, 1060 + i * 62), cr.clean(line), font=cr.font(40, 600), fill=(255, 255, 255), anchor="mm")
    cr.footer(base)
    buf = io.BytesIO()
    base.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def register(bot, config):
    """Register right after the paywall: staff location/photo steps must win over other handlers."""
    from telebot.types import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup,
                               ReplyKeyboardRemove)

    init_schema(config)
    steps = {}     # uid -> {"kind": "in"/"out"/"place", "stage": "loc"/"photo", "exp", "lat", "lon", "dist"}

    def owner_id():
        return getattr(config, "OWNER_ID", 0)

    def privileged(uid):
        return uid is not None and (uid == owner_id() or uid in getattr(config, "ADMIN_IDS", []))

    def busy(uid):
        st = steps.get(uid)
        if st and st["exp"] < time.monotonic():
            steps.pop(uid, None)
            return False
        return bool(st)
    bot._cooler_att_busy = busy          # the paywall lets these steps through for staff who are not members

    def name_of(user):
        n = " ".join(x for x in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if x)
        return n or (("@" + user.username) if getattr(user, "username", None) else str(user.id))

    def private(message):
        return getattr(message.chat, "type", None) == "private"

    def notify_owner(text, photo=None):
        if not owner_id():
            return
        try:
            if photo:
                bot.send_photo(owner_id(), photo, caption=text, parse_mode="HTML")
            else:
                bot.send_message(owner_id(), text, parse_mode="HTML")
        except Exception as exc:
            logger.warning("attendance owner notice failed (%s)", type(exc).__name__)

    me = {}

    def username():
        if "u" not in me:
            try:
                me["u"] = bot.get_me().username
            except Exception:
                return "TeamSmosSneabot"
        return me["u"]

    def scan_link():
        return f"https://t.me/{username()}?start=go_workscan"

    def menu_markup(uid=None):
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("🟢 ចូលធ្វើការ", callback_data="att:in"),
              InlineKeyboardButton("🔴 ចេញពីការងារ", callback_data="att:out"))
        if uid is not None and privileged(uid):
            m.row(InlineKeyboardButton("📲 QR បិទនៅកន្លែងធ្វើការ", callback_data="att:qr"),
                  InlineKeyboardButton("👥 វត្តមានថ្ងៃនេះ", callback_data="att:board"))
        return m

    def group_markup():
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("🟢 ចូលធ្វើការ", url=f"https://t.me/{username()}?start=go_workin"),
              InlineKeyboardButton("🔴 ចេញពីការងារ", url=f"https://t.me/{username()}?start=go_workout"))
        return m

    def send_qr(chat_id):
        s = settings()
        hm = lambda t: f"{t[0]:02d}:{t[1]:02d}"
        caption = ("📲 <b>QR ចូល/ចេញការងារ</b>\n\n"
                   "🖨️ Print ហើយបិទនៅទ្វារ ឬតុទទួលភ្ញៀវ\n"
                   "1️⃣ បុគ្គលិកបើក Camera ស្កេន QR → បើក Bot ភ្លាម\n"
                   "2️⃣ Bot ដឹងខ្លួនថា ចូល 🟢 ឬ ចេញ 🔴\n"
                   "3️⃣ ផ្ញើទីតាំង 📍 → ថត Selfie 🤳 → រួចរាល់ · Owner ទទួលដំណឹងភ្លាម\n\n"
                   f"🔗 Link ដដែល៖ {scan_link()}\n"
                   "🔒 សុវត្ថិភាព៖ ស្កេនពីផ្ទះមិនបានទេ — ត្រូវនៅក្នុងរង្វង់ទីតាំង + ថតមុខ")
        if not place(config):
            caption += "\n\n⚙️ កុំភ្លេច /setwork ដើម្បីកំណត់ទីតាំងកន្លែងធ្វើការ"
        try:
            bot.send_photo(chat_id, qr_poster(scan_link(), hm(s["start"]), hm(s["end"])), caption=caption,
                           parse_mode="HTML")
        except Exception as exc:
            logger.warning("work QR poster failed (%s)", type(exc).__name__)
            bot.send_message(chat_id, caption, parse_mode="HTML", disable_web_page_preview=True)

    def loc_keyboard():
        k = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        k.add(KeyboardButton("📍 ផ្ញើទីតាំងឥឡូវ", request_location=True))
        k.add(KeyboardButton("❌ បោះបង់"))
        return k

    def menu_text(uid):
        s = settings()
        day = now_kh().strftime("%Y-%m-%d")
        mine = [r for r in today_logs(config, day) if r["user_id"] == uid]
        state = "⚪ ថ្ងៃនេះមិនទាន់ចូលធ្វើការ"
        if mine:
            last = mine[-1]
            t = fmt_hm(datetime.fromtimestamp(last["ts"], TZ_KH))
            state = f"🟢 បានចូល {t}" if last["kind"] == "in" else f"🔴 បានចេញ {t}"
        return (f"🕘 <b>វត្តមានការងារ</b>\nម៉ោងធ្វើការ {s['start'][0]:02d}:{s['start'][1]:02d} – "
                f"{s['end'][0]:02d}:{s['end'][1]:02d}\n{state}\n\n"
                "ចុចប៊ូតុង → ផ្ញើទីតាំង 📍 → ថតរូបមុខ 🤳")

    def start(chat_id, uid, kind):
        if not place(config):
            bot.send_message(chat_id, "⚙️ Owner មិនទាន់កំណត់ទីតាំងកន្លែងធ្វើការទេ (/setwork)")
            return
        if staff_status(config, uid) != "active" and not privileged(uid):
            bot.send_message(chat_id, "🔒 អ្នកមិនទាន់ជាបុគ្គលិកក្នុងបញ្ជីទេ — វាយ /joinstaff ដើម្បីស្នើ Owner")
            return
        steps[uid] = {"kind": kind, "stage": "loc", "exp": time.monotonic() + STEP_SECONDS}
        bot.send_message(chat_id, ("🟢 <b>ចូលធ្វើការ</b>" if kind == "in" else "🔴 <b>ចេញពីការងារ</b>") +
                         "\n១️⃣ ចុច «📍 ផ្ញើទីតាំងឥឡូវ» ខាងក្រោម (ត្រូវនៅកន្លែងធ្វើការ)",
                         reply_markup=loc_keyboard(), parse_mode="HTML")

    # ---------- staff ----------

    @bot.message_handler(commands=["work", "workin", "workout", "workscan"])
    def work_cmd(message):
        if not private(message):   # location + selfie are private: buttons open the bot's chat right at the step
            bot.reply_to(message, "🕘 <b>ចូល/ចេញការងារ</b>\nចុចប៊ូតុងខាងក្រោម → Bot បើក Chat ផ្ទាល់ → "
                                  "ផ្ញើទីតាំង 📍 → ថត Selfie 🤳\n📲 ឬស្កេន QR នៅកន្លែងធ្វើការ",
                         reply_markup=group_markup(), parse_mode="HTML")
            return
        cmd = (message.text or "").split()[0][1:].split("@")[0].lower()
        uid = message.from_user.id
        if cmd == "workscan":
            kind = next_kind(config, uid)
            bot.send_message(message.chat.id, "📲 ស្កេន QR រួច — " + ("🟢 <b>ចូលធ្វើការ</b>" if kind == "in"
                                                                    else "🔴 <b>ចេញពីការងារ</b>"), parse_mode="HTML")
            start(message.chat.id, uid, kind)
        elif cmd == "workin":
            start(message.chat.id, uid, "in")
        elif cmd == "workout":
            start(message.chat.id, uid, "out")
        else:
            bot.reply_to(message, menu_text(uid), reply_markup=menu_markup(uid), parse_mode="HTML")

    @bot.message_handler(commands=["workqr"])
    def workqr_cmd(message):
        if not privileged(message.from_user.id):
            bot.reply_to(message, "📲 QR ចូលធ្វើការ មាននៅកន្លែងធ្វើការ — ស្កេនវាដោយ Camera ឬវាយ /work")
            return
        send_qr(message.chat.id)

    @bot.message_handler(commands=["joinstaff"])
    def join_cmd(message):
        if not private(message):
            return
        uid, user = message.from_user.id, message.from_user
        st = staff_status(config, uid)
        if st == "active":
            bot.reply_to(message, "✅ អ្នកជាបុគ្គលិករួចហើយ — /work")
            return
        with _db(config) as c:
            c.execute("INSERT OR REPLACE INTO att_staff(user_id, name, status, added_at) VALUES (?, ?, 'pending', ?)",
                      (uid, name_of(user), int(time.time())))
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("✅ ទទួល", callback_data=f"att:ok:{uid}"),
              InlineKeyboardButton("❌ បដិសេធ", callback_data=f"att:no:{uid}"))
        if owner_id():
            try:
                bot.send_message(owner_id(), f"👤 <b>{html.escape(name_of(user))}</b> (<code>{uid}</code>) "
                                 "ស្នើចូលជាបុគ្គលិក (វត្តមានការងារ)", reply_markup=m, parse_mode="HTML")
            except Exception:
                pass
        bot.reply_to(message, "📨 បានផ្ញើសំណើទៅ Owner ហើយ — រង់ចាំការអនុម័ត 🙏\n"
                              "ℹ️ ពេលចូល/ចេញការងារ Bot នឹងកត់ទីតាំង និងរូបថតមុខរបស់អ្នក ប៉ុណ្ណោះ")

    @bot.callback_query_handler(func=lambda c: (c.data or "").startswith("att:"))
    def att_cb(call):
        parts = (call.data or "").split(":")
        uid = call.from_user.id
        if parts[1] in ("in", "out"):
            bot.answer_callback_query(call.id)
            start(call.message.chat.id, uid, parts[1])
            return
        if not privileged(uid):
            bot.answer_callback_query(call.id, "⛔ សម្រាប់ Owner ប៉ុណ្ណោះ", show_alert=True)
            return
        if parts[1] == "qr":
            bot.answer_callback_query(call.id, "📲 ...")
            send_qr(call.message.chat.id)
            return
        if parts[1] == "board":
            bot.answer_callback_query(call.id)
            fake = copy.copy(call.message)
            fake.from_user = call.from_user
            fake.text = "/staff"
            staff_cmd(fake)
            return
        if not privileged(uid):
            bot.answer_callback_query(call.id, "⛔ សម្រាប់ Owner ប៉ុណ្ណោះ", show_alert=True)
            return
        target = int(parts[2]) if len(parts) > 2 and parts[2].lstrip("-").isdigit() else None
        if target is None:
            bot.answer_callback_query(call.id)
            return
        with _db(config) as c:
            if parts[1] == "ok":
                c.execute("UPDATE att_staff SET status='active' WHERE user_id=?", (target,))
            else:
                c.execute("DELETE FROM att_staff WHERE user_id=?", (target,))
        bot.answer_callback_query(call.id, "✅ រួចរាល់")
        try:
            bot.send_message(target, "✅ Owner បានទទួលអ្នកជាបុគ្គលិកហើយ — វាយ /work ដើម្បីចូល/ចេញការងារ"
                             if parts[1] == "ok" else "❌ Owner មិនបានទទួលសំណើរបស់អ្នកទេ" if parts[1] == "no"
                             else "ℹ️ អ្នកត្រូវបានដកចេញពីបញ្ជីបុគ្គលិក")
        except Exception:
            pass
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass

    def at_stage(stage):
        return lambda m: private(m) and busy(m.from_user.id) and steps[m.from_user.id]["stage"] == stage

    @bot.message_handler(func=lambda m: private(m) and busy(m.from_user.id) and (m.text or "").strip() == "❌ បោះបង់")
    def cancel(message):
        steps.pop(message.from_user.id, None)
        bot.reply_to(message, "បានបោះបង់", reply_markup=ReplyKeyboardRemove())

    @bot.message_handler(func=at_stage("loc"), content_types=["location"])
    def got_location(message):
        uid = message.from_user.id
        st = steps[uid]
        loc = message.location
        if getattr(message, "forward_origin", None) or getattr(message, "forward_date", None):
            bot.reply_to(message, "⚠️ សូមចុចប៊ូតុង «📍 ផ្ញើទីតាំងឥឡូវ» ដោយផ្ទាល់ (កុំ Forward)")
            return
        if st["kind"] == "place":
            set_place(config, loc.latitude, loc.longitude)
            steps.pop(uid, None)
            bot.reply_to(message, f"✅ បានកំណត់ទីតាំងកន្លែងធ្វើការ (រង្វង់ {settings()['radius']} ម៉ែត្រ)",
                         reply_markup=ReplyKeyboardRemove())
            return
        p = place(config)
        dist = distance_m(loc.latitude, loc.longitude, p[0], p[1])
        if dist > settings()["radius"]:
            bot.reply_to(message, f"📍 អ្នកនៅឆ្ងាយពីកន្លែងធ្វើការ <b>{dist} ម៉ែត្រ</b> — ត្រូវនៅក្នុង "
                         f"{settings()['radius']} ម៉ែត្រ។ មកដល់ហើយ ចុចផ្ញើទីតាំងម្ដងទៀត", parse_mode="HTML")
            notify_owner(f"⚠️ <b>{html.escape(name_of(message.from_user))}</b> សាក"
                         f"{'ចូល' if st['kind'] == 'in' else 'ចេញ'}ការងារពីចម្ងាយ {dist} ម៉ែត្រ")
            return
        st.update(stage="photo", lat=loc.latitude, lon=loc.longitude, dist=dist, exp=time.monotonic() + STEP_SECONDS)
        bot.reply_to(message, f"✅ ទីតាំងត្រឹមត្រូវ ({dist} ម៉ែត្រ)\n២️⃣ ឥឡូវ <b>ថតរូបមុខ (Selfie)</b> ផ្ញើមក 🤳 "
                              "(ចុច 📎 → Camera)", reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")

    @bot.message_handler(func=at_stage("photo"), content_types=["photo"])
    def got_photo(message):
        uid = message.from_user.id
        st = steps.pop(uid)
        if getattr(message, "forward_origin", None) or getattr(message, "forward_date", None):
            steps[uid] = st
            bot.reply_to(message, "⚠️ សូមថតរូបថ្មីឥឡូវ (កុំ Forward រូបចាស់)")
            return
        s = settings()
        now = now_kh()
        day = now.strftime("%Y-%m-%d")
        file_id = message.photo[-1].file_id
        late = minutes_late(now, s["start"], s["grace"]) if st["kind"] == "in" else 0
        with _db(config) as c:
            c.execute("INSERT INTO att_log(user_id, kind, ts, day, lat, lon, dist, photo, late_min) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (uid, st["kind"], int(now.timestamp()), day, st["lat"], st["lon"], st["dist"], file_id, late))
        who = html.escape(name_of(message.from_user))
        if st["kind"] == "in":
            mark = f"⏰ យឺត <b>{fmt_dur(late)}</b>" if late else "✅ ទាន់ម៉ោង"
            bot.reply_to(message, f"🟢 ចូលធ្វើការម៉ោង <b>{fmt_hm(now)}</b> · {mark}\nសូមឲ្យថ្ងៃនេះការងាររលូន 💪",
                         parse_mode="HTML")
            notify_owner(f"🟢 <b>{who}</b> ចូលធ្វើការ <b>{fmt_hm(now)}</b> · {mark}\n📍 {st['dist']} ម៉ែត្រពីកន្លែងធ្វើការ",
                         photo=file_id)
        else:
            ins = [r for r in today_logs(config, day) if r["user_id"] == uid and r["kind"] == "in"]
            worked = int((now.timestamp() - ins[0]["ts"]) // 60) if ins else None
            early = minutes_early(now, s["end"])
            mark = f"⚠️ ចេញមុនម៉ោង <b>{fmt_dur(early)}</b>" if early else "✅ គ្រប់ម៉ោង"
            total = f"\n⏱️ ធ្វើការសរុប {fmt_dur(worked)}" if worked is not None else ""
            bot.reply_to(message, f"🔴 ចេញពីការងារម៉ោង <b>{fmt_hm(now)}</b> · {mark}{total}\nអរគុណសម្រាប់ថ្ងៃនេះ 🙏",
                         parse_mode="HTML")
            notify_owner(f"🔴 <b>{who}</b> ចេញពីការងារ <b>{fmt_hm(now)}</b> · {mark}{total}", photo=file_id)

    @bot.message_handler(func=at_stage("photo"), content_types=["text", "document", "video", "sticker"])
    def need_photo(message):
        bot.reply_to(message, "🤳 សូមថតរូបមុខ ហើយផ្ញើជារូបភាព (Photo) — ឬវាយ «❌ បោះបង់»")

    # ---------- owner ----------

    @bot.message_handler(commands=["setwork"])
    def setwork_cmd(message):
        if not (private(message) and privileged(message.from_user.id)):
            return
        steps[message.from_user.id] = {"kind": "place", "stage": "loc", "exp": time.monotonic() + STEP_SECONDS}
        bot.reply_to(message, "📍 សូមទៅនៅកន្លែងធ្វើការ ហើយចុច «📍 ផ្ញើទីតាំងឥឡូវ»\n"
                              f"បុគ្គលិកត្រូវនៅក្នុងរង្វង់ {settings()['radius']} ម៉ែត្រ (ប្ដូរបានដោយ ATT_RADIUS)",
                     reply_markup=loc_keyboard())

    @bot.message_handler(commands=["staff"])
    def staff_cmd(message):
        if not privileged(message.from_user.id):
            if private(message):
                bot.reply_to(message, menu_text(message.from_user.id), reply_markup=menu_markup(message.from_user.id),
                         parse_mode="HTML")
            return
        now = now_kh()
        day = now.strftime("%Y-%m-%d")
        with _db(config) as c:
            staff = [dict(r) for r in c.execute("SELECT * FROM att_staff WHERE status='active' ORDER BY name")]
            pending = c.execute("SELECT COUNT(*) FROM att_staff WHERE status='pending'").fetchone()[0]
        logs = today_logs(config, day)
        lines = [f"🕘 <b>វត្តមានថ្ងៃនេះ</b> — {now.strftime('%d/%m/%Y')}", ""]
        m = InlineKeyboardMarkup()
        for sf in staff:
            mine = [r for r in logs if r["user_id"] == sf["user_id"]]
            ins = [r for r in mine if r["kind"] == "in"]
            outs = [r for r in mine if r["kind"] == "out"]
            name = html.escape(sf["name"] or str(sf["user_id"]))
            if not ins:
                lines.append(f"⚪ {name} — មិនទាន់មក")
            else:
                t_in = fmt_hm(datetime.fromtimestamp(ins[0]["ts"], TZ_KH))
                late = f" ⏰យឺត {ins[0]['late_min']}នាទី" if ins[0]["late_min"] else ""
                t_out = f" → 🔴 {fmt_hm(datetime.fromtimestamp(outs[-1]['ts'], TZ_KH))}" if outs else " · កំពុងធ្វើការ"
                lines.append(f"🟢 {name} — {t_in}{late}{t_out}")
            m.row(InlineKeyboardButton(f"🗑️ ដក {sf['name'][:20]}", callback_data=f"att:del:{sf['user_id']}"))
        if not staff:
            lines.append("មិនទាន់មានបុគ្គលិកទេ — ឲ្យបុគ្គលិកវាយ /joinstaff ក្នុង Bot")
        if pending:
            lines.append(f"\n📨 សំណើរង់ចាំ៖ {pending}")
        if not place(config):
            lines.append("\n⚙️ មិនទាន់កំណត់ទីតាំងកន្លែងធ្វើការ — /setwork")
        lines.append("\n📲 QR សម្រាប់បិទនៅកន្លែងធ្វើការ៖ /workqr")
        bot.reply_to(message, "\n".join(lines), reply_markup=m if staff else None, parse_mode="HTML")

    def intent_reply(message):
        """Answer «QR ចូលធ្វើការ?» in plain words: the menu (private) or the buttons (group)."""
        uid = message.from_user.id
        if not private(message):
            bot.reply_to(message, "🕘 <b>ចូល/ចេញការងារ</b> — មាន 📲 QR នៅកន្លែងធ្វើការ ឬចុចប៊ូតុងខាងក្រោម\n"
                                  "→ ផ្ញើទីតាំង 📍 → ថត Selfie 🤳", reply_markup=group_markup(), parse_mode="HTML")
            return
        extra = ("\n\n📲 <b>QR</b>៖ ស្កេន QR ដែលបិទនៅកន្លែងធ្វើការដោយ Camera — Bot បើក ហើយដឹងខ្លួនថាចូល ឬចេញ"
                 + ("\n👑 Owner៖ ចុច «📲 QR បិទនៅកន្លែងធ្វើការ» ដើម្បីបាន Poster សម្រាប់ Print" if privileged(uid) else ""))
        bot.reply_to(message, menu_text(uid) + extra, reply_markup=menu_markup(uid), parse_mode="HTML")

    return {"busy": busy, "steps": steps, "intent_reply": intent_reply}
