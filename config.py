import os
from dotenv import load_dotenv

# អានឯកសារ .env នៅពេល run លើម៉ាស៊ីនផ្ទាល់
load_dotenv()

# Telegram & AI Keys
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
STABILITY_API_KEY = os.getenv("STABILITY_API_KEY", "")
# AI Photo Studio (/studio) — Gemini image editing; needs billing enabled on the Gemini key
STUDIO_ENABLED = os.getenv("STUDIO_ENABLED", "False").lower() in ("true", "1", "yes")
STUDIO_IMAGE_MODEL = os.getenv("STUDIO_IMAGE_MODEL", "gemini-nano-banana-2.1")
STUDIO_DAILY_LIMIT = int(os.getenv("STUDIO_DAILY_LIMIT", "1"))  # edits per member per day; 0 = owner/admins only (they are unlimited)

# AI Configurations
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini")
AI_MODEL = os.getenv("AI_MODEL", "gemini-2.0-flash")
AI_VISION_MODEL = os.getenv("AI_VISION_MODEL", "gemini-3.8-flash")
AI_RATE_LIMIT_SECONDS = int(os.getenv("AI_RATE_LIMIT_SECONDS", "3"))
AI_GLOBAL_RATE_LIMIT_SECONDS = float(os.getenv("AI_GLOBAL_RATE_LIMIT_SECONDS", "0.5"))
AI_DAILY_LIMIT_PER_USER = int(os.getenv("AI_DAILY_LIMIT_PER_USER", "50"))

# Optional system-prompt persona file. Relative paths resolve from the project directory.
COOLER_PERSONA_PATH = os.getenv("COOLER_PERSONA_PATH", "persona/cooler_bot_2050.txt")



def _int_env(name: str, default: int) -> int:
    """Parse an integer environment variable safely, falling back to default."""
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    """Parse a floating-point environment variable safely."""
    raw = os.getenv(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


# Conversation context settings
AI_SUMMARY_ENABLED = os.getenv("AI_SUMMARY_ENABLED", "False").lower() in ("true", "1", "yes")
AI_CONTEXT_MESSAGE_LIMIT = _int_env("AI_CONTEXT_MESSAGE_LIMIT", 8)
AI_CONTEXT_CHAR_LIMIT = _int_env("AI_CONTEXT_CHAR_LIMIT", 4000)

# Persistent bot data. On Railway, point this inside the mounted volume.
BOT_DB_PATH = os.getenv("BOT_DB_PATH", "bot_database.db")

# Daily care notification pacing and bounded retry controls.
DAILYCARE_RATE_LIMIT_SECONDS = max(0.0, _float_env("DAILYCARE_RATE_LIMIT_SECONDS", 1.0))
DAILYCARE_MAX_SEND_ATTEMPTS = max(1, _int_env("DAILYCARE_MAX_SEND_ATTEMPTS", 3))
DAILYCARE_RETRY_MAX_WAIT_SECONDS = max(0.0, _float_env("DAILYCARE_RETRY_MAX_WAIT_SECONDS", 30.0))
DAILYCARE_BATCH_SIZE = max(1, min(_int_env("DAILYCARE_BATCH_SIZE", 50), 1000))
DAILYCARE_LOCK_TTL_SECONDS = max(15, _int_env("DAILYCARE_LOCK_TTL_SECONDS", 90))
DAILYCARE_ENABLED = os.getenv("DAILYCARE_ENABLED", "False").lower() in ("true", "1", "yes")

# Owner (Papa) identity. Never log this value. If 0, owner features are disabled.
OWNER_ID_RAW = os.getenv("OWNER_ID", "").strip()
OWNER_ID = int(OWNER_ID_RAW) if OWNER_ID_RAW.isdigit() else 0

# Admin & Settings
admin_ids_raw = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in admin_ids_raw.split(",") if x.strip().isdigit()]
DRY_RUN_MODE = os.getenv("DRY_RUN_MODE", "False").lower() in ("true", "1", "yes")
