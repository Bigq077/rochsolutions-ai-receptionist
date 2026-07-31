"""Which build is running — resolved once, cached for the process lifetime.

WHY THIS EXISTS
---------------
Until now, "which build served this call?" was answered by arithmetic:
scripts/detect_defects.py holds a list of deploy timestamps and buckets each call
by the most recent boundary before it started. That list has misattributed calls
four times — most recently on 2026-07-31, when a boundary set from push time plus
the usual 3-6 minute Render estimate labelled CA42486ff4 as the previous build.
The deploy had actually landed in about a minute. The call proved it only because
it happened to carry guard counters that existed on no earlier build.

A build label that is wrong is worse than no label: a fix looks unproven when it
worked, or proven when it never ran. The process knows its own commit — it should
say so on every call rather than have it reconstructed afterwards.

The boundary list does not go away. It still labels the ~4,000 historic calls
recorded before this field existed, and it is still the fallback when the SHA is
unavailable. But for every call from here on, the record is authoritative.

RESOLUTION ORDER
----------------
1. app/_version.py — written at build time by scripts/write_version.py, which
   render.yaml runs after pip install. The normal production path.
2. RENDER_GIT_COMMIT — set by Render during builds. Covers a container where
   step 1 did not run.
3. git rev-parse — local development, and Render if .git survived the build.

Returns "unknown" rather than raising or returning None. This is called on call
teardown: an exception here would lose the call record, which is a far worse
outcome than an unlabelled one, and "unknown" is honest in a way that a silently
missing field is not.
"""
from __future__ import annotations

import logging
import os
import subprocess

logger = logging.getLogger(__name__)

UNKNOWN = "unknown"

_cached: str | None = None


def _resolve() -> str:
    # 1. Build-time constant — the production path.
    try:
        from app._version import GIT_COMMIT  # type: ignore[attr-defined]

        if GIT_COMMIT and GIT_COMMIT != UNKNOWN:
            return str(GIT_COMMIT).strip()[:12]
    except Exception:
        # Absent locally (gitignored, written only at build time) — expected,
        # not an error worth logging on every process start.
        pass

    # 2. Render's own environment variable.
    env = (os.environ.get("RENDER_GIT_COMMIT") or "").strip()
    if env:
        return env[:12]

    # 3. git, for local runs. Short timeout: this must never hang startup.
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).strip()
        if out:
            return out[:12]
    except Exception:
        pass

    return UNKNOWN


def build_sha() -> str:
    """The short commit SHA of the running build, or "unknown".

    Cached: the answer cannot change while the process lives, and this is called
    once per call teardown on the hot path.
    """
    global _cached
    if _cached is None:
        _cached = _resolve()
        if _cached == UNKNOWN:
            logger.warning(
                "[build_info] could not determine the running commit — calls will "
                "be recorded with build_sha='unknown' and fall back to the "
                "detect_defects boundary list"
            )
        else:
            logger.info("[build_info] running build %s", _cached)
    return _cached
