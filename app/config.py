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

# Safety kill-switch for outbound transfers. When truthy, Susie will NOT dial a
# live transfer leg and will NOT fire the "transferring a patient" heads-up SMS.
# Defaults to OFF (unset) so production behaviour is unchanged; set on staging so
# sweep/test calls don't ring or text real staff numbers.
TRANSFER_DISABLED = os.getenv("TRANSFER_DISABLED", "").strip().lower() in ("1", "true", "yes", "on")

# --- Observability ---
# Sentry DSN — set to enable error reporting to Sentry.io.
# Leave unset (or empty) to disable Sentry entirely.
SENTRY_DSN = os.getenv("SENTRY_DSN", "")

# Phase 1 — durable call capture (see Susie_Call_Observability_Spec_for_Jules.md §5.1).
# When enabled AND DATABASE_URL is set, each completed call is persisted to a
# Postgres `calls` table (transcript + metadata) in the teardown path, additively
# and after the call ends. Default OFF so production behaviour is unchanged until
# a store is provisioned. With the flag OFF or DATABASE_URL empty, capture is a
# fast no-op — no new latency or failure modes on the live call path.
OBS_CAPTURE_ENABLED = os.getenv("OBS_CAPTURE_ENABLED", "false").lower() == "true"

# Connection string for the durable observability store (managed Postgres, EU
# region — see spec §7). Empty until provisioned; capture no-ops while unset.
# SQLAlchemy URL form, e.g. postgresql+psycopg2://user:pass@host:5432/dbname
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Phase 2 — failure alerting (see Susie_Call_Observability_Spec_for_Jules.md §5.2).
# When enabled, completed calls matching a failure condition alert the operator
# (Quentin) via SMS and/or Slack, and pipeline exceptions are captured to Sentry.
# Default OFF so production behaviour is unchanged. With the flag OFF the alert
# router is a fast no-op — no messages are ever sent — so it cannot affect any
# clinic or ping anyone until explicitly turned on.
OBS_ALERTS_ENABLED = os.getenv("OBS_ALERTS_ENABLED", "false").lower() == "true"

# Where immediate operator alerts go. SMS reuses the existing Twilio path; the
# Slack webhook is optional. If neither is set, immediate alerts no-op even when
# the flag is on (nothing to send to).
OBS_ALERT_SMS_TO = os.getenv("OBS_ALERT_SMS_TO", "")
OBS_SLACK_WEBHOOK = os.getenv("OBS_SLACK_WEBHOOK", "")

# Phase 3 — LLM-as-judge (see Susie_Call_Observability_Spec_for_Jules.md §5.3).
# When enabled, each captured call is scored by Claude after teardown against a
# versioned rubric, and the judgement is stored on the call row. Default OFF, and
# a no-op without an ANTHROPIC_API_KEY — production behaviour is unchanged until a
# store (Phase 1) is provisioned and this is turned on. Depends on Phase 1 capture.
OBS_JUDGE_ENABLED = os.getenv("OBS_JUDGE_ENABLED", "false").lower() == "true"

# Model used by the judge. Defaults to the most capable model; an operator can set
# a cheaper model (e.g. claude-sonnet-5 / claude-haiku-4-5) to trade some judgement
# quality for cost at high call volume. Re-run calibration if this changes.
OBS_JUDGE_MODEL = os.getenv("OBS_JUDGE_MODEL", "claude-opus-4-8")
