# tests/regression/test_b75b_reasoning_spoken_to_the_caller.py
"""
B-75b — Susie read her own working notes out to a caller.

JV `CA9262659c67e03b73b5ff2992f72bc832`, 21 Aug 2026. Two sentences reached
ElevenLabs and were spoken:

    20:00:06.717  "Let me look at what I have - the caller confirmed quarter pa..."  (len=128)
    20:00:07.001  "I don't actually have the lookup data or the slot ISO."

Gate 5a was running and caught a THIRD reasoning sentence on the same turn
("dropped reasoning chunk (reasoning_sentence_opener): 'I need to call the
reschedule tool now. Let me che...'"). It missed these two.

Why it missed them. Both live matchers are enumerated literals:

  * `_REASONING_OPENER_RE` lists sentence openers - "Let me work out",
    "Looking at the", "So I need to". The model said "Let me look at what I
    have", which is neither, and the list has no way to anticipate the next
    phrasing.
  * `internal_identifier_token` is already a proper CLASS rule (any snake_case
    token is machine vocabulary) - but it only ever sees `slot_iso`. The model
    read the identifier ALOUD, as "slot ISO", and the underscore it keys on was
    gone.

The fix is deliberately NOT "add those two phrasings". This repository has been
bitten four times by matchers pinned to one literal of model speech. Two class
rules instead:

  1. `third_person_caller_reference` - Susie speaks TO the caller and says
     "you". "the caller" is the PROMPT's word for them, which is exactly why
     the model reaches for it when narrating rather than speaking.
     `sanitise_response` only ever sees model output, never the greeting, the
     transfer whisper or any code-built line, so no legitimate speech is at
     risk. The same principle is already stated for "the patient" in
     `lookup_reasoning_leak`.
  2. `spoken_identifier_token` - the snake_case rule's blind spot is an
     identifier read out with the underscore as a space. "ISO" and "lookup"
     (one word, used as a noun) have no receptionist meaning in English.

Both strip only the containing sentence, like every other rule in that table.
"""
from __future__ import annotations

import pytest

from app.media_streams import turn_handler as th


def _session():
    return {"_clinical_depth_cache": "", "v3_cta_count": 0}


# ══════════════════════════════════════════════════════════════════════════
# 1 — the two sentences the caller actually heard
# ══════════════════════════════════════════════════════════════════════════
LEAK_ONE = (
    "Let me look at what I have - the caller confirmed quarter past five "
    "in the evening on Friday the 28th."
)
LEAK_TWO = "I don't actually have the lookup data or the slot ISO."


@pytest.mark.parametrize("leak", [LEAK_ONE, LEAK_TWO], ids=["caller_ref", "slot_iso"])
def test_the_spoken_reasoning_is_stripped(leak):
    assert th.sanitise_response(leak, _session()).strip() == "", (
        "internal reasoning still reaches TTS"
    )


def test_the_leak_survives_the_enumerated_opener_list():
    """Pins WHY it got through, so nobody 'fixes' this by extending that list.

    If someone adds "Let me look at" to _REASONING_OPENER_RE this still passes -
    the point is that the class rules catch it without anyone having to.
    """
    assert not th._REASONING_OPENER_RE.search(LEAK_ONE), (
        "re-pin this test - the opener list changed"
    )


def test_the_snake_case_rule_cannot_see_a_spoken_identifier():
    """`slot_iso` is caught; "slot ISO" is the same leak read aloud."""
    snake = dict(th._BANNED_SENTENCE_RE)["internal_identifier_token"]
    assert snake.search("The slot_iso is wrong.")
    assert not snake.search(LEAK_TWO)


# ══════════════════════════════════════════════════════════════════════════
# 2 — real speech must survive. The over-fire is the expensive failure.
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("line", [
    # the two-word verb, which is what a receptionist actually says
    "Let me look up your appointment for you.",
    "I'll look up that booking now.",
    # word boundaries: real words that merely contain the tokens
    "That is isolated to the lower back.",
    "The pain is isolated, not spreading.",
    # ordinary clinic speech
    "We are closed on Bank Holidays.",
    "You're booked in for Friday the 28th at quarter past five.",
    "That's you rescheduled - you're now in for Thursday at ten.",
    "Is that the right one?",
    "I've got you on oh seven five oh two, two one one, two oh seven.",
    "Do you have a preference for when you'd like to reschedule to?",
])
def test_legitimate_speech_is_untouched(line):
    assert th.sanitise_response(line, _session()).strip() == line.strip()


def test_only_the_offending_sentence_is_removed():
    """Every rule in this table strips a sentence, not the turn."""
    mixed = (
        "The caller wanted Friday. "
        "You're booked in for Friday the 28th at quarter past five."
    )
    out = th.sanitise_response(mixed, _session())
    assert "booked in for friday the 28th" in out.lower()
    assert "the caller" not in out.lower()


# ══════════════════════════════════════════════════════════════════════════
# 3 — the rules are classes, not phrasings
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("unseen", [
    # none of these appeared on the call; all are the same CLASS
    "The caller's number is already on file here.",
    "I should check what the caller said about Tuesday.",
    "The lookup returned two matches.",
    "I need the ISO before I can write that.",
])
def test_phrasings_never_observed_are_caught_too(unseen):
    """The whole point of a class rule: it covers the next phrasing as well.

    If these needed adding one by one, the fix would be the same trap it
    replaced.
    """
    assert th.sanitise_response(unseen, _session()).strip() == ""


def test_the_new_rules_are_registered_in_the_banned_table():
    names = [n for n, _ in th._BANNED_SENTENCE_RE]
    assert "third_person_caller_reference" in names
    assert "spoken_identifier_token" in names
