"""Market info for friends (Khmer UI): exchange rates, gold price, latest news.

  /rate [amount]   US dollar ↔ riel (and THB, VND, CNY, KRW, EUR); /rate 100 or /rate 400000៛
  /gold            world gold price per ជី / ដំឡឹង / gram, in USD and riel
  /news            latest Cambodian headlines by topic (Google News RSS, Khmer)

Free sources with no API key, several fallbacks each, cached so Telegram users do not
hammer them. Gold and exchange prices are market references; shops in Cambodia add
their own margin, which the messages say.
"""
import html
import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

TZ_KH = timezone(timedelta(hours=7))
TROY_OUNCE_G = 31.1034768
CHI_G = 3.75        # 1 ជី
DAMLUNG_G = 37.5    # 1 ដំឡឹង = 10 ជី
TIMEOUT = 12.0
HEADERS = {"User-Agent": "Mozilla/5.0 (CoolerBot; +https://t.me/TeamSmosSneabot)"}

RATE_URLS = (
    "https://open.er-api.com/v6/latest/USD",
    "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json",
    "https://latest.currency-api.pages.dev/v1/currencies/usd.json",
)
GOLD_URLS = (
    "https://api.gold-api.com/price/XAU",
    "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/xau.json",
    "https://latest.currency-api.pages.dev/v1/currencies/xau.json",
)
SHOWN_CURRENCIES = (
    ("THB", "🇹🇭 បាតថៃ"), ("VND", "🇻🇳 ដុងវៀតណាម"), ("CNY", "🇨🇳 យ័នចិន"),
    ("KRW", "🇰🇷 វ៉ុនកូរ៉េ"), ("JPY", "🇯🇵 យ៉េនជប៉ុន"), ("EUR", "🇪🇺 អឺរ៉ូ"),
)

NEWS_TOPICS = {
    # key: (label, Google News search query or "" for top stories)
    "top": ("🇰🇭 ថ្មីៗ", ""),
    "economy": ("💰 សេដ្ឋកិច្ច", "សេដ្ឋកិច្ច កម្ពុជា"),
    "tech": ("💻 បច្ចេកវិទ្យា", "បច្ចេកវិទ្យា AI"),
    "sport": ("⚽ កីឡា", "កីឡា"),
    "ent": ("🎬 កម្សាន្ត", "តារា ចម្រៀង ភាពយន្ត"),
}
NEWS_TOP_URL = "https://news.google.com/rss?hl=km&gl=KH&ceid=KH:km"
NEWS_SEARCH_URL = "https://news.google.com/rss/search?q={q}+when:2d&hl=km&gl=KH&ceid=KH:km"

_cache = {}
_lock = threading.Lock()


def _cached(key, ttl, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = fn()
    if value:
        with _lock:
            _cache[key] = (now, value)
        return value
    # keep serving the last good value (up to 12 h) if every source failed
    if hit and now - hit[0] < 12 * 3600:
        return hit[1]
    return None


def _get(url, client=None):
    c = client or httpx.Client(timeout=TIMEOUT, headers=HEADERS, follow_redirects=True)
    try:
        r = c.get(url)
        r.raise_for_status()
        return r
    finally:
        if client is None:
            c.close()


# ---------------- exchange rates ----------------

def parse_rates(data):
    """Rates per 1 USD as {"KHR": 4010.0, ...} from either supported JSON shape."""
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("rates"), dict):  # open.er-api.com
        rates = {k.upper(): float(v) for k, v in data["rates"].items() if isinstance(v, (int, float))}
        date = data.get("time_last_update_utc", "")
    elif isinstance(data.get("usd"), dict):  # fawazahmed0 currency-api
        rates = {k.upper(): float(v) for k, v in data["usd"].items() if isinstance(v, (int, float))}
        date = data.get("date", "")
    else:
        return None
    if not 3000 <= rates.get("KHR", 0) <= 6000:  # sanity check for the riel
        return None
    return {"rates": rates, "date": str(date)[:25]}


def fetch_rates(client=None):
    def load():
        for url in RATE_URLS:
            try:
                parsed = parse_rates(_get(url, client).json())
                if parsed:
                    return parsed
            except Exception as exc:
                logger.warning("rate source failed %s (%s)", url.split("/")[2], type(exc).__name__)
        return None
    return _cached("rates", 1800, load)


# ---------------- gold ----------------

def parse_gold(data):
    """USD per troy ounce from either supported JSON shape."""
    if not isinstance(data, dict):
        return None
    price = None
    if isinstance(data.get("price"), (int, float)):  # gold-api.com
        price = float(data["price"])
    elif isinstance(data.get("xau"), dict) and isinstance(data["xau"].get("usd"), (int, float)):
        price = float(data["xau"]["usd"])
    if price is None or not 500 <= price <= 20000:
        return None
    return price


def fetch_gold(client=None):
    def load():
        for url in GOLD_URLS:
            try:
                price = parse_gold(_get(url, client).json())
                if price:
                    return {"oz": price, "at": datetime.now(TZ_KH).strftime("%d/%m/%Y %H:%M")}
            except Exception as exc:
                logger.warning("gold source failed %s (%s)", url.split("/")[2], type(exc).__name__)
        return None
    return _cached("gold", 1800, load)


def gold_units(oz_usd):
    per_g = oz_usd / TROY_OUNCE_G
    return {"gram": per_g, "chi": per_g * CHI_G, "damlung": per_g * DAMLUNG_G}


# ---------------- news ----------------

def parse_news(xml_text, limit=8):
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        source = (item.findtext("source") or "").strip()
        if source and title.endswith(" - " + source):
            title = title[: -len(" - " + source)].strip()
        when = ""
        try:
            when = parsedate_to_datetime(item.findtext("pubDate") or "").astimezone(TZ_KH).strftime("%d/%m %H:%M")
        except Exception:
            pass
        if title and link.startswith("http"):
            items.append({"title": title, "link": link, "source": source, "when": when})
        if len(items) >= limit:
            break
    return items


NEWS_EN_SEARCH_URL = "https://news.google.com/rss/search?q={q}&hl=en-KH&gl=KH&ceid=KH:en"
NEWS_EN_TOPIC = {"top": "Cambodia", "economy": "Cambodia economy", "tech": "technology AI",
                 "sport": "Cambodia sport", "ent": "Cambodia entertainment"}
NEWS_KEYWORDS = {   # filter for the Khmer site feeds (NEWS_FEEDS) per topic
    "economy": ("សេដ្ឋកិច្ច", "ពាណិជ្ជកម្ម", "វិនិយោគ", "ធនាគារ", "economy", "trade", "bank", "invest"),
    "tech": ("បច្ចេកវិទ្យា", "ឌីជីថល", "AI", "tech", "digital", "app"),
    "sport": ("កីឡា", "បាល់", "ប្រដាល់", "sport", "football", "SEA Games"),
    "ent": ("តារា", "ចម្រៀង", "ភាពយន្ត", "កុន", "concert", "film", "music", "star"),
}


def _news_sources(topic, query):
    """Tried in order until one gives stories: Google News Khmer (2 days → 7 days), the Khmer news sites
    (NEWS_FEEDS), Google News in English. Datacenter IPs sometimes get blocked by one source, not all."""
    yield "google-km", (NEWS_SEARCH_URL.format(q=quote(query)) if query else NEWS_TOP_URL)
    if query:
        yield "google-km-7d", NEWS_SEARCH_URL.replace("when:2d", "when:7d").format(q=quote(query))
    yield "sites", None
    yield "google-en", NEWS_EN_SEARCH_URL.format(q=quote(NEWS_EN_TOPIC.get(topic, "Cambodia")))


def _site_stories(topic, client=None):
    import news_brief
    items = []
    for url in news_brief.feeds():
        try:
            items += news_brief.parse_feed(_get(url, client).text, news_brief._host(url), limit=15)
        except Exception as exc:
            logger.info("news site %s failed (%s)", news_brief._host(url), type(exc).__name__)
    words = NEWS_KEYWORDS.get(topic)
    if words:
        items = [i for i in items if any(w.lower() in i["title"].lower() for w in words)]
    return [{"title": i["title"], "link": i["link"], "source": i["source"], "when": ""} for i in items[:8]]


def fetch_news(topic="top", client=None):
    label, query = NEWS_TOPICS.get(topic, NEWS_TOPICS["top"])

    def load():
        for name, url in _news_sources(topic, query):
            try:
                items = _site_stories(topic, client) if url is None else parse_news(_get(url, client).text)
            except Exception as exc:
                logger.warning("news source %s failed (%s: %s)", name, type(exc).__name__, str(exc)[:120])
                continue
            if items:
                return items
            logger.info("news source %s gave no stories", name)
        return None
    return _cached("news:" + topic, 900, load)


# ---------------- text ----------------

def _n(value, digits=0):
    return f"{value:,.{digits}f}"


def parse_amount(text):
    """'/rate 100' → (100, 'USD'); '400000៛' / '400000 riel' / '400k r' → (400000, 'KHR')."""
    t = (text or "").strip().lower().replace(",", "")
    m = re.match(r"^\$?\s*(\d+(?:\.\d+)?)\s*(k)?\s*(\$|usd|ដុល្លារ|៛|r|riel|khr|រៀល)?$", t)
    if not m:
        return None
    amount = float(m.group(1)) * (1000 if m.group(2) else 1)
    unit = m.group(3) or ""
    return amount, ("KHR" if unit in ("៛", "r", "riel", "khr", "រៀល") else "USD")


def rates_text(data, amount=None):
    r = data["rates"]
    khr = r["KHR"]
    lines = ["💵 <b>អត្រាប្ដូរប្រាក់ថ្ងៃនេះ</b>", "", f"🇺🇸 1 ដុល្លារ = <b>{_n(khr)} ៛</b>"]
    if amount:
        value, unit = amount
        if unit == "USD":
            lines.append(f"🧮 {_n(value, 2).rstrip('0').rstrip('.')} $ = <b>{_n(value * khr)} ៛</b>")
        else:
            lines.append(f"🧮 {_n(value)} ៛ = <b>{_n(value / khr, 2)} $</b>")
    lines.append("")
    for code, label in SHOWN_CURRENCIES:
        if r.get(code):
            per_unit_khr = khr / r[code]
            lines.append(f"{label}: 1 $ = {_n(r[code], 2)} · 1 {code} ≈ {_n(per_unit_khr, 2 if per_unit_khr < 10 else 0)} ៛")
    lines += ["", f"🕒 {html.escape(data.get('date') or '')}",
              "ℹ️ អត្រាទីផ្សារអន្តរជាតិ — ហាងប្ដូរប្រាក់អាចខុសបន្តិច",
              "💡 គណនា៖ <code>/rate 100</code> ឬ <code>/rate 400000៛</code>"]
    return "\n".join(lines)


def gold_text(gold, rates=None):
    u = gold_units(gold["oz"])
    khr = (rates or {}).get("rates", {}).get("KHR")

    def both(usd):
        return f"<b>{_n(usd, 2)} $</b>" + (f" ≈ {_n(usd * khr)} ៛" if khr else "")
    return "\n".join([
        "🪙 <b>តម្លៃមាសពិភពលោក (99.99%)</b>", "",
        f"✨ 1 ជី (3.75g): {both(u['chi'])}",
        f"🏅 1 ដំឡឹង (37.5g): {both(u['damlung'])}",
        f"⚖️ 1 ក្រាម: {both(u['gram'])}",
        f"🌍 1 អោន (31.1g): <b>{_n(gold['oz'], 2)} $</b>",
        "",
        f"🕒 {gold['at']} (ម៉ោងកម្ពុជា)",
        "ℹ️ តម្លៃទីផ្សារពិភពលោក — ហាងមាសក្នុងស្រុកបូកថ្លៃជាង និងតម្លៃលក់/ទិញខុសគ្នា",
        "សូមសួរហាងមុនទិញ ឬលក់ 🙏",
    ])


def news_text(topic, items):
    label = NEWS_TOPICS.get(topic, NEWS_TOPICS["top"])[0]
    lines = [f"📰 <b>ព័ត៌មាន {label}</b>", ""]
    for i, it in enumerate(items, 1):
        meta = " · ".join(x for x in (it["source"], it["when"]) if x)
        lines.append(f"{i}. <a href=\"{html.escape(it['link'], quote=True)}\">{html.escape(it['title'])}</a>"
                     + (f"\n    <i>{html.escape(meta)}</i>" if meta else ""))
    lines += ["", "ℹ️ ចំណងជើងពី Google News — ចុចដើម្បីអានពេញពីប្រភពដើម"]
    return "\n".join(lines)


# ---------------- bot ----------------

def register(bot, config=None):
    from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

    def hub_markup():
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("💵 ដុល្លារ / រៀល", callback_data="mkt:rate"),
              InlineKeyboardButton("🪙 តម្លៃមាស", callback_data="mkt:gold"))
        m.row(InlineKeyboardButton("📰 ព័ត៌មានថ្មីៗ", callback_data="mkt:news:top"))
        m.row(InlineKeyboardButton("🏠 ម៉ឺនុយមេ", callback_data="nav:main"))
        return m

    def back_markup(refresh):
        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("🔄 ធ្វើបច្ចុប្បន្នភាព", callback_data=refresh),  # cached 30 min
              InlineKeyboardButton("⬅️ ត្រឡប់", callback_data="mkt:menu"))
        return m

    def news_markup(current):
        m = InlineKeyboardMarkup()
        row = []
        for key, (label, _q) in NEWS_TOPICS.items():
            row.append(InlineKeyboardButton(("• " if key == current else "") + label, callback_data=f"mkt:news:{key}"))
            if len(row) == 3:
                m.row(*row)
                row = []
        if row:
            m.row(*row)
        m.row(InlineKeyboardButton("⬅️ ត្រឡប់", callback_data="mkt:menu"))
        return m

    HUB_TEXT = ("📈 <b>ទីផ្សារ & សង្គមថ្ងៃនេះ</b>\n\n"
                "💵 អត្រាប្ដូរប្រាក់ ដុល្លារ ↔ រៀល\n🪙 តម្លៃមាស (ជី / ដំឡឹង)\n📰 ព័ត៌មានថ្មីៗនៅកម្ពុជា\n\n"
                "ចុចប៊ូតុងខាងក្រោម 👇")
    FAIL = "⚠️ មិនអាចទាញទិន្នន័យបានទេឥឡូវនេះ។ សូមសាកម្ដងទៀតបន្តិចទៀត 🙏"

    def view(kind, arg=None):
        if kind == "rate":
            data = fetch_rates()
            return (rates_text(data, arg) if data else FAIL), back_markup("mkt:rate:r")
        if kind == "gold":
            gold = fetch_gold()
            return (gold_text(gold, fetch_rates()) if gold else FAIL), back_markup("mkt:gold:r")
        if kind == "news":
            topic = arg if arg in NEWS_TOPICS else "top"
            items = fetch_news(topic)
            return (news_text(topic, items) if items else FAIL), news_markup(topic)
        return HUB_TEXT, hub_markup()

    def reply(message, kind, arg=None):
        try:
            bot.send_chat_action(message.chat.id, "typing")
        except Exception:
            pass
        text, markup = view(kind, arg)
        bot.reply_to(message, text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)

    @bot.message_handler(commands=["market", "today"])
    def market_cmd(message):
        bot.reply_to(message, HUB_TEXT, reply_markup=hub_markup(), parse_mode="HTML")

    @bot.message_handler(commands=["rate", "dollar", "exchange", "usd"])
    def rate_cmd(message):
        parts = (message.text or "").split(maxsplit=1)
        amount = parse_amount(parts[1]) if len(parts) > 1 else None
        reply(message, "rate", amount)

    @bot.message_handler(commands=["gold"])
    def gold_cmd(message):
        reply(message, "gold")

    @bot.message_handler(commands=["news"])
    def news_cmd(message):
        reply(message, "news", "top")

    @bot.callback_query_handler(func=lambda call: (call.data or "").startswith("mkt:"))
    def market_cb(call):
        parts = (call.data or "").split(":")
        kind = parts[1] if len(parts) > 1 else "menu"
        try:
            bot.answer_callback_query(call.id, "⏳ កំពុងទាញទិន្នន័យ..." if kind != "menu" else None)
        except Exception:
            pass
        message = getattr(call, "message", None)
        if message is None:
            return
        text, markup = view(kind, parts[2] if kind == "news" and len(parts) > 2 else None)
        try:
            bot.edit_message_text(text, message.chat.id, message.message_id, reply_markup=markup,
                                  parse_mode="HTML", disable_web_page_preview=True)
        except Exception as exc:
            if "message is not modified" not in str(exc):
                bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML",
                                 disable_web_page_preview=True)

    return hub_markup
