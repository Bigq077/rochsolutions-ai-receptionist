import os

BASE_URL = os.getenv("BASE_URL", "").rstrip("/")
ENV = os.getenv("ENV", "dev")

REDIS_URL = os.getenv("REDIS_URL")

DEFAULT_CALENDAR_ID = os.getenv("DEFAULT_CALENDAR_ID")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

# --- Phase 3: Tool-calling LLM receptionist ---
RECEPTIONIST_MODEL = os.getenv("RECEPTIONIST_MODEL", "claude-sonnet-4-6")
PHASE3_ENABLED = os.getenv("PHASE3_ENABLED", "false").lower() == "true"
