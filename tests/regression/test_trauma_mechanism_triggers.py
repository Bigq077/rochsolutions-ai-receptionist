"""The trauma screen must arm on how people actually describe hurting a joint.

Jules's sweep, call S1a. The caller said, verbatim from the obs transcript:

    "i've done my ankle went over on sunday playing football"

Layer 1 did not arm — neither ARMED nor ORPHAN, nothing at all. The
trauma_fracture trigger list covered falls, bike spills, car crashes, audible
cracks and inability to weight-bear, but not the commonest mechanisms in a
physio clinic: going over on it, rolling it, twisting it, spraining it. All
seven mechanism phrasings below missed on the pre-fix list.

The variant S1b ("came off my bike, landed on my wrist") DID arm, which is what
proved this was a vocabulary gap rather than a dead layer — the same
presentation armed or not depending on which word the caller reached for.

WHY THE PHRASES ARE TIGHT. _phrase_in tolerates up to _TRIGGER_MAX_GAP (3)
filler words between terms, so short two-word triggers reach much further than
they look:

    "rolled my"   matches  "i rolled over in my sleep and felt a twinge"
    "went over"   matches  "i went over the details with my gp already"

Both were measured as false fires and rejected in favour of the specific forms.
Over-screening is not harmless — C5A exists to prove a benign presentation gets
no screen at all — so the negative cases below are as load-bearing as the
positives.
"""
from __future__ import annotations

import pytest

from app.media_streams import clinical_screening as cs


@pytest.fixture
def clinic():
    from app.clinic_config import get_clinic
    c = get_clinic("jv_v1")
    assert cs.screening_enabled(c)
    return c


def _armed(clinic, text):
    """Screen id armed by this utterance on a fresh session, or None."""
    return cs.match_screen_trigger(text, clinic, {})


# ─────────────────────────────────────────────────────────────────────────
# The case that failed
# ─────────────────────────────────────────────────────────────────────────
def test_s1a_verbatim_now_arms(clinic):
    """The exact words from the obs transcript of S1a."""
    assert _armed(clinic, "i've done my ankle went over on sunday playing football") \
        == "trauma_fracture"


def test_s1b_still_arms(clinic):
    """The variant that already worked must not regress."""
    assert _armed(clinic, "i came off my bike and landed on my wrist") \
        == "trauma_fracture"


@pytest.mark.parametrize("text", [
    "i went over on it playing football on saturday",
    "went over on my ankle coming down the stairs",
    "rolled my ankle at five a side",
    "rolled my knee playing rugby",
    "twisted my knee getting out the car",
    "twisted my ankle on the pavement",
    "twisted my shoulder reaching for something",
    "i think ive sprained my ankle",
    "turned my ankle on a kerb",
    "done my shoulder at the gym",
    "ive done my wrist",
])
def test_mechanism_phrasings_arm(clinic, text):
    assert _armed(clinic, text) == "trauma_fracture", text


# ─────────────────────────────────────────────────────────────────────────
# Negatives — the brake. Do not relax these to widen recall.
# ─────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    # C5A, verbatim from the sweep — the benign canary. Must screen nothing.
    "hi i've worked tight hamstring from running and i'd like to book a sports massage",
    "i'd like to book a sports massage please",
    "can i move my appointment",
    "how much is an appointment",
    "my shoulder has been aching for a few weeks",
    # These killed the loose forms: 3-word gap tolerance makes short triggers
    # reach across unrelated clauses.
    "i rolled over in my sleep and felt a twinge",
    "i rolled over in bed and my neck went",
    "i went over the details with my gp already",
    "we twisted my referral around the appointment",
])
def test_benign_utterances_arm_nothing(clinic, text):
    assert _armed(clinic, text) is None, text


def test_hamstring_stays_completely_unscreened(clinic):
    """C5A end to end — not just 'no trauma screen', but no screen at all and
    no deterministic speech."""
    sess = {}
    result = cs.update_screening_state(
        sess, clinic, "hi i've worked tight hamstring from running "
                      "and i'd like to book a sports massage"
    )
    assert result == {"action": "none", "speak": None}
    assert not sess.get(cs.PENDING_SCREEN_KEY)
    assert not sess.get(cs.SCREEN_ARM_PATHS_KEY)


# ─────────────────────────────────────────────────────────────────────────
# End to end, and the arm path
# ─────────────────────────────────────────────────────────────────────────
def test_s1a_asks_the_screen_and_records_a_trigger_arm(clinic):
    """Arming is only half of it: the screen must be spoken, and the durable
    record must show Layer 1 caught it rather than the model covering."""
    sess = {}
    result = cs.update_screening_state(
        sess, clinic, "i've done my ankle went over on sunday playing football"
    )

    assert result["action"] == "ask_screen"
    assert "weight" in (result["speak"] or "").lower()
    assert sess[cs.SCREEN_ARM_PATHS_KEY] == {"trauma_fracture": cs.ARM_TRIGGER}
    assert cs.booking_blocked_reason(sess, clinic) is not None


def test_back_presentations_still_go_to_cauda_first(clinic):
    """'twisted my back' matches no new trigger, and back pain must continue to
    route to cauda_equina — config order decides, and it must not have moved."""
    assert _armed(clinic, "i twisted my back lifting something") == "cauda_equina"


def test_config_is_valid_json_and_the_screen_is_intact(clinic):
    """A hand-edited 30KB clinic.json is easy to break silently."""
    screen = cs.get_screen(clinic, "trauma_fracture")
    assert screen and screen.get("screen_question")
    assert screen.get("block_booking", True) is True
    kws = screen["trigger_keywords"]
    assert len(kws) == len(set(kws)), "duplicate trigger keywords"
    # The originals must all survive the edit.
    for original in ("fell", "had a fall", "came off my bike", "heard a crack",
                     "car accident", "bad tackle", "can't put weight"):
        assert original in kws, original
