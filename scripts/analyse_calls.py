#!/usr/bin/env python3
"""Aggregate Susie call logs into one table.

WHY THIS EXISTS
---------------
Diagnosing "Susie can't hear me" on 2026-07-24 meant reading three full call
logs by hand, and the conclusion drawn from those three ("the inbound audio
leg is dying") was refuted by the very next call. Small samples read manually
produce confident wrong answers.

One call log is ~10k tokens. A 30-call test round is ~300k — more than fits in
a context window, so paste-and-read stops working at exactly the volume worth
measuring. This turns N calls into one table.

INPUT
-----
Raw Render log text, as pasted. Files, directories, globs, or stdin:

    python scripts/analyse_calls.py render_paste.txt
    python scripts/analyse_calls.py logs/            # every *.log / *.txt under it
    pbpaste | python scripts/analyse_calls.py -

A call CLOSES at `[ms_conn] cleanup call_sid=`, but its authoritative lines —
`[ms_lost] CALL SUMMARY`, the outcome and the duration — are all emitted after
it, so the record stays open through that epilogue and rotates only when a
line proves the next call has started. Splitting on `cleanup` alone put every
call's summary into the following call's record.

A trailing segment with no cleanup (call still running, or a truncated paste)
is reported as `<unterminated>` rather than silently dropped — a truncated
paste that read as a clean run is exactly the quiet wrongness this exists to
stop. Text with no call evidence at all yields no calls, not a phantom one.

OUTPUT
------
Per-call rows plus an aggregate. `--json` emits the same data for scripting.

No third-party imports: this has to run anywhere, including on a box that has
never had `pip install` run against this repo (CLAUDE.md — no new deps).
"""

from __future__ import annotations

import argparse
import glob as _glob
import json
import math
import os
import re
import sys
from typing import Any, Dict, List, Optional

# The latency bar from CLAUDE.md §6: p95 caller-perceived turn latency < 1.5 s.
TTFA_BAR_MS = 1500

_CALL_BOUNDARY_RE = re.compile(r"\[ms_conn\] cleanup call_sid=(\S+)")

# Lines that can only come from a call that is actively running. Seeing one
# AFTER a cleanup is what proves the next call has begun — as opposed to the
# closed call's epilogue (CALL SUMMARY / obs.store / Row built / SMS routing),
# which must stay attributed to the call it describes.
_NEW_CALL_ACTIVITY_RE = re.compile(
    r"\[ms_router\] inbound params"
    r"|\[ms_conn\] new WebSocket connection"
    r"|\[ms_stt\] Begin received"
    r"|\[ms_stt\] TCP\+TLS connected"
    r"|\[LAT\]"
    r"|\[ms_watchdog\] WATCHDOG_START"
    r"|\[ms_silence\] tts_finished in"
)
_LOST_SUMMARY_RE = re.compile(
    r"\[ms_lost\] CALL SUMMARY call_sid=(\S+) lost_total=(\d+) "
    r"by_reason=(\{.*?\}) inbound_audio=(\S+) media_frames=(\d+)"
)
_LOST_EVENT_RE = re.compile(r"\[ms_lost\] reason=(\S+) ")
_ROW_BUILT_RE = re.compile(
    r"Row built — outcome=(\S+) name=(\S+) phone=(\S+) dur=(\d+)s"
)
_TTS_FINISHED_RE = re.compile(r"\[ms_silence\] tts_finished in ([\d.]+)s")
_STT_ATTEMPT_RE = re.compile(r"\[ms_stt\] connecting attempt=(\d+)")
# Every terminal state a screen can reach, so a sweep can be read as a table
# rather than by grepping five different strings.
#
#   ARMED      Layer 1 matched a trigger and asked the question itself
#   clear      answer classified negative, screen completed
#   POSITIVE   answer classified red flag, escalation spoken, booking frozen
#   unclear    answer resolved nothing; screen stays pending and is re-driven
#   ORPHAN     the MODEL asked a screen Layer 1 never armed (2485229). The
#              single most diagnostic line in a sweep: ORPHAN with no matching
#              ARMED anywhere means Layer 1 is dormant and Layer 2 is silently
#              doing the whole job — the 2026-07-25 16:20 failure.
#   TRUNCATED  the answer was endpointed mid-clause and was NOT allowed to
#              clear the screen (188e478). Counts how often a 600 ms pause is
#              cutting safety answers in half.
#
# 'answer' is optional because the unclear/TRUNCATED lines carry it and the
# others do not.
_SCREEN_RE = re.compile(
    r"\[clinical_screening\] screen (\S+) (?:answer )?"
    r"(ARMED|clear|POSITIVE|unclear|ORPHAN|TRUNCATED)"
)

# [LAT] key=value pairs we care about. Captured generically so a new field
# added to the latency line does not require touching this script.
_LAT_FIELDS = ("ttfa_ms", "llm_ttft_ms", "chunk_gate_ms", "tts_first_byte_ms")


def _percentile(values: List[float], pct: float) -> Optional[float]:
    """Nearest-rank percentile. Returns None for an empty series.

    Nearest-rank (not interpolated) because these are per-turn observations
    and a real measured turn is more useful to quote than a synthetic midpoint
    between two turns.

    ceil, not round: the standard nearest-rank definition is ceil(P/100 * N),
    and round() would also hit Python's banker's rounding — round(2.5) == 2 —
    so p50 of five samples returned the 2nd value instead of the median.
    """
    if not values:
        return None
    ordered = sorted(values)
    k = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[min(k, len(ordered)) - 1]


def _has_call_evidence(call: Dict[str, Any]) -> bool:
    """True when a record shows a call actually happened.

    Guards the unterminated-tail path: without this, any trailing non-Susie
    text (a shell prompt, a stack trace, an empty paste) became a phantom
    `<unterminated>` call and inflated the call count — a wrong number, which
    is worse than no number for a tool whose whole job is counting.
    """
    return bool(
        call["ttfa_ms"]
        or call["watchdog_fires"]
        or call["safety_net_fires"]
        or call["dead_ends"]
        or call["backstop_arms"]
        or call["screens"]
        or call["lost_events"]
        or call["media_frames"] is not None
        or call["lost_total"] is not None
        or call["outcome"] is not None
        or call["longest_turn_s"] > 0.0
        or call["call_sid"] is not None
    )


def _blank_call() -> Dict[str, Any]:
    return {
        "call_sid": None,
        "outcome": None,
        "duration_s": None,
        "name": None,
        "lost_total": None,
        "lost_by_reason": {},
        "lost_events": {},
        "inbound_audio": None,
        "media_frames": None,
        "ttfa_ms": [],
        "llm_ttft_ms": [],
        "chunk_gate_ms": [],
        "tts_first_byte_ms": [],
        "watchdog_fires": 0,
        "safety_net_fires": 0,
        "graceful_closes": 0,
        "dead_ends": 0,
        "backstop_arms": 0,
        "screens": {},
        "emergency_intercepts": 0,
        "stt_reconnects": 0,
        "tts_auth_401": 0,
        "longest_turn_s": 0.0,
        "lines": 0,
    }


def _scan_line(call: Dict[str, Any], line: str) -> None:
    """Fold one log line into the in-progress call record."""
    call["lines"] += 1

    m = _LOST_SUMMARY_RE.search(line)
    if m:
        call["call_sid"] = m.group(1)
        call["lost_total"] = int(m.group(2))
        # by_reason is a Python dict repr ({'a': 1}) — JSON needs double quotes.
        try:
            call["lost_by_reason"] = json.loads(m.group(3).replace("'", '"'))
        except Exception:
            call["lost_by_reason"] = {"<unparsed>": m.group(3)}
        call["inbound_audio"] = m.group(4)
        call["media_frames"] = int(m.group(5))
        return

    m = _LOST_EVENT_RE.search(line)
    if m:
        r = m.group(1)
        call["lost_events"][r] = call["lost_events"].get(r, 0) + 1
        return

    m = _ROW_BUILT_RE.search(line)
    if m:
        call["outcome"] = m.group(1)
        call["name"] = None if m.group(2) == "None" else m.group(2)
        call["duration_s"] = int(m.group(4))
        return

    if "[LAT]" in line:
        for field in _LAT_FIELDS:
            fm = re.search(rf"\b{field}=(-?\d+)", line)
            if fm:
                v = int(fm.group(1))
                if v >= 0:  # -1 is the "not applicable this turn" sentinel
                    call[field].append(v)
        return

    m = _TTS_FINISHED_RE.search(line)
    if m:
        call["longest_turn_s"] = max(call["longest_turn_s"], float(m.group(1)))
        return

    m = _SCREEN_RE.search(line)
    if m:
        key = f"{m.group(1)}:{m.group(2)}"
        call["screens"][key] = call["screens"].get(key, 0) + 1
        return

    m = _STT_ATTEMPT_RE.search(line)
    if m and int(m.group(1)) > 1:
        call["stt_reconnects"] += 1
        return

    if "WATCHDOG_FIRE q_gen" in line:
        call["watchdog_fires"] += 1
    elif "[ms_safety_net] 10s dead-air" in line:
        call["safety_net_fires"] += 1
    elif "max re-asks reached" in line:
        call["graceful_closes"] += 1
    elif "turn asked nothing and no question is outstanding" in line:
        call["dead_ends"] += 1
    elif "[ms_watchdog] BACKSTOP armed" in line:
        call["backstop_arms"] += 1
    elif "EMERGENCY detected" in line:
        call["emergency_intercepts"] += 1
    elif "elevenlabs.io/v1/models" in line and "401" in line:
        call["tts_auth_401"] += 1


def parse(text: str) -> List[Dict[str, Any]]:
    """Split raw log text into per-call records.

    Boundary model, driven by the real log shape rather than by position:

        [ms_conn] cleanup call_sid=X      <- call CLOSES here
        [ms_lost] CALL SUMMARY ...        <- but the authoritative totals,
        [obs.store] captured ...             the outcome and the duration all
        Row built — outcome=... dur=...s     arrive AFTER it
        [ms_router] inbound params ...    <- and only HERE does the next
        [ms_stt] Begin received ...          call actually begin

    Splitting on `cleanup` alone put every call's own summary into the NEXT
    call's record, which double-counted `[ms_lost]` (once as an event, once in
    the summary) and stranded outcome/inbound_audio one row late. Verified
    against the 2026-07-24/25 logs. So: mark closed on cleanup, keep folding
    the epilogue in, and rotate only when a line proves a new call has started.
    """
    calls: List[Dict[str, Any]] = []
    current = _blank_call()
    closed = False

    for line in text.splitlines():
        if closed and _NEW_CALL_ACTIVITY_RE.search(line):
            calls.append(current)
            current = _blank_call()
            closed = False

        _scan_line(current, line)

        boundary = _CALL_BOUNDARY_RE.search(line)
        if boundary:
            current["call_sid"] = current["call_sid"] or boundary.group(1)
            closed = True

    if _has_call_evidence(current):
        if not closed:
            # No cleanup line: call still running, or the paste was truncated.
            # Reported explicitly — a truncated paste that read as a clean run
            # is exactly the quiet wrongness this tool exists to prevent.
            current["call_sid"] = current["call_sid"] or "<unterminated>"
        calls.append(current)
    return calls


def summarise(calls: List[Dict[str, Any]]) -> Dict[str, Any]:
    all_ttfa = [v for c in calls for v in c["ttfa_ms"]]
    lost_by_reason: Dict[str, int] = {}
    outcomes: Dict[str, int] = {}
    audio: Dict[str, int] = {}

    for c in calls:
        for r, n in (c["lost_by_reason"] or {}).items():
            lost_by_reason[r] = lost_by_reason.get(r, 0) + int(n)
        # Fall back to per-event counts when no CALL SUMMARY line was captured
        # (truncated paste) so a partial log still contributes.
        if not c["lost_by_reason"]:
            for r, n in c["lost_events"].items():
                lost_by_reason[r] = lost_by_reason.get(r, 0) + n
        if c["outcome"]:
            outcomes[c["outcome"]] = outcomes.get(c["outcome"], 0) + 1
        if c["inbound_audio"]:
            audio[c["inbound_audio"]] = audio.get(c["inbound_audio"], 0) + 1

    return {
        "calls": len(calls),
        "turns": len(all_ttfa),
        "ttfa_p50_ms": _percentile(all_ttfa, 50),
        "ttfa_p95_ms": _percentile(all_ttfa, 95),
        "ttfa_max_ms": max(all_ttfa) if all_ttfa else None,
        "turns_over_bar": sum(1 for v in all_ttfa if v > TTFA_BAR_MS),
        "turns_over_bar_pct": (
            round(100.0 * sum(1 for v in all_ttfa if v > TTFA_BAR_MS) / len(all_ttfa), 1)
            if all_ttfa else None
        ),
        "chunk_gate_p95_ms": _percentile(
            [v for c in calls for v in c["chunk_gate_ms"]], 95
        ),
        "llm_ttft_p95_ms": _percentile(
            [v for c in calls for v in c["llm_ttft_ms"]], 95
        ),
        "lost_by_reason": dict(sorted(lost_by_reason.items(), key=lambda kv: -kv[1])),
        "lost_total": sum(lost_by_reason.values()),
        "outcomes": dict(sorted(outcomes.items(), key=lambda kv: -kv[1])),
        "inbound_audio": dict(sorted(audio.items(), key=lambda kv: -kv[1])),
        "watchdog_fires": sum(c["watchdog_fires"] for c in calls),
        "safety_net_fires": sum(c["safety_net_fires"] for c in calls),
        "graceful_closes": sum(c["graceful_closes"] for c in calls),
        "dead_ends": sum(c["dead_ends"] for c in calls),
        "backstop_arms": sum(c["backstop_arms"] for c in calls),
        "emergency_intercepts": sum(c["emergency_intercepts"] for c in calls),
        "stt_reconnects": sum(c["stt_reconnects"] for c in calls),
        "tts_auth_401": sum(c["tts_auth_401"] for c in calls),
        "longest_turn_s": max([c["longest_turn_s"] for c in calls], default=0.0),
        "screens": {
            k: sum(c["screens"].get(k, 0) for c in calls)
            for k in sorted({k for c in calls for k in c["screens"]})
        },
    }


def _fmt(v: Any, width: int = 0) -> str:
    s = "-" if v is None else str(v)
    return s.rjust(width) if width else s


def render_text(calls: List[Dict[str, Any]], agg: Dict[str, Any]) -> str:
    out: List[str] = []
    out.append("=" * 100)
    out.append(f"{agg['calls']} call(s), {agg['turns']} turn(s)")
    out.append("=" * 100)

    hdr = (
        f"{'call':<14} {'outcome':<20} {'dur':>5} {'turns':>5} "
        f"{'ttfa p95':>9} {'lost':>5} {'audio':<9} {'wd':>3} {'net':>3} "
        f"{'dead':>4} {'bkstp':>5} {'maxTTS':>7}"
    )
    out.append(hdr)
    out.append("-" * len(hdr))
    for c in calls:
        sid = (c["call_sid"] or "?")
        sid = sid if sid.startswith("<") else sid[-12:]
        out.append(
            f"{sid:<14} {_fmt(c['outcome'])[:20]:<20} "
            f"{_fmt(c['duration_s'], 5)} {len(c['ttfa_ms']):>5} "
            f"{_fmt(_percentile(c['ttfa_ms'], 95), 9)} "
            f"{_fmt(c['lost_total'] if c['lost_total'] is not None else sum(c['lost_events'].values()), 5)} "
            f"{_fmt(c['inbound_audio'])[:9]:<9} "
            f"{c['watchdog_fires']:>3} {c['safety_net_fires']:>3} "
            f"{c['dead_ends']:>4} {c['backstop_arms']:>5} "
            f"{c['longest_turn_s']:>7.1f}"
        )

    out.append("")
    out.append("LATENCY   (CLAUDE.md bar: p95 < %d ms)" % TTFA_BAR_MS)
    out.append(
        f"  ttfa p50={_fmt(agg['ttfa_p50_ms'])} ms  p95={_fmt(agg['ttfa_p95_ms'])} ms  "
        f"max={_fmt(agg['ttfa_max_ms'])} ms"
    )
    over = agg["turns_over_bar_pct"]
    out.append(
        f"  turns over bar: {agg['turns_over_bar']}/{agg['turns']}"
        + (f" ({over}%)" if over is not None else "")
    )
    out.append(
        f"  components p95: llm_ttft={_fmt(agg['llm_ttft_p95_ms'])} ms  "
        f"chunk_gate={_fmt(agg['chunk_gate_p95_ms'])} ms"
    )
    out.append(f"  longest single TTS turn: {agg['longest_turn_s']:.1f}s")

    out.append("")
    out.append("UTTERANCES LOST")
    if agg["lost_by_reason"]:
        for r, n in agg["lost_by_reason"].items():
            out.append(f"  {n:>4}  {r}")
        out.append(f"  {agg['lost_total']:>4}  TOTAL")
    else:
        out.append("     0  (none)")

    out.append("")
    out.append("RECOVERY")
    out.append(
        f"  watchdog fires={agg['watchdog_fires']}  safety-net fires={agg['safety_net_fires']}  "
        f"graceful closes={agg['graceful_closes']}"
    )
    out.append(
        f"  question-less dead ends={agg['dead_ends']}  backstop arms={agg['backstop_arms']}"
    )

    out.append("")
    out.append("INBOUND AUDIO")
    out.append(
        "  " + ("  ".join(f"{k}={v}" for k, v in agg["inbound_audio"].items()) or "(none recorded)")
    )

    out.append("")
    out.append("OUTCOMES")
    out.append("  " + ("  ".join(f"{k}={v}" for k, v in agg["outcomes"].items()) or "(none recorded)"))

    if agg["screens"]:
        out.append("")
        out.append("CLINICAL SCREENING")
        for k, v in agg["screens"].items():
            out.append(f"  {v:>4}  {k}")

    warn = []
    if agg["stt_reconnects"]:
        warn.append(f"STT reconnects={agg['stt_reconnects']}")
    if agg["tts_auth_401"]:
        warn.append(f"ElevenLabs 401s={agg['tts_auth_401']}")
    if agg["emergency_intercepts"]:
        warn.append(f"emergency intercepts={agg['emergency_intercepts']}")
    if warn:
        out.append("")
        out.append("NOTE")
        for w in warn:
            out.append(f"  {w}")

    return "\n".join(out)


def _iter_paths(args: List[str]) -> List[str]:
    paths: List[str] = []
    for a in args:
        if os.path.isdir(a):
            for ext in ("*.log", "*.txt", "*.jsonl"):
                paths.extend(sorted(_glob.glob(os.path.join(a, "**", ext), recursive=True)))
        else:
            expanded = sorted(_glob.glob(a))
            paths.extend(expanded or [a])
    return paths


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Aggregate Susie call logs into one table.",
        epilog="Pass '-' to read from stdin.",
    )
    ap.add_argument("paths", nargs="*", default=["-"],
                    help="log files, directories or globs (default: stdin)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args(argv)

    if not args.paths or args.paths == ["-"]:
        text = sys.stdin.read()
    else:
        chunks = []
        for p in _iter_paths(args.paths):
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as fh:
                    chunks.append(fh.read())
            except OSError as exc:
                print(f"skip {p}: {exc}", file=sys.stderr)
        if not chunks:
            print("no readable input", file=sys.stderr)
            return 2
        text = "\n".join(chunks)

    calls = parse(text)
    if not calls:
        print("no calls found — is this Susie log output?", file=sys.stderr)
        return 1

    agg = summarise(calls)
    if args.json:
        print(json.dumps({"summary": agg, "calls": calls}, indent=2, default=str))
    else:
        print(render_text(calls, agg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
