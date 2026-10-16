"""
AI Photo Studio សម្រាប់ Team ស្មោះស្នេហ៍ Telegram Bot
កែច្នៃរូបភាពគុណភាពខ្ពស់ (SDXL Inpainting & Transformation)
"""
import io
import os
import base64
import logging
from typing import Dict, Tuple, Optional, Any, List
from PIL import Image, ImageDraw, ImageFilter
import httpx

import config

logger = logging.getLogger(__name__)

DEFAULT_STYLE = "realistic"

# ទំហំស្តង់ដារ SDXL សម្រាប់រូបភាពនីមួយៗ
SDXL_DIMENSIONS = {
    "4:5": (896, 1152),      # Portrait ល្អបំផុតសម្រាប់ Facebook
    "1:1": (1024, 1024),     # Square
    "16:9": (1344, 768)      # Landscape សម្រាប់រូបថតជាក្រុម
}

# បញ្ជីម៉ូដស្ទីលប្រណិតៗទាំង ៩ (Artistic & Luxury Styles)
STYLES: Dict[str, Dict[str, str]] = {
    "realistic": {
        "name": "Realistic Photo",
        "name_kh": "📸 រូបថតធម្មជាតិច្បាស់កម្រិត HD",
        "prompt": "masterpiece, 8k uhd, realistic photograph, studio lighting, highly detailed face, professional photography, natural skin texture",
        "negative": "anime, cartoon, painting, illustration, deformed, blur, bad anatomy, bad eyes, disfigured"
    },
    "luxury": {
        "name": "Handsome Luxury Portrait",
        "name_kh": "👔 រូបភាពអភិជនប្រណិត (Luxury)",
        "prompt": "masterpiece, ultra-luxurious lifestyle, elegant tailored formal attire, subtle gold accents, high-end mansion background, executive prestige, cinematic warm lighting, magazine cover quality, 8k",
        "negative": "casual wear, cheap look, distorted, low quality, oversaturated, blurry, cartoon"
    },
    "kdrama": {
        "name": "Korean K-Drama Star",
        "name_kh": "🌟 តារាសម្តែងកូរ៉េ (K-Drama)",
        "prompt": "Korean drama aesthetic, glowing porcelain skin, soft romantic lighting, stylish modern Seoul fashion, gentle gaze, bokeh city background, high visual aesthetic, 8k resolution",
        "negative": "dark harsh shadows, rough skin, cartoon, anime, low resolution, bad hands"
    },
    "royal_khmer": {
        "name": "Cambodian Traditional Royal Style",
        "name_kh": "👑 ក្សត្របុរាណខ្មែរ (Royal Khmer)",
        "prompt": "majestic ancient Khmer royalty, intricate golden embroidery sampot, royal ornaments, Angkorian palace hall background, dignified royal posture, rich silk texture, divine golden lighting, cultural masterpiece, 8k",
        "negative": "modern clothing, jeans, plastic accessories, distorted architecture, blurry, low quality"
    },
    "khmer_warrior": {
        "name": "Ancient Khmer Warrior",
        "name_kh": "⚔️ មេទ័ពបុរាណខ្មែរ (Khmer Warrior)",
        "prompt": "ancient Cambodian Angkor warrior commander, leather and bronze armor, traditional battle ornaments, heroic and brave expression, cinematic misty battlefield background, epic lighting, sharp details",
        "negative": "guns, modern armor, weak posture, cartoon, illustration, bad anatomy"
    },
    "fashion": {
        "name": "Modern Fashion",
        "name_kh": "🕶️ ម៉ូដទាន់សម័យ (Modern Fashion)",
        "prompt": "high fashion editorial, Paris fashion week style, trendy designer outfit, confident pose, sleek street runway backdrop, magazine photoshoot, crisp focus, 8k",
        "negative": "outdated clothes, cartoon, blurry, noisy, bad lighting"
    },
    "cyberpunk": {
        "name": "Cyberpunk Neon",
        "name_kh": "🌆 អនាគត Cyberpunk Neon",
        "prompt": "futuristic cyberpunk aesthetic, neon reflective lighting, glowing cyan and magenta accents, high-tech dystopian city background, sleek cybernetic gear, 8k wallpaper",
        "negative": "vintage, historical, dull colors, natural outdoor, low quality"
    },
    "movie_poster": {
        "name": "Cinematic Movie Poster",
        "name_kh": "🎬 ផ្ទាំងរូបភាពភាពយន្ត (Movie Poster)",
        "prompt": "Hollywood cinematic blockbuster poster, dramatic rim lighting, action hero mood, film grain, atmospheric background, depth of field, 8k",
        "negative": "flat lighting, amateur photo, cartoon, childish, watermark, blurry"
    },
    "anime": {
        "name": "Anime & Artistic",
        "name_kh": "🎨 គំនូរ Anime ជប៉ុន (Anime Style)",
        "prompt": "anime digital painting style, Makoto Shinkai aesthetic, vibrant colors, expressive eyes, beautifully painted hair, gorgeous sky background, high quality anime key visual",
        "negative": "photo, photorealistic, 3d render, creepy, disfigured, bad lineart"
    },
    "custom": {
        "name": "Custom Style",
        "name_kh": "✍️ កំណត់ម៉ូដតាមការចង់បាន (Custom)",
        "prompt": "masterpiece, ultra-detailed, professional quality",
        "negative": "low quality, distorted, bad anatomy, blurry"
    }
}


def get_best_sdxl_dimensions(width: int, height: int, aspect_ratio: str = "auto") -> Tuple[int, int]:
    """ជ្រើសរើសទំហំ SDXL សមស្របទៅតាមសមាមាត្ររូបភាព"""
    if aspect_ratio in SDXL_DIMENSIONS:
        return SDXL_DIMENSIONS[aspect_ratio]
    
    ratio = width / height
    if ratio > 1.25:
        return SDXL_DIMENSIONS["16:9"]  # Landscape
    elif ratio < 0.85:
        return SDXL_DIMENSIONS["4:5"]   # Portrait
    else:
        return SDXL_DIMENSIONS["1:1"]   # Square


def prepare_image_for_sdxl(image_path: str, aspect_ratio: str = "4:5") -> bytes:
    """កែសម្រួលរូបភាពឱ្យត្រូវតាមខ្នាតដែល Stability SDXL ត្រូវការ"""
    with Image.open(image_path) as img:
        img = img.convert("RGB")
        target_size = SDXL_DIMENSIONS.get(aspect_ratio, SDXL_DIMENSIONS["4:5"])
        
        # កាត់ និងពង្រីក (Crop to Fit) ដោយមិនឱ្យខូចទ្រង់ទ្រាយ
        img_ratio = img.width / img.height
        target_ratio = target_size[0] / target_size[1]
        
        if img_ratio > target_ratio:
            new_height = target_size[1]
            new_width = int(new_height * img_ratio)
        else:
            new_width = target_size[0]
            new_height = int(new_width / img_ratio)
            
        img_resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        # Center Crop
        left = (new_width - target_size[0]) // 2
        top = (new_height - target_size[1]) // 2
        right = left + target_size[0]
        bottom = top + target_size[1]
        
        final_img = img_resized.crop((left, top, right, bottom))
        
        buf = io.BytesIO()
        final_img.save(buf, format="JPEG", quality=95)
        return buf.getvalue()


def generate_face_mask(image: Image.Image, allow_hair_change: bool = False) -> Image.Image:
    """
    បង្កើត Mask ដើម្បីការពារផ្ទៃមុខ (Face Shield) ក្នុងដំណើរការ Inpainting
    ពណ៌ខ្មៅ (0) = ការពារមិនឱ្យ AI កែប្រែ (មុខដើម)
    ពណ៌ស (255) = អនុញ្ញាតឱ្យ AI កែប្រែ (សម្លៀកបំពាក់ និង Background)
    """
    mask = Image.new("L", image.size, color=255)
    draw = ImageDraw.Draw(mask)
    
    width, height = image.size
    # កំណត់តំបន់កណ្តាលដែលផ្ទៃមុខស្ថិតនៅ
    center_x = width // 2
    center_y = int(height * 0.42)
    face_radius_x = int(width * 0.22)
    face_radius_y = int(height * 0.25)
    
    # គូររង្វង់ពងក្រពើការពារផ្ទៃមុខ
    draw.ellipse(
        [
            center_x - face_radius_x,
            center_y - face_radius_y,
            center_x + face_radius_x,
            center_y + face_radius_y
        ],
        fill=0
    )
    
    # បន្ថែម Feathering / Blur ដើម្បីឱ្យគែមបញ្ចូលគ្នាទន់ភ្លន់
    mask = mask.filter(ImageFilter.GaussianBlur(radius=15))
    return mask


def composite_face_shield(orig_img: Image.Image, gen_img: Image.Image, mask: Image.Image) -> Image.Image:
    """ផ្សំបញ្ចូលភីកសែលផ្ទៃមុខដើមចូលក្នុងរូបភាពដែល AI បង្កើតថ្មី"""
    if gen_img.size != orig_img.size:
        gen_img = gen_img.resize(orig_img.size, Image.Resampling.LANCZOS)
    if mask.size != orig_img.size:
        mask = mask.resize(orig_img.size, Image.Resampling.LANCZOS)
    
    # បញ្ចូលគ្នាតាម Mask (កន្លែងខ្មៅយករូបដើម កន្លែងសយករូបដែល AI កែ)
    blended = Image.composite(gen_img, orig_img, mask)
    return blended


async def transform_photo(
    image_path: str,
    style_key: str = "realistic",
    aspect_ratio: str = "auto",
    custom_prompt: Optional[str] = None
) -> bytes:
    """ហៅទៅកាន់ Stability AI SDXL ដើម្បីកែច្នៃរូបភាព"""
    api_key = getattr(config, "STABILITY_API_KEY", "")
    if not api_key:
        raise ValueError("STABILITY_API_KEY_NOT_CONFIGURED")

    style = STYLES.get(style_key, STYLES["realistic"])
    positive_prompt = style["prompt"]
    negative_prompt = style["negative"]

    if style_key == "custom" and custom_prompt:
        positive_prompt = f"{custom_prompt}, {positive_prompt}"

    # រៀបចំទំហំ
    with Image.open(image_path) as src_im:
        target_w, target_h = get_best_sdxl_dimensions(src_im.width, src_im.height, aspect_ratio)

    img_bytes = prepare_image_for_sdxl(image_path, aspect_ratio="4:5" if target_h > target_w else "1:1")
    
    # បង្កើត Mask សម្រាប់ការពារមុខ
    with Image.open(io.BytesIO(img_bytes)) as ready_img:
        mask_img = generate_face_mask(ready_img)
        mask_buf = io.BytesIO()
        mask_img.save(mask_buf, format="PNG")
        mask_bytes = mask_buf.getvalue()

    url = "https://api.stability.ai/v1/generation/stable-diffusion-xl-1024-v1-0/image-to-image/masking"

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    data = {
        "mask_source": "MASK_IMAGE_WHITE",
        "text_prompts[0][text]": positive_prompt,
        "text_prompts[0][weight]": "1.0",
        "text_prompts[1][text]": negative_prompt,
        "text_prompts[1][weight]": "-1.0",
        "cfg_scale": "7.5",
        "samples": "1",
        "steps": "30",
        "style_preset": "photographic" if style_key != "anime" else "anime"
    }

    files = {
        "init_image": ("init.jpg", img_bytes, "image/jpeg"),
        "mask_image": ("mask.png", mask_bytes, "image/png")
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, headers=headers, data=data, files=files)
        
        if response.status_code != 200:
            logger.error(f"Stability AI Error: {response.status_code} - {response.text}")
            response.raise_for_status()

        result = response.json()
        artifact = result.get("artifacts", [])[0]
        gen_img_bytes = base64.b64decode(artifact["base64"])

    # ផ្សំមុខដើមបញ្ចូលឡើងវិញដើម្បីកុំឱ្យបាត់អត្តសញ្ញាណ
    with Image.open(io.BytesIO(img_bytes)) as base_img, \
         Image.open(io.BytesIO(gen_img_bytes)) as generated_img:
        final_result = composite_face_shield(base_img, generated_img, mask_img)
        
        out_buf = io.BytesIO()
        final_result.save(out_buf, format="JPEG", quality=95)
        return out_buf.getvalue()
