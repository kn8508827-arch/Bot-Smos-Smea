"""☀️ Morning news card: one designed picture in Khmer — date, sila, weather, dollar, gold and
the top 3 news stories, each with its own photo taken from the news website.

  fetch_stories(limit)      → [{title, link, source, image}] from NEWS_FEEDS (RSS), Google News as fallback
  khmerize(stories, ask)    → titles that are not Khmer are turned into a short Khmer headline by AI
  news_card(...)            → PNG bytes (1080 wide, height grows with the content), built with card_render's fonts and glass panels

NEWS_FEEDS (Railway variable, comma separated RSS URLs) decides where the news comes from.
Owner command /newstest shows which feeds work and whether they carry photos.
"""
import html
import io
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import httpx

logger = logging.getLogger(__name__)

DEFAULT_FEEDS = (
    "https://kohsantepheapdaily.com.kh/feed",
    "https://www.dap-news.com/feed",
    "https://www.khmertimeskh.com/feed/",
    "https://cambojanews.com/feed/",
)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CoolerBot/1.0; +https://t.me/TeamSmosSneabot)"}
TIMEOUT = 8.0
MAX_IMAGE_BYTES = 4_000_000
KHMER = re.compile(r"[ក-៿]")
_cache = {}


def feeds():
    raw = os.getenv("NEWS_FEEDS", "").strip()
    return [u.strip() for u in raw.split(",") if u.strip().startswith("http")] if raw else list(DEFAULT_FEEDS)


def _get(url, client=None):
    c = client or httpx.Client(timeout=TIMEOUT, headers=HEADERS, follow_redirects=True)
    try:
        r = c.get(url)
        r.raise_for_status()
        return r
    finally:
        if client is None:
            c.close()


def _strip(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(text or ""))).strip()


def _image_of(item):
    """Photo URL inside an RSS item: media:content / media:thumbnail / enclosure / first <img>."""
    for el in item.iter():
        tag = el.tag.split("}")[-1]
        url = el.attrib.get("url", "")
        if tag in ("content", "thumbnail") and url.startswith("http") and \
                (el.attrib.get("medium", "image") == "image" or re.search(r"\.(jpe?g|png|webp)", url, re.I)):
            return url
        if tag == "enclosure" and url.startswith("http") and el.attrib.get("type", "image").startswith("image"):
            return url
    for el in item.iter():
        if el.tag.split("}")[-1] in ("description", "encoded") and el.text:
            m = re.search(r"<img[^>]+src=[\"']([^\"']+)", el.text)
            if m and m.group(1).startswith("http"):
                return html.unescape(m.group(1))
    return ""


def parse_feed(xml_text, source="", limit=6):
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError:
        return []
    out = []
    for item in root.iter("item"):
        title = _strip(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        src = _strip(item.findtext("source")) or source
        if title and link.startswith("http"):
            out.append({"title": title, "link": link, "source": src, "image": _image_of(item)})
        if len(out) >= limit:
            break
    return out


def og_image(url, client=None):
    """The article's share photo (og:image), for feeds that carry no picture."""
    if "news.google." in url:
        return ""
    try:
        page = _get(url, client).text[:300_000]
    except Exception:
        return ""
    m = re.search(r"<meta[^>]+property=[\"']og:image[\"'][^>]+content=[\"']([^\"']+)", page) or \
        re.search(r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+property=[\"']og:image", page)
    return html.unescape(m.group(1)) if m and m.group(1).startswith("http") else ""


def _host(url):
    m = re.match(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else url


def fetch_stories(limit=3, client=None, feed_urls=None):
    """Newest stories across the feeds, one per source first so the card is varied."""
    key = ("stories", limit, tuple(feed_urls or feeds()))
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    per_feed = []
    for url in (feed_urls or feeds()):
        try:
            per_feed.append(parse_feed(_get(url, client).text, _host(url)))
        except Exception as exc:
            logger.info("news feed %s failed (%s)", _host(url), type(exc).__name__)
    picked, i = [], 0
    while len(picked) < limit and any(i < len(f) for f in per_feed):
        for f in per_feed:
            if i < len(f) and len(picked) < limit:
                picked.append(dict(f[i]))
        i += 1
    if len(picked) < limit:      # fallback: Google News Cambodia (no photos)
        try:
            import market_info
            for it in (market_info.fetch_news("top") or [])[: limit - len(picked)]:
                picked.append({"title": it["title"], "link": it["link"], "source": it.get("source", ""), "image": ""})
        except Exception:
            pass
    for s in picked:
        if not s["image"]:
            s["image"] = og_image(s["link"], client)
    _cache[key] = (time.time() + 900, picked)
    return picked


def khmerize(stories, ask):
    """Short Khmer headline for every story that is not already in Khmer (one AI call)."""
    todo = [i for i, s in enumerate(stories) if not KHMER.search(s["title"])]
    if not todo or ask is None:
        return stories
    numbered = "\n".join(f"{n + 1}. {stories[i]['title']}" for n, i in enumerate(todo))
    prompt = ("Translate each news headline into natural, short Khmer (max 60 Khmer characters each, no emoji, "
              "keep names of people and places). Reply ONLY with the numbered lines, same numbering:\n" + numbered)
    try:
        reply = ask(prompt) or ""
    except Exception as exc:
        logger.info("headline translation failed (%s)", type(exc).__name__)
        return stories
    for line in reply.splitlines():
        m = re.match(r"\s*(\d+)[.)]\s*(.+)", line)
        if m and 1 <= int(m.group(1)) <= len(todo) and KHMER.search(m.group(2)):
            stories[todo[int(m.group(1)) - 1]]["title"] = m.group(2).strip()
    return stories


def _photo(url, size, client=None):
    from PIL import Image, ImageOps
    if not url:
        return None
    try:
        r = _get(url, client)
        if len(r.content) > MAX_IMAGE_BYTES:
            return None
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        return ImageOps.fit(img, size, Image.LANCZOS)
    except Exception:
        return None


_CLUSTER = re.compile(r"[\u1780-\u17A2](?:\u17D2[\u1780-\u17A2]|[\u17B6-\u17D1\u17D3\u17DD])*|\s+|[^\u1780-\u17FF\s]+|.")


def _tokens(text):
    """Words, but Khmer runs (written without spaces) are cut into syllable clusters so lines can break."""
    return _CLUSTER.findall(text)


def _wrap_px(draw, text, fnt, width, lines=2):
    out, cur = [], ""
    for tok in _tokens(text.strip()):
        test = cur + tok
        if draw.textlength(test.strip(), font=fnt) <= width or not cur.strip():
            cur = test
            continue
        out.append(cur.strip())
        cur = tok.lstrip()
        if len(out) == lines:
            break
    if cur.strip() and len(out) < lines:
        out.append(cur.strip())
    if len(out) == lines and "".join(out).replace(" ", "") != text.replace(" ", "").strip():
        while out[-1] and draw.textlength(out[-1] + "…", font=fnt) > width:
            out[-1] = out[-1][:-1]
        out[-1] += "…"
    return out


def news_card(date_text, lunar_text, sila_text=None, holiday=None, weather=None, rate=None, gold_chi=None,
              stories=(), client=None):
    """weather: dict(name, desc, tmin, tmax, rain). stories: [{title, source, image}]."""
    import card_render as cr
    from PIL import Image, ImageDraw
    tags = [t for t in (holiday and ("🎉", holiday), sila_text and ("🙏", sila_text)) if t][:2]
    n_news = len(stories[:3])
    W = 1080
    H = 320 + 92 * len(tags) + 236 + (78 + n_news * 230 if n_news else 0) + 110   # grows with the content
    img = cr.aurora("ocean", w=W, h=H, seed=len(date_text))
    d = ImageDraw.Draw(img)
    cr.neon_text(img, (70, 70), cr.L("អរុណសួស្ដី", "GOOD MORNING"), cr.font(78, 700), glow=(255, 190, 80))
    d.text((70, 190), cr.clean(date_text), font=cr.font(42, 600), fill=(255, 255, 255))
    d.text((70, 252), cr.clean(lunar_text)[:46], font=cr.font(34, 500), fill=(190, 235, 255))
    y = 320
    for _icon, text in tags:
        cr.glass(img, (60, y, W - 60, y + 76), radius=26, alpha=70)
        d.text((92, y + 38), cr.clean(text)[:44], font=cr.font(32, 600), fill=(255, 226, 140), anchor="lm")
        y += 92
    # weather · dollar · gold tiles
    tw = (W - 120 - 2 * 18) // 3
    tiles = [
        (cr.clean((weather or {}).get("name", "")) or cr.L("អាកាសធាតុ", "Weather"),
         f"{weather['tmin']}–{weather['tmax']}°" if weather else "—",
         cr.clean((weather or {}).get("desc", ""))[:14]),
        (cr.L("១ ដុល្លារ", "1 USD"), f"{rate:,.0f}" if rate else "—", cr.L("រៀល", "riel")),
        (cr.L("មាស / ជី", "Gold / chi"), f"${gold_chi:,.0f}" if gold_chi else "—", cr.L("ពិភពលោក", "world")),
    ]
    for i, (label, value, sub) in enumerate(tiles):
        x0 = 60 + i * (tw + 18)
        cr.glass(img, (x0, y, x0 + tw, y + 200), radius=30)
        d.text((x0 + tw // 2, y + 42), label, font=cr.font(30, 600), fill=(220, 240, 255), anchor="mm")
        d.text((x0 + tw // 2, y + 108), value, font=cr.font(52, 700, khmer=False), fill=(255, 214, 110), anchor="mm")
        d.text((x0 + tw // 2, y + 163), sub, font=cr.font(26, 500), fill=(200, 225, 245), anchor="mm")
    y += 236
    # news
    if stories:
        cr.neon_text(img, (70, y), cr.L("ព័ត៌មានថ្ងៃនេះ", "TODAY'S NEWS"), cr.font(44, 700), glow=(0, 200, 255), strength=8)
        y += 78
        photos = [None] * len(stories)
        with ThreadPoolExecutor(max_workers=3) as pool:
            photos = list(pool.map(lambda s: _photo(s.get("image"), (250, 190), client), stories[:3]))
        row_h = 214
        for n, (s, ph) in enumerate(zip(stories[:3], photos)):
            top = y + n * (row_h + 16)
            cr.glass(img, (60, top, W - 60, top + row_h), radius=28, alpha=55)
            px, py = 76, top + 12
            mask = Image.new("L", (250, 190), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, 249, 189], 20, fill=255)
            if ph is None:   # no photo: numbered tile
                ph = Image.new("RGB", (250, 190), (20, 60, 120))
                ImageDraw.Draw(ph).text((125, 95), str(n + 1), font=cr.font(90, 700, khmer=False),
                                        fill=(120, 220, 255), anchor="mm")
            img.paste(ph, (px, py), mask)
            tx, tw2 = px + 250 + 26, W - 60 - (px + 250 + 26) - 26
            f = cr.font(34, 600)
            for k, line in enumerate(_wrap_px(d, cr.clean(s["title"]), f, tw2, lines=2)):
                d.text((tx, py + 4 + k * 50), line, font=f, fill=(255, 255, 255))
            d.text((tx, top + row_h - 30), cr.clean(s.get("source", ""))[:30], font=cr.font(24, 500, khmer=False),
                   fill=(150, 210, 255), anchor="ls")
    d.text((W // 2, H - 55), "Cooler Bot · @TeamSmosSneabot", font=cr.font(28, 500, khmer=False),
           fill=(255, 255, 255), anchor="mm")
    return cr.to_png(img)


def caption(stories, quote=""):
    """Short caption: the quote + numbered links (the card already shows the headlines)."""
    lines = ["☀️ <b>អរុណសួស្ដី! — Cooler Bot</b>"]
    if quote:
        lines.append(f"💬 {html.escape(quote)}")
    if stories:
        lines.append("📰 " + " · ".join(f"<a href=\"{html.escape(s['link'])}\">អានព័ត៌មាន {n}</a>"
                                       for n, s in enumerate(stories[:3], 1)))
    lines.append("<i>/morning off — ឈប់ទទួល</i>")
    return "\n".join(lines)


def feed_report(client=None):
    """For /newstest: which feeds answer, how many stories, how many with photos."""
    rows = []
    for url in feeds():
        try:
            items = parse_feed(_get(url, client).text, _host(url))
            rows.append(f"{'✅' if items else '⚠️'} {_host(url)} — {len(items)} · 🖼️ {sum(1 for i in items if i['image'])}")
        except Exception as exc:
            rows.append(f"❌ {_host(url)} — {type(exc).__name__}")
    return rows
