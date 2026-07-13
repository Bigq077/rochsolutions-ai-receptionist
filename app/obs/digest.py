"""
app/obs/digest.py
-----------------
Once-a-day review digest (tiered alerting, v2).

    python -m app.obs.digest [--hours 24]

Callback-worthy calls text the operator immediately (see app/obs/judge.py). The
merely-clumsy "review" calls do NOT — they are collected here into a single daily
summary SMS so the phone isn't buzzed per call. Schedule this once a day (e.g. a
Render Cron Job).

Reads the durable `calls` table for the window, so it needs no in-memory state and
survives restarts. No-op (sends nothing) when there are no review-classified calls,
or when alerts/DB are not configured.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app import config
from app.obs import redact, store


def _review_calls(hours: int) -> List[dict]:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    return [c for c in store.list_calls(since=since)
            if c.get("action_needed") == "review"]


def _call_names(call: dict) -> List[str]:
    """Known caller names on the record — the only names redaction can reliably strike."""
    names: List[str] = []
    collected = call.get("collected") or {}
    for key in ("name", "full_name", "first_name", "last_name"):
        v = collected.get(key)
        if v:
            names.append(str(v))
    return names


def _transcript_lines(call: dict) -> List[str]:
    """Redacted turn-by-turn transcript for one call, indented under its listing.

    Phones/emails are hard-stripped and known caller names struck (app/obs/redact.py).
    If the safety check still finds a phone/email, the transcript is WITHHELD rather
    than risk emitting PII — a missing transcript never aborts the digest.
    """
    turns = call.get("transcript") or []
    if not turns:
        return ["      (no transcript stored)"]
    redacted = redact.redact_transcript(turns, _call_names(call))
    try:
        redact.assert_transcript_clean(redacted)  # HARD: no phone/email survives
    except redact.PIILeakError:
        return ["      (transcript withheld — redaction check failed)"]
    lines = ["      --- transcript (redacted) ---"]
    for i, t in enumerate(redacted, 1):
        role = str(t.get("role", "?")).upper()
        lines.append(f"      [{i:>3}] {role:<9} | {t.get('text', '')}")
    return lines


def build_summary(calls: List[dict], hours: int) -> Optional[str]:
    """One text summarising the window's review calls, or None if there are none."""
    if not calls:
        return None
    tags: Counter = Counter()
    for c in calls:
        for t in c.get("failure_tags") or []:
            tags[t] += 1
    top = ", ".join(f"{k}×{v}" for k, v in tags.most_common(5)) or "assorted"
    lines = [
        f"[Susie] Daily review — {len(calls)} call(s) to improve in the last {hours}h "
        f"(no callbacks needed).",
        f"Themes: {top}.",
        "Run `python -m app.obs.weekly` to review; nothing here needs a patient callback.",
    ]
    return "\n".join(lines)


def build_email(calls: List[dict], hours: int) -> Optional[tuple]:
    """(subject, body) for the digest email — a fuller, per-call listing. None if empty."""
    if not calls:
        return None
    subject = f"Susie daily review — {len(calls)} call(s) to improve ({hours}h)"
    head = build_summary(calls, hours) or ""
    rows = ["", "Calls to review (worst first):"]
    for c in sorted(calls, key=lambda c: c.get("quality_score") or 5):
        tags = ", ".join(c.get("failure_tags") or []) or "—"
        rows.append(f"  [{c.get('quality_score')}/5] {c.get('call_sid')}  ({tags})")
        if c.get("evidence"):
            rows.append(f"      {c['evidence']}")
        if config.OBS_DIGEST_INCLUDE_TRANSCRIPTS:
            rows.extend(_transcript_lines(c))
            rows.append("")
    rows.append("")
    rows.append("Replay any with:  python -m app.obs.replay <call_sid>")
    return subject, "\n".join([head, *rows])


async def _run(hours: int) -> int:
    calls = _review_calls(hours)
    summary = build_summary(calls, hours)
    if summary is None:
        print(f"No review-classified calls in the last {hours}h — nothing to send.")
        return 0

    from app.obs import emailer
    # Prefer email for the daily digest when configured; fall back to operator SMS.
    if emailer.is_configured():
        subject, body = build_email(calls, hours)
        sent = await asyncio.to_thread(emailer.send_email, subject, body)
        channel = "email"
    else:
        from app.obs import alerts
        sent = await alerts.review_alert(summary)
        channel = "SMS"

    print(summary)
    print(f"\nSent by {channel}." if sent
          else f"\n(no {channel} recipient configured / send failed — not sent)")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.obs.digest")
    parser.add_argument("--hours", type=int, default=24, help="lookback window in hours")
    args = parser.parse_args(argv)

    if not config.DATABASE_URL:
        print("ERROR: OBS_DATABASE_URL (or DATABASE_URL) not set — no store to summarise.",
              file=sys.stderr)
        return 2
    return asyncio.run(_run(args.hours))


if __name__ == "__main__":
    raise SystemExit(main())
