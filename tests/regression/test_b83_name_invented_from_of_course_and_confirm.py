# tests/regression/test_b83_name_invented_from_of_course_and_confirm.py
"""
B-83 — the RECORD path was capitalised on 22 Aug; the LIVE path was not.

Found while fixing B-81 (pattern 1b, `so it's good` -> name 'Good'), by
comparing the two copies of this matcher that exist in the repo:

    app/tools/actionable_summary.py:268-269   [A-Z]     <- fixed 22 Aug, e59f86b
    app/media_streams/connection.py           [A-Za-z]  <- not fixed

The 22 Aug sweep enumerated SIX false names and capitalised both of its arms.
It never crossed to connection.py — so the path that merely mislabels a call
record was hardened, while the path that *arms phone DTMF* and can deafen a
live call (see B-81) kept the defect.

Reproduced directly against the shipped matcher, both ANCHORED so both bypass
the name-collection phase gate:

    pattern 1e  "Of course darling, one moment."             -> 'Darling'
    pattern 1f  "Just to confirm, that's booked for Tuesday." -> 'Booked'

'Booked' is one of the six the 22 Aug sweep already found.

Fixed the same way and for the same stated reason: require the captured word to
be capitalised. A stoplist can only ever name false positives someone has
already been bitten by — "lovely" was in it and "darling" was not — whereas
generated prose capitalises a name and lower-cases an adjective or an
endearment. Weekdays and months were already blocked and are asserted below so
that stays true.

Safe against Gate 5, checked rather than assumed: `_INTERIM_DUPE_RE` strips
none of these three openers, so nothing re-capitalises the captured word; and
`join_after_head` lowers only the FIRST word of a payload, which in every
ANCHORED pattern is the lead-in ("Of course", "Just to confirm"), never the
captured word.
"""
from __future__ import annotations

import pytest

from app.media_streams import connection as c


def _persist(last_bot: str, post_slot: bool = False):
    session: dict = {}
    stored = c._v3_try_persist_name(
        session, last_bot, post_slot_pending=post_slot, caller_utterance=""
    )
    return session.get("patient_name") if stored else None


# ── the two reproductions ──────────────────────────────────────────────────

def test_of_course_darling_is_not_a_name():
    assert _persist("Of course darling, one moment.") is None


def test_just_to_confirm_thats_booked_is_not_a_name():
    """'Booked' is one of the six names the 22 Aug record-path sweep found."""
    assert _persist("Just to confirm, that's booked for Tuesday.") is None


def test_neither_is_rescued_by_the_name_phase():
    for reply in (
        "Of course darling, one moment.",
        "Just to confirm, that's booked for Tuesday.",
    ):
        assert _persist(reply, post_slot=True) is None


@pytest.mark.parametrize(
    "word",
    ["darling", "sweetheart", "good", "fine", "booked", "sorted", "done",
     "confirmed", "absolutely", "certainly"],
)
def test_no_lowercase_word_survives_either_pattern(word):
    """The family, not the two sentences that happened to be probed."""
    assert _persist(f"Of course {word}, one moment.") is None
    assert _persist(f"Just to confirm, that's {word} for Tuesday.") is None


# ── what must still work ───────────────────────────────────────────────────

def test_of_course_still_captures_a_real_name():
    assert _persist("Of course Sarah, no problem at all.") == "Sarah"


def test_just_to_confirm_still_captures_a_real_name():
    assert _persist("Just to confirm, that's Sarah — and your number?") == "Sarah"


def test_just_to_confirm_em_dash_form_still_captures():
    assert _persist("Just to confirm — that's Sarah, booked in.") == "Sarah"


def test_the_dates_that_were_already_blocked_stay_blocked():
    """These were caught by the stoplist, not by the capital. Keep both."""
    assert _persist("Just to confirm, that's Tuesday at three.") is None
    assert _persist("Just to confirm, that's Monday the 31st.") is None
    assert _persist("Of course Tuesday works, let me check.") is None


def test_the_sibling_patterns_are_untouched():
    """Only 1e and 1f changed here. 1b was B-81."""
    assert _persist("Thanks Sarah — I've got you down for Tuesday.") == "Sarah"
    assert _persist("Right Sarah — Tuesday it is.") == "Sarah"
    assert _persist("So that's Sarah, and I've got your number.") == "Sarah"
