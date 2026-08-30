"""B-78 — a caller could only ever reach two of a day's slots.

CA7cd9bed5aba6705ceb5c40db49b5185e, 24 Aug 2026 11:11 GMT, jv_v1 demo line.
check_availability returned FIVE times for Tuesday 1 September:

    ["17:00", "17:45", "18:30", "19:15", "20:00"]

Two were read out. The caller asked twice for more and was told:

    "Those are the two available slots on that day."

Three bookable times sat unoffered in session["available_days"] the whole call.

The chain, from the log:

  11:11:39.893  slot map armed, two offered
  11:11:57.832  [ms_llm] slot cache cleared on new turn  ← last_offered_slots = None
  11:11:57.833  [ms_llm] iteration=1                     ← 1 ms later

`try_unspoken_followup_speech` opens with `if not offered: return None`, so it
bailed and the turn fell to the model. The in-tool follow-up branch in
`_execute_tools` is gated on the same key, so both paths died on one line.

Root cause: the clear was guarded on `v3_awaiting_slot_selection`, a DERIVED
flag that connection.py's "caller is responding" branch pops mid-turn while
deliberately keeping `v3_dtmf_slot_map` so a keypad press still resolves. So
the guard stood down on precisely the turn the caller spoke instead of pressing
— the turn the follow-up exists for.

`_derive_slot_window`'s docstring already recorded this trap taking out the
write-CTA clear. This is the same trap, one consumer later. The guard now reads
the map.
"""

import pytest

from app.media_streams.connection import _should_clear_slot_cache


# ── The real availability payload from the call ────────────────────────────
DAY_ISO = "2026-09-01"
ALL_TIMES = ["17:00", "17:45", "18:30", "19:15", "20:00"]
SPOKEN = [
    "five in the evening",
    "quarter to six in the evening",
    "half past six in the evening",
    "quarter past seven in the evening",
    "eight in the evening",
]


def _slot(hhmm):
    return {"start": f"{DAY_ISO}T{hhmm}:00+01:00", "end": ""}


def _available_days():
    return [{
        "date": DAY_ISO,
        "day_label": "Tuesday 1st September",
        "slot_times": list(ALL_TIMES),
        "slot_times_spoken": list(SPOKEN),
        "slots": [_slot(t) for t in ALL_TIMES],
    }]


def _session_mid_slot_choice():
    """Session exactly as it stood when the caller said "anything else?".

    The flag is ABSENT — connection.py:9184 ("caller is responding") popped it
    on the way in. The map is present, because that same branch keeps it on
    purpose. That combination is the whole bug.
    """
    return {
        "available_days": _available_days(),
        "last_offered_slots": [_slot("17:00"), _slot("17:45")],
        "slot_labels": SPOKEN[:2],
        "v3_dtmf_slot_map": {"1": SPOKEN[0], "2": SPOKEN[1]},
        # v3_awaiting_slot_selection deliberately NOT set
    }


# ───────────────────────────────────────────────────────────────────────────
# The guard itself
# ───────────────────────────────────────────────────────────────────────────

def test_clear_stands_down_when_the_map_is_live_but_the_flag_was_popped():
    """The exact state of CA7cd9bed5 at 11:11:57.832."""
    session = _session_mid_slot_choice()

    assert _should_clear_slot_cache(session) is False, (
        "the clear fired while the caller was still choosing — this is the bug"
    )


def test_clear_stands_down_on_the_flag_too():
    """Belt and braces: flag set, map somehow absent."""
    session = _session_mid_slot_choice()
    session.pop("v3_dtmf_slot_map")
    session["v3_awaiting_slot_selection"] = True

    assert _should_clear_slot_cache(session) is False


def test_clear_still_fires_once_the_window_is_closed():
    """The clear must not be disabled — a genuinely new request goes fresh."""
    session = _session_mid_slot_choice()
    session.pop("v3_dtmf_slot_map")

    assert _should_clear_slot_cache(session) is True


def test_nothing_to_clear_is_not_a_clear():
    assert _should_clear_slot_cache({"last_offered_slots": None}) is False
    assert _should_clear_slot_cache({}) is False


def test_guard_does_not_read_the_flag_alone():
    """A map-present session must never clear, whatever the flag says.

    Pinning the rule rather than the branch: `_derive_slot_window` rebuilds the
    flag from the map every turn, so any consumer that trusts the flag on its
    own is wrong by construction.
    """
    for flag in (True, False, None):
        session = _session_mid_slot_choice()
        if flag is None:
            session.pop("v3_awaiting_slot_selection", None)
        else:
            session["v3_awaiting_slot_selection"] = flag
        assert _should_clear_slot_cache(session) is False, f"flag={flag!r}"


# ───────────────────────────────────────────────────────────────────────────
# End to end: the caller actually gets the other three times
# ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "utterance",
    [
        "um have you got anything else that day",
        "yeah could you tell me what other you have what other slots you have that day",
        "anything later?",
        "what else have you got",
    ],
)
def test_followup_offers_the_unspoken_times(utterance):
    """Both utterances the caller actually used, plus two neighbours."""
    from app.tools.slot_followup import try_unspoken_followup_speech

    session = _session_mid_slot_choice()
    speech = try_unspoken_followup_speech(session, utterance)

    assert speech, f"no follow-up produced for {utterance!r}"
    assert "half past six" in speech.lower()
    assert "quarter past seven" in speech.lower()
    # And it must NOT claim the day is exhausted while 20:00 remains.
    assert "any further times" not in speech.lower()


def test_followup_is_dead_when_the_cache_was_wiped():
    """Proves the causal link — this is the pre-fix behaviour.

    With last_offered_slots wiped the follow-up returns None and the turn falls
    to the model, which is how "Those are the two available slots" happened.
    """
    from app.tools.slot_followup import try_unspoken_followup_speech

    session = _session_mid_slot_choice()
    session["last_offered_slots"] = None  # what the clear used to do

    assert try_unspoken_followup_speech(
        session, "um have you got anything else that day"
    ) is None


def test_walking_the_whole_day_reaches_every_time_then_stops():
    """Two asks must surface all five times, and the third says so honestly.

    The day has 5 slots, 2 are already spoken and batches are 2, so: offered 2,
    then 1. The loop ran three times until 2026-08-30, which meant its own last
    iteration consumed the exhaustion sentence and `final` below was really the
    caller's FOURTH ask -- slack in the fixture, and it hid which ask the
    sentence belongs to. Every assertion is otherwise unchanged.
    """
    from app.tools.slot_followup import try_unspoken_followup_speech

    session = _session_mid_slot_choice()
    heard = list(SPOKEN[:2])
    final = None

    # Ask until the answer stops being an offer. The count is deliberately not
    # asserted: it is a function of the batch size, and pinning it made the
    # loop overshoot into the exhaustion sentence and then re-ask it, which is
    # how `final` below silently became the caller's FOURTH ask rather than the
    # first one on an exhausted day. The bound is a safety net, not a contract.
    for _ in range(6):
        speech = try_unspoken_followup_speech(
            session, "have you got anything else that day"
        )
        if speech is None:
            break
        if "further times" in speech.lower():
            final = speech
            break
        heard += [s for s in SPOKEN if s.lower() in speech.lower() and s not in heard]

    assert set(heard) == set(SPOKEN), f"never offered: {set(SPOKEN) - set(heard)}"

    # Day exhausted → says so deterministically, never claims a count.
    assert final is not None
    assert "further times" in final.lower()
    assert "different day" in final.lower()

    # ...once. Finding 2 of the demo call of 2026-08-30: this exact sentence
    # was produced twice, six seconds apart, with the caller saying something
    # new in between. It is a completeness claim about one offer, so it carries
    # its information the first time and nothing at all the second. Asked
    # again, the path declines and the turn falls through to a real lookup --
    # which is what a caller still pressing for times is asking for, and which
    # asserts nothing.
    again = try_unspoken_followup_speech(
        session, "anything else at all"
    )
    assert again is None, (
        f"the identical sentence came back a second time: {again!r}"
    )


def test_no_false_more_claim_on_the_last_batch():
    """B-77 and B-78 meet here.

    The final batch must not carry the "a few others that day" tail — that is
    the contradiction the caller heard: promised more, then told there were two.
    """
    from app.tools.slot_followup import try_unspoken_followup_speech

    session = _session_mid_slot_choice()
    last = None
    for _ in range(4):
        speech = try_unspoken_followup_speech(
            session, "have you got anything else that day"
        )
        if speech is None:
            break
        last = speech

    assert last is not None
    assert "few others" not in last.lower(), last
