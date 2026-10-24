"""Shareable "Fan Card" image for /card (original design, Pillow only).

Text on the image is Latin only: Pillow's built-in font cannot shape Khmer, so a
Khmer first name falls back to the @username or "Cooler Fan". The Khmer caption is
sent with the photo instead.
"""
import io
import os
from typing import Optional

import khmer_shaping  # noqa: F401  (must load before PIL.ImageFont: Khmer text shaping)
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 640
NAVY = (8, 22, 52)
BLUE = (32, 200, 255)
GOLD = (255, 200, 71)
WHITE = (240, 248, 255)


_HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(_HERE, "assets", "fan_card_template.jpg")
BOLD_FONT = os.path.join(_HERE, "assets", "fonts", "DejaVuSans-Bold.ttf")

# Text areas on the template (x0, y0, x1, y1) that are repainted with fresh text.
_SLOTS = {
    "name": (926, 432, 1372, 518),
    "rank": (1044, 590, 1366, 652),
    "points": (984, 748, 1330, 812),
    "fan": (1036, 868, 1350, 932),
    "footer": (278, 878, 692, 932),
}


def _bold(size):
    try:
        return ImageFont.truetype(BOLD_FONT, size)
    except OSError:
        return _font(size)


def _fit(draw, text, size, max_w, bold=True):
    while size > 12:
        f = _bold(size) if bold else _font(size)
        box = draw.textbbox((0, 0), text, font=f)
        if box[2] - box[0] <= max_w:
            return f, box
        size -= 2
    f = _bold(size) if bold else _font(size)
    return f, draw.textbbox((0, 0), text, font=f)


def _erase(img, slot):
    """Repaint a text area row by row, blending the colours just outside it."""
    x0, y0, x1, y1 = slot
    px = img.load()
    for y in range(y0, y1):
        left, right = px[x0 - 3, y], px[x1 + 3, y]
        for x in range(x0, x1):
            t = (x - x0) / max(1, x1 - x0)
            px[x, y] = tuple(int(left[i] + (right[i] - left[i]) * t) for i in range(3))


def _glow_text(img, xy, text, font, fill, glow, radius=6):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(xy, text, font=font, fill=glow + (200,))
    layer = layer.filter(ImageFilter.GaussianBlur(radius))
    img.paste(layer, (0, 0), layer)
    ImageDraw.Draw(img).text(xy, text, font=font, fill=fill)


def has_template() -> bool:
    return os.path.exists(TEMPLATE) and os.path.exists(BOLD_FONT)


def render_template(name: str, rank: str, points: int, fan_number: int, footer: str) -> bytes:
    img = Image.open(TEMPLATE).convert("RGB")
    for slot in _SLOTS.values():
        _erase(img, slot)
    img = img.convert("RGBA")
    d = ImageDraw.Draw(img)

    def place(slot, text, size, align="left", pad=0):
        x0, y0, x1, y1 = slot
        f, box = _fit(d, text, size, x1 - x0 - pad * 2)
        w, h = box[2] - box[0], box[3] - box[1]
        x = x0 + pad - box[0] if align == "left" else x0 + (x1 - x0 - w) / 2 - box[0]
        y = y0 + (y1 - y0 - h) / 2 - box[1]
        return f, (x, y), w

    f, xy, _ = place(_SLOTS["name"], name, 60, pad=8)
    _glow_text(img, xy, name, f, (255, 255, 255), (90, 170, 255), 8)

    f, xy, _ = place(_SLOTS["rank"], f"RANK  {rank.upper()}", 50, pad=10)
    _glow_text(img, xy, f"RANK  {rank.upper()}", f, (32, 18, 4), (255, 236, 160), 3)

    sx0, sy0, sx1, sy1 = _SLOTS["points"]
    label, value = "POINTS", f"{points:,}"
    fl, bl = _fit(d, label, 42, 200, bold=False)
    fv, bv = _fit(d, value, 52, sx1 - sx0 - (bl[2] - bl[0]) - 40)
    x = sx0 + 10
    d.text((x - bl[0], sy0 + (sy1 - sy0 - (bl[3] - bl[1])) / 2 - bl[1]), label, font=fl, fill=(235, 242, 255))
    x += bl[2] - bl[0] + 30
    _glow_text(img, (x - bv[0], sy0 + (sy1 - sy0 - (bv[3] - bv[1])) / 2 - bv[1]), value, fv,
               (255, 214, 92), (255, 170, 40), 6)

    f, xy, _ = place(_SLOTS["fan"], f"FAN #{fan_number:04d}", 52, pad=8)
    d.text(xy, f"FAN #{fan_number:04d}", font=f, fill=(24, 14, 4))

    f, xy, _ = place(_SLOTS["footer"], footer, 34, align="center")
    d.text(xy, footer, font=f, fill=(235, 242, 255))

    out = io.BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=92, optimize=True)
    return out.getvalue()


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def latin_name(first_name: Optional[str], username: Optional[str]) -> str:
    name = (first_name or "").strip()
    if name and all(32 <= ord(ch) < 127 for ch in name):
        return name[:22]
    if username:
        return ("@" + username)[:22]
    return "Cooler Fan"


def render(name: str, rank: str, points: int, fan_number: int, level_label: str = "",
           avatar: Optional[bytes] = None, footer: str = "Cooler Bot") -> bytes:
    img = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(img)
    # background: diagonal gradient + glow
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(8 + 10 * t), int(22 + 40 * t), int(52 + 70 * t)))
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([W - 520, -260, W + 200, 420], fill=(30, 110, 200))
    gd.ellipse([-260, H - 260, 380, H + 260], fill=(120, 90, 20))
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(120)), 0.45)
    d = ImageDraw.Draw(img)
    # gold frame
    d.rounded_rectangle([18, 18, W - 18, H - 18], radius=42, outline=GOLD, width=6)
    d.rounded_rectangle([34, 34, W - 34, H - 34], radius=34, outline=(255, 200, 71, 90), width=2)

    d.text((70, 62), "COOLER BOT", font=_font(64), fill=GOLD)
    d.text((72, 136), "OFFICIAL FAN CARD  ·  VID COOLER", font=_font(26), fill=BLUE)

    # avatar circle
    ax, ay, ar = 70, 210, 150
    if avatar:
        try:
            pic = Image.open(io.BytesIO(avatar)).convert("RGB")
            side = min(pic.size)
            pic = pic.crop(((pic.width - side) // 2, (pic.height - side) // 2,
                            (pic.width + side) // 2, (pic.height + side) // 2)).resize((ar * 2, ar * 2))
            mask = Image.new("L", (ar * 2, ar * 2), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, ar * 2, ar * 2], fill=255)
            img.paste(pic, (ax, ay), mask)
        except Exception:
            avatar = None
    if not avatar:
        d.ellipse([ax, ay, ax + ar * 2, ay + ar * 2], fill=(20, 60, 120))
        initial = (name.lstrip("@")[:1] or "C").upper()
        f = _font(150)
        box = d.textbbox((0, 0), initial, font=f)
        d.text((ax + ar - (box[2] - box[0]) / 2 - box[0], ay + ar - (box[3] - box[1]) / 2 - box[1]),
               initial, font=f, fill=GOLD)
    d.ellipse([ax - 6, ay - 6, ax + ar * 2 + 6, ay + ar * 2 + 6], outline=GOLD, width=6)

    x = 420
    d.text((x, 214), name, font=_font(62), fill=WHITE)
    d.text((x, 300), f"RANK  {rank.upper()}", font=_font(40), fill=GOLD)
    if level_label:
        d.text((x, 352), level_label, font=_font(30), fill=BLUE)
    d.text((x, 400), f"POINTS  {points:,}", font=_font(40), fill=WHITE)

    badge = f"FAN #{fan_number:04d}"
    f = _font(44)
    box = d.textbbox((0, 0), badge, font=f)
    bw = box[2] - box[0] + 60
    d.rounded_rectangle([W - 70 - bw, H - 128, W - 70, H - 62], radius=30, fill=GOLD)
    d.text((W - 70 - bw + 30, H - 124), badge, font=f, fill=NAVY)
    d.text((70, H - 106), footer[:40], font=_font(30), fill=(170, 200, 230))

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()
