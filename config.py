import os
from dotenv import load_dotenv

# អានឯកសារ .env នៅពេល run លើម៉ាស៊ីនផ្ទាល់
load_dotenv()

# Telegram & AI Keys
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
STABILITY_API_KEY = os.getenv("STABILITY_API_KEY", "")

# AI Configurations
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini")
AI_MODEL = os.getenv("AI_MODEL", "gemini-2.0-flash")
AI_RATE_LIMIT_SECONDS = int(os.getenv("AI_RATE_LIMIT_SECONDS", "3"))
AI_GLOBAL_RATE_LIMIT_SECONDS = float(os.getenv("AI_GLOBAL_RATE_LIMIT_SECONDS", "0.5"))
AI_DAILY_LIMIT_PER_USER = int(os.getenv("AI_DAILY_LIMIT_PER_USER", "50"))

# Admin & Settings
admin_ids_raw = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in admin_ids_raw.split(",") if x.strip().isdigit()]
# Numeric Telegram user ID of the bot owner ("Papa"). Set via environment variable; 0 means unset.
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
# Lowercase keywords: a Telegram first name, last name or username containing any of these
# (ignoring case, spaces and hyphens) is treated as Papa. Name checks can be spoofed, so
# OWNER_ID remains the stronger check.
PAPA_NAME_KEYWORDS = ["vidcooler"]
DRY_RUN_MODE = os.getenv("DRY_RUN_MODE", "False").lower() in ("true", "1", "yes")
