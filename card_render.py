"""Modern picture cards for Telegram (Pillow): aurora gradients, glass panels, neon accents.

  membership_card(name, until_ts, days_total)  → VIP pass with a ring of days left
  report_card(month_label, income, spent, cats) → money report with bars
  brief_card(date_text, lunar_text, weather, rate, gold_chi, quote) → morning card

Khmer needs text shaping (Pillow + libraqm/fribidi). When the server can't shape Khmer
(KHMER_OK False) the cards switch to English labels so text never looks broken.
Every function returns PNG bytes or raises; callers fall back to text messages.
"""
import io
import math
import os

import khmer_shaping  # noqa: F401  (must load before PIL.ImageFont: Khmer text shaping)
from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

_HERE = os.path.dirname(os.path.abspath(__file__))
KH_FONT = os.path.join(_HERE, "assets", "fonts", "KantumruyPro.ttf")
EN_FONT = os.path.join(_HERE, "assets", "fonts", "DejaVuSans-Bold.ttf")
try:
    KHMER_OK = features.check("raqm") and os.path.exists(KH_FONT)
except Exception:
    KHMER_OK = False
W, H = 1080, 1350          # 4:5 — looks big in Telegram and on TikTok/Facebook

PALETTES = {
    "aurora": [(12, 10, 40), (60, 20, 120), (0, 160, 200), (255, 70, 180)],
    "sunset": [(25, 8, 30), (140, 30, 90), (255, 120, 60), (255, 210, 90)],
    "ocean": [(4, 16, 40), (10, 70, 140), (0, 200, 190), (140, 255, 200)],
    "gold": [(18, 14, 8), (90, 60, 10), (220, 170, 50), (255, 235, 160)],
}


def font(size, weight=700, khmer=True):
    if khmer and KHMER_OK:
        f = ImageFont.truetype(KH_FONT, size, layout_engine=ImageFont.Layout.RAQM)
        try:
            f.set_variation_by_axes([weight])
        except Exception:
            pass
        return f
    return ImageFont.truetype(EN_FONT, size)


def clean(text):
    """Drop emoji and symbols the fonts don't have (they would show as boxes)."""
    keep_khmer = KHMER_OK      # without shaping, Khmer would draw as empty boxes — drop it instead
    return "".join(ch for ch in (text or "")
                   if ord(ch) < 0x1780 or (keep_khmer and 0x1780 <= ord(ch) <= 0x19FF)
                   or 0x19FF < ord(ch) < 0x2190).strip()


def L(kh, en):
    """Label in Khmer when the server can shape Khmer, else English."""
    return kh if KHMER_OK else en


def aurora(palette="aurora", w=W, h=H, seed=1):
    """Dark base with soft glowing colour blobs."""
    cols = PALETTES.get(palette, PALETTES["aurora"])
    img = Image.new("RGB", (w, h), cols[0])
    glow = Image.new("RGB", (w, h), (0, 0, 0))
    d = ImageDraw.Draw(glow)
    spots = [(0.15, 0.12, 0.55, cols[1]), (0.95, 0.25, 0.5, cols[2]), (0.1, 0.9, 0.6, cols[3]),
             (0.85, 0.95, 0.45, cols[1])]
    for i, (x, y, r, c) in enumerate(spots):
        jitter = ((seed * 37 + i * 17) % 11 - 5) / 100
        cx, cy, rr = (x + jitter) * w, (y - jitter) * h, r * w
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=c)
    small = glow.resize((w // 4, h // 4)).filter(ImageFilter.GaussianBlur(40))   # fast soft glow
    glow = small.resize((w, h), Image.BILINEAR)
    img = Image.blend(img, glow, 0.75)
    # fine grid for a "tech" feel
    grid = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    g = ImageDraw.Draw(grid)
    for x in range(0, w, 54):
        g.line([(x, 0), (x, h)], fill=(255, 255, 255, 10))
    for y in range(0, h, 54):
        g.line([(0, y), (w, y)], fill=(255, 255, 255, 10))
    return Image.alpha_composite(img.convert("RGBA"), grid)


def glass(base, box, radius=42, alpha=60, outline=(255, 255, 255, 70)):
    """Frosted-glass panel."""
    x0, y0, x1, y1 = [int(v) for v in box]
    region = base.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(18))
    mask = Image.new("L", region.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, x1 - x0, y1 - y0], radius, fill=255)
    base.paste(region, (x0, y0), mask)
    over = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(over).rounded_rectangle(box, radius, fill=(255, 255, 255, alpha), outline=outline, width=2)
    base.alpha_composite(over)


def neon_text(base, xy, text, fnt, fill=(255, 255, 255), glow=(0, 220, 255), anchor="la", strength=10):
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(xy, text, font=fnt, fill=glow + (255,), anchor=anchor)
    base.alpha_composite(layer.filter(ImageFilter.GaussianBlur(strength)))
    ImageDraw.Draw(base).text(xy, text, font=fnt, fill=fill, anchor=anchor)


def footer(base, text="Cooler Bot · @TeamSmosSneabot"):
    d = ImageDraw.Draw(base)
    d.text((W // 2, H - 60), text, font=font(30, 500, khmer=False), fill=(255, 255, 255, 170), anchor="mm")


def to_png(img):
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def ring(base, center, r, frac, width=34, c1=(0, 230, 255), c2=(255, 80, 200)):
    """Gradient progress ring (0..1)."""
    cx, cy = center
    track = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(track).ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255, 45), width=width)
    base.alpha_composite(track)
    steps = max(1, int(360 * max(0.0, min(1.0, frac))))
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    for i in range(steps):
        t = i / 360
        col = tuple(int(c1[k] + (c2[k] - c1[k]) * t) for k in range(3)) + (255,)
        ld.arc([cx - r, cy - r, cx + r, cy + r], -90 + i, -90 + i + 1.6, fill=col, width=width)
    base.alpha_composite(layer.filter(ImageFilter.GaussianBlur(8)))
    base.alpha_composite(layer)


# ---------------- cards ----------------

def membership_card(name, until_text, days_left, days_total=30, active=True):
    img = aurora("aurora" if active else "ocean", seed=len(name or "x"))
    d = ImageDraw.Draw(img)
    neon_text(img, (80, 110), "COOLER", font(96, khmer=False), glow=(255, 60, 200))
    neon_text(img, (80, 215), "VIP PASS" if active else "MEMBER", font(64, khmer=False), fill=(170, 240, 255))
    glass(img, (60, 360, W - 60, 1150))
    frac = days_left / max(1, days_total) if active else 0
    ring(img, (W // 2, 660), 210, frac)
    neon_text(img, (W // 2, 640), str(max(0, days_left)), font(150, khmer=False), anchor="mm")
    d.text((W // 2, 760), L("ថ្ងៃនៅសល់", "DAYS LEFT"), font=font(44, 600), fill=(220, 235, 255), anchor="mm")
    d.text((W // 2, 950), (clean(name) or "Cooler Fan")[:24], font=font(64, 700), fill=(255, 255, 255), anchor="mm")
    status = (L("សមាជិកសកម្ម · ដល់ ", "ACTIVE · until ") + until_text) if active else L("មិនទាន់ជាសមាជិក · /pay", "NOT ACTIVE · /pay")
    d.text((W // 2, 1040), status, font=font(38, 500), fill=(170, 255, 210) if active else (255, 190, 190), anchor="mm")
    footer(img)
    return to_png(img)


def report_card(month_label, income, spent, cats, rate=None):
    """cats: list of (label, usd) for spending, biggest first."""
    img = aurora("ocean", seed=int(spent or 0) % 9)
    d = ImageDraw.Draw(img)
    neon_text(img, (80, 100), L("របាយការណ៍លុយ", "MONEY REPORT"), font(70, 700), glow=(0, 255, 200))
    d.text((80, 205), month_label, font=font(44, 500, khmer=False), fill=(200, 230, 255))
    # three stat tiles
    tiles = [(L("ចំណូល", "INCOME"), income, (120, 255, 190)), (L("ចំណាយ", "SPENT"), spent, (255, 150, 170)),
             (L("សល់", "LEFT"), income - spent, (255, 230, 120))]
    tw = (W - 160 - 40) // 3
    for i, (label, value, col) in enumerate(tiles):
        x0 = 80 + i * (tw + 20)
        glass(img, (x0, 290, x0 + tw, 470), radius=32)
        d.text((x0 + tw // 2, 340), label, font=font(36, 600), fill=(230, 240, 255), anchor="mm")
        d.text((x0 + tw // 2, 415), f"${value:,.0f}" if abs(value) >= 100 else f"${value:,.2f}",
               font=font(54, khmer=False), fill=col, anchor="mm")
    glass(img, (60, 520, W - 60, 1180))
    d.text((100, 560), L("ចំណាយតាមប្រភេទ", "SPENDING BY CATEGORY"), font=font(42, 600), fill=(255, 255, 255))
    top = cats[:6] or [(L("មិនទាន់មាន", "No spending yet"), 0)]
    peak = max(v for _l, v in top) or 1
    y = 650
    colors = [(0, 230, 255), (255, 80, 200), (120, 255, 160), (255, 200, 80), (170, 130, 255), (255, 120, 100)]
    for i, (label, value) in enumerate(top):
        d.text((100, y), (clean(label) or "-")[:18], font=font(36, 600), fill=(235, 240, 255))
        bx0, bx1 = 430, 430 + int(440 * value / peak)
        tr = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(tr).rounded_rectangle([430, y + 6, 870, y + 44], 19, fill=(255, 255, 255, 35))
        img.alpha_composite(tr)
        if value > 0:
            layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(layer).rounded_rectangle([bx0, y + 6, max(bx0 + 38, bx1), y + 44], 19,
                                                     fill=colors[i % len(colors)] + (255,))
            img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(6)))
            img.alpha_composite(layer)
        d.text((W - 100, y + 24), f"${value:,.0f}", font=font(34, khmer=False), fill=(255, 255, 255), anchor="rm")
        y += 85
    if rate:
        d.text((W // 2, 1230), f"1$ ≈ {rate:,.0f} ៛" if KHMER_OK else f"1$ = {rate:,.0f} KHR",
               font=font(32, 500), fill=(200, 220, 255), anchor="mm")
    footer(img)
    return to_png(img)


def brief_card(date_text, lunar_text, weather=None, rate=None, gold_chi=None, quote="", holiday=None):
    """weather: dict(name, icon_text, tmin, tmax, rain) or None."""
    img = aurora("sunset", seed=len(date_text))
    d = ImageDraw.Draw(img)
    neon_text(img, (80, 100), L("អរុណសួស្ដី", "GOOD MORNING"), font(84, 700), glow=(255, 150, 60))
    d.text((80, 225), clean(date_text), font=font(46, 600), fill=(255, 240, 220))
    if lunar_text:
        d.text((80, 295), clean(lunar_text)[:40], font=font(36, 500), fill=(255, 215, 190))
    y = 380
    if holiday:
        glass(img, (60, y, W - 60, y + 110), radius=30, alpha=70)
        d.text((100, y + 55), clean(holiday)[:34], font=font(38, 600),
               fill=(255, 255, 255), anchor="lm")
        y += 140
    if weather:
        glass(img, (60, y, W - 60, y + 300))
        d.text((100, y + 40), clean(weather.get("name", "")), font=font(42, 600), fill=(255, 255, 255))
        neon_text(img, (100, y + 110), f"{weather.get('tmin', '?')}°–{weather.get('tmax', '?')}°",
                  font(110, khmer=False), glow=(255, 120, 60))
        d.text((W - 100, y + 120), clean(weather.get("desc", ""))[:20], font=font(40, 500), fill=(255, 235, 210), anchor="ra")
        if weather.get("rain") is not None:
            d.text((W - 100, y + 190), (L("ភ្លៀង ", "Rain ") + f"{weather['rain']}%"), font=font(38, 500),
                   fill=(180, 230, 255), anchor="ra")
        y += 330
    tw = (W - 160 - 20) // 2
    for i, (label, value) in enumerate([(L("ដុល្លារ", "USD"), (f"{rate:,.0f} ៛" if KHMER_OK else f"{rate:,.0f}") if rate else "—"),
                                        (L("មាស / ជី", "GOLD / chi"), f"${gold_chi:,.0f}" if gold_chi else "—")]):
        x0 = 80 + i * (tw + 20)
        glass(img, (x0, y, x0 + tw, y + 190), radius=32)
        d.text((x0 + tw // 2, y + 55), label, font=font(38, 600), fill=(255, 240, 220), anchor="mm")
        d.text((x0 + tw // 2, y + 125), value, font=font(56, 700), fill=(255, 220, 120), anchor="mm")
    y += 230
    if quote:
        q = clean(quote) if KHMER_OK else "Have a great day! - Cooler"
        d.multiline_text((W // 2, min(y + 60, H - 160)), _wrap(q, 30), font=font(38, 500), fill=(255, 255, 255),
                         anchor="ma", align="center", spacing=14)
    footer(img)
    return to_png(img)


def enabled():
    return os.getenv("CARDS_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def send_card(bot, chat_id, make_png, caption, reply_to=None, parse_mode="HTML", **extra):
    """Send a picture card with the text as caption; falls back to the plain text message."""
    kwargs = {"reply_to_message_id": reply_to} if reply_to else {}
    if enabled():
        try:
            png = make_png()
            f = io.BytesIO(png)
            f.name = "cooler.png"
            if caption and len(caption) <= 1000:
                return bot.send_photo(chat_id, f, caption=caption, parse_mode=parse_mode, **kwargs, **extra)
            bot.send_photo(chat_id, f, **kwargs)
            kwargs = {}
        except Exception as exc:
            import logging
            logging.getLogger(__name__).info("card fell back to text (%s)", type(exc).__name__)
    return bot.send_message(chat_id, caption, parse_mode=parse_mode, disable_web_page_preview=True, **kwargs, **extra)


def _wrap(text, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines[:3])
