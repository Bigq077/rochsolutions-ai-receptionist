"""
app/obs/digest.py
-----------------
Once-a-day review digest (tiered alerting, v2).

    python -m app.obs.digest [--hours 24]

Callback-worthy calls text the operator immediately (see app/obs/judge.py). The
merely-clumsy "review" calls do NOT — they are collected here into a single daily
summary so the phone isn't buzzed per call. Schedule this once a day (e.g. a
Render Cron Job).

Two channels, deliberately different in scope:
- EMAIL (preferred, when SMTP + OBS_DIGEST_EMAIL_TO are set): a whole-system report
  covering EVERY call in the window — volume, bookings, mean score, failure themes,
  the review calls broken out, then the full listing. Sent every day even when there
  is nothing to review, so an empty inbox means the cron is broken, not that the day
  was clean.
- SMS (fallback, only when email is not configured): review calls only, and silent
  when there are none — the phone must not gain a daily heartbeat.

Reads the durable `calls` table for the window, so it needs no in-memory state and
survives restarts.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app import config
from app.obs import redact, reports, store


def _all_calls(hours: int) -> List[dict]:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    return store.list_calls(since=since)


def _review_calls(hours: int) -> List[dict]:
    return [c for c in _all_calls(hours) if c.get("action_needed") == "review"]


def _needs_attention(call: dict) -> bool:
    """True for calls worth a human's eye — the judge's own review test, reused so
    the digest and the alert router can never disagree about what 'bad' means."""
    from app.obs import judge
    return judge.needs_review(call)


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


def _worst_first(calls: List[dict]) -> List[dict]:
    """Lowest quality_score first; unjudged calls sort last (treated as a 5)."""
    return sorted(calls, key=lambda c: c.get("quality_score") or 5)


def build_email(calls: List[dict], hours: int) -> tuple:
    """(subject, body) for the daily email over EVERY call in the window.

    Unlike the SMS summary this always renders — a zero-call day produces a real
    email saying so, so an empty inbox means the cron failed rather than that the
    day was clean.

    :param calls: every stored call in the window, not just the review ones.
    """
    review = [c for c in calls if _needs_attention(c)]
    s = reports.summarise(calls)
    mean = f"{s['mean_quality_score']:.1f}/5" if s["mean_quality_score"] is not None else "—"
    themes = ", ".join(f"{k}×{v}" for k, v in list(s["failure_tags"].items())[:5]) or "none"

    subject = (f"Susie daily — {len(calls)} call(s), {s['booked']} booked, "
               f"{len(review)} to review ({hours}h)")

    head = [
        f"Susie daily report — last {hours}h",
        "",
        f"  Calls:    {s['volume']}",
        f"  Booked:   {s['booked']} ({s['booking_rate']:.0%})",
        f"  Judged:   {s['scored']}, mean {mean}",
        f"  Themes:   {themes}",
    ]

    rows: List[str] = []
    if review:
        rows += ["", f"Calls to review (worst first) — {len(review)}:"]
        for c in _worst_first(review):
            tags = ", ".join(c.get("failure_tags") or []) or "—"
            rows.append(f"  [{c.get('quality_score')}/5] {c.get('call_sid')}  ({tags})")
            if c.get("evidence"):
                rows.append(f"      {c['evidence']}")
            if config.OBS_DIGEST_INCLUDE_TRANSCRIPTS:
                rows.extend(_transcript_lines(c))
                rows.append("")
    else:
        rows += ["", "Calls to review: none — nothing needs a human this window."]

    rows += ["", "All calls (worst first):"]
    if calls:
        for c in _worst_first(calls):
            score = c.get("quality_score")
            flag = "  <-- review" if _needs_attention(c) else ""
            booked = "booked" if c.get("booking_confirmed") else (c.get("outcome") or "—")
            rows.append(
                f"  [{score if score is not None else '?'}/5] {c.get('call_sid')}  "
                f"{c.get('clinic_id') or '?'}  {booked}{flag}"
            )
    else:
        rows.append("  (no calls in this window)")

    rows += ["", "Replay any with:  python -m app.obs.replay <call_sid>"]
    return subject, "\n".join([*head, *rows])


async def _run(hours: int) -> int:
    from app.obs import emailer

    calls = _all_calls(hours)
    review = [c for c in calls if c.get("action_needed") == "review"]

    # Preferred channel: a whole-system email, sent every day regardless of whether
    # anything needs review, so a missing email is a broken cron rather than a quiet day.
    if emailer.is_configured():
        subject, body = build_email(calls, hours)
        sent = await asyncio.to_thread(emailer.send_email, subject, body)
        print(subject)
        print("\nSent by email." if sent else "\n(email send failed — not sent)")
        return 0

    # Fallback: operator SMS, review calls only, silent when there is nothing to say.
    summary = build_summary(review, hours)
    if summary is None:
        print(f"No review-classified calls in the last {hours}h — nothing to send.")
        return 0

    from app.obs import alerts
    sent = await alerts.review_alert(summary)
    print(summary)
    print("\nSent by SMS." if sent
          else "\n(no SMS recipient configured / send failed — not sent)")
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
