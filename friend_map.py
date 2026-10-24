"""📍 Friend Map — group members who CHOOSE to share can see where each other are.

Privacy rules (built in, not optional):
  • opt-in only, per group, for a limited time (1 h / 8 h / 24 h), stop any time (/stopshare)
  • reciprocal: you only see others while you are sharing in that group yourself
  • nobody else (not even the bot owner) can look anyone up; locations are never posted in the group
  • the group gets a visible notice when someone starts sharing, so it is never secret
  • "area" mode rounds the point to about 1 km; only the latest point is kept, deleted when it expires
  • live locations (📎 → Location → Share Live Location) keep updating until they end

Commands: /nearby (in a group: share / see buttons · in private: your sharing) · /stopshare
"""
import base64
import html
import json
import logging
import math
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)
TZ_KH = timezone(timedelta(hours=7))
DURATIONS = {60: "១ ម៉ោង", 480: "៨ ម៉ោង", 1440: "២៤ ម៉ោង"}
MAP_URL_DEFAULT = "https://kn8508827-arch.github.io/Blue-Gold-Rush/hub/map.html"


def km_between(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def fuzz(lat, lon, precise):
    return (lat, lon) if precise else (round(lat, 2), round(lon, 2))   # ~1 km


def ago_text(seconds):
    m = int(seconds // 60)
    if m < 1:
        return "ទើបតែឥឡូវ"
    if m < 60:
        return f"{m} នាទីមុន"
    return f"{m // 60} ម៉ោងមុន"


def dist_text(km):
    return f"{km * 1000:.0f} m" if km < 1 else f"{km:.1f} km"


def _db(config):
    conn = sqlite3.connect(getattr(config, "BOT_DB_PATH", "bot_database.db"), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(config):
    conn = _db(config)
    try:
        with conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS friend_locs (user_id INTEGER NOT NULL, group_id INTEGER NOT NULL,
                name TEXT, lat REAL, lon REAL, precise INTEGER NOT NULL DEFAULT 1, updated_at INTEGER,
                expires_at INTEGER NOT NULL, PRIMARY KEY (user_id, group_id))""")
    finally:
        conn.close()


def cleanup(config, now=None):
    conn = _db(config)
    try:
        with conn:
            conn.execute("DELETE FROM friend_locs WHERE expires_at <= ?", (int(now or time.time()),))
    finally:
        conn.close()


def sharing_rows(config, group_id, now=None):
    cleanup(config, now)
    conn = _db(config)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM friend_locs WHERE group_id=? AND lat IS NOT NULL",
                                              (group_id,)).fetchall()]
    finally:
        conn.close()


def my_shares(config, user_id, now=None):
    cleanup(config, now)
    conn = _db(config)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM friend_locs WHERE user_id=?", (user_id,)).fetchall()]
    finally:
        conn.close()


def register(bot, config):
    from telebot.types import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup,
                               ReplyKeyboardRemove, WebAppInfo)

    init_schema(config)
    setup = {}      # user_id -> {"gid", "mins", "precise", "title", "until"}
    titles = {}
    me = {}

    def username():
        if "u" not in me:
            try:
                me["u"] = bot.get_me().username
            except Exception:
                return "TeamSmosSneabot"
        return me["u"]

    def is_group(chat):
        return getattr(chat, "type", None) in ("group", "supergroup")

    def member_of(gid, uid):
        try:
            return bot.get_chat_member(gid, uid).status in ("creator", "administrator", "member", "restricted")
        except Exception:
            return False

    def group_title(gid):
        if gid not in titles:
            try:
                titles[gid] = getattr(bot.get_chat(gid), "title", None) or str(gid)
            except Exception:
                titles[gid] = str(gid)
        return titles[gid]

    def name_of(user):
        return (getattr(user, "first_name", None) or "មិត្ត")[:24]

    # ---------- in the group ----------
    def group_panel(gid):
        n = len(sharing_rows(config, gid))
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("📍 ចែករំលែកទីតាំងខ្ញុំ", url=f"https://t.me/{username()}?start=loc_{abs(gid)}"))
        m.row(InlineKeyboardButton(f"👀 មើលមិត្ត ({n})", url=f"https://t.me/{username()}?start=see_{abs(gid)}"))
        text = ("📍 <b>Friend Map — ដឹងថាមិត្តនៅឯណា</b>\n\n"
                "• អ្នកណាចង់ ទើបចែករំលែក — <b>អ្នកមិនចែក គ្មានអ្នកណាឃើញអ្នកទេ</b>\n"
                "• ឃើញមិត្តបាន លុះត្រាតែខ្លួនឯងក៏ចែករំលែកដែរ\n"
                "• កំណត់ពេល ១ / ៨ / ២៤ ម៉ោង ហើយឈប់ពេលណាក៏បាន\n"
                "• ទីតាំងមិនបង្ហាញក្នុង Group ទេ — មើលតែក្នុង Private Chat\n\n"
                f"👥 កំពុងចែករំលែក៖ <b>{n}</b> នាក់")
        return text, m

    @bot.message_handler(commands=["nearby", "friends", "whereis", "friendmap"])
    def nearby_cmd(message):
        if is_group(message.chat):
            text, m = group_panel(message.chat.id)
            bot.reply_to(message, text, reply_markup=m, parse_mode="HTML")
            return
        rows = my_shares(config, message.from_user.id)
        if not rows:
            bot.reply_to(message, "📍 <b>Friend Map</b>\nវាយ <code>/nearby</code> <b>ក្នុង Group</b> ដែលអ្នកចង់ចែករំលែកទីតាំងជាមួយមិត្ត "
                                  "ហើយចុច «📍 ចែករំលែកទីតាំងខ្ញុំ» 👍", parse_mode="HTML")
            return
        m = InlineKeyboardMarkup()
        lines = ["📍 <b>អ្នកកំពុងចែករំលែកក្នុង</b>"]
        for r in rows:
            until = datetime.fromtimestamp(r["expires_at"], TZ_KH).strftime("%H:%M")
            lines.append(f"• {html.escape(group_title(r['group_id']))} — ដល់ {until}")
            m.row(InlineKeyboardButton(f"👀 {group_title(r['group_id'])[:25]}", callback_data=f"fm:see:{r['group_id']}"),
                  InlineKeyboardButton("⛔ ឈប់", callback_data=f"fm:stop:{r['group_id']}"))
        bot.reply_to(message, "\n".join(lines), reply_markup=m, parse_mode="HTML")

    # ---------- private: start=loc_<gid> / see_<gid> ----------
    def is_deeplink(message):
        parts = (getattr(message, "text", None) or "").split()
        return (len(parts) == 2 and parts[0].split("@")[0] == "/start"
                and (parts[1].startswith("loc_") or parts[1].startswith("see_")))

    @bot.message_handler(commands=["start"], func=is_deeplink)
    def start_deeplink(message):
        payload = message.text.split()[1]
        try:
            gid = -int(payload[4:])
        except ValueError:
            return
        uid = message.from_user.id
        if not member_of(gid, uid):
            bot.reply_to(message, "⛔ អ្នកមិនមែនជាសមាជិក Group នោះទេ")
            return
        if payload.startswith("see_"):
            show_friends(message.chat.id, uid, gid)
            return
        m = InlineKeyboardMarkup()
        m.row(*[InlineKeyboardButton(f"⏱️ {label}", callback_data=f"fm:d:{gid}:{mins}") for mins, label in DURATIONS.items()])
        bot.reply_to(message, f"📍 ចែករំលែកទីតាំងជាមួយ <b>{html.escape(group_title(gid))}</b>\n\nរយៈពេលប៉ុន្មាន? 👇",
                     reply_markup=m, parse_mode="HTML")

    def ask_location(chat_id, uid):
        s = setup[uid]
        kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.add(KeyboardButton("📍 ផ្ញើទីតាំងខ្ញុំ", request_location=True))
        bot.send_message(chat_id, "👇 ចុច «📍 ផ្ញើទីតាំងខ្ញុំ»\n\n"
                                  "💡 ចង់ឲ្យទីតាំងប្ដូរតាមពេលធ្វើដំណើរ៖ ចុច 📎 → <b>Location</b> → "
                                  f"<b>Share Live Location</b> ({DURATIONS[s['mins']]})", reply_markup=kb, parse_mode="HTML")

    def save(uid, user, lat, lon, gid=None):
        rows = [s for s in my_shares(config, uid) if gid is None or s["group_id"] == gid]
        conn = _db(config)
        try:
            with conn:
                for r in rows:
                    flat, flon = fuzz(lat, lon, r["precise"])
                    conn.execute("UPDATE friend_locs SET lat=?, lon=?, updated_at=? WHERE user_id=? AND group_id=?",
                                 (flat, flon, int(time.time()), uid, r["group_id"]))
        finally:
            conn.close()
        return rows

    def awaiting(message):
        uid = getattr(getattr(message, "from_user", None), "id", None)
        s = setup.get(uid)
        return (getattr(getattr(message, "chat", None), "type", None) == "private" and s is not None
                and s["until"] > time.monotonic())

    @bot.message_handler(func=awaiting, content_types=["location"])
    def got_location(message):
        uid = message.from_user.id
        s = setup.pop(uid)
        gid = s["gid"]
        if not member_of(gid, uid):
            bot.reply_to(message, "⛔ អ្នកមិនមែនជាសមាជិក Group នោះទៀតទេ", reply_markup=ReplyKeyboardRemove())
            return
        loc = message.location
        expires = int(time.time()) + s["mins"] * 60
        live = getattr(loc, "live_period", None)
        if live:
            expires = min(expires, int(time.time()) + int(live))
        flat, flon = fuzz(loc.latitude, loc.longitude, s["precise"])
        conn = _db(config)
        try:
            with conn:
                new = conn.execute("SELECT 1 FROM friend_locs WHERE user_id=? AND group_id=?", (uid, gid)).fetchone() is None
                conn.execute("INSERT OR REPLACE INTO friend_locs(user_id, group_id, name, lat, lon, precise, updated_at, "
                             "expires_at) VALUES (?,?,?,?,?,?,?,?)",
                             (uid, gid, name_of(message.from_user), flat, flon, s["precise"], int(time.time()), expires))
        finally:
            conn.close()
        until = datetime.fromtimestamp(expires, TZ_KH).strftime("%H:%M")
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("👀 មើលមិត្ត", callback_data=f"fm:see:{gid}"),
              InlineKeyboardButton("⛔ ឈប់ចែករំលែក", callback_data=f"fm:stop:{gid}"))
        bot.reply_to(message, f"✅ កំពុងចែករំលែកជាមួយ <b>{html.escape(group_title(gid))}</b> ដល់ម៉ោង {until}"
                              f"{' · 🔄 Live' if live else ''}{'' if s['precise'] else ' · 📍 ប្រហែល ១ km'}",
                     reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")
        bot.send_message(message.chat.id, "👇", reply_markup=m)
        if new:
            try:
                bot.send_message(gid, f"📍 <b>{html.escape(name_of(message.from_user))}</b> បានបើកចែករំលែកទីតាំងជាមួយ Group នេះ "
                                      f"ដល់ម៉ោង {until} · ចង់ឃើញគ្នា ចែករំលែកដែរ 👉 /nearby", parse_mode="HTML")
            except Exception:
                pass

    @bot.edited_message_handler(content_types=["location"])
    def live_update(message):
        if getattr(getattr(message, "chat", None), "type", None) != "private":
            return
        loc = message.location
        if loc is None:
            return
        save(message.from_user.id, message.from_user, loc.latitude, loc.longitude)

    # ---------- seeing friends ----------
    def map_url(viewer_row, others, gid):
        data = {"g": group_title(gid)[:40],
                "me": [round(viewer_row["lat"], 5), round(viewer_row["lon"], 5)],
                "f": [[o["name"], round(o["lat"], 5), round(o["lon"], 5), int((time.time() - (o["updated_at"] or 0)) // 60),
                       o["precise"]] for o in others[:40]]}
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
        return (os.getenv("FRIEND_MAP_URL", "").strip() or MAP_URL_DEFAULT) + "?d=" + base64.urlsafe_b64encode(raw).decode().rstrip("=")

    def show_friends(chat_id, uid, gid):
        rows = sharing_rows(config, gid)
        mine = next((r for r in rows if r["user_id"] == uid), None)
        if mine is None:
            m = InlineKeyboardMarkup()
            m.row(InlineKeyboardButton("📍 ចែករំលែកទីតាំងខ្ញុំ", url=f"https://t.me/{username()}?start=loc_{abs(gid)}"))
            bot.send_message(chat_id, "🔒 ដើម្បីឃើញមិត្ត អ្នកត្រូវចែករំលែកទីតាំងខ្លួនឯងសិន (យុត្តិធម៌សម្រាប់គ្រប់គ្នា) 👇",
                             reply_markup=m)
            return
        others = [r for r in rows if r["user_id"] != uid and member_of(gid, r["user_id"])]
        if not others:
            bot.send_message(chat_id, f"📍 មិនទាន់មានមិត្តផ្សេងចែករំលែកក្នុង <b>{html.escape(group_title(gid))}</b> ទេ — "
                                      "ប្រាប់គេឲ្យវាយ /nearby ក្នុង Group 😉", parse_mode="HTML")
            return
        me_pt = (mine["lat"], mine["lon"])
        others.sort(key=lambda r: km_between(me_pt, (r["lat"], r["lon"])))
        lines = [f"👥 <b>មិត្តនៅ {html.escape(group_title(gid))}</b>", ""]
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("🗺️ មើលលើផែនទី", web_app=WebAppInfo(url=map_url(mine, others, gid))))
        for r in others[:15]:
            km = km_between(me_pt, (r["lat"], r["lon"]))
            approx = "" if r["precise"] else " (ប្រហែល)"
            lines.append(f"• <b>{html.escape(r['name'])}</b> — {dist_text(km)}{approx} · {ago_text(time.time() - (r['updated_at'] or 0))}")
            m.row(InlineKeyboardButton(f"🧭 ទៅរក {r['name'][:18]}",
                                       url=f"https://www.google.com/maps/dir/?api=1&destination={r['lat']:.5f},{r['lon']:.5f}"))
        m.row(InlineKeyboardButton("🔄 Refresh", callback_data=f"fm:see:{gid}"),
              InlineKeyboardButton("⛔ ឈប់ចែករំលែក", callback_data=f"fm:stop:{gid}"))
        bot.send_message(chat_id, "\n".join(lines), reply_markup=m, parse_mode="HTML")

    def stop(uid, gid=None):
        conn = _db(config)
        try:
            with conn:
                if gid is None:
                    return conn.execute("DELETE FROM friend_locs WHERE user_id=?", (uid,)).rowcount
                return conn.execute("DELETE FROM friend_locs WHERE user_id=? AND group_id=?", (uid, gid)).rowcount
        finally:
            conn.close()

    @bot.message_handler(commands=["stopshare", "stoplocation"])
    def stopshare_cmd(message):
        n = stop(message.from_user.id)
        setup.pop(message.from_user.id, None)
        bot.reply_to(message, f"⛔ បានឈប់ចែករំលែកទីតាំង ({n} Group) ហើយលុបទីតាំងចោលហើយ ✅" if n
                     else "ℹ️ អ្នកមិនបានចែករំលែកទីតាំងនៅកន្លែងណាទេ", reply_markup=ReplyKeyboardRemove())

    @bot.callback_query_handler(func=lambda c: (c.data or "").startswith("fm:"))
    def fm_cb(call):
        parts = (call.data or "").split(":")
        uid, chat_id = call.from_user.id, call.message.chat.id
        try:
            gid = int(parts[2])
        except (IndexError, ValueError):
            bot.answer_callback_query(call.id)
            return
        if parts[1] == "d" and len(parts) == 4 and parts[3].isdigit() and int(parts[3]) in DURATIONS:
            bot.answer_callback_query(call.id)
            m = InlineKeyboardMarkup()
            m.row(InlineKeyboardButton("🎯 ទីតាំងពិតប្រាកដ", callback_data=f"fm:p:{gid}:{parts[3]}:1"),
                  InlineKeyboardButton("🌫️ ប្រហែល ~១ km", callback_data=f"fm:p:{gid}:{parts[3]}:0"))
            bot.send_message(chat_id, "ចង់ឲ្យមិត្តឃើញ៖", reply_markup=m)
            return
        if parts[1] == "p" and len(parts) == 5:
            bot.answer_callback_query(call.id)
            setup[uid] = {"gid": gid, "mins": int(parts[3]) if int(parts[3]) in DURATIONS else 60,
                          "precise": 1 if parts[4] == "1" else 0, "until": time.monotonic() + 600}
            ask_location(chat_id, uid)
            return
        if parts[1] == "see":
            bot.answer_callback_query(call.id)
            if member_of(gid, uid):
                show_friends(chat_id, uid, gid)
            return
        if parts[1] == "stop":
            stop(uid, gid)
            bot.answer_callback_query(call.id, "⛔ ឈប់ហើយ — ទីតាំងត្រូវបានលុប", show_alert=True)
            return
        bot.answer_callback_query(call.id)

    class Api:
        pass
    api = Api()
    api.show_friends = show_friends
    api.setup = setup
    return api
