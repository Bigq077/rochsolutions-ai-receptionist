#!/usr/bin/env python3
"""Replay stored calls through the clinical screening layer, offline.

WHY THIS EXISTS
---------------
Clinical screening is tuned by editing keyword lists in clinic.json. Until now
the only evidence that a change was an improvement was a handful of test calls
and the parametrised regression tests — neither of which says how often a screen
fires on REAL traffic, or how many of those firings a physiotherapist would call
warranted.

2026-08-21, JV call CA4feeeec6f9077d4912eb7d2a7f1d6846: the caller described
losing feeling in his legs and losing bladder control, and Susie asked him
whether he had any bladder changes. Separately, the trigger lists are bare
body-part mentions, so "I want a sports massage, my lower back is tight" arms
the cauda equina screen and the caller is asked about bowel control.

Narrowing a SAFETY trigger without measuring it first is how a regression hides
for a week. This turns the stored corpus into a before/after table.

WHAT IT DOES
------------
Drives the real `update_screening_state` state machine over each stored call's
transcript, in order, with a fresh session dict per call. Pure functions only —
no engine, no network, no LLM, no audio. `last_bot_prompt` / `last_question` are
primed from the preceding assistant turn so the answer-grading handshake and the
orphan detector behave exactly as they do live.

    python scripts/replay_screening.py --clinic-id jv_v1
    python scripts/replay_screening.py --clinic-id jv_v1 --since 2026-07-26
    python scripts/replay_screening.py --clinic-json /tmp/candidate.json
    python scripts/replay_screening.py --json > after.json

CORPUS TRAPS (both cost a session when ignored)
-----------------------------------------------
* Columns added after a call was captured read back blank. Screening capture
  landed 2026-07-26 (ba195e8), so that is the default floor for anything
  comparing against the STORED screening state.
* THE TRANSCRIPT IS NOT THE CALL. Turn capture only became complete on
  2026-08-07 (ca69374), and even after it the OPENING user turn is frequently
  missing on screening-armed calls — which is precisely the turn that arms a
  screen. Replaying an incomplete transcript therefore arms screens mid-call
  that live had already resolved, and reports them as `stranded`. Cross-check
  any surprising row against the stored `screening` column (which records what
  actually happened live) before believing it: on CA85b1f4cc63 this harness
  said "stranded", and the stored state said completed/cleared.
  Consequence: this corpus CANNOT measure Layer 1's true arm rate. It measures
  answer grading and the model's own screening behaviour reliably; treat arm
  counts as a floor, not a rate.
* Test calls and real traffic live in the same table. --build-sha and
  --exclude-caller exist so a round of scripted test calls does not get reported
  as clinic traffic.

No new dependencies (CLAUDE.md): stdlib plus what app.obs.store already needs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# app.config reads os.getenv at import time and does NOT load .env itself (only
# app/main.py does, at server start). A script that skips this silently reads an
# empty DATABASE_URL and reports "no corpus" on a machine that has one.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, ".env"))
except ImportError:  # pragma: no cover - dotenv ships with the app
    pass

from app.media_streams import clinical_screening as cs  # noqa: E402


# ── Corpus provenance ────────────────────────────────────────────
# The plan called for splitting the corpus by build_sha branch membership, so a
# round of test calls is not counted as real traffic. Measured 2026-08-21 on
# jv_v1, that axis does not answer the question:
#
#   * 77 of 214 calls (36%) carry no build_sha at all;
#   * 50 of the 58 shas present ARE on a JV live branch — because the demo line
#     runs the same builds. "This sha is on jv_v2" says nothing about who rang.
#
# Nor does the number dialled: both Susie lines are rung by the same handsets.
#
# The discriminator that works is the CALLER. Two handsets account for 204 of
# the 214 calls and ring both numbers, one of them repeatedly at 03:00 UK time.
# Of 38 calls that touch a screen, 37 are from those two handsets.
#
# Keep this list current. A number added here is removed from every "real
# traffic" figure the harness prints, so an over-broad entry hides real calls —
# which is the failure this block exists to prevent, pointed the other way.
_TEST_CALLERS = {
    "+33617769867",   # dev handset, 97 calls, many overnight
    "+447502211207",  # dev handset, 107 calls
}

# Which Susie number was rung. Recorded for provenance only — NOT a test/real
# split, for the reason above. See the demo-line memory: +447366263180 is the
# demo line and its Sheets/EVAL_STAFF warnings are known-accepted;
# +447367002651 is the live line, where they are not.
_SUSIE_LINES = {
    "+447366263180": "demo",
    "+447367002651": "live",
}


def audience_of(call: Dict[str, Any], test_callers: Optional[set] = None) -> str:
    """'test' if this call came from a known dev handset, else 'real'.

    'real' means only "not from a handset we know is ours" — it is the
    residual, not a positive identification of a patient.
    """
    callers = _TEST_CALLERS if test_callers is None else test_callers
    return "test" if str(call.get("caller_number") or "") in callers else "real"


def _load_clinic(clinic_json: Optional[str], clinic_id: str) -> Dict[str, Any]:
    """Clinic config to screen against.

    --clinic-json points at a candidate file so a proposed trigger change can be
    measured against the same corpus before it is committed. Without it, the
    clinic's committed config is used.
    """
    if clinic_json:
        with open(clinic_json, encoding="utf-8") as fh:
            return json.load(fh)
    from app.clinic_config import get_clinic
    return get_clinic(clinic_id)


def _turns(call: Dict[str, Any]) -> List[Dict[str, str]]:
    t = call.get("transcript") or []
    return [x for x in t if isinstance(x, dict) and x.get("text")]


def replay_call(call: Dict[str, Any], clinic: Dict[str, Any]) -> Dict[str, Any]:
    """Drive one stored call through the screening state machine.

    Returns the events this call produced. A call that armed nothing returns an
    empty `events` list — those are the majority and are counted, not listed.
    """
    session: Dict[str, Any] = {}
    events: List[Dict[str, Any]] = []

    for turn in _turns(call):
        role = (turn.get("role") or "").lower()
        text = turn.get("text") or ""
        if role != "user":
            # Prime the cross-turn handshake exactly as connection.py does:
            # both keys, capped at the same 200 chars the live path caps at.
            last_bot = text[:cs._LAST_BOT_PROMPT_CAP]
            session["last_bot_prompt"] = last_bot
            session["last_question"] = last_bot
            continue

        before_pending = session.get(cs.PENDING_SCREEN_KEY)
        before_done = list(session.get(cs.SCREENS_COMPLETED_KEY) or [])
        before_paths = dict(session.get(cs.SCREEN_ARM_PATHS_KEY) or {})

        # Whether this turn is even ELIGIBLE to be graded as the screen answer.
        # A pending screen is graded only while the last thing Susie said still
        # looks like the screen question; once she says anything else the screen
        # can never be answered again and simply sits pending. Counting those
        # turns as `unclear` would blame the classifier for a turn it never saw.
        was_asked = False
        if before_pending:
            _scr = cs.get_screen(clinic, before_pending) or {}
            was_asked = cs._question_was_asked(session, _scr)
        result = cs.update_screening_state(session, clinic, text)
        action = result.get("action")
        after_pending = session.get(cs.PENDING_SCREEN_KEY)
        after_done = list(session.get(cs.SCREENS_COMPLETED_KEY) or [])
        arm_paths = session.get(cs.SCREEN_ARM_PATHS_KEY) or {}

        # The orphan and already-answered paths ARM and RESOLVE inside a single
        # turn, so pending_screen is None both before and after. Reading only
        # pending_screen silently drops them — which would understate exactly
        # the paths this harness exists to measure.
        armed_this_turn = [k for k in arm_paths if k not in before_paths]
        graded_id = before_pending or (armed_this_turn[0] if armed_this_turn else None)

        if action == "ask_screen":
            # A screen already in arm_paths is being RE-asked, not armed. The
            # bounded re-ask (e595df5) and the hedge probe both return
            # ask_screen on a screen that armed turns ago; counting those as
            # arms inflates the arm column and fills the arming-utterance list
            # with turns that armed nothing — "please book that in" appeared
            # there as a cauda equina trigger and matches no keyword at all.
            # Phase 3 is measured off that list, so the distinction must hold.
            events.append({
                "kind": "arm" if after_pending not in before_paths else "reask",
                "screen": after_pending,
                "path": arm_paths.get(after_pending),
                "utterance": text,
            })
        elif action == "escalate":
            newly = [s for s in after_done if s not in before_done]
            sid = newly[0] if newly else before_pending
            events.append({
                "kind": "escalate",
                "screen": sid,
                "path": arm_paths.get(sid),
                "utterance": text,
                # An escalation with no prior pending screen never asked the
                # question — it fired straight off the arming utterance.
                "without_asking": before_pending is None,
            })
        elif action == "emergency":
            events.append({"kind": "emergency", "screen": None, "utterance": text})
        elif graded_id:
            # No action spoken. Either the screen was graded this turn, or it
            # was pending but no longer gradable (see was_asked).
            newly = [s for s in after_done if s not in before_done]
            if newly:
                kind = "clear"
            elif was_asked or graded_id not in before_paths:
                kind = "unclear"
            else:
                kind = "stranded"
            events.append({
                "kind": kind,
                "screen": graded_id,
                "path": arm_paths.get(graded_id),
                "utterance": text,
            })

    return {
        "call_sid": call.get("call_sid"),
        "clinic_id": call.get("clinic_id"),
        "build_sha": call.get("build_sha"),
        "caller_number": call.get("caller_number"),
        "dialled_number": call.get("dialled_number"),
        "line": _SUSIE_LINES.get(str(call.get("dialled_number") or ""), "?"),
        "audience": audience_of(call),
        "start_utc": str(call.get("start_utc") or ""),
        "outcome": call.get("outcome") or call.get("reason"),
        "stored_screening": call.get("screening"),
        "events": events,
    }


def summarise(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    per_screen: Dict[str, Counter] = defaultdict(Counter)
    paths: Dict[str, Counter] = defaultdict(Counter)
    armed_calls = 0
    for r in results:
        if r["events"]:
            armed_calls += 1
        for e in r["events"]:
            sid = e.get("screen") or "(emergency)"
            per_screen[sid][e["kind"]] += 1
            if e.get("path"):
                paths[sid][e["path"]] += 1
            if e.get("without_asking"):
                per_screen[sid]["escalate_without_asking"] += 1
    return {
        "calls": len(results),
        "calls_with_any_screening": armed_calls,
        "per_screen": {k: dict(v) for k, v in per_screen.items()},
        "arm_paths": {k: dict(v) for k, v in paths.items()},
    }


def _pct(n: int, d: int) -> str:
    return f"{(100.0 * n / d):.0f}%" if d else "n/a"


def _print_report(results: List[Dict[str, Any]], summary: Dict[str, Any]) -> None:
    print("=" * 78)
    print(f"calls replayed          : {summary['calls']}")
    print(f"calls touching a screen : {summary['calls_with_any_screening']}"
          f"  ({_pct(summary['calls_with_any_screening'], summary['calls'])})")

    # Provenance, printed on every run and not behind a flag. A screening
    # figure read as real traffic when it is a round of our own test calls is
    # the specific mistake this harness exists to stop, and it is invisible
    # unless the split is in front of you.
    armed = [r for r in results if r["events"]]
    for label, rows in (("all calls", results), ("of which armed", armed)):
        tally = Counter(r.get("audience", "?") for r in rows)
        lines = Counter(r.get("line", "?") for r in rows)
        print(f"{label:<24}: test {tally.get('test', 0)} / real "
              f"{tally.get('real', 0)}   "
              f"[demo {lines.get('demo', 0)} / live {lines.get('live', 0)}"
              + (f" / other {lines.get('?', 0)}" if lines.get("?") else "") + "]")
    real_armed = sum(1 for r in armed if r.get("audience") == "real")
    if armed and not real_armed:
        print("  ⚠ every armed call is from a dev handset — there is no real "
              "traffic in this corpus to tune triggers against")
    print("=" * 78)
    if not summary["per_screen"]:
        print("no screening events in this corpus")
        return

    header = (f"\n{'screen':<18} {'arm':>5} {'reask':>6} {'esc':>5} {'esc/noask':>10} "
              f"{'clear':>6} {'unclear':>8} {'stranded':>9}")
    print(header)
    print("-" * 78)
    for sid in sorted(summary["per_screen"]):
        c = summary["per_screen"][sid]
        print(f"{sid:<18} {c.get('arm', 0):>5} {c.get('reask', 0):>6} {c.get('escalate', 0):>5} "
              f"{c.get('escalate_without_asking', 0):>10} "
              f"{c.get('clear', 0):>6} {c.get('unclear', 0):>8} "
              f"{c.get('stranded', 0):>9}")

    print("")
    print("  arm      = FIRST time this screen armed on this call")
    print("  reask    = asked again (stranded re-ask / hedge probe), "
          "not a new arm")
    print("  unclear  = graded, verdict undecidable (a classifier gap)")
    print("  stranded = pending but NOT gradable: Susie has since said "
          "something else,")
    print("             so the answer window is shut and the screen never "
          "resolves")
    print("\narm paths")
    print("-" * 78)
    for sid in sorted(summary["arm_paths"]):
        print(f"  {sid:<18} {summary['arm_paths'][sid]}")

    # The list a human marks genuine / spurious. This is the whole point of the
    # harness — a count cannot tell you whether a firing was warranted.
    print("\narming utterances (mark each genuine / spurious)")
    print("-" * 78)
    for r in results:
        for e in r["events"]:
            if e["kind"] in ("arm", "escalate"):
                flag = " [no-ask]" if e.get("without_asking") else ""
                sid = e.get("screen") or "-"
                print(f"  {str(r['call_sid'])[:12]}  {e['kind']:<9} {sid:<16}{flag}")
                print(f"      {e['utterance'][:150]!r}")


def _filter_sha_on_branch(calls: List[Dict[str, Any]], ref: str) -> List[Dict[str, Any]]:
    """Keep calls whose build_sha is an ancestor of `ref`.

    Note what this DROPS: a call with no build_sha cannot be placed on any
    branch, and 36% of the jv_v1 corpus has none. It is reported rather than
    silently discarded, because a corpus that quietly shrinks by a third is how
    a replay comes to measure something other than what was asked.

    Cherry-picks also defeat it — the same change carries a different sha on
    each branch — so a False here means "not built from this ref", never "this
    fix is missing from that branch".
    """
    import subprocess
    cache: Dict[str, bool] = {}

    def on_branch(sha: str) -> bool:
        if sha not in cache:
            cache[sha] = subprocess.call(
                ["git", "merge-base", "--is-ancestor", sha, ref],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                cwd=_ROOT,
            ) == 0
        return cache[sha]

    kept, no_sha = [], 0
    for c in calls:
        sha = (c.get("build_sha") or "").strip()
        if not sha:
            no_sha += 1
            continue
        if on_branch(sha):
            kept.append(c)
    if no_sha:
        print(f"[--sha-on-branch] dropped {no_sha} call(s) carrying no "
              f"build_sha — they cannot be placed on any branch",
              file=sys.stderr)
    return kept


def _parse_date(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--clinic-id", default="jv_v1",
                    help="clinic whose config to screen with, and whose calls "
                         "to load (default: jv_v1 — the only clinic with a "
                         "clinical_screening block)")
    ap.add_argument("--clinic-json",
                    help="path to a CANDIDATE clinic.json, to measure a "
                         "proposed trigger change against the same corpus")
    ap.add_argument("--since", default="2026-07-26",
                    help="ISO date floor (default 2026-07-26, when screening "
                         "capture landed; pass 1970-01-01 to replay everything)")
    ap.add_argument("--until")
    ap.add_argument("--build-sha", action="append",
                    help="only calls on this build (repeatable) — use to "
                         "separate a round of test calls from real traffic")
    ap.add_argument("--exclude-caller", action="append", default=[],
                    help="drop calls from this caller number (repeatable)")
    ap.add_argument("--audience", choices=("all", "test", "real"), default="all",
                    help="'test' = calls from a known dev handset, 'real' = "
                         "everything else. See _TEST_CALLERS; 'real' is a "
                         "residual, not a positive identification of a patient")
    ap.add_argument("--test-caller", action="append", default=[],
                    help="extra number to treat as a dev handset (repeatable)")
    ap.add_argument("--sha-on-branch",
                    help="keep only calls whose build_sha is contained in this "
                         "git ref (e.g. origin/jv_v2). The literal build_sha "
                         "split — note 36%% of calls carry no sha, so this "
                         "silently drops them; prefer --audience")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    clinic = _load_clinic(args.clinic_json, args.clinic_id)
    if not cs.screening_enabled(clinic):
        print(f"clinic {args.clinic_id!r} has no enabled clinical_screening "
              f"block — nothing to replay", file=sys.stderr)
        return 2

    # NB: deliberately NOT store.is_enabled() — that gates on
    # OBS_CAPTURE_ENABLED, which governs WRITING new calls. Reading a corpus
    # only needs a store URL, and capture is off by default on every branch.
    from app import config
    from app.obs import store
    if not config.DATABASE_URL:
        print("no obs store configured (OBS_DATABASE_URL) — cannot load a "
              "corpus", file=sys.stderr)
        return 2

    since = _parse_date(args.since)
    until = _parse_date(args.until) if args.until else None
    calls = store.list_calls(since=since, until=until, clinic_id=args.clinic_id)

    if args.build_sha:
        wanted = set(args.build_sha)
        calls = [c for c in calls
                 if any((c.get("build_sha") or "").startswith(w) for w in wanted)]
    if args.exclude_caller:
        dropped = set(args.exclude_caller)
        calls = [c for c in calls if (c.get("caller_number") or "") not in dropped]
    test_callers = _TEST_CALLERS | set(args.test_caller)
    if args.audience != "all":
        calls = [c for c in calls
                 if audience_of(c, test_callers) == args.audience]
    if args.sha_on_branch:
        calls = _filter_sha_on_branch(calls, args.sha_on_branch)
    calls = [c for c in calls if _turns(c)]

    results = [replay_call(c, clinic) for c in calls]
    summary = summarise(results)

    if args.json:
        json.dump({"summary": summary, "calls": results}, sys.stdout, indent=2,
                  default=str)
        print()
    else:
        _print_report(results, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
