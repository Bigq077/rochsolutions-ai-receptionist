"""
Regression: the readout said Tuesday and the offer record was written with
Monday's times.

B-93. `CA903bd6ef1ce0c8b848b8e8fcb24ab520` (26 Aug 2026, vital_edge, build
`e090908f`). Found by the V-A call on the one-pass sheet, which was testing
something else entirely and passed.

    19:45:01  offer: "Number 1, Monday 31st August — one in the afternoon.
                      Number 2, Tuesday 1st September — one in the afternoon."
              slot buf: spoken options span 2 days — offer record left unchanged
    19:45:13  caller: "uh the second one please"
    19:45:16  check_availability BLOCKED — slots already retrieved this turn
    19:45:17  SPOKEN:   "Tuesday 1st September — Number 1, one in the afternoon.
                         Number 2, two in the afternoon.
                         Number 3, three in the afternoon."
    19:45:17  RECORDED: ['2026-08-31T13:00:00+01:00',
                         '2026-08-31T14:00:00+01:00',
                         '2026-08-31T15:00:00+01:00']
              v3_last_offered_day_iso='2026-08-31'
    19:45:34  "So that's Tuesday the 1st of September at three in the afternoon
               — could I take your first name and surname?"

31 August is a MONDAY. She spoke Tuesday and wrote Monday, three times over,
and the read-back the caller then confirmed disagreed with the only
machine-readable record of what had been offered.

WHY THE DAY WAS WRONG: a header-style readout leaves every option a bare time,
and bare times cannot say which day they belong to. The resolver falls back to
`_slot_presented_day`, which is set from the tool result's `first_day`; a
BLOCKED/cached call carries no first_day, so llm_stream takes
`last_offered_slots[0]` instead. The comment there states the assumption:

    "they only fire when an offer is already on the table, and that offer's
     day IS the day under discussion"

True for a single-day offer. This offer spanned TWO days and the caller chose
the SECOND, so the inherited day was the one they had just declined.

WHY NOTHING NOTICED: Monday and Tuesday both had 13:00, 14:00 and 15:00 free,
so every spoken label resolved cleanly against the wrong day. There is no
mismatch to detect downstream — this is only visible by comparing the sentence
against the record, which is what this file does.

THE FIX: the day the sentence NAMES outranks the day inherited from an earlier
payload. Matched against the payload's own `day_label` strings, not parsed out
of prose — the formatter is handed those labels and echoes them, so this is a
data question with a checkable answer. A paraphrase matches nothing and the
previous behaviour stands; a multi-day readout matches two and also stands,
because the multi_day branch already declines to write the offer record.
"""
from __future__ import annotations

import pytest

from app.tools.slot_followup import (
    day_named_in_readout,
    resolve_spoken_options,
)


# Jonathan's diary as the cached payload held it: the SAME afternoon times free
# on both days, which is what made the wrong day invisible.
AVAILABLE_DAYS = [
    {
        "date": "2026-08-31",
        "day_label": "Monday 31st August",
        "slot_times": ["13:00", "14:00", "15:00", "16:00"],
        "slot_times_spoken": [
            "one in the afternoon", "two in the afternoon",
            "three in the afternoon", "four in the afternoon",
        ],
        "slots": [
            {"start": f"2026-08-31T{h}:00:00+01:00", "date": "2026-08-31",
             "spoken": s}
            for h, s in [
                ("13", "one in the afternoon"), ("14", "two in the afternoon"),
                ("15", "three in the afternoon"), ("16", "four in the afternoon"),
            ]
        ],
    },
    {
        "date": "2026-09-01",
        "day_label": "Tuesday 1st September",
        "slot_times": ["13:00", "14:00", "15:00", "16:00"],
        "slot_times_spoken": [
            "one in the afternoon", "two in the afternoon",
            "three in the afternoon", "four in the afternoon",
        ],
        "slots": [
            {"start": f"2026-09-01T{h}:00:00+01:00", "date": "2026-09-01",
             "spoken": s}
            for h, s in [
                ("13", "one in the afternoon"), ("14", "two in the afternoon"),
                ("15", "three in the afternoon"), ("16", "four in the afternoon"),
            ]
        ],
    },
]

# The exact sentence spoken at 19:45:17.
LIVE_READOUT = (
    "Tuesday 1st September — Number 1, one in the afternoon. "
    "Number 2, two in the afternoon. "
    "Number 3, three in the afternoon. And I've a few others that day."
)

STALE_PRESENTED_DAY = "2026-08-31"   # what the fallback inherited


# ---------------------------------------------------------------------------
# The helper
# ---------------------------------------------------------------------------
def test_the_live_readout_names_tuesday():
    """The defect itself, stated as a question with one right answer."""
    assert day_named_in_readout(AVAILABLE_DAYS, LIVE_READOUT) == "2026-09-01"


def test_a_multi_day_readout_names_no_single_day():
    """The offer one turn earlier. Two labels, so no override — and the
    multi_day branch already declines to write the offer record."""
    multi = (
        "Here's what we've got coming up — Number 1, Monday 31st August — "
        "one in the afternoon. Number 2, Tuesday 1st September — "
        "one in the afternoon. Either of those suit you?"
    )
    assert day_named_in_readout(AVAILABLE_DAYS, multi) is None


@pytest.mark.parametrize(
    "text",
    [
        "Number 1, one in the afternoon. Number 2, two in the afternoon.",
        "I've got a few times that day — one, two or three in the afternoon.",
        "",
    ],
)
def test_a_readout_naming_no_known_day_does_not_override(text):
    """A paraphrase or a bare list must fall back, never guess."""
    assert day_named_in_readout(AVAILABLE_DAYS, text) is None


def test_bad_input_is_safe():
    assert day_named_in_readout(None, LIVE_READOUT) is None
    assert day_named_in_readout(AVAILABLE_DAYS, None) is None
    assert day_named_in_readout([{"nonsense": 1}], LIVE_READOUT) is None


# ---------------------------------------------------------------------------
# The consequence — what actually got written down
# ---------------------------------------------------------------------------
def _record(prefer_day):
    labels = [
        "one in the afternoon",
        "two in the afternoon",
        "three in the afternoon",
    ]
    resolved = resolve_spoken_options(
        AVAILABLE_DAYS, labels, prefer_day=prefer_day
    )
    return [s["start"] for s in (resolved or [])]


def test_the_stale_day_is_what_produced_the_wrong_record():
    """Pins the defect so the fix cannot be mistaken for a no-op: with the
    inherited day, every option resolves to MONDAY — the three ISO strings the
    live call actually logged."""
    assert _record(STALE_PRESENTED_DAY) == [
        "2026-08-31T13:00:00+01:00",
        "2026-08-31T14:00:00+01:00",
        "2026-08-31T15:00:00+01:00",
    ]


def test_the_recorded_day_matches_the_spoken_day():
    """The fix. The record must agree with the sentence the caller heard."""
    named = day_named_in_readout(AVAILABLE_DAYS, LIVE_READOUT)
    starts = _record(named or STALE_PRESENTED_DAY)
    assert starts == [
        "2026-09-01T13:00:00+01:00",
        "2026-09-01T14:00:00+01:00",
        "2026-09-01T15:00:00+01:00",
    ]
    assert all(s.startswith("2026-09-01") for s in starts), (
        "the caller was told Tuesday; a Monday slot in the offer record is the "
        "wrong-day booking one confirmation away"
    )


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------
def test_the_slot_buffer_prefers_the_named_day():
    import inspect

    from app.media_streams import llm_stream

    src = inspect.getsource(llm_stream)
    i = src.index("_all_heard = resolve_all_spoken_times(")
    window = src[i - 2500:i + 600]
    assert "day_named_in_readout(" in window, (
        "the slot buffer still resolves bare times against the inherited "
        "presented_day only — this is what recorded Monday on CA903bd6ef"
    )
    assert window.count("prefer_day=_prefer_day") == 2, (
        "both resolvers must use the same preferred day; a split leaves the "
        "cumulative spoken record and the offer record disagreeing"
    )
