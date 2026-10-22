"""
AI Photo Studio សម្រាប់ Team ស្មោះស្នេហ៍ Telegram Bot
កែរូបមនុស្សពិតដោយ Gemini (Nano Banana 2.1) — រក្សាមុខដើម ប្ដូរសម្លៀកបំពាក់ និងផ្ទៃខាងក្រោយ
"""
import io
import base64
import logging
from typing import Dict, Optional, Tuple

import httpx
from PIL import Image

import config

logger = logging.getLogger(__name__)

DEFAULT_STYLE = "realistic"
DEFAULT_MODEL = "gemini-nano-banana-2.1"
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MAX_INPUT_SIDE = 1536          # ពង្រួមរូបមុនផ្ញើ ដើម្បីលឿន និងសន្សំ token
REQUEST_TIMEOUT_SECONDS = 120.0

# សមាមាត្រដែល Gemini imageConfig គាំទ្រ
SUPPORTED_ASPECT_RATIOS = {
    "1:1": 1.0, "4:5": 0.8, "3:4": 0.75, "2:3": 2 / 3, "9:16": 9 / 16,
    "5:4": 1.25, "4:3": 4 / 3, "3:2": 1.5, "16:9": 16 / 9,
}

# ម៉ូដទាំង ៩ — "prompt" ពិពណ៌នាតែអ្វីដែលត្រូវប្ដូរ (សម្លៀកបំពាក់ ផ្ទៃខាងក្រោយ ពន្លឺ)
STYLES: Dict[str, Dict[str, str]] = {
    "realistic": {
        "name": "Realistic Photo",
        "name_kh": "📸 រូបថតធម្មជាតិច្បាស់កម្រិត HD",
        "prompt": "a clean professional studio portrait: neat smart-casual outfit, soft studio lighting, simple neutral backdrop, sharp HD photographic quality",
    },
    "luxury": {
        "name": "Handsome Luxury Portrait",
        "name_kh": "👔 រូបភាពអភិជនប្រណិត (Luxury)",
        "prompt": "an ultra-luxurious portrait: elegant tailored formal suit with subtle gold accents, high-end mansion interior background, warm cinematic lighting, magazine-cover quality",
    },
    "kdrama": {
        "name": "Korean K-Drama Star",
        "name_kh": "🌟 តារាសម្តែងកូរ៉េ (K-Drama)",
        "prompt": "a Korean drama star look: stylish modern Seoul fashion, soft romantic lighting, dreamy bokeh city-lights background",
    },
    "royal_khmer": {
        "name": "Cambodian Traditional Royal Style",
        "name_kh": "👑 ក្សត្របុរាណខ្មែរ (Royal Khmer)",
        "prompt": "ancient Khmer royalty: intricate golden embroidered silk sampot and traditional royal ornaments, Angkorian palace hall background, dignified royal posture, golden light",
    },
    "khmer_warrior": {
        "name": "Ancient Khmer Warrior",
        "name_kh": "⚔️ មេទ័ពបុរាណខ្មែរ (Khmer Warrior)",
        "prompt": "an ancient Angkor-era Khmer warrior commander: leather and bronze armor with traditional battle ornaments, misty epic battlefield background, cinematic heroic lighting",
    },
    "fashion": {
        "name": "Modern Fashion",
        "name_kh": "🕶️ ម៉ូដទាន់សម័យ (Modern Fashion)",
        "prompt": "a high-fashion editorial look: trendy designer outfit, sleek runway or street-style backdrop, crisp magazine photoshoot lighting",
    },
    "cyberpunk": {
        "name": "Cyberpunk Neon",
        "name_kh": "🌆 អនាគត Cyberpunk Neon",
        "prompt": "a futuristic cyberpunk look: sleek high-tech jacket, neon cyan and magenta reflections on the scene, rainy futuristic city at night background",
    },
    "movie_poster": {
        "name": "Cinematic Movie Poster",
        "name_kh": "🎬 ផ្ទាំងរូបភាពភាពយន្ត (Movie Poster)",
        "prompt": "a Hollywood action-movie poster look: dramatic rim lighting, cinematic color grading, atmospheric explosive background, film grain — but no text or title on the image",
    },
    "anime": {
        "name": "Anime & Artistic",
        "name_kh": "🎨 គំនូរ Anime ជប៉ុន (Anime Style)",
        "prompt": "a high-quality Japanese anime illustration of this same person, vibrant colors, beautiful painted sky background",
    },
    "custom": {
        "name": "Custom Style",
        "name_kh": "✍️ កំណត់ម៉ូដតាមការចង់បាន (Custom)",
        "prompt": "a polished, high-quality portrait",
    },
}


class StudioError(Exception):
    """កំហុសពី Photo Studio។ code: not_configured | billing | rate_limited | blocked | no_image | api"""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def closest_aspect_ratio(width: int, height: int) -> str:
    ratio = width / height if height else 1.0
    return min(SUPPORTED_ASPECT_RATIOS, key=lambda key: abs(SUPPORTED_ASPECT_RATIOS[key] - ratio))


def prepare_image(image_bytes: bytes) -> Tuple[bytes, str]:
    """បម្លែងទៅ JPEG ពង្រួមទំហំ ហើយត្រឡប់ (bytes, aspect_ratio)។"""
    with Image.open(io.BytesIO(image_bytes)) as img:
        img = img.convert("RGB")
        aspect = closest_aspect_ratio(img.width, img.height)
        img.thumbnail((MAX_INPUT_SIDE, MAX_INPUT_SIDE), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92)
        return buf.getvalue(), aspect


def build_prompt(style_key: str, custom_prompt: Optional[str] = None) -> str:
    style = STYLES.get(style_key, STYLES[DEFAULT_STYLE])
    change = style["prompt"]
    if style_key == "custom" and custom_prompt:
        change = f"{custom_prompt.strip()}, {change}"
    if style_key == "anime":
        keep = (
            "Redraw the person in this photo as an anime character while keeping them clearly "
            "recognizable as the same person: same face shape, hairstyle, skin tone and expression."
        )
    else:
        keep = (
            "Edit this photo of a real person. Keep the person's face, facial features, skin tone, age, "
            "body shape, pose and expression exactly the same — it must still be clearly the same real person. "
            "Do not beautify, slim, or change the face."
        )
    return (
        f"{keep} Change only the outfit, background and lighting to: {change}. "
        "Make the lighting on the face match the new scene so it looks like one natural photograph. "
        "No text, no watermark, no logos. If there are several people, keep every face unchanged."
    )


def _extract_image(result: dict) -> bytes:
    candidates = result.get("candidates") or []
    if not candidates:
        reason = (result.get("promptFeedback") or {}).get("blockReason", "")
        raise StudioError("blocked" if reason else "no_image", reason)
    for part in (candidates[0].get("content") or {}).get("parts") or []:
        data = part.get("inlineData") or part.get("inline_data")
        if data and data.get("data"):
            return base64.b64decode(data["data"])
    finish = candidates[0].get("finishReason", "")
    if finish and finish not in ("STOP", "MAX_TOKENS"):
        raise StudioError("blocked", finish)
    raise StudioError("no_image", finish)


async def transform_photo(
    image_bytes: bytes,
    style_key: str = DEFAULT_STYLE,
    custom_prompt: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> bytes:
    """កែរូបដោយ Gemini ហើយត្រឡប់ JPEG bytes។ បោះ StudioError ពេលបរាជ័យ។"""
    api_key = getattr(config, "GEMINI_API_KEY", "")
    if not api_key:
        raise StudioError("not_configured", "GEMINI_API_KEY")
    model = getattr(config, "STUDIO_IMAGE_MODEL", "") or DEFAULT_MODEL

    jpeg_bytes, aspect = prepare_image(image_bytes)
    payload = {
        "contents": [{
            "role": "user",
            "parts": [
                {"text": build_prompt(style_key, custom_prompt)},
                {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(jpeg_bytes).decode("ascii")}},
            ],
        }],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {"aspectRatio": aspect, "imageSize": "1K"},
        },
    }
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    url = API_URL.format(model=model)

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        response = await http.post(url, headers=headers, json=payload)
        if response.status_code == 400 and "imageSize" in response.text:
            # model ខ្លះមិនស្គាល់ imageSize — សាកម្ដងទៀតដោយគ្មានវា
            payload["generationConfig"]["imageConfig"].pop("imageSize", None)
            response = await http.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise StudioError("api", type(exc).__name__) from exc
    finally:
        if owns_client:
            await http.aclose()

    if response.status_code != 200:
        body = response.text[:500]
        logger.error("Gemini image error %s: %s", response.status_code, body)
        lowered = body.lower()
        if response.status_code == 429:
            code = "billing" if ("free_tier" in lowered or "limit: 0" in lowered) else "rate_limited"
            raise StudioError(code, body)
        if response.status_code in (401, 403) or "billing" in lowered:
            raise StudioError("billing", body)
        raise StudioError("api", f"HTTP {response.status_code}")

    image = _extract_image(response.json())
    with Image.open(io.BytesIO(image)) as out:
        buf = io.BytesIO()
        out.convert("RGB").save(buf, format="JPEG", quality=93)
        return buf.getvalue()
