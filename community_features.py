"""Community engagement features for Team ស្មោះស្នេហ៍ Telegram Bot.

Additive SQLite tables only. No paid gambling or cash-prize mechanics.
"""
import random
import html
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from functools import wraps
from types import SimpleNamespace
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Phnom_Penh")
import quiz_bank

QUIZ = [(q, opts, correct) for _cat, q, opts, correct, _why in quiz_bank.QUESTIONS]
QUIZ_PER_DAY = 5
POINTS_CHECKIN = 5
POINTS_QUIZ = 10
POINTS_REFERRAL = 25


@contextmanager
def _connect(config):
    path = getattr(config, "BOT_DB_PATH", "bot_database.db")
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _init_schema(config):
    with _connect(config) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS community_points (
            user_id INTEGER PRIMARY KEY,
            points INTEGER NOT NULL DEFAULT 0,
            referred_by INTEGER,
            vip INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS community_checkins (
            user_id INTEGER NOT NULL,
            local_date TEXT NOT NULL,
            points INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, local_date)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS community_quiz_answers (
            user_id INTEGER NOT NULL,
            local_date TEXT NOT NULL,
            question_id INTEGER NOT NULL,
            correct INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, local_date, question_id)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS community_codes (
            code TEXT PRIMARY KEY,
            points INTEGER NOT NULL,
            max_uses INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS community_code_claims (
            code TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            claimed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (code, user_id)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS community_referrals (
            referred_user_id INTEGER PRIMARY KEY,
            referrer_user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.commit()


def _ensure_user(conn, user_id):
    conn.execute("INSERT OR IGNORE INTO community_points(user_id) VALUES (?)", (user_id,))


def _points(conn, user_id):
    row = conn.execute("SELECT points, vip FROM community_points WHERE user_id=?", (user_id,)).fetchone()
    return (int(row["points"]), bool(row["vip"])) if row else (0, False)


def _users_table_exists(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone() is not None


def _user_exists(conn, user_id):
    if not _users_table_exists(conn):
        return False
    return conn.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone() is not None


def _is_new_user(conn, user_id, minutes=10):
    if conn.execute("SELECT 1 FROM community_points WHERE user_id=?", (user_id,)).fetchone():
        return False
    if not _users_table_exists(conn):
        return True
    row = conn.execute(
        "SELECT (julianday('now') - julianday(first_seen)) * 1440 AS age FROM users WHERE user_id=?",
        (user_id,),
    ).fetchone()
    return row is None or row["age"] is None or row["age"] <= minutes


def _level(points, vip):
    if vip:
        return "👑 VIP"
    if points >= 500:
        return "💎 Diamond"
    if points >= 200:
        return "🥇 Gold"
    if points >= 75:
        return "🥈 Silver"
    return "🥉 Bronze"


def register(bot, database, config):
    """Register feature handlers before the existing catch-all handlers."""
    _init_schema(config)

    def register_interaction(user, chat):
        if user is None or chat is None or getattr(user, "id", None) is None:
            return
        try:
            database.register_or_update_user(
                user_id=user.id,
                chat_id=chat.id,
                username=getattr(user, "username", None),
                first_name=getattr(user, "first_name", None),
                last_name=getattr(user, "last_name", None),
                chat_type=getattr(chat, "type", "unknown"),
            )
        except Exception as exc:
            logger.warning("Could not register community interaction (%s)", type(exc).__name__)

    def registered_message_handler(*handler_args, **handler_kwargs):
        def decorate(handler):
            @wraps(handler)
            def wrapped(message, *args, **kwargs):
                register_interaction(
                    getattr(message, "from_user", None),
                    getattr(message, "chat", None),
                )
                return handler(message, *args, **kwargs)

            return bot.message_handler(*handler_args, **handler_kwargs)(wrapped)

        return decorate

    def registered_callback_handler(*handler_args, **handler_kwargs):
        def decorate(handler):
            @wraps(handler)
            def wrapped(call, *args, **kwargs):
                message = getattr(call, "message", None)
                register_interaction(
                    getattr(call, "from_user", None),
                    getattr(message, "chat", None),
                )
                return handler(call, *args, **kwargs)

            return bot.callback_query_handler(*handler_args, **handler_kwargs)(wrapped)

        return decorate

    def profile_text(user_id, name):
        with _connect(config) as conn:
            _ensure_user(conn, user_id)
            points, vip = _points(conn, user_id)
            rank_row = conn.execute(
                "SELECT COUNT(*) + 1 AS rank FROM community_points WHERE points > ?",
                (points,),
            ).fetchone()
        return (
            f"🌟 <b>{html.escape(name or 'ប្រិយមិត្ត')}</b>\n"
            f"🎖️ កម្រិត៖ {_level(points, vip)}\n"
            f"⭐ ពិន្ទុ៖ <b>{points}</b>\n"
            f"🏆 ចំណាត់ថ្នាក់៖ #{rank_row['rank']}\n\n"
            "ប្រើ /checkin ដើម្បីទទួលពិន្ទុប្រចាំថ្ងៃ ឬ /quiz ដើម្បីលេង Quiz។"
        )

    @registered_message_handler(commands=["profile", "points"])
    def profile(message):
        if not getattr(message, "from_user", None):
            return
        bot.reply_to(message, profile_text(message.from_user.id, message.from_user.first_name), parse_mode="HTML")

    @registered_message_handler(commands=["checkin"])
    def checkin(message):
        user_id = message.from_user.id
        today = datetime.now(TZ).date().isoformat()
        with _connect(config) as conn:
            _ensure_user(conn, user_id)
            try:
                conn.execute(
                    "INSERT INTO community_checkins(user_id, local_date, points) VALUES (?, ?, ?)",
                    (user_id, today, POINTS_CHECKIN),
                )
            except sqlite3.IntegrityError:
                already = True
            else:
                already = False
                conn.execute("UPDATE community_points SET points=points+? WHERE user_id=?", (POINTS_CHECKIN, user_id))
            conn.commit()
            points, _ = _points(conn, user_id)
        if already:
            bot.reply_to(message, f"✅ អ្នកបាន Check-in ថ្ងៃនេះរួចហើយ!\n⭐ ពិន្ទុបច្ចុប្បន្ន៖ {points}")
        else:
            bot.reply_to(message, f"🎉 Check-in ជោគជ័យ! អ្នកទទួលបាន +{POINTS_CHECKIN} ពិន្ទុ។\n⭐ ពិន្ទុសរុប៖ {points}")

    @registered_message_handler(commands=["leaderboard", "top"])
    def leaderboard(message):
        with _connect(config) as conn:
            rows = conn.execute(
                """SELECT p.user_id, p.points, p.vip, u.first_name, u.username
                   FROM community_points p LEFT JOIN users u ON u.user_id=p.user_id
                   ORDER BY p.points DESC, p.user_id ASC LIMIT 10"""
            ).fetchall()
        if not rows:
            bot.reply_to(message, "មិនទាន់មានទិន្នន័យចំណាត់ថ្នាក់ទេ។ សូមប្រើ /checkin ដើម្បីចាប់ផ្ដើម!")
            return
        lines = ["🏆 <b>Top 10 ប្រិយមិត្ត</b>", ""]
        for i, row in enumerate(rows, 1):
            name = row["first_name"] or (("@" + row["username"]) if row["username"] else f"សមាជិក {row['user_id']}")
            name = html.escape(name)
            badge = " 👑" if row["vip"] else ""
            lines.append(f"{i}. {name}{badge} — ⭐ {row['points']}")
        bot.reply_to(message, "\n".join(lines), parse_mode="HTML")

    @registered_message_handler(commands=["quiz"])
    def quiz(message):
        user_id = message.from_user.id
        today = datetime.now(TZ).date().isoformat()
        with _connect(config) as conn:
            _ensure_user(conn, user_id)
            rows = conn.execute(
                "SELECT question_id, local_date FROM community_quiz_answers WHERE user_id=?",
                (user_id,),
            ).fetchall()
        answered_today = {r["question_id"] for r in rows if r["local_date"] == today}
        answered_ever = {r["question_id"] for r in rows}
        if len(answered_today) >= QUIZ_PER_DAY:
            bot.reply_to(message, f"🎮 អ្នកបានលេង Quiz គ្រប់ {QUIZ_PER_DAY} សំណួរសម្រាប់ថ្ងៃនេះហើយ។ ត្រឡប់មកថ្ងៃស្អែក មានសំណួរថ្មីទៀត!")
            return
        # Never repeat a question until the player has seen the whole bank.
        available = [i for i in range(len(QUIZ)) if i not in answered_ever]
        if not available:
            available = [i for i in range(len(QUIZ)) if i not in answered_today]
        if not available:
            bot.reply_to(message, "🎮 អ្នកឆ្លើយគ្រប់សំណួរហើយ! ត្រឡប់មកថ្ងៃស្អែក។")
            return
        qid = random.choice(available)
        category = quiz_bank.QUESTIONS[qid][0]
        question, options, correct = QUIZ[qid]
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        markup = InlineKeyboardMarkup(row_width=2)
        order = list(range(len(options)))
        random.shuffle(order)
        for i in order:
            markup.add(InlineKeyboardButton(options[i], callback_data=f"community:quiz:{qid}:{i}"))
        number = len(answered_today) + 1
        bot.reply_to(
            message,
            f"🧠 <b>Quiz {number}/{QUIZ_PER_DAY}</b> · {html.escape(category)}\n\n{html.escape(question)}\n\n"
            f"ចម្លើយត្រឹមត្រូវ +{POINTS_QUIZ} ពិន្ទុ!",
            reply_markup=markup, parse_mode="HTML",
        )

    @registered_callback_handler(func=lambda call: (call.data or "").startswith("community:"))
    def community_callback(call):
        data = call.data or ""
        user_id = getattr(call.from_user, "id", None)
        if user_id is None:
            bot.answer_callback_query(call.id)
            return
        parts = data.split(":")
        if len(parts) == 4 and parts[1] == "menu":
            action = parts[2]
            actions = {
                "checkin": checkin,
                "quiz": quiz,
                "top": leaderboard,
                "card": card,
                "profile": profile,
                "invite": invite,
                "vip": vip,
            }
            handler = actions.get(action)
            bot.answer_callback_query(call.id)
            if handler and getattr(call, "message", None):
                source_message = call.message
                callback_message = SimpleNamespace(
                    from_user=call.from_user,
                    chat=source_message.chat,
                    message_id=source_message.message_id,
                    text="",
                )
                handler(callback_message)
            return
        if len(parts) != 4 or parts[1] != "quiz":
            bot.answer_callback_query(call.id, "ជម្រើសនេះមិនត្រឹមត្រូវទេ។", show_alert=True)
            return
        try:
            qid, choice = int(parts[2]), int(parts[3])
            if qid < 0 or qid >= len(QUIZ):
                raise ValueError("invalid question")
            question, options, correct = QUIZ[qid]
            if choice < 0 or choice >= len(options):
                raise ValueError("invalid choice")
        except (ValueError, IndexError):
            bot.answer_callback_query(call.id, "សំណួរនេះមិនត្រឹមត្រូវទេ។", show_alert=True)
            return
        today = datetime.now(TZ).date().isoformat()
        is_correct = choice == correct
        with _connect(config) as conn:
            _ensure_user(conn, user_id)
            answered_today = conn.execute(
                "SELECT COUNT(*) AS n FROM community_quiz_answers WHERE user_id=? AND local_date=?",
                (user_id, today),
            ).fetchone()["n"]
            if answered_today >= QUIZ_PER_DAY:
                bot.answer_callback_query(call.id, "អ្នកបានលេង Quiz គ្រប់ចំនួនថ្ងៃនេះហើយ។ ត្រឡប់មកថ្ងៃស្អែក!", show_alert=True)
                return
            try:
                conn.execute(
                    "INSERT INTO community_quiz_answers(user_id, local_date, question_id, correct) VALUES (?, ?, ?, ?)",
                    (user_id, today, qid, int(is_correct)),
                )
            except sqlite3.IntegrityError:
                bot.answer_callback_query(call.id, "អ្នកបានឆ្លើយសំណួរនេះរួចហើយ។", show_alert=True)
                return
            if is_correct:
                conn.execute("UPDATE community_points SET points=points+? WHERE user_id=?", (POINTS_QUIZ, user_id))
            conn.commit()
            points, _ = _points(conn, user_id)
        bot.answer_callback_query(call.id, "ត្រឹមត្រូវ! 🎉" if is_correct else "មិនទាន់ត្រូវទេ 😊")
        result = f"✅ ត្រឹមត្រូវ! {options[correct]}" if is_correct else f"❌ ចម្លើយត្រឹមត្រូវគឺ៖ {options[correct]}"
        why = quiz_bank.QUESTIONS[qid][4]
        left = max(0, QUIZ_PER_DAY - answered_today - 1)
        more = f"នៅសល់ {left} សំណួរថ្ងៃនេះ · ចុច /quiz" if left else "ថ្ងៃនេះគ្រប់ហើយ ត្រឡប់មកថ្ងៃស្អែក 🌙"
        bot.send_message(call.message.chat.id, f"{result}\n💡 {why}\n⭐ ពិន្ទុសរុប៖ {points}\n\n{more}")

    @registered_message_handler(commands=["invite"])
    def invite(message):
        user_id = message.from_user.id
        try:
            me = bot.get_me()
            username = me.username
        except Exception:
            username = None
        if not username:
            bot.reply_to(message, "មិនអាចបង្កើតតំណអញ្ជើញបានទេឥឡូវនេះ។ សូមសាកល្បងម្ដងទៀត។")
            return
        link = f"https://t.me/{username}?start=ref_{user_id}"
        bot.reply_to(message, f"🤝 <b>អញ្ជើញមិត្តភក្តិ</b>\n\nចែករំលែកតំណនេះ៖\n{link}\n\nអ្នកទាំងពីរទទួលបាន +{POINTS_REFERRAL} ពិន្ទុ នៅពេលមិត្តថ្មីចូលតាមតំណជាលើកដំបូង។", parse_mode="HTML")

    @registered_message_handler(commands=["start"], func=lambda m: bool(getattr(m, "text", None)) and " ref_" in m.text)
    def referral_start(message):
        user = getattr(message, "from_user", None)
        if not user:
            return
        payload = message.text.split(maxsplit=1)[1].strip() if len(message.text.split(maxsplit=1)) > 1 else ""
        if not payload.startswith("ref_"):
            return
        try:
            referrer_id = int(payload[4:])
        except ValueError:
            return
        if referrer_id == user.id:
            bot.reply_to(message, "សូមអរគុណដែលបានចូលរួមសហគមន៍! មិនអាចអញ្ជើញខ្លួនឯងបានទេ។")
            return
        awarded = False
        with _connect(config) as conn:
            # Only brand-new Telegram users (first seen in the last 10 minutes) earn
            # referral points, and only for a referrer who has really used the bot.
            if not _is_new_user(conn, user.id) or not _user_exists(conn, referrer_id):
                bot.reply_to(message, "សួស្ដី! តំណអញ្ជើញសម្រាប់តែសមាជិកថ្មីប៉ុណ្ណោះ។\n\nប្រើ /menu ដើម្បីមើលសេវាកម្ម ឬ /profile ដើម្បីមើលពិន្ទុ។")
                return
            _ensure_user(conn, user.id)
            _ensure_user(conn, referrer_id)
            try:
                conn.execute("INSERT INTO community_referrals(referred_user_id, referrer_user_id) VALUES (?, ?)", (user.id, referrer_id))
            except sqlite3.IntegrityError:
                pass
            else:
                conn.execute("UPDATE community_points SET points=points+? WHERE user_id IN (?, ?)", (POINTS_REFERRAL, user.id, referrer_id))
                conn.execute("UPDATE community_points SET referred_by=? WHERE user_id=?", (referrer_id, user.id))
                awarded = True
            conn.commit()
        text = "🎉 អ្នកបានចូលរួមតាមតំណអញ្ជើញ! អ្នកទាំងពីរទទួលបានពិន្ទុ។" if awarded else "សួស្ដី! អ្នកបានចូលរួមតាមតំណអញ្ជើញរួចហើយ។"
        bot.reply_to(message, text + "\n\nប្រើ /menu ដើម្បីមើលសេវាកម្ម ឬ /profile ដើម្បីមើលពិន្ទុ។")

    @registered_message_handler(commands=["vip"])
    def vip(message):
        user_id = message.from_user.id
        with _connect(config) as conn:
            _ensure_user(conn, user_id)
            points, is_vip = _points(conn, user_id)
        text = "👑 អ្នកជាសមាជិក VIP!" if is_vip else f"ស្ថានភាពបច្ចុប្បន្ន៖ {_level(points, is_vip)}\n\nVIP ត្រូវបានកំណត់ដោយ Admin។ សូមប្រើ /profile ដើម្បីមើលពិន្ទុ និងកម្រិត។"
        bot.reply_to(message, text)

    @registered_message_handler(commands=["communityadmin"])
    def community_admin(message):
        user_id = getattr(getattr(message, "from_user", None), "id", None)
        if user_id is None or (user_id not in getattr(config, "ADMIN_IDS", []) and user_id != getattr(config, "OWNER_ID", 0)):
            bot.reply_to(message, "⛔ ពាក្យបញ្ជានេះសម្រាប់ Admin ប៉ុណ្ណោះ។")
            return
        args = (message.text or "").split()
        if len(args) == 3 and args[1] == "vip":
            try:
                target_id = int(args[2])
            except ValueError:
                bot.reply_to(message, "របៀបប្រើ៖ /communityadmin vip USER_ID")
                return
            with _connect(config) as conn:
                _ensure_user(conn, target_id)
                conn.execute("UPDATE community_points SET vip=1 WHERE user_id=?", (target_id,))
                conn.commit()
            bot.reply_to(message, f"បានកំណត់ VIP សម្រាប់ USER_ID {target_id}។")
            return
        with _connect(config) as conn:
            members = conn.execute("SELECT COUNT(*) AS n FROM community_points").fetchone()["n"]
            points_total = conn.execute("SELECT COALESCE(SUM(points),0) AS n FROM community_points").fetchone()["n"]
            vip_count = conn.execute("SELECT COUNT(*) AS n FROM community_points WHERE vip=1").fetchone()["n"]
        bot.reply_to(message, f"🛠️ <b>Community Admin</b>\n👥 សមាជិកមាន Profile៖ {members}\n⭐ ពិន្ទុសរុប៖ {points_total}\n👑 VIP៖ {vip_count}\n\nកំណត់ VIP៖ <code>/communityadmin vip USER_ID</code>", parse_mode="HTML")

    def is_manager(user_id):
        return user_id is not None and (
            user_id in getattr(config, "ADMIN_IDS", []) or user_id == getattr(config, "OWNER_ID", 0)
        )

    # ---------- 🎟️ Secret codes (hidden in MVs / lives) ----------
    @registered_message_handler(commands=["setcode"])
    def set_code(message):
        user_id = getattr(getattr(message, "from_user", None), "id", None)
        if not is_manager(user_id):
            bot.reply_to(message, "⛔ ពាក្យបញ្ជានេះសម្រាប់ Admin ប៉ុណ្ណោះ។")
            return
        args = (message.text or "").split()
        if len(args) == 3 and args[1].lower() == "off":
            with _connect(config) as conn:
                conn.execute("UPDATE community_codes SET active=0 WHERE code=?", (args[2].upper(),))
            bot.reply_to(message, f"🔒 បិទកូដ {html.escape(args[2].upper())} ហើយ។", parse_mode="HTML")
            return
        try:
            code, points = args[1].upper(), int(args[2])
            max_uses = int(args[3]) if len(args) > 3 else 0
            if not (3 <= len(code) <= 24 and code.isalnum() and 1 <= points <= 1000 and max_uses >= 0):
                raise ValueError
        except (IndexError, ValueError):
            bot.reply_to(message, "របៀបប្រើ៖ <code>/setcode COOLER2026 50</code> (ពិន្ទុ 1-1000)\n"
                                  "កំណត់អ្នកទាមទារ៖ <code>/setcode COOLER2026 50 100</code> (100 នាក់ដំបូង)\n"
                                  "បិទ៖ <code>/setcode off COOLER2026</code>", parse_mode="HTML")
            return
        with _connect(config) as conn:
            conn.execute("""INSERT INTO community_codes(code, points, max_uses, active) VALUES (?, ?, ?, 1)
                ON CONFLICT(code) DO UPDATE SET points=excluded.points, max_uses=excluded.max_uses, active=1""",
                         (code, points, max_uses))
        limit = f" · {max_uses} នាក់ដំបូង" if max_uses else ""
        bot.reply_to(message, f"🎟️ កូដ <b>{code}</b> = +{points} ពិន្ទុ{limit}\nលាក់វាក្នុង MV ឬ Live ហើយឲ្យ fan វាយ <code>/code {code}</code> 🔍", parse_mode="HTML")

    @registered_message_handler(commands=["code"])
    def claim_code(message):
        user_id = message.from_user.id
        args = (message.text or "").split()
        if len(args) < 2:
            bot.reply_to(message, "🎟️ រកកូដសម្ងាត់ក្នុង MV ឬ Live របស់ Vid Cooler ហើយវាយ៖ <code>/code កូដ</code>", parse_mode="HTML")
            return
        code = args[1].strip().upper()
        with _connect(config) as conn:
            row = conn.execute("SELECT points, max_uses, active FROM community_codes WHERE code=?", (code,)).fetchone()
            if not row or not row["active"]:
                bot.reply_to(message, "❌ កូដនេះមិនត្រឹមត្រូវ ឬផុតកំណត់ហើយ។ មើល MV ម្ដងទៀត 👀")
                return
            used = conn.execute("SELECT COUNT(*) AS n FROM community_code_claims WHERE code=?", (code,)).fetchone()["n"]
            if row["max_uses"] and used >= row["max_uses"]:
                bot.reply_to(message, "😢 យឺតបន្តិច! កូដនេះមានអ្នកទាមទារគ្រប់ចំនួនហើយ។")
                return
            _ensure_user(conn, user_id)
            try:
                conn.execute("INSERT INTO community_code_claims(code, user_id) VALUES (?, ?)", (code, user_id))
            except sqlite3.IntegrityError:
                bot.reply_to(message, "✅ អ្នកបានប្រើកូដនេះរួចហើយ។")
                return
            conn.execute("UPDATE community_points SET points=points+? WHERE user_id=?", (row["points"], user_id))
            points, _ = _points(conn, user_id)
        bot.reply_to(message, f"🎉 កូដត្រឹមត្រូវ! +{row['points']} ពិន្ទុ\n⭐ ពិន្ទុសរុប៖ {points}\nអ្នកជា fan ទី {used + 1} ដែលរកឃើញ 🔥")

    # ---------- 🃏 Fan Card ----------
    @registered_message_handler(commands=["card"])
    def card(message):
        import fan_card
        user = message.from_user
        with _connect(config) as conn:
            _ensure_user(conn, user.id)
            points, vip = _points(conn, user.id)
            row = conn.execute("SELECT created_at FROM community_points WHERE user_id=?", (user.id,)).fetchone()
            fan_number = conn.execute(
                "SELECT COUNT(*) AS n FROM community_points WHERE created_at < ? OR (created_at = ? AND user_id <= ?)",
                (row["created_at"], row["created_at"], user.id),
            ).fetchone()["n"]
        level = _level(points, vip)
        rank_title = level.split(" ", 1)[-1]
        use_template = fan_card.has_template()
        avatar = None
        if not use_template:
            try:
                photos = bot.get_user_profile_photos(user.id, limit=1)
                if photos and photos.total_count:
                    file_info = bot.get_file(photos.photos[0][-1].file_id)
                    avatar = bot.download_file(file_info.file_path)
            except Exception:
                avatar = None
        footer = "Cooler Bot"
        try:
            me = bot.get_me()
            if getattr(me, "username", None):
                footer = "t.me/" + me.username
        except Exception:
            pass
        try:
            name = fan_card.latin_name(user.first_name, user.username)
            if use_template:
                image = fan_card.render_template(name, rank_title, points, fan_number, footer)
            else:
                image = fan_card.render(name, rank_title, points, fan_number, avatar=avatar, footer=footer)
        except Exception as exc:
            logger.warning("fan card failed (%s)", type(exc).__name__)
            bot.reply_to(message, "សុំទោស មិនអាចបង្កើតកាតបានទេឥឡូវនេះ។")
            return
        caption = (f"🃏 Fan Card របស់ {user.first_name or 'អ្នក'}\n{level} · ⭐ {points} ពិន្ទុ · Fan #{fan_number:04d}\n\n"
                   "📲 Share ទៅ Facebook / TikTok ហើយអញ្ជើញមិត្តតាម /invite ដើម្បីឡើង rank!")
        bot.send_photo(message.chat.id, image, caption=caption, reply_to_message_id=getattr(message, "message_id", None))

    @registered_message_handler(commands=["community"])
    def community_menu(message):
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("🎁 Check-in", callback_data="community:menu:checkin:0"),
            InlineKeyboardButton("🧠 Quiz", callback_data="community:menu:quiz:0"),
            InlineKeyboardButton("🏆 Leaderboard", callback_data="community:menu:top:0"),
            InlineKeyboardButton("⭐ Profile", callback_data="community:menu:profile:0"),
            InlineKeyboardButton("🤝 Invite", callback_data="community:menu:invite:0"),
            InlineKeyboardButton("👑 VIP", callback_data="community:menu:vip:0"),
            InlineKeyboardButton("🃏 Fan Card", callback_data="community:menu:card:0"),
        )
        bot.reply_to(message, "🌟 <b>សហគមន៍ VidCooler</b>\nជ្រើសរើសមុខងារដែលអ្នកចង់ប្រើ៖", reply_markup=markup, parse_mode="HTML")
