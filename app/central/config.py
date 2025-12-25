import os

BASE_URL = os.getenv("BASE_URL", "")
ENV = os.getenv("ENV", "dev")

# These will be used later (safe to be empty now)
DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL")

GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
DEFAULT_CALENDAR_ID = os.getenv("DEFAULT_CALENDAR_ID")

