"""
Regression: a slot read out without "Number 1" is invisible to everything.

B-100, CA315e501a893cd2a183a483a1f61c0c75 (27 Aug 2026, theorem_v3, Alcester,
build 53c97482). Found while verifying B-98/B-99 on a live call.

    09:21:19  check_availability "Friday 28 August 2026 afternoons"
              2026-08-28 — 2 raw slot(s)  ->  slot_times ["14:00"]
    09:21:20  "Friday 28th August — the available time is two in the afternoon.
               Does that work?"
              slot buf: no numbered options this turn
              (and NO "spoken option(s) recorded as offered" line)

    09:21:26  caller: "um do you have any other slots on that day"
    09:21:32  lookup -> ["14:00"] again, NO band-spent line
              "Friday 28th August — Number 1, two in the afternoon."
    09:21:42  caller: "um you don't have midday on that day by any chance"
    09:21:50  band ... is SPENT -> ["12:00","14:00"]   ← only now

Friday 28 August holds MIDDAY and two in the afternoon. Two defects, one root.

1. THE CLAIM. "the available time is two in the afternoon" says the day holds
   exactly one bookable appointment. It holds two. This is B-97's completeness
   claim in a fourth producer: reconcile_extra_slots_claim sees more_times=True,
   sees the reply assert completeness, and — by its own documented rule that
   appending a tail next to a completeness opener would be self-contradictory —
   returns "unchanged". Staying quiet was the wrong conclusion: the assertion is
   FALSE and has to be corrected, not tolerated.

   The correction is narrow on purpose. _COMPLETENESS_RE also matches the
   LEGITIMATE plural opener ("The available slots for Wednesday are — Number 1
   …"), and its own comment records that it is safe only because it never
   rewrites. So a separate singular-only pattern does the rewriting, and the
   phrase is replaced rather than the sentence removed — the sentence carries
   the time the caller needs.

2. THE RECORD. Everything that knows what was spoken reads
   option_label_candidates, which is driven by "Number N" anchors. No numbering
   -> no anchors -> nothing recorded. That cumulative record is exactly what
   B-98's band-spent rule reads, so the un-numbered readout silently disarmed
   the fix that was supposed to open the day: the next lookup could not tell the
   2pm had been spoken, and the caller had to name "midday" themselves.

   The fallback takes the slot from the PAYLOAD, not the sentence, and only when
   the payload holds exactly one — then there is nothing else the readout could
   have been about. Recording more on a guess is the dangerous direction: a slot
   wrongly marked heard is a slot never offered again, which is the B-97 family
   coming back through this door.
"""
from __future__ import annotations

import inspect

from app.tools.slot_followup import (
    _SINGULAR_COMPLETENESS_RE,
    reconcile_extra_slots_claim,
)

# The sentence, verbatim, that a live caller heard about a day holding two.
LIVE_REPLY = (
    "Friday 28th August — the available time is two in the afternoon. "
    "Does that work?"
)
# The same shape on a day that really does hold one — 08:44:15 the same morning.
TRUE_SINGLE = (
    "I haven't got any afternoon slots on Tuesday 1st September, the only time "
    "available that day is nine in the morning. Does that work?"
)
# The legitimate multi-slot opener. _COMPLETENESS_RE matches this too.
PLURAL_OPENER = (
    "The available slots for Wednesday 2nd September are — Number 1, ten in "
    "the morning. Number 2, two in the afternoon. Any of those work?"
)


# ---------------------------------------------------------------------------
# 1 — the false completeness claim
# ---------------------------------------------------------------------------
def test_the_live_defect_the_false_claim_is_corrected():
    out, action = reconcile_extra_slots_claim(
        LIVE_REPLY, more_times=True, n_offered=1,
    )
    assert "the available time is" not in out.lower(), (
        f"the day holds two and she still called it one: {out!r}"
    )
    assert action != "unchanged"
    # The time itself must survive — the sentence is corrected, not deleted.
    assert "two in the afternoon" in out
    assert "Friday 28th August" in out


def test_the_correction_lets_the_tail_through():
    """Once the false claim is gone there is no self-contradiction left, so the
    "a few others that day" tail can finally be added — which is the whole
    reason more_times was true."""
    out, action = reconcile_extra_slots_claim(
        LIVE_REPLY, more_times=True, n_offered=1,
    )
    assert action == "appended"
    assert "others that day" in out.lower()


def test_a_day_that_really_holds_one_is_left_alone():
    """more_times False means the claim is TRUE. Correcting it would make Susie
    deny a real scarcity and invite the caller to ask for times that do not
    exist."""
    out, action = reconcile_extra_slots_claim(
        TRUE_SINGLE, more_times=False, n_offered=1,
    )
    assert out == TRUE_SINGLE
    assert action == "unchanged"


def test_the_legitimate_plural_opener_is_never_rewritten():
    """_COMPLETENESS_RE matches this sentence. If the correction were hung off
    that pattern instead of a singular-only one, a true two-slot readout would
    be mangled."""
    out, action = reconcile_extra_slots_claim(
        PLURAL_OPENER, more_times=True, n_offered=2,
    )
    assert out == PLURAL_OPENER
    assert action == "unchanged"
    assert not _SINGULAR_COMPLETENESS_RE.search(PLURAL_OPENER)


def test_the_singular_pattern_covers_the_forms_the_model_uses():
    for phrase in (
        "the available time is",
        "the only time is",
        "the only time available is",
        "the available slot is",
        "the only slot available that day is",
        "the only time available on that day is",
    ):
        assert _SINGULAR_COMPLETENESS_RE.search(phrase), phrase


def test_the_singular_pattern_does_not_touch_plurals():
    for phrase in (
        "the available slots for Wednesday are",
        "the available times are",
        "the slots I have are",
    ):
        assert not _SINGULAR_COMPLETENESS_RE.search(phrase), phrase


def test_correcting_never_blanks_the_reply():
    out, _ = reconcile_extra_slots_claim(
        LIVE_REPLY, more_times=True, n_offered=1,
    )
    assert out.strip()
    assert len(out) > len("Does that work?")


# ---------------------------------------------------------------------------
# 2 — the spoken record
# ---------------------------------------------------------------------------
def test_an_unnumbered_readout_records_the_payload_slot():
    """Source check on the slot-buffer block: with no numbered options it must
    fall back to the payload, or B-98's band-spent rule stays disarmed for the
    next turn — which is what made the caller name "midday" themselves."""
    import app.media_streams.llm_stream as ls

    src = inspect.getsource(ls).replace("\r\n", "\n")
    i = src.index("_spoken_labels = list(option_label_candidates(")
    j = src.index("# ── 3b. Reconcile", i)
    block = src[i:j]
    assert "elif not _spoken_labels:" in block, (
        "no fallback for a readout with no numbered options"
    )
    assert "record_spoken_slots(session, _payload_one)" in block


def test_the_fallback_records_at_most_one_slot():
    """Guards the SAFE DIRECTION. Recording a slot the caller never heard means
    it is never offered again — silently withholding a real appointment, which
    is the defect family this whole line of fixes exists to close."""
    import app.media_streams.llm_stream as ls

    src = inspect.getsource(ls).replace("\r\n", "\n")
    i = src.index("elif not _spoken_labels:")
    j = src.index("# ── 3b. Reconcile", i)
    block = src[i:j]
    assert "len(_payload_one) == 1" in block, (
        "the fallback must not record a multi-slot payload on a guess"
    )


def test_the_fallback_reads_the_payload_not_the_sentence():
    """The payload knows exactly which slot was presented. Re-deriving it by
    parsing Susie's own wording is what created this defect."""
    import app.media_streams.llm_stream as ls

    src = inspect.getsource(ls).replace("\r\n", "\n")
    i = src.index("elif not _spoken_labels:")
    j = src.index("# ── 3b. Reconcile", i)
    # The prose names the very functions the code must not call — that is what
    # it is explaining — so drop the comments and judge the CODE.
    code = "\n".join(
        ln for ln in src[i:j].splitlines() if not ln.lstrip().startswith("#")
    )
    assert 'session.get("last_offered_slots")' in code
    for parsed in ("option_label_candidates", "_joined", "resolve_spoken_options"):
        assert parsed not in code, (
            f"the fallback must not go back to the sentence ({parsed})"
        )
