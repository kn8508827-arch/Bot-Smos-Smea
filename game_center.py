"""🎮 Game Center — instant games inside Telegram (animated dice), points and leaderboards.

  /play     menu: 🎲 dice duel vs Cooler Bot, 🎯 darts, 🏀 basketball, ⚽ football, 🎳 bowling, 🎰 slot
  /duel     in a group: challenge anyone — first to accept rolls against you
  /gametop  today's leaderboard (game points)

Telegram animates the throw and tells the bot the value immediately; the bot waits for the
animation (≈3.5 s, on a timer thread so workers are not blocked) before announcing the result.
Game points also add a little to community points (capped per day to stop farming).
"""
import html
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

logger = logging.getLogger(__name__)
TZ_KH = timezone(timedelta(hours=7))
ANIM_DELAY = float(os.getenv("GAME_ANIM_DELAY", "3.4"))
DAILY_BONUS_CAP = int(os.getenv("GAME_DAILY_BONUS_CAP", "30"))   # community points from games per day
COOLDOWN = 4.0

GAMES = {
    # key: (emoji, Khmer name, scorer(value) -> (game_points, Khmer verdict))
    "dice": ("🎲", "ឡុកឡាក់ប្រកួត Bot", None),
    "dart": ("🎯", "បាញ់ព្រួញ", lambda v: (10, "🎯 ចំកណ្ដាល BULLSEYE!") if v == 6 else ((4, "ជិតហើយ! 👏") if v >= 4 else (0, "ខុសគោលដៅ 😅"))),
    "ball": ("🏀", "បោះបាល់បោះ", lambda v: (8, "🏀 ចូល! SWISH!") if v == 5 else ((5, "ចូល! 🔥") if v == 4 else (0, "មិនចូល 😬"))),
    "foot": ("⚽", "ទាត់បាល់", lambda v: (8, "⚽ GOOOAL! 🔥") if v >= 4 else ((5, "ចូលទី! ⚽") if v == 3 else (0, "អ្នកចាំទីចាប់បាន 🧤"))),
    "bowl": ("🎳", "បោះប៊ូលីង", lambda v: (10, "🎳 STRIKE!") if v == 6 else ((4, "ដួលច្រើន! 👍") if v >= 4 else (1, "ដួលបន្តិច 😅"))),
    "slot": ("🎰", "ម៉ាស៊ីនសំណាង", lambda v: (50, "🎰 JACKPOT 777! 💰💰💰") if v == 64 else ((20, "🎉 ៣ ដូចគ្នា!") if v in (1, 22, 43) else (0, "សាកម្ដងទៀត 🍀"))),
}
_EMOJI = {k: g[0] for k, g in GAMES.items()}


def today():
    return datetime.now(TZ_KH).strftime("%Y-%m-%d")


def dice_duel(player, bot_value):
    """(points, verdict) for a dice duel."""
    if player > bot_value:
        return 6, "🏆 អ្នកឈ្នះ Cooler Bot!"
    if player == bot_value:
        return 2, "🤝 ស្មើ!"
    return 0, "😎 Cooler Bot ឈ្នះ! សាកម្ដងទៀត"


@contextmanager
def _connect(config):
    conn = sqlite3.connect(getattr(config, "BOT_DB_PATH", "bot_database.db"), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_schema(config):
    with _connect(config) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS game_scores (
            user_id INTEGER NOT NULL, day TEXT NOT NULL, name TEXT,
            points INTEGER NOT NULL DEFAULT 0, plays INTEGER NOT NULL DEFAULT 0, wins INTEGER NOT NULL DEFAULT 0,
            bonus INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (user_id, day))""")


def record(config, user, points, win):
    """Add a play; returns (today_points, community_bonus_given)."""
    day, uid = today(), user.id
    name = (getattr(user, "first_name", None) or getattr(user, "username", None) or str(uid))[:32]
    with _connect(config) as conn:
        conn.execute("INSERT OR IGNORE INTO game_scores(user_id, day, name) VALUES (?, ?, ?)", (uid, day, name))
        row = conn.execute("SELECT bonus FROM game_scores WHERE user_id=? AND day=?", (uid, day)).fetchone()
        bonus = max(0, min(points // 2, DAILY_BONUS_CAP - (row["bonus"] if row else 0)))
        conn.execute("UPDATE game_scores SET points=points+?, plays=plays+1, wins=wins+?, bonus=bonus+?, name=? "
                     "WHERE user_id=? AND day=?", (points, 1 if win else 0, bonus, name, uid, day))
        if bonus:
            try:
                conn.execute("INSERT OR IGNORE INTO community_points(user_id) VALUES (?)", (uid,))
                conn.execute("UPDATE community_points SET points=points+? WHERE user_id=?", (bonus, uid))
            except sqlite3.OperationalError:
                bonus = 0
        total = conn.execute("SELECT points FROM game_scores WHERE user_id=? AND day=?", (uid, day)).fetchone()["points"]
    return total, bonus


def leaderboard(config, limit=10, day=None):
    with _connect(config) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT user_id, name, points, plays, wins FROM game_scores WHERE day=? AND points>0 "
            "ORDER BY points DESC, wins DESC LIMIT ?", (day or today(), limit)).fetchall()]


def register(bot, config):
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    init_schema(config)
    last_play = {}
    duels = {}      # (chat_id, message_id) -> {"uid", "name", "expires"}
    lock = threading.Lock()

    def menu():
        m = InlineKeyboardMarkup()
        keys = list(GAMES)
        for i in range(0, len(keys), 3):
            m.row(*[InlineKeyboardButton(f"{GAMES[k][0]} {GAMES[k][1].split()[0]}", callback_data=f"gc:p:{k}") for k in keys[i:i + 3]])
        m.row(InlineKeyboardButton("⚔️ ប្រកួតមិត្ត (Group)", callback_data="gc:duel"),
              InlineKeyboardButton("🏆 Top ថ្ងៃនេះ", callback_data="gc:top"))
        m.row(InlineKeyboardButton("🕹️ ហ្គេមផ្សេងទៀត", callback_data="nav:games"),
              InlineKeyboardButton("🏠 ម៉ឺនុយមេ", callback_data="nav:main"))
        return m

    MENU_TEXT = ("🎮 <b>Game Center</b>\nលេងភ្លាមក្នុង Telegram — ចុចហ្គេម 👇\n\n"
                 "🎲 ឡុកឡាក់ប្រកួត Bot · 🎯 បាញ់ព្រួញ · 🏀 បាល់បោះ\n⚽ ទាត់បាល់ · 🎳 ប៊ូលីង · 🎰 ម៉ាស៊ីនសំណាង\n"
                 "⚔️ ប្រកួតជាមួយមិត្តក្នុង Group\n\n🏆 ប្រមូលពិន្ទុ ឡើង Top ប្រចាំថ្ងៃ!")

    def again(key):
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton(f"{GAMES[key][0]} លេងម្ដងទៀត", callback_data=f"gc:p:{key}"),
              InlineKeyboardButton("🎮 ហ្គេមផ្សេង", callback_data="gc:menu"))
        return m

    def later(fn, *args):
        t = threading.Timer(ANIM_DELAY, lambda: _safe(fn, *args))
        t.daemon = True
        t.start()

    def _safe(fn, *args):
        try:
            fn(*args)
        except Exception as exc:
            logger.warning("game center reply failed (%s)", type(exc).__name__)

    def play(chat_id, user, key, reply_to=None):
        now = time.monotonic()
        with lock:
            if now - last_play.get(user.id, 0) < COOLDOWN:
                return False
            last_play[user.id] = now
        name = html.escape(getattr(user, "first_name", None) or "អ្នកលេង")
        emoji, title, scorer = GAMES[key]
        msg = bot.send_dice(chat_id, emoji=emoji, reply_to_message_id=reply_to)
        value = msg.dice.value
        if key == "dice":
            bot_msg = bot.send_dice(chat_id, emoji="🎲")
            pts, verdict = dice_duel(value, bot_msg.dice.value)
            detail = f"{name} 🎲 <b>{value}</b>  vs  Cooler Bot 🎲 <b>{bot_msg.dice.value}</b>"
        else:
            pts, verdict = scorer(value)
            detail = f"{name} · {emoji} {title}"
        total, bonus = record(config, user, pts, pts > 0)

        def announce():
            extra = f" · ⭐ +{bonus} ពិន្ទុសហគមន៍" if bonus else ""
            bot.send_message(chat_id, f"{detail}\n<b>{verdict}</b>\n🎮 +{pts} · ថ្ងៃនេះ {total}{extra}",
                             parse_mode="HTML", reply_markup=again(key))
        later(announce)
        return True

    def top_text(chat_title=None):
        rows = leaderboard(config)
        if not rows:
            return "🏆 <b>Top ថ្ងៃនេះ</b>\n\nមិនទាន់មានអ្នកលេង — លេងមុនគេ ហើយឈរលេខ ១! 🚀"
        medals = ["🥇", "🥈", "🥉"] + [f"{i}." for i in range(4, 11)]
        lines = [f"{medals[i]} {html.escape(r['name'] or '?')} — <b>{r['points']}</b> (🏅{r['wins']}/{r['plays']})"
                 for i, r in enumerate(rows)]
        return "🏆 <b>Top Game ថ្ងៃនេះ</b>\n\n" + "\n".join(lines) + "\n\n<i>កំណត់ឡើងវិញរៀងរាល់ថ្ងៃ ម៉ោង ១២ យប់</i>"

    def duel_offer(chat, user, reply_to=None):
        if getattr(chat, "type", None) not in ("group", "supergroup"):
            bot.send_message(chat.id, "⚔️ ការប្រកួតមិត្តប្រើក្នុង <b>Group</b> — Add Bot ចូល Group ហើយវាយ /duel", parse_mode="HTML")
            return
        name = getattr(user, "first_name", None) or "អ្នកលេង"
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("⚔️ ទទួលការប្រកួត!", callback_data=f"gc:acc:{user.id}"))
        msg = bot.send_message(chat.id, f"⚔️ <b>{html.escape(name)}</b> ចង់ប្រកួតឡុកឡាក់ 🎲\nអ្នកណាហ៊ានទទួល? (២ នាទី)",
                               parse_mode="HTML", reply_markup=m, reply_to_message_id=reply_to)
        with lock:
            duels[(chat.id, msg.message_id)] = {"uid": user.id, "name": name, "user": user, "expires": time.monotonic() + 120}

    def duel_accept(call):
        key = (call.message.chat.id, call.message.message_id)
        with lock:
            d = duels.get(key)
            if not d or d["expires"] < time.monotonic():
                duels.pop(key, None)
                d = None
            elif d["uid"] == call.from_user.id:
                d = "self"
            else:
                duels.pop(key, None)
        if d is None:
            bot.answer_callback_query(call.id, "ការប្រកួតនេះផុតកំណត់ហើយ ⌛", show_alert=True)
            return
        if d == "self":
            bot.answer_callback_query(call.id, "រង់ចាំមិត្តម្នាក់ទទួល 😄", show_alert=True)
            return
        bot.answer_callback_query(call.id, "⚔️ ចាប់ផ្ដើម!")
        chat_id = call.message.chat.id
        a_name, b_name = d["name"], getattr(call.from_user, "first_name", None) or "អ្នកលេង"
        try:
            bot.edit_message_text(f"⚔️ <b>{html.escape(a_name)}</b> vs <b>{html.escape(b_name)}</b> 🎲🎲",
                                  chat_id, call.message.message_id, parse_mode="HTML")
        except Exception:
            pass
        va = bot.send_dice(chat_id, emoji="🎲").dice.value
        vb = bot.send_dice(chat_id, emoji="🎲").dice.value
        if va == vb:
            result, pa, pb = "🤝 ស្មើ! ម្នាក់ +2", 2, 2
        elif va > vb:
            result, pa, pb = f"🏆 {html.escape(a_name)} ឈ្នះ! +8", 8, 0
        else:
            result, pa, pb = f"🏆 {html.escape(b_name)} ឈ្នះ! +8", 0, 8
        record(config, d["user"], pa, pa > pb)
        record(config, call.from_user, pb, pb > pa)

        def announce():
            m = InlineKeyboardMarkup()
            m.row(InlineKeyboardButton("⚔️ ប្រកួតម្ដងទៀត", callback_data="gc:duel"), InlineKeyboardButton("🏆 Top", callback_data="gc:top"))
            bot.send_message(chat_id, f"⚔️ {html.escape(a_name)} 🎲 <b>{va}</b>  vs  {html.escape(b_name)} 🎲 <b>{vb}</b>\n<b>{result}</b>",
                             parse_mode="HTML", reply_markup=m)
        later(announce)

    @bot.message_handler(commands=["play", "gamecenter", "arcade_bot"])
    def play_cmd(message):
        bot.reply_to(message, MENU_TEXT, reply_markup=menu(), parse_mode="HTML")

    @bot.message_handler(commands=["dice", "dart", "basket", "football", "bowling", "slot"])
    def quick_cmd(message):
        cmd = (message.text or "").split()[0][1:].split("@")[0].lower()
        key = {"dice": "dice", "dart": "dart", "basket": "ball", "football": "foot", "bowling": "bowl", "slot": "slot"}[cmd]
        if not play(message.chat.id, message.from_user, key, reply_to=message.message_id):
            bot.reply_to(message, "⏳ រង់ចាំបន្តិច...")

    @bot.message_handler(commands=["duel"])
    def duel_cmd(message):
        duel_offer(message.chat, message.from_user, reply_to=message.message_id)

    @bot.message_handler(commands=["gametop", "gtop"])
    def top_cmd(message):
        bot.reply_to(message, top_text(), parse_mode="HTML", reply_markup=menu())

    @bot.callback_query_handler(func=lambda call: (call.data or "").startswith("gc:"))
    def gc_cb(call):
        parts = (call.data or "").split(":")
        action = parts[1] if len(parts) > 1 else "menu"
        message = getattr(call, "message", None)
        if message is None:
            bot.answer_callback_query(call.id)
            return
        if action == "acc":
            duel_accept(call)
            return
        if action == "p" and len(parts) > 2 and parts[2] in GAMES:
            ok = play(message.chat.id, call.from_user, parts[2])
            bot.answer_callback_query(call.id, None if ok else "⏳ រង់ចាំបន្តិច...")
            return
        bot.answer_callback_query(call.id)
        if action == "duel":
            duel_offer(message.chat, call.from_user)
        elif action == "top":
            bot.send_message(message.chat.id, top_text(), parse_mode="HTML", reply_markup=menu())
        else:
            try:
                bot.edit_message_text(MENU_TEXT, message.chat.id, message.message_id, reply_markup=menu(), parse_mode="HTML")
            except Exception as exc:
                if "message is not modified" not in str(exc):
                    bot.send_message(message.chat.id, MENU_TEXT, reply_markup=menu(), parse_mode="HTML")

    return SimpleNamespace(play=play, duels=duels, menu=menu, top_text=top_text)
