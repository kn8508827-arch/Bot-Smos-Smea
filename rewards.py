"""🎁 /redeem — swap community points (check-in, quiz, invites) for VIP days.

Prices come from REDEEM_TIERS ("points:days,...", default 150:3,300:7,1000:30). Points are taken
and the VIP pass extended in one step; the owner gets a short note. Also gives the Hub its
leaderboard data (top 5 + your rank).
"""
import html
import logging
import os
import sqlite3

logger = logging.getLogger(__name__)
DEFAULT_TIERS = "150:3,300:7,1000:30"


def tiers():
    out = []
    for part in (os.getenv("REDEEM_TIERS", "") or DEFAULT_TIERS).split(","):
        try:
            pts, days = (int(x) for x in part.split(":"))
            if pts > 0 and days > 0:
                out.append((pts, days))
        except ValueError:
            continue
    return sorted(out) or [(150, 3), (300, 7), (1000, 30)]


def _db(config):
    conn = sqlite3.connect(getattr(config, "BOT_DB_PATH", "bot_database.db"), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def points_of(config, user_id):
    conn = _db(config)
    try:
        row = conn.execute("SELECT points FROM community_points WHERE user_id=?", (user_id,)).fetchone()
        return int(row["points"]) if row else 0
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def spend(config, user_id, cost):
    """Atomically take `cost` points; False when there aren't enough."""
    conn = _db(config)
    try:
        with conn:
            return conn.execute("UPDATE community_points SET points=points-? WHERE user_id=? AND points>=?",
                                (cost, user_id, cost)).rowcount == 1
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def leaderboard(config, user_id=None, limit=5):
    """([(name, points), ...], my_rank or None)."""
    conn = _db(config)
    try:
        has_users = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
        sql = ("SELECT p.user_id, p.points, u.first_name FROM community_points p LEFT JOIN users u ON u.user_id=p.user_id"
               if has_users else "SELECT user_id, points, NULL AS first_name FROM community_points")
        rows = conn.execute(sql + " WHERE points>0 ORDER BY points DESC, user_id ASC LIMIT ?", (limit,)).fetchall()
        top = [((r["first_name"] or "សមាជិក")[:14], int(r["points"])) for r in rows]
        rank = None
        if user_id is not None:
            me = conn.execute("SELECT points FROM community_points WHERE user_id=?", (user_id,)).fetchone()
            if me and me["points"] > 0:
                rank = 1 + conn.execute("SELECT COUNT(*) FROM community_points WHERE points>? OR (points=? AND user_id<?)",
                                        (me["points"], me["points"], user_id)).fetchone()[0]
        return top, rank
    except sqlite3.Error:
        return [], None
    finally:
        conn.close()


def register(bot, config):
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup
    import subscription as sub

    def view(user_id):
        pts = points_of(config, user_id)
        lines = ["🎁 <b>ប្ដូរពិន្ទុ → ថ្ងៃ VIP</b>", "", f"⭐ ពិន្ទុរបស់អ្នក៖ <b>{pts}</b>", ""]
        m = InlineKeyboardMarkup()
        for cost, days in tiers():
            ok = pts >= cost
            lines.append(f"{'✅' if ok else '🔒'} {cost} ពិន្ទុ → VIP {days} ថ្ងៃ")
            if ok:
                m.row(InlineKeyboardButton(f"🎟️ ប្ដូរ {cost} → {days} ថ្ងៃ", callback_data=f"rdm:{cost}:{days}"))
        lines += ["", "💡 រកពិន្ទុ៖ /checkin រាល់ថ្ងៃ · /quiz · /invite មិត្តភក្តិ"]
        return "\n".join(lines), m

    @bot.message_handler(commands=["redeem", "rewards"])
    def redeem_cmd(message):
        text, m = view(message.from_user.id)
        bot.reply_to(message, text, reply_markup=m, parse_mode="HTML")

    @bot.callback_query_handler(func=lambda c: (c.data or "").startswith("rdm:"))
    def redeem_cb(call):
        try:
            _, cost, days = call.data.split(":")
            cost, days = int(cost), int(days)
        except ValueError:
            bot.answer_callback_query(call.id)
            return
        if (cost, days) not in tiers():
            bot.answer_callback_query(call.id, "តម្លៃបានប្ដូរ — សូមបើក /redeem ម្ដងទៀត", show_alert=True)
            return
        uid = call.from_user.id
        if not spend(config, uid, cost):
            bot.answer_callback_query(call.id, "ពិន្ទុមិនគ្រប់គ្រាន់ទេ", show_alert=True)
            return
        try:
            until = sub.extend(config, "u", uid, days)
        except Exception as exc:
            logger.error("redeem extend failed (%s) — refunding", type(exc).__name__)
            conn = _db(config)
            try:
                with conn:
                    conn.execute("UPDATE community_points SET points=points+? WHERE user_id=?", (cost, uid))
            finally:
                conn.close()
            bot.answer_callback_query(call.id, "មានបញ្ហា — ពិន្ទុត្រូវបានប្រគល់វិញ", show_alert=True)
            return
        bot.answer_callback_query(call.id, f"🎉 VIP +{days} ថ្ងៃ!", show_alert=True)
        name = html.escape(call.from_user.first_name or "សមាជិក")
        try:
            bot.edit_message_text(f"🎉 <b>{name}</b> បានប្ដូរ {cost} ពិន្ទុ → <b>VIP {days} ថ្ងៃ</b>\n"
                                  f"🎟️ ប្រើបានដល់ {sub.fmt_date(until)}\n\nមើលកាត៖ /myplan · /hub",
                                  call.message.chat.id, call.message.message_id, parse_mode="HTML")
        except Exception:
            pass
        owner = getattr(config, "OWNER_ID", 0)
        if owner and owner != uid:
            try:
                bot.send_message(owner, f"🎁 {name} (<code>{uid}</code>) ប្ដូរ {cost} ពិន្ទុ → VIP {days} ថ្ងៃ", parse_mode="HTML")
            except Exception:
                pass
