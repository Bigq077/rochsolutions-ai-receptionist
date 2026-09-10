"""T2: percentile the caller-perceived latency components over the stored corpus.

    python scripts/latency_percentiles.py [--clinic northgate] [--since 2026-09-01]

MEASUREMENT ONLY. This script reads `calls.latency` and prints distributions.
It changes nothing and is not on any hot path.

WHAT THE COLUMN HOLDS
---------------------
One record per call, `summary` plus a `turns` list. Per turn:

    ttfa_ms           first audio of any kind reaches the caller
    content_ttfa_ms   first audio of the REAL answer -- later than ttfa_ms
                      exactly when a filler covered the wait
    llm_ttft_ms       first token out of the model
    chunk_gate_ms     first token -> first chunk released to TTS
    tts_first_byte_ms first chunk -> first byte of audio back
    endpoint_wait_ms  caller stopped speaking -> the turn was dispatched

`content_ttfa_ms` is the number a caller experiences as "how long until she
answered me". `ttfa_ms` is how long until she made ANY sound, which the filler
ladder exists to keep low, and the two must be read as a pair: a low ttfa with a
high content_ttfa is the ladder working, not latency being fine.

THE SPLIT THE RUNBOOK ASKS FOR
------------------------------
"Split by turn kind (tool-result vs plain)" is now answerable. S-9 added
`tool_calls` to `TurnTiming` -- the number of tools the model asked for on the
turn, summed across its tool loop -- so the real split is reported below under
REAL SPLIT.

IT WILL BE EMPTY UNTIL NEW TRAFFIC ARRIVES, and that is not a fault. No row
written before 10 Sep 2026 carries the key, and a missing key is NOT OBSERVED,
never zero: counting the ~3,500 stored turns as "no tools ran" would put every
historical tool turn in the plain bucket and make the split worse than the
proxy it replaces. The REAL SPLIT section prints its own n, so an empty one is
visible rather than silently mistaken for a finding.

The PROXY is therefore kept alongside it rather than deleted -- it is what can
be said about the turns already stored. It is `covered`: turns where a filler
played before the content (content_ttfa_ms - ttfa_ms > 1s). Every tool turn
slow enough to matter is in that set, but so is any slow plain generation, so
its percentiles are an UPPER bound on the tool-turn cost, never the tool figure
itself. Retire it once the real split has enough turns to stand on.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

#: The five numbers that add up to what the caller waits for, plus the two
#: totals either side of the filler.
FIELDS = (
    "endpoint_wait_ms", "llm_ttft_ms", "chunk_gate_ms", "tts_first_byte_ms",
    "ttfa_ms", "content_ttfa_ms",
)
#: The bar in the definition of production-ready: p95 caller-perceived turn
#: latency under 1.5s, no dead air over 3s without a filler.
BAR_MS = 1500
DEAD_AIR_MS = 3000

#: A turn longer than this did not happen. On jv_v1 between 22 and 23 Aug 2026,
#: across seven retired build shas (c4b5b0c54dbd, 562715d35021, e4e5f936b821,
#: 8221248ff018, d9670eaeb18b, 2048582db65d, 625344fb4d1f), `ttfa_ms` and
#: `content_ttfa_ms` were written as an absolute clock reading rather than a
#: delta from t0 -- 1,934 turns carrying values around 11,941,606,930 ms, which
#: is 138 days. The component fields on those same turns (llm_ttft_ms,
#: chunk_gate_ms, tts_first_byte_ms) are sound; only the two totals are not.
#:
#: HISTORICAL, not live: no clinic has produced one since 23 Aug, and northgate,
#: theorem_v3 and vital_edge never did. It is filtered rather than fixed because
#: the rows cannot be repaired -- t0 was never stored -- and a percentile script
#: that ingests a 138-day turn reports a number nobody can act on. The count of
#: what was dropped is printed, so this can never be a silent filter.
IMPOSSIBLE_MS = 10_000_000


def _pct(values, p):
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * (p / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def _fmt(value):
    return "     -" if value is None else f"{value/1000.0:6.2f}"


def _row(label, values):
    return (
        f"  {label:<22}{len(values):>6}"
        f"{_fmt(_pct(values, 50))}{_fmt(_pct(values, 75))}"
        f"{_fmt(_pct(values, 90))}{_fmt(_pct(values, 95))}"
        f"{_fmt(_pct(values, 99))}{_fmt(max(values) if values else None)}"
    )


def _header(title):
    print(f"\n{title}")
    print(f"  {'':<22}{'n':>6}{'p50':>7}{'p75':>7}{'p90':>7}{'p95':>7}{'p99':>7}{'max':>7}   seconds")


def _load(clinic=None, since=None):
    from sqlalchemy import create_engine, text as sql

    where = ["latency is not null"]
    params = {}
    if clinic:
        where.append("clinic_id = :clinic")
        params["clinic"] = clinic
    if since:
        where.append("start_utc >= :since")
        params["since"] = since
    engine = create_engine(os.environ["OBS_DATABASE_URL"])
    with engine.connect() as conn:
        return conn.execute(sql(
            "select call_sid, clinic_id, build_sha, start_utc, latency from calls "
            "where " + " and ".join(where) + " order by start_utc"
        ), params).fetchall()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clinic")
    ap.add_argument("--since")
    args = ap.parse_args()

    rows = _load(args.clinic, args.since)
    turns = []
    calls = 0
    for sid, clinic, sha, start, latency in rows:
        record = json.loads(latency) if isinstance(latency, str) else latency
        if not isinstance(record, dict):
            continue
        calls += 1
        for turn in record.get("turns") or []:
            if isinstance(turn, dict):
                turn = dict(turn)
                turn["_clinic"] = clinic
                turn["_sha"] = sha
                turn["_call"] = sid
                turns.append(turn)

    print(f"calls with latency: {calls}     turns: {len(turns)}")
    dropped = defaultdict(int)
    for turn in turns:
        for field in ("ttfa_ms", "content_ttfa_ms"):
            value = turn.get(field)
            if isinstance(value, (int, float)) and value >= IMPOSSIBLE_MS:
                dropped[f"{turn.get('_clinic')}/{field}"] += 1
    if dropped:
        print("dropped as impossible (see IMPOSSIBLE_MS):",
              ", ".join(f"{k} x{v}" for k, v in sorted(dropped.items())))
    if args.clinic:
        print(f"clinic filter: {args.clinic}")
    if args.since:
        print(f"since: {args.since}")

    def series(rows_, field):
        # -1 is the sentinel for "this stage did not run on this turn"
        # (a superseded turn, or one answered without generation). Dropped
        # rather than counted as zero, which would flatter every percentile.
        return [
            int(r[field]) for r in rows_
            if isinstance(r.get(field), (int, float))
            and 0 <= int(r[field]) < IMPOSSIBLE_MS
        ]

    _header("ALL TURNS")
    for field in FIELDS:
        print(_row(field, series(turns, field)))

    by_path = defaultdict(list)
    for turn in turns:
        by_path[str(turn.get("path"))].append(turn)
    for path in sorted(by_path, key=lambda p: -len(by_path[p])):
        _header(f"path = {path}")
        for field in FIELDS:
            print(_row(field, series(by_path[path], field)))

    # The proxy, named as one. See the module docstring.
    covered, plain = [], []
    for turn in turns:
        ttfa = turn.get("ttfa_ms")
        content = turn.get("content_ttfa_ms")
        if not isinstance(ttfa, (int, float)) or not isinstance(content, (int, float)):
            continue
        (covered if content - ttfa > 1000 else plain).append(turn)
    _header("PROXY: a filler covered the wait (content_ttfa - ttfa > 1s)")
    for field in FIELDS:
        print(_row(field, series(covered, field)))
    _header("PROXY: no filler needed")
    for field in FIELDS:
        print(_row(field, series(plain, field)))

    # S-9: the real thing the proxy above was standing in for. A turn with no
    # `tool_calls` key was written before the field existed -- NOT OBSERVED,
    # and it must not fall into either bucket.
    tool_turns, plain_turns, unobserved = [], [], 0
    for turn in turns:
        count = turn.get("tool_calls")
        if not isinstance(count, (int, float)) or count < 0:
            unobserved += 1
            continue
        (tool_turns if count > 0 else plain_turns).append(turn)
    _header("REAL SPLIT: the turn ran a tool (S-9, tool_calls > 0)")
    for field in FIELDS:
        print(_row(field, series(tool_turns, field)))
    _header("REAL SPLIT: no tool ran")
    for field in FIELDS:
        print(_row(field, series(plain_turns, field)))
    print()
    print(f"  turns with no tool_calls key (written before S-9, NOT "
          f"observed): {unobserved}")
    if not tool_turns and not plain_turns:
        print("  -- the real split is empty. Every stored turn predates the "
              "field; read the PROXY above and nothing else.")

    by_clinic = defaultdict(list)
    for turn in turns:
        by_clinic[str(turn.get("_clinic"))].append(turn)
    _header("content_ttfa_ms by clinic")
    for clinic in sorted(by_clinic, key=lambda c: -len(by_clinic[c])):
        print(_row(clinic, series(by_clinic[clinic], "content_ttfa_ms")))

    by_stt = defaultdict(list)
    for turn in turns:
        by_stt[str(turn.get("stt_model"))].append(turn)
    _header("content_ttfa_ms by STT model")
    for model in sorted(by_stt, key=lambda m: -len(by_stt[m])):
        print(_row(model, series(by_stt[model], "content_ttfa_ms")))

    # Against the bar.
    content = series(turns, "content_ttfa_ms")
    ttfa = series(turns, "ttfa_ms")
    print()
    print(f"content_ttfa over {BAR_MS}ms: "
          f"{sum(1 for v in content if v > BAR_MS)}/{len(content)} "
          f"({100.0*sum(1 for v in content if v > BAR_MS)/max(1,len(content)):.1f}%)")
    print(f"ttfa over {DEAD_AIR_MS}ms (dead air, nothing said at all): "
          f"{sum(1 for v in ttfa if v > DEAD_AIR_MS)}/{len(ttfa)} "
          f"({100.0*sum(1 for v in ttfa if v > DEAD_AIR_MS)/max(1,len(ttfa)):.1f}%)")

    worst = sorted(
        (t for t in turns
         if isinstance(t.get("content_ttfa_ms"), (int, float))
         and 0 <= t["content_ttfa_ms"] < IMPOSSIBLE_MS),
        key=lambda t: -t["content_ttfa_ms"],
    )[:10]
    print("\nworst 10 turns by content_ttfa_ms")
    print(f"  {'call':<16}{'seq':>4}{'content':>9}{'ttfa':>8}{'ttft':>8}"
          f"{'gate':>8}{'tts':>7}  path / clinic")
    for turn in worst:
        print(f"  {str(turn['_call'])[:14]:<16}{turn.get('turn_seq'):>4}"
              f"{turn.get('content_ttfa_ms'):>9}{turn.get('ttfa_ms'):>8}"
              f"{turn.get('llm_ttft_ms'):>8}{turn.get('chunk_gate_ms'):>8}"
              f"{turn.get('tts_first_byte_ms'):>7}  {turn.get('path')} / {turn.get('_clinic')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
