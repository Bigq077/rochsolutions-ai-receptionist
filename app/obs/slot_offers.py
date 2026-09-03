"""
app/obs/slot_offers.py
----------------------
Every availability lookup, paired with the offer built from it.

WHY THIS EXISTS
---------------
Three defects in the week to 2026-09-03 were found by a phone call and none by
the 7,945-test suite: the 8pm/10am wrong-time acceptance, an apology matcher
that covered one of its head's two wordings, and a fix whose call-site wiring
was never exercised. Each was a mismatch between two components, which a unit
test structurally cannot see, and each cost a live call to find.

`DETERMINISTIC_SLOT_PRESENTATION.md` and the convergence plan both answer that
with a replay harness over the stored corpus. It cannot be built:
`replay_slot_readouts.py` says so in its own first paragraph —

    The obs store keeps transcripts, not availability payloads, so a payload-in
    / sentence-out replay of `build_slot_offer` is not possible from it.

`obs_turns` records SPEECH. Four of the five predicates the harness needs to
diff -- `slot_accepted_by_caller`, `remaining_unspoken_on_current_day`,
`choose_presented_indices`, `reconcile_readback_time` -- take an availability
payload or a session record, and neither is stored anywhere. The Render log is
no rescue: its `tool result:` line truncates the payload mid-array at ~200
characters.

So this module stores the missing half.

FORWARD-ONLY, AND THAT IS THE URGENCY
-------------------------------------
Nothing here can be back-filled. The ~807 existing calls stay text-only
forever, and every day this is not deployed is another day of corpus that can
never be replayed. That is the whole argument for shipping it ahead of more
visible work.

WHAT IS STORED, AND WHAT IS DELIBERATELY NOT
--------------------------------------------
The payload is trimmed to the fields the pure functions actually read --
`date`, `day_label`, `slot_times`, `slot_times_spoken`, `times_not_shown` --
which is exactly the shape the existing regression fixtures hand them. Anything
else in `available_days` is provider detail that no predicate consults and that
would only make the column bigger.

NO PII. A slot is a date and a time; the caller's name, number and reason live
in `collected` and are governed by the Phase 4 redactor. Nothing from this
module needs redacting, and it must stay that way -- if a future field would
need it, it does not belong here.

NEVER RAISES. This runs on the live call path at the moment the offer is built.
An observability layer must not be able to cost a caller their booking, so
every entry point swallows everything and the worst outcome is a missing row.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

#: Session key. Underscore-prefixed like the other engine-internal records, so
#: it is not mistaken for something the flow reads.
_KEY = "_obs_slot_offers"

#: A cap, because the session is serialised to Redis on every turn. A caller
#: who asks for a different day six times is a real shape and the interesting
#: one; a runaway loop is not, and must not grow the session without bound.
_MAX_OFFERS = 12

#: The payload fields the pure predicates actually read. See the module
#: docstring: this list is the contract with the replay harness, not a summary.
_PAYLOAD_FIELDS = (
    "date",
    "day_label",
    "slot_times",
    "slot_times_spoken",
    "times_not_shown",
)


def _trim_days(days: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for day in days or []:
        if not isinstance(day, dict):
            continue
        out.append({k: day.get(k) for k in _PAYLOAD_FIELDS if k in day})
    return out


def record_offer(
    session: Any,
    *,
    payload_days: Any,
    offer: Any,
    presented_days: Any = None,
) -> None:
    """Append one lookup and the offer built from it. NEVER RAISES.

    ``payload_days`` is `available_days` -- everything the diary returned.
    ``presented_days`` is what `_cap_presented_slots` decided should be spoken.
    Both are kept: the gap between them IS the `presented != bookable` split
    that B-95 is about, and a harness that only saw one could not measure it.

    ``offer`` is the `SlotOffer`. Its `chunks` are stored because the sentence
    is the thing a replay diffs against, and its `slots` because that is the
    record every downstream guard reads.
    """
    try:
        if not isinstance(session, dict):
            return
        offers = session.setdefault(_KEY, [])
        if not isinstance(offers, list) or len(offers) >= _MAX_OFFERS:
            return
        offers.append({
            "seq": len(offers),
            "mode": getattr(offer, "mode", None),
            "payload": _trim_days(payload_days),
            "presented": _trim_days(presented_days),
            "offer": {
                "chunks": list(getattr(offer, "chunks", []) or []),
                "slots": [
                    {
                        "start": s.get("start"),
                        "spoken": s.get("spoken"),
                        "date": s.get("date"),
                    }
                    for s in (getattr(offer, "slots", []) or [])
                    if isinstance(s, dict)
                ],
                "dtmf_map": dict(getattr(offer, "dtmf_map", {}) or {}),
                "more_times": bool(getattr(offer, "more_times", False)),
            },
        })
    except Exception:  # pragma: no cover - defensive; live call path
        logger.warning("[obs.slot_offers] record failed", exc_info=True)


def offers_block(session: Any) -> "list | None":
    """What `call_logger` writes to the column, or None when there is nothing.

    None rather than `[]` so the column stays NULL on a call that never looked
    up availability -- the convention `_screening_summary` and `_latency_block`
    already follow, and the one that keeps "no data" distinguishable from
    "measured, and empty".
    """
    try:
        offers = (session or {}).get(_KEY)
        return list(offers) if offers else None
    except Exception:  # pragma: no cover - defensive; teardown path
        return None
