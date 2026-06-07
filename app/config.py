import os

BASE_URL = os.getenv("BASE_URL", "").rstrip("/")
ENV = os.getenv("ENV", "dev")

REDIS_URL = os.getenv("REDIS_URL")

DEFAULT_CALENDAR_ID = os.getenv("DEFAULT_CALENDAR_ID")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

# --- Phase 3: Tool-calling LLM receptionist ---
RECEPTIONIST_MODEL = os.getenv("RECEPTIONIST_MODEL", "claude-sonnet-4-6")
HAIKU_MODEL = os.getenv("HAIKU_MODEL", "claude-haiku-4-5-20251001")
PHASE3_ENABLED = os.getenv("PHASE3_ENABLED", "false").lower() == "true"

# --- OpenAI Realtime API voice pipeline ---
# When REALTIME_ENABLED=true, /twilio/voice returns <Connect><Stream> instead
# of <Gather>, routing the call through the OpenAI Realtime WebSocket bridge.
# Set REALTIME_ENABLED=false to instantly revert to the legacy HTTP flow.
REALTIME_ENABLED = os.getenv("REALTIME_ENABLED", "false").lower() == "true"
REALTIME_VOICE = os.getenv("REALTIME_VOICE", "coral")        # coral = warm British female
REALTIME_VAD_SILENCE_MS = int(os.getenv("REALTIME_VAD_SILENCE_MS", "800"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# --- Security ---
# Twilio Auth Token — used to validate X-Twilio-Signature on webhook requests.
# Set this env var in production to reject fake/replayed webhook calls.
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")

# HMAC secret for hardening Redis session keys.
# If set, session keys become call:{call_sid}:{hmac8}, making them unguessable
# even if an attacker gains read access to the Redis keyspace.
SESSION_SECRET = os.getenv("SESSION_SECRET", "")

# --- Call transfer fallback ---
# Dial target used when a clinic has no 'transfer_phone' configured.
# Override via env var to avoid hardcoding a real UK number in source code.
TRANSFER_FALLBACK_NUMBER = os.getenv("TRANSFER_FALLBACK_NUMBER", "+447502211207")

# --- Observability ---
# Sentry DSN — set to enable error reporting to Sentry.io.
# Leave unset (or empty) to disable Sentry entirely.
SENTRY_DSN = os.getenv("SENTRY_DSN", "")
