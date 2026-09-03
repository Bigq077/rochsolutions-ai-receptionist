"""A caller who picks a DAY and no time has picked something.

Live 2026-09-03 01:56:49, demo line, build 2a8a6ee6:

    'yeah monday works'  ->  LAT turn_seq=3 ttfa_ms=2097 content_ttfa_ms=2097

Equal, so nothing spoke -- on the exact utterance `Intent.SLOT_PICKED`
("Monday it is —") was written for. Both readers of the head's
`slot_selection` argument declined, and each was right to:

  * `utterance_is_slot_selection` is containment against the spoken labels, and
    a bare weekday matches none of them;
  * `slot_accepted_by_caller` needs a TIME. After a multi_day readout Monday
    holds two times the caller chose between neither, so declining is the
    correct answer to "which SLOT did they accept".

It is the wrong answer to "did they pick something", and that is the question
the head is asking. `day_accepted_by_caller` answers that one.

WHY THIS WAS NOT A TWO-LINE CHANGE. "What about Monday" also names exactly one
offered day. Treating it as a pick would put "Monday it is —" in front of a
lookup that really is happening -- the promised-work defect, which this family
has produced three times and is why the 30 Aug decision gave picks silence in
the first place. So the predicate is deny-by-default on BOTH sides: a request
shape declines, and a day named with no acceptance word declines too.

The asymmetry is the point. Returning None costs a head. Returning a day for a
caller who was ASKING costs a promise Susie cannot keep.

Note what the OTHER direction must keep doing: a request still gets its
NAMED_DAY lookup head, because promising a lookup that IS happening is exactly
right. That is asserted below, not assumed.
"""
from __future__ import annotations

import pytest

from app.hold_speech import (
    Intent,
    classify_intent,
    render_intent_head,
    subject_for,
)
from app.tools.slot_followup import day_accepted_by_caller, record_spoken_slots

READOUT = (
    "Here's what we've got coming up - Number 1, Monday 7th September - "
    "eight in the morning, or ten past five in the evening. Number 2, "
    "Tuesday 8th September - ten to nine in the morning. Any of those work?"
)


def _day(date, label, times, spoken):
    return {
        "date": date, "day_label": label,
        "slot_times": list(times), "slot_times_spoken": list(spoken),
        "times_not_shown": 0,
        "slots": [{"start": f"{date}T{t}:00+01:00", "end": ""} for t in times],
    }


OFFER = [
    _day("2026-09-07", "Monday 7th September", ["08:00", "17:10"],
         ["eight in the morning", "ten past five in the evening"]),
    _day("2026-09-08", "Tuesday 8th September", ["08:50", "17:10"],
         ["ten to nine in the morning", "ten past five in the evening"]),
    _day("2026-09-09", "Wednesday 9th September", ["08:00", "16:20"],
         ["eight in the morning", "twenty past four in the afternoon"]),
]


def _mid_offer():
    session = {
        "available_days": OFFER,
        "last_offered_slots": [
            {"start": f"{d['date']}T{d['slot_times'][0]}:00+01:00", "end": ""}
            for d in OFFER
        ],
        "slot_labels": [d["day_label"] for d in OFFER],
    }
    record_spoken_slots(session, [
        {"start": f"{d['date']}T{t}:00+01:00", "spoken": sp, "date": d["date"]}
        for d in OFFER
        for t, sp in zip(d["slot_times"], d["slot_times_spoken"])
    ])
    return session


# ── The acceptances ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("utterance,date", [
    ("yeah monday works",             "2026-09-07"),   # the live miss
    ("uh yeah monday works",          "2026-09-07"),
    ("monday works",                  "2026-09-07"),
    ("monday please",                 "2026-09-07"),
    ("monday's good",                 "2026-09-07"),
    ("monday suits",                  "2026-09-07"),
    ("yeah tuesday is fine",          "2026-09-08"),
    ("let's do wednesday",            "2026-09-09"),
    ("yeah monday 7th september works", "2026-09-07"),
])
def test_a_day_the_caller_accepted_resolves(utterance, date):
    assert day_accepted_by_caller(_mid_offer(), utterance) == date


def test_the_live_utterance_now_gets_its_head():
    """End to end: the words spoken at 01:56:49 produce the head, not silence."""
    session = _mid_offer()
    picked = bool(day_accepted_by_caller(session, "yeah monday works"))
    hits = classify_intent("yeah monday works", READOUT, slot_selection=picked)
    assert Intent.SLOT_PICKED in hits
    assert render_intent_head(
        hits[0], subject=subject_for("yeah monday works"), index=0
    ) == "Monday it is —"


# ── The requests, which must keep their LOOKUP head ─────────────────────────

@pytest.mark.parametrize("utterance", [
    "what about monday",
    "how about monday",
    "do you have monday",
    "have you got monday",
    "is monday free",
    "anything on monday",
    "can i get monday",
    "what have you got on monday",
    "is there anything monday",
    "monday?",
])
def test_a_request_for_a_day_is_not_an_acceptance(utterance):
    assert day_accepted_by_caller(_mid_offer(), utterance) is None


@pytest.mark.parametrize("utterance", ["what about monday", "do you have monday"])
def test_a_request_still_gets_the_lookup_head(utterance):
    """The other half, and the reason the predicate is narrow. A lookup IS
    happening for these, so promising one is correct and silence would be the
    regression."""
    session = _mid_offer()
    picked = bool(day_accepted_by_caller(session, utterance))
    hits = classify_intent(utterance, READOUT, slot_selection=picked)
    assert Intent.NAMED_DAY in hits, f"{utterance!r} lost its lookup head"


# ── Everything else still declines ──────────────────────────────────────────

@pytest.mark.parametrize("utterance,why", [
    ("monday",                   "a bare day is not an acceptance"),
    ("yeah",                     "names no day"),
    ("yeah friday works",        "Friday was never offered"),
    ("yeah another day",         "a different-day request has its own path"),
    ("yeah what else have you got", "a more-slots request has its own path"),
    ("yeah monday at 8 am works", "names a time — slot_accepted_by_caller owns it"),
    ("number two",               "a position names no day, and 30 Aug gave it silence"),
    ("yeah ten in the morning",  "band only, and 30 Aug gave it silence"),
])
def test_the_declines(utterance, why):
    assert day_accepted_by_caller(_mid_offer(), utterance) is None, why


def test_it_declines_with_no_standing_offer():
    """"Monday works" said when nothing was read out is not a pick."""
    assert day_accepted_by_caller({"available_days": OFFER}, "yeah monday works") is None
    assert day_accepted_by_caller({}, "yeah monday works") is None


def test_the_thirty_august_silence_decision_is_untouched():
    """Positional and band-only picks still get silence, whatever this
    predicate does. Asserted here as well as in its own file, because this
    change is the one most likely to erode it by accident."""
    session = _mid_offer()
    for picked in ("number two", "yeah, that one", "ten in the morning",
                   "yeah ten in the morning"):
        assert day_accepted_by_caller(session, picked) is None
        assert classify_intent(picked, READOUT, slot_selection=True) == []


def test_the_predicate_is_actually_WIRED_INTO_the_head():
    """Pinned by source, and here is why that is not paranoia.

    `021c0fc0` shipped the night before with a test that passed
    `slot_selection=True` directly. It proved the classifier arm and never the
    path, so the live call still got silence -- the real blocker was upstream,
    in the argument the test had hard-coded. The same trap is open here: every
    test above calls `day_accepted_by_caller` itself, and all of them still
    pass with the llm_stream wiring deleted. Confirmed by deleting it.

    A behavioural test through `_one_streaming_call` would need the whole
    streaming rig. This is the cheap half that would have caught the actual
    defect: the head's `slot_selection` argument must READ this predicate.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2]
           / "app" / "media_streams" / "llm_stream.py").read_text(
        encoding="utf-8", errors="replace")

    assert "day_accepted_by_caller" in src, (
        "llm_stream no longer reads day_accepted_by_caller, so a caller who "
        "picks a day and no time is back to silence"
    )
    # And it must feed the head's verdict, not merely be imported somewhere.
    assert "_hs_picking = bool(_day_picked(session, _hs_utterance))" in src, (
        "day_accepted_by_caller is imported but no longer decides _hs_picking"
    )


# ── What the corpus said, when it was finally asked ─────────────────────────

@pytest.mark.parametrize("utterance", [
    # Every one of these is a real caller turn from the obs corpus, found by
    # scripts/replay_day_picks.py across 828 calls. All three scored ACCEPT on
    # the shipped predicate, because "yeah" opens them and no request pattern
    # matched -- so each would have produced "Tuesday it is —" in front of a
    # lookup that really was about to happen.
    "yeah check for tuesday please",
    "yeah i'll ask for you to present tuesday the 8th",
    "what do you mean monday the 10th works",
])
def test_the_requests_the_corpus_found(utterance):
    """Phase 1a's first catch, and the argument for Phase 1a in six words:
    real callers phrase requests in ways test authors do not.

    A generated sweep could not have found these -- it generates DIARIES, and
    these are failures of LANGUAGE. The two halves of Phase 1 answer different
    questions and neither substitutes for the other.
    """
    assert day_accepted_by_caller(_mid_offer(), utterance) is None


@pytest.mark.parametrize("utterance", [
    # And the acceptances from the same sweep, which must survive the fix.
    "uh yeah monday works",
    "yeah the monday works",
    "monday please",
    "tuesday please",
    "yeah wednesday the 2nd",
    "uh let's see that saturday slot please",
    "tuesday the 1st yeah",
])
def test_the_acceptances_the_corpus_found_still_resolve(utterance):
    """Widening the request pattern must not eat real picks. "let's see that
    saturday slot" is the near miss: it contains "see", which is why "see" is
    deliberately absent from the request pattern while "check" is in it."""
    session = _mid_offer()
    # Only the days this offer actually holds can resolve; the rest are here to
    # prove the LANGUAGE half, so accept either a date or a decline-on-day.
    from app.tools.slot_followup import _DAY_REQUEST_RE
    assert not _DAY_REQUEST_RE.search(utterance), (
        f"{utterance!r} is a real acceptance from the corpus and the request "
        f"pattern now eats it"
    )
