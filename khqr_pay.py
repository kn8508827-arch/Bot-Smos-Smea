"""💳 Automatic KHQR payments (Bakong Open API).

When BAKONG_API_TOKEN and BAKONG_ACCOUNT_ID are set, /pay shows a DYNAMIC KHQR with the exact
amount. A background loop asks Bakong (check_transaction_by_md5) every few seconds; when the
payment arrives the membership is switched on at once — no receipt, no owner approval.
Without those variables (or if Bakong can't be reached) the old flow stays: static QR + receipt
photo + owner ✅ Approve.

Variables:
  BAKONG_API_TOKEN       token from https://api-bakong.nbc.gov.kh/register (renew every 90 days)
  BAKONG_ACCOUNT_ID      your Bakong ID, e.g. name@aba / name@bkrt (Bakong app → profile)
  BAKONG_ACCOUNT_INFO    optional sub-field 01 (account number) — some banks need it
  BAKONG_ACQUIRING_BANK  optional sub-field 02 (bank name), e.g. "ABA Bank"
  BAKONG_MERCHANT_NAME   name shown in the banking app (default "Cooler Bot")
  BAKONG_API_BASE_URL    default https://api-bakong.nbc.gov.kh (set a relay here if Bakong only
                         accepts Cambodian IP addresses)
  BAKONG_DAILY_LIMIT     requests per day the bot may use (default 95; Bakong allows 100). Bills are checked
                         on a schedule and share one bulk request, so ~10 payments a day fit easily.
Owner: /khqrtest — makes a 100 riel bill to test the whole flow.
"""
import hashlib
import html
import io
import logging
import os
import sqlite3
import threading
import time

import httpx

logger = logging.getLogger(__name__)

BILL_MINUTES = 15
# Bakong Open API allows only ~100 requests a day, so each bill is checked on a schedule
# (seconds after it was made) and all due bills share ONE bulk request.
CHECK_SCHEDULE = (25, 50, 90, 150, 240, 360, 540, 780, 1080)


# ---------------- KHQR (EMVCo) ----------------

def tlv(tag, value):
    value = str(value)
    if len(value) > 99:
        raise ValueError(f"tag {tag} too long")
    return f"{tag}{len(value):02d}{value}"


def crc16(data):
    """CRC-16/CCITT-FALSE as required by EMVCo / KHQR."""
    crc = 0xFFFF
    for ch in data.encode("utf-8"):
        crc ^= ch << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return f"{crc:04X}"


def fmt_amount(amount, currency):
    if currency == "KHR":
        return str(int(round(float(amount))))
    return f"{float(amount):.2f}".rstrip("0").rstrip(".")


def build_khqr(account_id, amount, currency="USD", merchant_name="Cooler Bot", city="Phnom Penh",
               bill_number=None, account_info=None, bank=None, now_ms=None, expire_minutes=BILL_MINUTES):
    if not account_id or "@" not in account_id:
        raise ValueError("BAKONG_ACCOUNT_ID must look like name@bank")
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    acc = tlv("00", account_id)
    if account_info:
        acc += tlv("01", account_info)
    if bank:
        acc += tlv("02", bank)
    data = tlv("00", "01") + tlv("01", "12") + tlv("29", acc) + tlv("52", "5999")
    data += tlv("53", "116" if currency == "KHR" else "840")
    data += tlv("54", fmt_amount(amount, currency))
    data += tlv("58", "KH") + tlv("59", merchant_name[:25]) + tlv("60", city[:15])
    if bill_number:
        data += tlv("62", tlv("01", str(bill_number)[:25]))
    data += tlv("99", tlv("00", str(now_ms)) + tlv("01", str(now_ms + expire_minutes * 60000)))
    data += "6304"
    return data + crc16(data)


def md5_of(qr):
    return hashlib.md5(qr.encode("utf-8")).hexdigest()


def qr_png(text):
    import qrcode
    img = qrcode.make(text, box_size=10, border=3)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    buf.name = "khqr.png"
    return buf


# ---------------- Bakong API ----------------

def settings():
    e = os.getenv
    return {
        "token": e("BAKONG_API_TOKEN", "").strip(),
        "account": e("BAKONG_ACCOUNT_ID", "").strip(),
        "info": e("BAKONG_ACCOUNT_INFO", "").strip() or None,
        "bank": e("BAKONG_ACQUIRING_BANK", "").strip() or None,
        "name": e("BAKONG_MERCHANT_NAME", "Cooler Bot").strip() or "Cooler Bot",
        "base": (e("BAKONG_API_BASE_URL", "") or "https://api-bakong.nbc.gov.kh").strip().rstrip("/"),
    }


def enabled():
    s = settings()
    return bool(s["token"] and s["account"])


def check_md5(md5, client=None):
    """'PAID' | 'UNPAID'; raises on network / auth problems."""
    s = settings()
    base = s["base"] if s["base"].endswith("/v1") else s["base"] + "/v1"
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(12.0, connect=6.0))
    try:
        r = client.post(f"{base}/check_transaction_by_md5", json={"md5": md5},
                        headers={"Authorization": f"Bearer {s['token']}", "Content-Type": "application/json"})
    finally:
        if own:
            client.close()
    if r.status_code in (401, 403):
        raise PermissionError(f"Bakong HTTP {r.status_code} — check BAKONG_API_TOKEN / server IP")
    try:
        body = r.json()
    except ValueError:
        raise RuntimeError(f"Bakong HTTP {r.status_code}: not JSON")
    if body.get("responseCode") == 0 and (r.status_code // 100 == 2):
        return "PAID", body.get("data") or {}
    return "UNPAID", body


def check_md5_list(md5s, client=None):
    """Set of md5s Bakong reports as paid (one request for up to 50 bills)."""
    s = settings()
    base = s["base"] if s["base"].endswith("/v1") else s["base"] + "/v1"
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(12.0, connect=6.0))
    try:
        r = client.post(f"{base}/check_transaction_by_md5_list", json=list(md5s)[:50],
                        headers={"Authorization": f"Bearer {s['token']}", "Content-Type": "application/json"})
    finally:
        if own:
            client.close()
    if r.status_code in (401, 403):
        raise PermissionError(f"Bakong HTTP {r.status_code} — check BAKONG_API_TOKEN / server IP")
    if r.status_code == 429:
        raise RuntimeError("Bakong daily limit reached (HTTP 429)")
    try:
        body = r.json()
    except ValueError:
        raise RuntimeError(f"Bakong HTTP {r.status_code}: not JSON")
    paid = set()
    for item in body.get("data") or []:
        if isinstance(item, dict) and str(item.get("status", "")).upper() in ("SUCCESS", "PAID") and item.get("md5"):
            paid.add(item["md5"])
    return paid


def daily_limit():
    try:
        return int(os.getenv("BAKONG_DAILY_LIMIT", "95"))
    except ValueError:
        return 95


# ---------------- storage ----------------

def _db(config):
    conn = sqlite3.connect(getattr(config, "BOT_DB_PATH", "bot_database.db"), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(config):
    conn = _db(config)
    try:
        with conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS khqr_bills (
                md5 TEXT PRIMARY KEY, kind TEXT NOT NULL, target_id INTEGER NOT NULL, payer_id INTEGER NOT NULL,
                payer_name TEXT, amount TEXT NOT NULL, currency TEXT NOT NULL, chat_id INTEGER, message_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending', created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
                test INTEGER NOT NULL DEFAULT 0)""")
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(khqr_bills)").fetchall()}
            if "checks" not in cols:
                conn.execute("ALTER TABLE khqr_bills ADD COLUMN checks INTEGER NOT NULL DEFAULT 0")
            conn.execute("CREATE TABLE IF NOT EXISTS bakong_usage (day TEXT PRIMARY KEY, n INTEGER NOT NULL)")
    finally:
        conn.close()


def _today():
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 7 * 3600))


def used_today(config):
    conn = _db(config)
    try:
        row = conn.execute("SELECT n FROM bakong_usage WHERE day=?", (_today(),)).fetchone()
        return row["n"] if row else 0
    finally:
        conn.close()


def take_request(config):
    """Count one Bakong request; False when today's budget is used up."""
    conn = _db(config)
    try:
        with conn:
            row = conn.execute("SELECT n FROM bakong_usage WHERE day=?", (_today(),)).fetchone()
            if row and row["n"] >= daily_limit():
                return False
            conn.execute("INSERT INTO bakong_usage(day, n) VALUES (?, 1) ON CONFLICT(day) DO UPDATE SET n=n+1", (_today(),))
            conn.execute("DELETE FROM bakong_usage WHERE day < ?", (time.strftime("%Y-%m-%d", time.gmtime(time.time() - 7 * 86400)),))
        return True
    finally:
        conn.close()


def register(bot, config):
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup
    import subscription as sub

    init_schema(config)
    lock = threading.Lock()
    last_error = {"text": None, "at": 0}

    def privileged(uid):
        return uid == getattr(config, "OWNER_ID", 0) or uid in getattr(config, "ADMIN_IDS", [])

    def new_bill(chat_id, payer, kind, target_id, amount, currency="USD", test=False, title=None):
        """Send a dynamic KHQR; returns the md5 or None (then the caller falls back to the old flow)."""
        s = settings()
        now = int(time.time())
        bill_no = f"CB{now % 100000000}"
        try:
            qr = build_khqr(s["account"], amount, currency, s["name"], bill_number=bill_no,
                            account_info=s["info"], bank=s["bank"])
            img = qr_png(qr)
        except Exception as exc:
            logger.warning("KHQR build failed (%s: %s)", type(exc).__name__, exc)
            return None
        md5 = md5_of(qr)
        what = (f"👥 Group <b>{html.escape(title or str(target_id))}</b>" if kind == "g"
                else "➕ សិទ្ធិដាក់ Bot ចូល Group ១" if kind == "c"
                else "👑 VIP" if kind == "v"
                else "🧪 តេស្ត KHQR" if test else "👤 សមាជិកម្នាក់")
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("✅ ខ្ញុំបង់រួចហើយ — ពិនិត្យឥឡូវ", callback_data=f"khqr:chk:{md5[:16]}"))
        m.row(InlineKeyboardButton("🧾 ផ្ញើបង្កាន់ដៃជំនួស", callback_data=f"khqr:manual:{kind}"))
        caption = (f"💳 <b>បង់ {html.escape(fmt_amount(amount, currency))} {currency}</b> — {what}\n\n"
                   "1️⃣ បើក ABA / ACLEDA / Wing / Bakong ... ហើយ Scan KHQR នេះ\n"
                   "2️⃣ បង់ — <b>Bot បើកដំណើរការឲ្យដោយស្វ័យប្រវត្តិ</b> ក្នុងប៉ុន្មានវិនាទី ⚡\n"
                   f"⏳ QR នេះប្រើបាន {BILL_MINUTES} នាទី · លេខវិក្កយបត្រ {bill_no}")
        msg = bot.send_photo(chat_id, img, caption=caption, reply_markup=m, parse_mode="HTML")
        conn = _db(config)
        try:
            with conn:
                conn.execute("INSERT INTO khqr_bills(md5, kind, target_id, payer_id, payer_name, amount, currency, chat_id, "
                             "message_id, created_at, expires_at, test) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                             (md5, kind, target_id, payer, title, fmt_amount(amount, currency), currency, chat_id,
                              getattr(msg, "message_id", None), now, now + BILL_MINUTES * 60 + 300, 1 if test else 0))
        finally:
            conn.close()
        return md5

    def settle(row, data):
        """Mark a bill paid exactly once and switch the membership on."""
        conn = _db(config)
        try:
            with conn:
                won = conn.execute("UPDATE khqr_bills SET status='paid' WHERE md5=? AND status='pending'",
                                   (row["md5"],)).rowcount
        finally:
            conn.close()
        if not won:
            return False
        owner = getattr(config, "OWNER_ID", 0)
        if row["test"]:
            bot.send_message(row["chat_id"], "✅ <b>KHQR ស្វ័យប្រវត្តិដំណើរការ!</b> Bakong បានបញ្ជាក់ការបង់ 🎉\n"
                                             f"<code>{html.escape(str(data)[:300])}</code>", parse_mode="HTML")
            return True
        days = sub.settings().days
        until = sub.extend(config, row["kind"], row["target_id"], days)
        with sub._connect(config) as c:
            c.execute("INSERT INTO sub_payments(kind, target_id, payer_id, payer_name, file_id, status, created_at, decided_at) "
                      "VALUES (?, ?, ?, ?, ?, 'approved', ?, ?)",
                      (row["kind"], row["target_id"], row["payer_id"], row["payer_name"], "khqr:" + row["md5"],
                       row["created_at"], int(time.time())))
        try:
            if row["kind"] == "c":   # group pass: until is the payer's credit total
                notice = getattr(bot, "_cooler_credit_notice", None)
                if notice is not None:
                    notice(row["payer_id"])
            elif row["kind"] == "g":
                bot.send_message(row["chat_id"], f"🎉 បង់រួចរាល់! Group បើកដំណើរការរហូតដល់ <b>{sub.fmt_date(until)}</b> ✅",
                                 parse_mode="HTML")
                bot.send_message(row["target_id"], f"🎉 Group នេះបានបង់រួចហើយ! Cooler Bot ធ្វើការរហូតដល់ "
                                                   f"<b>{sub.fmt_date(until)}</b> 🙏❤️", parse_mode="HTML")
            else:
                bot.send_message(row["chat_id"], f"🎉 <b>បង់រួចរាល់ — អរគុណដែលគាំទ្រ Vid Cooler!</b>\n"
                                                 f"✅ អ្នកជាសមាជិករហូតដល់ <b>{sub.fmt_date(until)}</b>\nវាយ /menu ដើម្បីចាប់ផ្ដើម 🚀",
                                 parse_mode="HTML")
        except Exception as exc:
            logger.warning("KHQR paid notice failed (%s)", type(exc).__name__)
        if owner:
            try:
                bot.send_message(owner, f"💰 <b>KHQR ចូលស្វ័យប្រវត្តិ</b> {html.escape(row['amount'])} {row['currency']} · "
                                        f"{'👥' if row['kind'] == 'g' else '👤'} <code>{row['target_id']}</code> "
                                        f"(payer <code>{row['payer_id']}</code>) → "
                                        f"{'សិទ្ធិ Group ' + str(until) if row['kind'] == 'c' else sub.fmt_date(until)}",
                                 parse_mode="HTML")
            except Exception:
                pass
        try:
            import ai_extras  # 🎁 3 paying friends = 7 free days for the inviter
            ai_extras.referral_paid(bot, config, row["payer_id"])
        except Exception:
            pass
        return True

    budget_note = {"day": None}
    tapped = {}   # md5 -> last manual check (monotonic)

    def out_of_budget():
        if budget_note["day"] != _today():
            budget_note["day"] = _today()
            owner = getattr(config, "OWNER_ID", 0)
            logger.warning("Bakong daily request budget used up")
            if owner:
                try:
                    bot.send_message(owner, f"⚠️ Bakong API ប្រើអស់ {daily_limit()} ដង/ថ្ងៃហើយ — ថ្ងៃនេះ KHQR ត្រូវ Approve "
                                            "តាមបង្កាន់ដៃ (ស្អែកដំណើរការវិញ)")
                except Exception:
                    pass
        return False

    def check_row(row):
        if not take_request(config):
            return out_of_budget()
        status, data = check_md5(row["md5"])
        if status == "PAID":
            return settle(row, data)
        return False

    def pending_rows(now):
        conn = _db(config)
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM khqr_bills WHERE status='pending' AND expires_at > ?",
                                                  (now,)).fetchall()]
        finally:
            conn.close()

    def tick(now=None):
        now = int(now or time.time())
        conn = _db(config)
        try:
            with conn:
                conn.execute("UPDATE khqr_bills SET status='expired' WHERE status='pending' AND expires_at <= ?", (now,))
        finally:
            conn.close()
        due = [r for r in pending_rows(now) if (r.get("checks") or 0) < len(CHECK_SCHEDULE)
               and now >= r["created_at"] + CHECK_SCHEDULE[r.get("checks") or 0]]
        if not due:
            return 0
        due = due[:50]
        conn = _db(config)
        try:
            with conn:
                conn.executemany("UPDATE khqr_bills SET checks=checks+1 WHERE md5=?", [(r["md5"],) for r in due])
        finally:
            conn.close()
        if not take_request(config):
            out_of_budget()
            return 0
        try:
            paid_md5 = check_md5_list([r["md5"] for r in due])
        except Exception as exc:
            text = f"{type(exc).__name__}: {str(exc)[:200]}"
            if last_error["text"] != text or time.time() - last_error["at"] > 3600:
                logger.warning("KHQR check failed (%s)", text)
                last_error.update(text=text, at=time.time())
            return 0
        return sum(1 for r in due if r["md5"] in paid_md5 and settle(r, {"md5": r["md5"]}))

    def start_loop(interval=5):
        def run():
            time.sleep(10)
            while True:
                try:
                    if enabled():
                        tick()
                except Exception as exc:
                    logger.warning("KHQR loop error (%s)", type(exc).__name__)
                time.sleep(interval)
        threading.Thread(target=run, name="khqr-loop", daemon=True).start()

    @bot.callback_query_handler(func=lambda c: (c.data or "").startswith("khqr:"))
    def khqr_cb(call):
        parts = (call.data or "").split(":")
        if parts[1] == "chk" and len(parts) == 3:
            rows = [r for r in pending_rows(int(time.time())) if r["md5"].startswith(parts[2])]
            if not rows:
                conn = _db(config)
                try:
                    st = conn.execute("SELECT status FROM khqr_bills WHERE md5 LIKE ?", (parts[2] + "%",)).fetchone()
                finally:
                    conn.close()
                msg = ("✅ ការបង់នេះបានបញ្ជាក់រួចហើយ" if st and st["status"] == "paid"
                       else "⌛ QR នេះផុតកំណត់ហើយ — វាយ /pay ម្ដងទៀត")
                bot.answer_callback_query(call.id, msg, show_alert=True)
                return
            if rows[0]["md5"] in tapped and time.monotonic() - tapped[rows[0]["md5"]] < 30:
                bot.answer_callback_query(call.id, "⏳ ទើបពិនិត្យមិញ — Bot កំពុងពិនិត្យដោយស្វ័យប្រវត្តិ រង់ចាំ ៣០ វិនាទី",
                                          show_alert=True)
                return
            tapped[rows[0]["md5"]] = time.monotonic()
            try:
                ok = check_row(rows[0])
            except Exception as exc:
                note = f"\n{str(exc)[:150]}" if privileged(call.from_user.id) else ""
                bot.answer_callback_query(call.id, "😔 ពិនិត្យមិនបានឥឡូវ — សូមផ្ញើរូបបង្កាន់ដៃជំនួស" + note,
                                          show_alert=True)
                return
            bot.answer_callback_query(call.id, "🎉 បានទទួល!" if ok else
                                      "⏳ មិនទាន់ឃើញលុយចូលទេ — រង់ចាំ ១០-៣០ វិនាទី រួចចុចម្ដងទៀត", show_alert=not ok)
            return
        if parts[1] == "manual":
            bot.answer_callback_query(call.id)
            kind = parts[2] if len(parts) > 2 else "u"
            sub_waiting = getattr(bot, "_cooler_sub_waiting", None)
            if sub_waiting is not None:
                sub_waiting[call.from_user.id] = (kind if kind in ("u", "g", "c", "v") else "u", call.from_user.id,
                                                  time.monotonic() + 3600)
            bot.send_message(call.message.chat.id, "🧾 ថតរូប / Screenshot បង្កាន់ដៃ ហើយផ្ញើមកទីនេះ — Owner នឹង Approve 🙏")
            return
        bot.answer_callback_query(call.id)

    @bot.message_handler(commands=["khqrtest"], func=lambda m: privileged(getattr(m.from_user, "id", None)))
    def khqr_test(message):
        s = settings()
        if not enabled():
            bot.reply_to(message, "⚙️ <b>KHQR ស្វ័យប្រវត្តិ មិនទាន់កំណត់</b>\nដាក់ក្នុង Railway Variables៖\n"
                                  "• <code>BAKONG_API_TOKEN</code> (ពី api-bakong.nbc.gov.kh/register)\n"
                                  "• <code>BAKONG_ACCOUNT_ID</code> (ឧ. name@aba — មើលក្នុង Bakong App)", parse_mode="HTML")
            return
        try:
            if not take_request(config):
                raise RuntimeError(f"ថ្ងៃនេះប្រើអស់ {daily_limit()} ដងហើយ")
            status, _ = check_md5("0" * 32)
            reach = (f"✅ Bakong API ឆ្លើយ (test md5 → {status})\n"
                     f"📊 ប្រើថ្ងៃនេះ {used_today(config)}/{daily_limit()} ដង")
        except Exception as exc:
            reach = f"❌ ភ្ជាប់ Bakong មិនបាន៖ <code>{html.escape(str(exc)[:200])}</code>"
        bot.reply_to(message, f"🧪 <b>KHQR តេស្ត</b>\nគណនី៖ <code>{html.escape(s['account'])}</code>\n{reach}\n\n"
                              "👇 Scan ហើយបង់ ១០០ រៀល ដើម្បីសាកការបញ្ជាក់ស្វ័យប្រវត្តិ", parse_mode="HTML")
        new_bill(message.chat.id, message.from_user.id, "u", message.from_user.id, 100, "KHR", test=True)

    class Api:
        pass
    api = Api()
    api.new_bill = new_bill
    api.tick = tick
    api.start_loop = start_loop
    api.settle = settle
    return api
