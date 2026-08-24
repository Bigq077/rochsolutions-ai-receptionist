# tests/regression/test_b81_name_invented_from_so_its.py
"""
B-81 — a patient name was invented from Susie's own utterance. Again.

`CA03ea1ce6df9ba962daec5cb77168cb18`, 24 Aug 2026 13:17, jv_v1, build
58319e89bc65. The caller had said one thing — "yeah i'd like to book an initial
assessment at your clinic the earliest slot you have um it's nothing serious
just a small ankle injury" — and had given no name.

Susie replied, empathetically and correctly:

    "...the balance and control don't always come back fully,
     SO IT'S GOOD you're getting it looked at."

    13:17:19  name persisted (normal path): 'Good'
    13:17:19  v3_phone_dtmf_active = True (name confirmed — phone collection)
    13:17:39  DTMF raw digit='2' v3_phone_dtmf_active=True   ← their SLOT choice
    13:17:48  barge-in: partial="hi i'm lucy"                ← suppressed
    13:17:50  transcript suppressed — phone DTMF active: 'right okay'
    13:18:08  transcript suppressed — phone DTMF active: 'hello'
    13:18:16  transcript suppressed — phone DTMF active: 'hello you still there'
    13:18:27  transcript suppressed — phone DTMF active: 'hello hello hello'
    13:18:28  outcome=abandoned, name=Good, dur=93s
              drop-off callback ping queued to ***5462 (lead='Good')

A caller named Lucy was recorded as "Good", could not select the slot she had
just been offered, and sat through ~50 seconds of silence before hanging up.

This is B-33's shape ('Rehab', 3 Aug) reaching the same outcome through a THIRD
pattern. B-33 fixed 1c and 1d. 1b was left because "So that's Sarah" reads like
a readback opener — but "So it's <X>" is ordinary English with no
acknowledgement verb, which is the stated criterion for being in the ANCHORED
list at all, and ANCHORED bypasses the phase gate.

The fix is the CAPITAL, not another wordlist entry. The three stopword lists
already hold the function words that reach this slot — "so", "then", "again",
"much", "course" are all blocked. What got through were ADJECTIVES, which are
unbounded. Requiring the captured word to look like a name is one rule.
"""
from __future__ import annotations

import pytest

from app.media_streams import connection as c

# Verbatim from the 13:17:16 TTS chunks, reassembled.
LIVE_REPLY = (
    "I'm sorry to hear that — ankle injuries can be really frustrating. "
    "When a rolled ankle doesn't get the right rehab, "
    "the balance and control don't always come back fully, "
    "so it's good you're getting it looked at."
)


def _persist(last_bot: str, caller: str = "", post_slot: bool = False):
    """The stored patient_name, or None if nothing was persisted."""
    session: dict = {}
    stored = c._v3_try_persist_name(
        session, last_bot, post_slot_pending=post_slot, caller_utterance=caller
    )
    return session.get("patient_name") if stored else None


# ── the live call ──────────────────────────────────────────────────────────

def test_the_verbatim_b81_reply_persists_nothing():
    assert _persist(LIVE_REPLY, caller="just a small ankle injury") is None


def test_it_persists_nothing_even_in_the_name_phase():
    """post_slot_pending widens the pattern set; it must not rescue this one."""
    assert _persist(LIVE_REPLY, post_slot=True) is None


@pytest.mark.parametrize(
    "adjective",
    ["good", "worth", "best", "fine", "important", "sensible", "wise"],
)
def test_no_adjective_after_so_its_becomes_a_name(adjective):
    """The family, not the one word that happened to be logged.

    Enumerating adjectives in a false-positive list is unbounded — which is why
    this is fixed at the matcher instead. Each of these is a sentence Susie
    plausibly says to a patient describing an injury.
    """
    reply = f"You've left it a while, so it's {adjective} you're getting seen."
    assert _persist(reply) is None


def test_the_function_words_stay_blocked_too():
    """These were already caught by the stopword lists; keep it that way."""
    for word in ("so", "then", "again", "much", "course"):
        assert _persist(f"So that's {word} — anyway.") is None


# ── what must still work ───────────────────────────────────────────────────

def test_a_real_readback_still_captures_the_name():
    assert _persist("So that's Sarah, and I've got your number as 07502.") == "Sarah"


def test_a_mid_sentence_readback_still_captures_the_name():
    """Why this was NOT fixed by anchoring to a sentence boundary, the way
    pattern 1c was. "Right, so that's Sarah" is a natural readback; anchoring
    would have silently lost it."""
    assert _persist("Right, so that's Sarah — perfect.") == "Sarah"


def test_the_its_form_still_captures_a_capitalised_name():
    assert _persist("So it's Sarah, lovely.") == "Sarah"


def test_an_em_dash_readback_still_captures_the_name():
    assert _persist("So that's Sarah — let me get that booked in.") == "Sarah"


def test_the_sibling_anchored_patterns_are_untouched():
    """Only 1b changed. A regression here means the edit was too wide."""
    assert _persist("Thanks Sarah — I've got you down for Tuesday.") == "Sarah"
    assert _persist("Of course Sarah, no problem at all.") == "Sarah"
    assert _persist("Right Sarah — Tuesday it is.") == "Sarah"


# ── the reason this one was fatal rather than merely wrong ─────────────────
#
# connection.py arms v3_phone_dtmf_active the moment a name is persisted, and
# from then on the DTMF handler reads every digit as a phone digit. On
# CA03ea1ce6 the caller's slot choice — a single '2' — went into the phone
# buffer, and a buffer that short never finalizes, so it suppressed every
# transcript for the rest of the call.
#
# That coupling is NOT fixed here and has no test here on purpose. It lives
# inside handle_transcript, and the only way to pin it from a unit test is to
# regex this module for the branch text — which is exactly how
# test_spec_i_keeps_cache_while_awaiting_slot_selection passed all the way
# through the B-78 bug it claimed to cover. A test that asserts the shape of a
# guard instead of its rule is worse than no test, because it reports green.
#
# What IS fixed, and is tested above, is that the name is never invented, so
# the arm never runs. The escape hatch that should have rescued the call —
# _stray_dtmf_buffer_yields_to_speech — needs >4 words, and every utterance
# the caller managed ("hello", "hello you still there") is four or fewer.
# Logged separately.
