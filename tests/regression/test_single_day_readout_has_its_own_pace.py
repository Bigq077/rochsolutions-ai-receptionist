# tests/regression/test_single_day_readout_has_its_own_pace.py
"""
Owner report, 2026-09-10: *"the slot readout is too quick... it's fine when
there are multiple days"*, with the guess that week and day presentation are
configured differently.

MEASURED FIRST, and the guess was wrong in a useful way. On
CA5f45b7aa0d8720f3fa22c9c58b81f0f4 both readouts run at the SAME rate and both
synthesise at `speed=default`:

    multi_day   318 chars   17.63s   18.0 chars/sec   2.94s per time offered
    single_day  201 chars   11.20s   17.9 chars/sec   3.73s per time offered

Single-day is if anything the more generous of the two per slot. There was no
speed difference to correct, so a change framed as "make them consistent" would
have been chasing a defect that did not exist.

What differs is what the caller must DO with it. In a multi-day readout each
number is a DAY carrying its own landmark ("Number 2, Tuesday the 15th -"),
which resets attention before the times arrive. In a single-day readout each
number is a TIME with only "Number 2," between them: three values to hold and
compare in about six seconds. Same seconds, more decisions per second.

THE MODE MUST COME FROM THE READOUT, NOT FROM THE LOOKUP. `_slot_presentation_
mode` has ONE writer, inside tool execution. A named-day follow-up runs no tool
-- "no tool call needed (D-B)" -- so on exactly the turn this feature is for,
that key still holds the mode of the last LOOKUP and says `multi_day`. Steering
off it would slow the readout the owner said was FINE and leave the reported one
untouched. `apply_offer_to_session` records `_slot_readout_mode` beside the
chunks instead, same owner, same lifetime.

SINGLE-DAY ONLY, deliberately. The multi-day readout is already the longer of
the two at 17.63s and S-1 measures callers barging in on nearly every turn;
slowing it would make a length problem worse to fix a density problem it does
not have.
"""
from __future__ import annotations

from app.media_streams.config import (
    ELEVENLABS_HEAD_SPEED,
    ELEVENLABS_PHONE_SPEED,
    ELEVENLABS_SLOT_SPEED,
    _SPEED_MAX,
    _SPEED_MIN,
    _clamped_speed,
)
from app.tools.slot_offer import apply_offer_to_session

MON = "2026-09-14"


def _record(mode, n=3):
    return {
        "chunks": [],
        "slots": [
            {"start": f"{MON}T1{i}:00:00+01:00", "end": "", "spoken": f"t{i}",
             "date": MON}
            for i in range(n)
        ],
        "dtmf_map": {str(i + 1): f"t{i}" for i in range(n)},
        "more_times": False,
        "day_iso": MON,
        "mode": mode,
    }


CHUNKS = [
    "Tuesday 15th September — Number 1, half past ten in the morning.",
    "Number 2, twenty past eleven in the morning.",
    "Number 3, twenty to three in the afternoon.",
]


# ---------------------------------------------------------------------------
# The mode is recorded where the chunks are
# ---------------------------------------------------------------------------
def test_the_readout_records_its_own_mode():
    """Beside the chunks, by the same owner, so the two cannot drift."""
    session = {}

    apply_offer_to_session(session, _record("single_day"), CHUNKS)

    assert session["_slot_readout_mode"] == "single_day"
    assert session["_slot_readout_chunks"] == CHUNKS


def test_the_readout_mode_is_not_the_lookup_mode():
    """The whole reason this key exists. A named-day follow-up runs no tool, so
    `_slot_presentation_mode` still describes the last LOOKUP - and steering
    off it would slow the multi-day readout the owner said was fine."""
    session = {"_slot_presentation_mode": "multi_day"}

    apply_offer_to_session(session, _record("single_day"), CHUNKS)

    assert session["_slot_presentation_mode"] == "multi_day"
    assert session["_slot_readout_mode"] == "single_day"


def test_a_multi_day_readout_says_so():
    """The other direction, so the guard cannot pass by always answering
    single_day."""
    session = {}

    apply_offer_to_session(session, _record("multi_day"), CHUNKS)

    assert session["_slot_readout_mode"] == "multi_day"


def test_the_mode_dies_with_the_chunks():
    """A readout too short to number arms no map and keeps no chunks. Leaving a
    mode behind would let the NEXT readout inherit a pace it never chose - the
    stale-latch family, one field over."""
    session = {}
    apply_offer_to_session(session, _record("single_day"), CHUNKS)

    apply_offer_to_session(session, _record("single_day", n=1), ["one time only"])

    assert "_slot_readout_chunks" not in session
    assert "_slot_readout_mode" not in session


# ---------------------------------------------------------------------------
# The constant
# ---------------------------------------------------------------------------
def test_the_default_changes_nothing():
    """1.0 means `speed` is omitted entirely and the request is byte-identical
    to today's. The number needs an ear, not a guess: it is tuned from the
    Render dashboard against a live call, like the other two."""
    assert ELEVENLABS_SLOT_SPEED == 1.0


def test_it_is_its_own_class_not_a_reuse_of_the_other_two():
    """0.8 is the careful digit-by-digit articulation of a phone number and
    0.88 is a hold head flash would otherwise rush. A slot readout is neither,
    and pointing it at either constant would tie three unrelated decisions
    together."""
    assert ELEVENLABS_PHONE_SPEED == 0.8
    assert ELEVENLABS_HEAD_SPEED == 0.88


def test_a_tuned_value_is_clamped_like_the_others():
    """Env-overridable from the dashboard, so junk and extremes both have to
    land somewhere sane - 0 would be silence and 3.0 unintelligible."""
    assert _clamped_speed("0.92", 1.0) == 0.92
    assert _clamped_speed("0.1", 1.0) == _SPEED_MIN
    assert _clamped_speed("9", 1.0) == _SPEED_MAX
    assert _clamped_speed("not a number", 1.0) == 1.0


# ---------------------------------------------------------------------------
# The wiring, because everything above could pass with nothing connected
# ---------------------------------------------------------------------------
def _tts_loop_source():
    import inspect

    from app.media_streams import connection

    return inspect.getsource(connection)


def test_the_call_site_passes_a_speed():
    """`synthesise_chunk` picks phone and head rates off the text itself. A
    slot readout is ordinary English with no shape to match, so this layer -
    which has the recorded chunks and their mode - must pass it."""
    src = _tts_loop_source()

    assert "speed=_slot_readout_speed" in src, (
        "the TTS call site passes no slot speed, so ELEVENLABS_SLOT_SPEED is "
        "inert however it is set in the dashboard"
    )


def test_the_call_site_gates_on_the_readout_mode_not_the_lookup_mode():
    """The trap this whole feature turns on. Reading
    `_slot_presentation_mode` here would slow the multi-day readout and miss
    the single-day one - the exact inversion of the request."""
    src = _tts_loop_source()

    window = src[src.index("_slot_readout_speed = None"):]
    window = window[:window.index("_subs_spoken = 0")]

    assert "_slot_readout_mode" in window, window[:400]
    assert "_slot_presentation_mode" not in window, (
        "the pacing decision reads the LOOKUP's mode, which is stale on every "
        "named-day follow-up - the one turn this exists for."
    )
