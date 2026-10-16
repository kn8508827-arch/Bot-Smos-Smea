import os
from dotenv import load_dotenv

# អានឯកសារ .env (សម្រាប់ពេល run លើកុំព្យូទ័រផ្ទាល់ខ្លួន)
load_dotenv()

# Telegram & AI Keys
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
STABILITY_API_KEY = os.getenv("STABILITY_API_KEY", "")

# AI Configurations
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini")
# កែសម្រួលមកប្រើ Model ស្តង់ដារដើម្បីកុំឱ្យបោះ Error 404
AI_MODEL = os.getenv("AI_MODEL", "gemini-2.0-flash")
AI_RATE_LIMIT_SECONDS = int(os.getenv("AI_RATE_LIMIT_SECONDS", "3"))
AI_GLOBAL_RATE_LIMIT_SECONDS = float(os.getenv("AI_GLOBAL_RATE_LIMIT_SECONDS", "0.5"))
AI_DAILY_LIMIT_PER_USER = int(os.getenv("AI_DAILY_LIMIT_PER_USER", "50"))

# Photo Studio Settings
PHOTO_PROVIDER = os.getenv("PHOTO_PROVIDER", "stability")
PHOTO_DAILY_LIMIT_PER_USER = int(os.getenv("PHOTO_DAILY_LIMIT_PER_USER", "0"))

# Admin & Settings
admin_ids_raw = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in admin_ids_raw.split(",") if x.strip().isdigit()]
DRY_RUN_MODE = os.getenv("DRY_RUN_MODE", "False").lower() in ("true", "1", "yes")
