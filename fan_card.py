"""Shareable "Fan Card" image for /card (original design, Pillow only).

Text on the image is Latin only: Pillow's built-in font cannot shape Khmer, so a
Khmer first name falls back to the @username or "Cooler Fan". The Khmer caption is
sent with the photo instead.
"""
import io
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 640
NAVY = (8, 22, 52)
BLUE = (32, 200, 255)
GOLD = (255, 200, 71)
WHITE = (240, 248, 255)


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
