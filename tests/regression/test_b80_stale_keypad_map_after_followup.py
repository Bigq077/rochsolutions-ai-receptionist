"""B-80 — after a deterministic follow-up the keypad still pointed at the old offer.

Found 24 Aug 2026 while fixing B-79, on `CA6b90c3a2` at 12:24:39: the session's
`v3_dtmf_slot_map` still listed all the earlier numbered options while the
follow-up had just offered 20:00 alone. A keypress would have resolved to a time
the caller heard EARLIER and which is no longer the offer — a silent wrong-slot
booking, which is strictly worse than the keypress doing nothing.

Mechanism. `v3_dtmf_slot_map` is built in `_flush_slot_buf` (llm_stream) from a
NUMBERED readout, and `_derive_slot_window` is the only thing that takes it away.
The deterministic follow-up paths in `slot_followup` speak their times directly
and UNNUMBERED — they never reach `_flush_slot_buf` — so the map survives while
the offer moves on underneath it.

Why the fix MARKS the map rather than clearing it: `v3_dtmf_slot_map` owns the
slot window. `_derive_slot_window` re-derives `v3_awaiting_slot_selection` from
it every turn, and `_should_clear_slot_cache` reads its presence to decide
whether the next turn may wipe `last_offered_slots`. Popping the map here would
hand the next turn permission to wipe the very input the follow-up paths open
with (`if not offered: return None`) — re-breaking B-78, which
`test_b78_slot_cache_clear_kills_followup.py` exists to hold. The window must
stay open for VOICE; only digit-to-label resolution is invalidated.

The B-78 interaction is asserted here directly, because it is the reason the
obvious fix is wrong and a future reader will otherwise try it.
"""
from __future__ import annotations

from app.media_streams.connection import (
    _derive_slot_window,
    _should_clear_slot_cache,
)
from app.tools import slot_followup as sf


# ── the real payload from CA6b90c3a2 / CA7cd9bed5 ──────────────────────────
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


def _session_after_a_numbered_readout():
    """Three numbered options spoken; the caller has not chosen yet."""
    return {
        "available_days": _available_days(),
        "last_offered_slots": [_slot("17:00"), _slot("17:45"), _slot("18:30")],
        "slot_labels": SPOKEN[:3],
        "spoken_slot_starts": [_slot(t)["start"] for t in ALL_TIMES[:3]],
        "v3_dtmf_slot_map": {
            "1": SPOKEN[0], "2": SPOKEN[1], "3": SPOKEN[2],
        },
        "v3_awaiting_slot_selection": True,
    }


# ── the defect ─────────────────────────────────────────────────────────────

def test_a_followup_batch_supersedes_the_keypad_map():
    """"Anything else that day?" -> the old numbers no longer refer to anything."""
    session = _session_after_a_numbered_readout()
    assert not session.get("v3_slot_map_superseded")

    speech = sf.try_unspoken_followup_speech(session, "anything else that day?")

    assert speech, "the follow-up must still answer deterministically"
    assert session["v3_slot_map_superseded"] is True


def test_a_resolved_specific_time_supersedes_the_map_too():
    """"What about eight?" narrows the offer to one time; 1..3 are stale."""
    session = _session_after_a_numbered_readout()

    speech = sf.try_unspoken_followup_speech(
        session, "have you got anything at eight in the evening?"
    )

    assert speech
    assert session["v3_slot_map_superseded"] is True


def test_the_map_itself_is_left_in_place():
    """The mark must not become a clear — see the B-78 tests below."""
    session = _session_after_a_numbered_readout()
    sf.try_unspoken_followup_speech(session, "anything else that day?")

    assert session.get("v3_dtmf_slot_map"), (
        "clearing the map here re-breaks B-78 — mark, do not clear"
    )


# ── the reason the obvious fix is wrong ────────────────────────────────────

def test_the_slot_cache_is_still_protected_after_a_followup():
    """B-78's guard reads the MAP. A superseded map must still protect it.

    If a future change pops the map instead of marking it, this fails and the
    follow-up chain dies on the next turn.
    """
    session = _session_after_a_numbered_readout()
    sf.try_unspoken_followup_speech(session, "anything else that day?")

    assert _should_clear_slot_cache(session) is False


def test_the_voice_window_stays_open_after_a_followup():
    """The caller can still SAY a time. Only the keypad is invalidated."""
    session = _session_after_a_numbered_readout()
    sf.try_unspoken_followup_speech(session, "anything else that day?")

    assert _derive_slot_window(session) is True
    assert session.get("v3_awaiting_slot_selection") is True


# ── the mark's lifecycle ───────────────────────────────────────────────────

def test_closing_the_window_drops_the_mark():
    """No map means nothing to be stale about — it must not leak into a
    later, unrelated slot window."""
    session = _session_after_a_numbered_readout()
    sf.try_unspoken_followup_speech(session, "anything else that day?")
    session.pop("v3_dtmf_slot_map")

    assert _derive_slot_window(session) is False
    assert "v3_slot_map_superseded" not in session


def test_a_plain_slot_choice_does_not_supersede_anything():
    """Only the follow-up paths move the offer. An ordinary turn must not
    disarm a keypad the caller is about to use."""
    session = _session_after_a_numbered_readout()

    assert sf.try_unspoken_followup_speech(session, "yes that works") is None
    assert not session.get("v3_slot_map_superseded")
