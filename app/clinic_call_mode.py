"""
app/clinic_call_mode.py
-----------------------
Runtime override for a clinic's human-first call routing.

A clinic texts OFF to their own Susie number and inbound calls ring the
practitioner's phone first, with Susie picking up only what they miss. They text
ON to go back. The override expires at the next London midnight.

The call-routing behaviour itself already existed and is untouched by this
module: `/ms/incoming` emits a human-first `<Dial>` with a "press 1" whisper leg
(`app/media_streams/router.py`). What did not exist was any way to change it
without a commit and a redeploy — `call_overflow.enabled` is read from a repo
file through an mtime-keyed cache in `get_clinic()`. This module is the Redis
layer in front of that read.

── Two rules this module exists to enforce ────────────────────────────────────

1. **Resolution never raises.** `resolve_overflow` sits on the critical path of
   every inbound call. A broken toggle must degrade to the clinic.json default,
   never to a failed webhook — a clinic whose phone stops working because a
   convenience feature had a bad day is a far worse outcome than a toggle that
   silently stops toggling.

2. **A write that cannot be trusted is refused, not faked.** `redis_set_json`
   degrades to a per-process `_MEM_JSON` dict when `redis_client` is None. With
   more than one Render worker that produces a toggle which appears to work
   intermittently — the practitioner's phone rings or doesn't depending on which
   worker served the request. `set_mode` therefore returns None rather than
   writing, so the caller can decline to confirm.

   `redis_client` is a module-level global assigned at startup, so it is read
   through the module (`redis_store.redis_client`) rather than imported by name.
   `from ... import redis_client` binds None at import time and would never see
   the connection — the same binding trap that made patching `send_sms`
   ineffective in the SMS tests.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from app.booking.booking.utils import LONDON_TZ

logger = logging.getLogger(__name__)

MODE_HUMAN_FIRST = "human_first"
MODE_AI_FIRST = "ai_first"

# TTL bounds. The floor stops a toggle sent at 23:59:30 expiring before the
# clinic has read the confirmation; the cap keeps a clock or timezone fault from
# pinning a clinic into human-first for days.
_TTL_FLOOR_SECONDS = 60
_TTL_CAP_SECONDS = 12 * 60 * 60


def _key(clinic_id: str) -> str:
    return f"call_mode:{(clinic_id or '').strip().lower()}"


def _next_london_midnight(now: Optional[datetime] = None) -> datetime:
    now = (now or datetime.now(LONDON_TZ)).astimezone(LONDON_TZ)
    return (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _ttl_seconds(now: Optional[datetime] = None) -> int:
    now = (now or datetime.now(LONDON_TZ)).astimezone(LONDON_TZ)
    seconds = int((_next_london_midnight(now) - now).total_seconds())
    return max(_TTL_FLOOR_SECONDS, min(seconds, _TTL_CAP_SECONDS))


def _config_enabled(clinic: Dict[str, Any]) -> bool:
    """The clinic.json default — what routing does with no override."""
    return bool((clinic.get("call_overflow") or {}).get("enabled"))


def _redis_live() -> bool:
    """True when a real Redis connection exists.

    Read through the module on purpose — see the note in the docstring.
    """
    try:
        from app.storage import redis_store
        return redis_store.redis_client is not None
    except Exception:
        return False


async def resolve_overflow(
    clinic_id: str, clinic: Dict[str, Any]
) -> Tuple[bool, str]:
    """Return (human_first_enabled, reason). Never raises.

    reason is one of "override", "config", "config:redis_unavailable" and is
    folded into the router's log line so the Render log answers "why did Susie
    answer this call?" without anyone having to read Redis.
    """
    if not _redis_live():
        return _config_enabled(clinic), "config:redis_unavailable"
    try:
        from app.storage.redis_store import redis_get_json

        record = await redis_get_json(_key(clinic_id))
        if isinstance(record, dict) and record.get("mode"):
            return record.get("mode") == MODE_HUMAN_FIRST, "override"
    except Exception as exc:
        # Deliberately swallowed. See rule 1 in the module docstring.
        logger.warning(
            "[call_mode] override lookup failed for %r — falling back to "
            "clinic.json (routing unchanged): %r",
            clinic_id, exc,
        )
        return _config_enabled(clinic), "config:redis_unavailable"
    return _config_enabled(clinic), "config"


async def set_mode(
    clinic_id: str, mode: str, set_by: str
) -> Optional[Dict[str, Any]]:
    """Write the override.

    Returns the stored payload, or None when it could not be stored durably —
    in which case the caller must NOT tell the clinic their phone routing
    changed. See rule 2 in the module docstring.
    """
    if mode not in (MODE_HUMAN_FIRST, MODE_AI_FIRST):
        logger.error("[call_mode] refusing unknown mode %r", mode)
        return None
    if not _redis_live():
        logger.error(
            "[call_mode] no Redis connection — refusing to write a per-process "
            "override that would apply on some workers and not others"
        )
        return None

    now = datetime.now(LONDON_TZ)
    payload = {
        "mode": mode,
        "set_by": set_by,
        "set_at": now.isoformat(),
        "expires_at": _next_london_midnight(now).isoformat(),
    }
    try:
        from app.storage.redis_store import redis_set_json

        await redis_set_json(_key(clinic_id), payload, ttl_seconds=_ttl_seconds(now))
    except Exception as exc:
        logger.error("[call_mode] override write failed for %r: %r", clinic_id, exc)
        return None
    logger.info(
        "[call_mode] %s set to %s by ***%s until %s",
        clinic_id, mode, (set_by or "")[-4:], payload["expires_at"],
    )
    return payload


async def clear_mode(clinic_id: str) -> None:
    """Delete the override.

    Used to revert a toggle whose confirmation SMS never sent: the clinic must
    never be left in a routing state they were not told about.
    """
    try:
        from app.storage.redis_store import redis_delete_key

        await redis_delete_key(_key(clinic_id))
        logger.info("[call_mode] override cleared for %r", clinic_id)
    except Exception as exc:
        logger.error("[call_mode] override clear failed for %r: %r", clinic_id, exc)


async def current_mode(clinic_id: str, clinic: Dict[str, Any]) -> Dict[str, Any]:
    """For the STATUS command: {"mode", "source", "expires_at"}."""
    human_first, reason = await resolve_overflow(clinic_id, clinic)
    expires_at = None
    if reason == "override":
        try:
            from app.storage.redis_store import redis_get_json

            record = await redis_get_json(_key(clinic_id)) or {}
            expires_at = record.get("expires_at")
        except Exception:
            expires_at = None
    return {
        "mode": MODE_HUMAN_FIRST if human_first else MODE_AI_FIRST,
        "source": reason,
        "expires_at": expires_at,
    }
