"""Mine the obs corpus into committed, PII-free regression scenarios.

    python -m scripts.harvest_regressions --dry-run
    python -m scripts.harvest_regressions --per-signature 3 --limit 60

WHY THIS EXISTS
---------------
`app/obs/to_scenario.py` and `app/obs/regress.py` both work, are unit-tested,
and had never once been run: `tests/auto/scenarios/regressions/` did not exist.
This is the driver that was missing. to_scenario takes ONE call_sid, and the
corpus has 320 calls judged 2 or worse -- committing all of them would be 320
modules nobody reads, most of them the same defect over and over.

SELECTION
---------
Deduplicated by failure SIGNATURE (the judge's sorted failure_tags), capped per
signature, worst score first. That yields breadth rather than depth: a hundred
copies of `booking_error` prove nothing a single one does not, and the point of
this net is to notice a NEW kind of failure, not to weigh the known ones.

There is deliberately NO bias toward the hold-speech situations. It was in an
earlier draft of this docstring and never in the code; dedup-by-signature
already gives the breadth that would have bought, and a net that selects for the
thing being changed is not much of a net.

WHAT THESE SCENARIOS ARE, AND ARE NOT
-------------------------------------
`regress.py` re-checks a STORED transcript. It does not re-drive the engine, so
a scenario here is a never-regress-this-transcript net -- it catches a phrase
coming back -- and NOT a before/after instrument. It cannot tell you a fix
worked. `tests/harness/` is what re-drives the live turn loop, and
`scripts/replay_situational_heads.py` is what measures a change across the
corpus. Do not read a green run here as more than it is.

The emitted `expected` is a conservative deterministic baseline. Sharpen it by
hand when you fix the underlying failure -- to_scenario captures the input and
the recorded failure, it does not invent the correct behaviour.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# BEFORE app.config is imported. app.config reads DATABASE_URL at import time and
# app.obs.store reads it from there, so loading the .env inside main() leaves the
# store with no engine -- get_call then returns None for every call and the
# harvest silently writes nothing.
try:
    from dotenv import load_dotenv

    load_dotenv(".env")
except Exception:
    pass

from app.obs import redact, store, to_scenario  # noqa: E402

_OUT = Path("tests/auto/scenarios/regressions")


def _signature(call) -> str:
    tags = call.get("failure_tags") or []
    return ",".join(sorted(str(t) for t in tags)) or "untagged"


def candidates(limit_scan: int = 2000):
    """Judged-bad calls, worst first. Raises nothing; returns plain dicts."""
    from sqlalchemy import create_engine, text

    url = os.getenv("OBS_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("OBS_DATABASE_URL is not set")
    engine = create_engine(url)
    query = text(
        "select call_sid from calls "
        "where transcript is not null and quality_score is not null "
        "  and quality_score <= 2 "
        "order by quality_score asc, start_utc desc limit :n"
    )
    with engine.connect() as conn:
        return [r[0] for r in conn.execute(query, {"n": limit_scan})]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(_OUT))
    parser.add_argument("--per-signature", type=int, default=3,
                        help="most scenarios to keep per failure signature")
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sids = candidates()
    seen = Counter()
    written = 0
    leaked = 0
    skipped_dupe = 0
    out_dir = Path(args.out)

    for sid in sids:
        if written >= args.limit:
            break
        call = store.get_call(sid)
        if call is None:
            continue
        signature = _signature(call)
        if seen[signature] >= args.per_signature:
            skipped_dupe += 1
            continue
        try:
            scenario = to_scenario.build_scenario(call)
        except redact.PIILeakError as exc:
            # Not a failure of this script. The assertion is doing its job, and
            # a call whose PII cannot be scrubbed must not become a committed
            # fixture -- it is dropped, loudly.
            leaked += 1
            print(f"SKIP (pii) {sid[:14]}: {exc}", file=sys.stderr)
            continue
        if len(scenario.get("responses") or []) < 2:
            # A one-utterance call has nothing to replay.
            continue
        seen[signature] += 1
        written += 1
        if args.dry_run:
            print(f"would write {scenario['id']:28s} sig={signature}")
            continue
        path = to_scenario.write_scenario(scenario, out_dir)
        print(f"wrote {path.name:32s} sig={signature}")

    print()
    print(f"written        : {written}")
    print(f"skipped (dupe) : {skipped_dupe}")
    print(f"skipped (pii)  : {leaked}")
    print(f"signatures     : {len(seen)}")
    for signature, n in seen.most_common():
        print(f"  {n:3d}  {signature}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
