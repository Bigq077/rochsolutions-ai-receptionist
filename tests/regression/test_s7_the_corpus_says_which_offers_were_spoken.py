# tests/regression/test_s7_the_corpus_says_which_offers_were_spoken.py
"""
S-7 - `record_offer` fires where the offer is BUILT, above the P6/P6b
stand-downs, so a row is a record of an INTENTION and not of a readout.

Measured over the first 120 calls carrying the column: **77 offers built, 67
spoken, 10 (13%) discarded and replaced by model speech.** Anything reading
`calls.slot_offers` as "what the caller heard" was wrong one time in eight.

That was filed as "no code needed - it is a fact every future harness author
must know", and at roughly one row per call it was a defensible footnote.
**S-14 changed the arithmetic.** Three producers now record as well, at the
point the sentence is SPOKEN, and the corpus went from ~1.0 rows per call to
4.0 on the first call after it landed:

    build_sha         calls   rows  rows/call
    aa4c324c7462          1      4      4.00   <- S-14
    7f9c066e54cb          3      4      1.33
    337fbd9e1a93          2      2      1.00

So one column now holds two populations that mean different things, the new
one becomes the majority within a week, and nothing on the row says which kind
it is. A footnote does not survive that; a field does.

TWO FIELDS, and they are separate facts on purpose:

  `source`  which writer made the row - `gate5` (built) or `producer` (spoken).
  `spoken`  whether it was actually said. NEVER inferred from `source`: a
            gate5 row starts False and is flipped by `mark_offer_spoken` at
            the one place a built offer becomes speech.

The stand-down path must NOT flip it. `_record_stood_down_slots` speaks the
MODEL's sentence and stands the built offer down - that is the 13%, and a row
still reading False at teardown is how you count it.
"""
from __future__ import annotations

from app.obs.slot_offers import mark_offer_spoken, offers_block, record_offer
from app.tools.slot_followup import named_day_speech, record_spoken_slots

MON, TUE = "2026-09-14", "2026-09-15"
GRID = [
    "08:00", "08:50", "09:40", "10:30", "11:20", "12:10",
    "13:00", "13:50", "14:40", "15:30", "16:20", "17:10",
]
LABELS = {MON: "Monday 14th September", TUE: "Tuesday 15th September"}


class _Offer:
    """The shape `record_offer` reads off a SlotOffer."""

    def __init__(self, chunks, mode="multi_day"):
        self.chunks = list(chunks)
        self.mode = mode
        self.slots = [{"start": f"{MON}T08:00:00+01:00", "spoken": "eight", "date": MON}]
        self.dtmf_map = {"1": "eight in the morning"}
        self.more_times = False


def _day(day_iso):
    return {
        "date": day_iso,
        "day_label": LABELS[day_iso],
        "slot_times": list(GRID),
        "slot_times_spoken": [f"spoken-{t}" for t in GRID],
        "slots": [
            {"start": f"{day_iso}T{t}:00+01:00", "end": f"{day_iso}T{t}:50+01:00"}
            for t in GRID
        ],
        "times_found_on_day": len(GRID),
        "times_not_shown": 0,
    }


def _session():
    days = [_day(MON), _day(TUE)]
    session = {"available_days": days, "_slot_presentation_mode": "multi_day"}
    record_spoken_slots(session, [{"start": f"{MON}T08:00:00+01:00"}])
    return session


# ---------------------------------------------------------------------------
# The two fields exist and are independent
# ---------------------------------------------------------------------------
def test_a_built_offer_is_recorded_as_not_yet_spoken():
    """Gate 5's row. It may still be stood down, so it cannot claim otherwise."""
    session = {}

    record_offer(session, payload_days=[_day(MON)], offer=_Offer(["a", "b"]))

    row = offers_block(session)[0]
    assert row["source"] == "gate5", row
    assert row["spoken"] is False, row


def test_applying_the_offer_marks_it_spoken():
    """The one transition. `mark_offer_spoken` is called where
    `apply_offer_to_session` puts the built offer on the wire."""
    session = {}
    record_offer(session, payload_days=[_day(MON)], offer=_Offer(["a", "b"]))

    mark_offer_spoken(session, ["a", "b"])

    assert offers_block(session)[0]["spoken"] is True


def test_a_stood_down_offer_stays_unspoken():
    """The 13%. Nothing calls `mark_offer_spoken` on the stand-down path, and a
    row still reading False at teardown IS the measurement."""
    session = {}
    record_offer(session, payload_days=[_day(MON)], offer=_Offer(["a", "b"]))

    # P6/P6b: the model's sentence is spoken instead. No mark.

    assert offers_block(session)[0]["spoken"] is False


def test_marking_never_touches_a_different_offer():
    """Two builds, one spoken. Matching is on the words the row would have
    said, so the wrong row cannot be credited."""
    session = {}
    record_offer(session, payload_days=[_day(MON)], offer=_Offer(["first"]))
    record_offer(session, payload_days=[_day(MON)], offer=_Offer(["second"]))

    mark_offer_spoken(session, ["first"])

    rows = offers_block(session)
    assert [r["spoken"] for r in rows] == [True, False], rows


def test_marking_is_idempotent_and_never_double_credits():
    """A second apply of the same chunks must not silently promote the next
    unspoken row - which is what a bare "flip the last False" would do."""
    session = {}
    record_offer(session, payload_days=[_day(MON)], offer=_Offer(["first"]))
    record_offer(session, payload_days=[_day(MON)], offer=_Offer(["second"]))

    mark_offer_spoken(session, ["first"])
    mark_offer_spoken(session, ["first"])

    assert [r["spoken"] for r in offers_block(session)] == [True, False]


# ---------------------------------------------------------------------------
# The producers, through the real entry point
# ---------------------------------------------------------------------------
def test_a_producer_row_is_born_spoken():
    """S-14's three producers record BESIDE `apply_offer_to_session`, on the
    path that says the sentence. There is no stand-down after them."""
    session = _session()

    assert named_day_speech(session, "tell me about monday")

    row = offers_block(session)[0]
    assert row["source"] == "producer", row
    assert row["spoken"] is True, row


def test_the_two_populations_are_distinguishable_in_one_column():
    """The whole point. A harness asking "what did the caller hear" filters on
    `spoken`; one asking "what did we decide to say" does not."""
    session = _session()
    record_offer(session, payload_days=[_day(MON)], offer=_Offer(["stood down"]))
    named_day_speech(session, "tell me about monday")

    rows = offers_block(session)
    heard = [r for r in rows if r["spoken"]]
    assert len(rows) == 2, rows
    assert len(heard) == 1, rows
    assert heard[0]["source"] == "producer", heard


def test_recording_never_costs_the_caller_the_readout():
    """Standing rule for anything on the live call path."""
    import app.obs.slot_offers as mod

    session = _session()
    original = mod.record_offer
    mod.record_offer = lambda *a, **k: 1 / 0
    try:
        text = named_day_speech(session, "tell me about monday")
    finally:
        mod.record_offer = original

    assert text, "a failing obs write swallowed the caller's readout"
