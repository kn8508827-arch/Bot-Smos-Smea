import os

# Telegram & AI Keys
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# AI Configurations
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini")
AI_MODEL = os.getenv("AI_MODEL", "gemini-2.0-flash")
AI_RATE_LIMIT_SECONDS = int(os.getenv("AI_RATE_LIMIT_SECONDS", "3"))
AI_GLOBAL_RATE_LIMIT_SECONDS = float(os.getenv("AI_GLOBAL_RATE_LIMIT_SECONDS", "0.5"))
AI_DAILY_LIMIT_PER_USER = int(os.getenv("AI_DAILY_LIMIT_PER_USER", "50"))

# Admin & Settings
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
DRY_RUN_MODE = os.getenv("DRY_RUN_MODE", "False").lower() in ("true", "1", "yes")
