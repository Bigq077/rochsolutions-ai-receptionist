"""
The theorem_v3 reschedule/cancel flow is model-driven, not code-injected (T-18).

Observed on the second reschedule call ever attempted, 2026-08-05 00:34:13.

    00:34:16  FINAL → "um yeah i'd like to move my appointment"
    00:34:19  [ms_gate5] removed banned phrase (banned_opener)
    00:34:19  synthesise_chunk: "Let's get that moved for you."
    00:34:21  Spec W: turn asked nothing and no question is outstanding
    …                                    seven seconds of dead air …
    00:34:27  caller: 'uh hello'

The prompt mandated the ack "Of course, let's get that moved for you." and
connection.py detected it by literal-matching `_V3_ACK_PHRASES`, which held
"of course, let's get that moved". But conversation_history has stored the
POST-Gate-5 text since 2 Aug, and Gate 5's banned_opener rule strips a leading
"Of course, ". The entry could never match. No ack was detected, no question
was injected, and the flow opened on silence.

The fix ports latency-eval's contract: the model owns the opening turn (ack +
phone readback together) and code injects nothing. These tests pin the
properties that has to keep, all of which are cheap to break by editing one
string in either file.
"""

import inspect
import re

from app.media_streams import connection as conn
from app.media_streams.turn_handler import _BANNED_SENTENCE_RE
from app.prompts.susie_system_prompt import build_system_prompt_parts


def _banned_opener_re():
    for name, pattern in _BANNED_SENTENCE_RE:
        if name == "banned_opener":
            return pattern
    raise AssertionError("Gate 5 no longer has a 'banned_opener' rule")


def _ack_phrases() -> list:
    """Parse _V3_ACK_PHRASES out of the handler rather than restating it.

    Reads the comment-stripped source: the note above the tuple quotes the
    dead entries it explains, and those must not be read back as live ones.
    """
    literal = re.search(
        r"_V3_ACK_PHRASES = \((.*?)\n\s*\)", _handler_code(), re.DOTALL
    )
    assert literal, "_V3_ACK_PHRASES tuple not found — was it renamed?"
    phrases = re.findall(r'"([^"]+)"', literal.group(1))
    assert phrases, "no phrases parsed out of _V3_ACK_PHRASES"
    return phrases


def _handler_code() -> str:
    """Handler source with comment lines removed.

    These tests assert on what the code does; without this they also match the
    comments that explain what it deliberately stopped doing.
    """
    source = inspect.getsource(conn.WebSocketCallHandler)
    return "\n".join(
        line for line in source.split("\n")
        if not line.lstrip().startswith("#")
    )


def _theorem_prompt(**overrides) -> str:
    session = {
        "clinic_id": "theorem_v3",
        "twilio_from_local": "07502211207",
        "turn_count": 3,
        "collected": {},
        "soft_context": {},
    }
    session.update(overrides)
    static, dynamic = build_system_prompt_parts(session)
    return static + "\n" + dynamic


def _reschedule_block(prompt: str) -> str:
    """The flow section itself, not the booking flow's cross-reference to it."""
    start = prompt.index("\nRESCHEDULE / CANCEL FLOW\n")
    return prompt[start:start + 8000]


# ── 1. No ack phrase may begin with an opener Gate 5 strips ──────────────────

def test_no_v3_ack_phrase_starts_with_a_gate5_banned_opener():
    """The bug in one assertion: a phrase Gate 5 rewrites cannot be matched.

    _V3_ACK_PHRASES is compared against the post-Gate-5 text, so an entry
    beginning with "Of course," is dead on arrival. Reads both real sources
    rather than restating them, so the test tracks edits to either.
    """
    banned_opener = _banned_opener_re()
    dead = [p for p in _ack_phrases() if banned_opener.match(p)]
    assert not dead, (
        "these ack phrases start with an opener Gate 5 strips, so they can "
        "never match the post-Gate-5 text they are compared against: "
        f"{dead}. Anchor them on the part of the phrase Gate 5 leaves alone."
    )


def test_reschedule_ack_survives_gate5_and_is_still_detected():
    """End to end: the ack, run through Gate 5, still matches an entry.

    Covers the case even if the model volunteers the banned opener anyway.
    """
    spoken = _banned_opener_re().sub(
        "", "Of course, let's get that moved for you."
    ).lower()
    assert any(p in spoken for p in _ack_phrases()), (
        f"post-Gate-5 ack {spoken!r} matches no entry in _V3_ACK_PHRASES — "
        "the reschedule ack would go undetected exactly as it did at 00:34:13"
    )


# ── 2. The prompt must own the opening turn ─────────────────────────────────

def test_reschedule_flow_asks_for_the_phone_in_its_opening_turn():
    block = _reschedule_block(_theorem_prompt())
    assert "OPENING TURN" in block
    assert "is that the number the appointment was booked under?" in block, (
        "the model no longer has the phone readback, and code no longer "
        "injects a phone question — the flow would open on silence"
    )


def test_reschedule_flow_never_asks_which_clinic():
    """Two sites, but the location comes from the lookup, never from a question.

    The prompt has always said the caller's clinic preference is discarded in
    this flow; asking anyway cost a turn, and that question's re-queue is what
    collapsed the 00:08:43 call.
    """
    block = _reschedule_block(_theorem_prompt())
    assert "THERE IS NO CLINIC QUESTION IN THIS FLOW" in block


def test_prompt_does_not_promise_that_code_asks_for_the_number():
    """No block may tell the model the system will ask the phone question.

    Two blocks of one prompt disagreeing about who asks for the number is the
    shape that produced this defect.
    """
    prompt = _theorem_prompt()
    assert "the system asks for the clinic and then the phone number" not in prompt


def test_call_state_does_not_forbid_reading_the_number_back():
    """CALL STATE states facts; the flow blocks set policy.

    It used to append "no readback needed" to the caller phone, which
    contradicted both the booking keypad readback and this flow's opening turn.
    """
    prompt = _theorem_prompt()
    assert "caller phone (pre-loaded from caller ID): 07502211207" in prompt
    assert "no readback needed" not in prompt


# ── 3. Code must not inject into this flow ──────────────────────────────────

def test_use_this_number_is_not_injected_on_reschedule_or_cancel():
    """The banned set-phrase question is gone from the booking-ack handler.

    'If so, just say "use this number"' asks the caller to reason about a
    number they cannot hear — banned by the owner on the other branches on
    3 Aug. The booking flow's own keypad prompts keep their copies of the
    phrase, so this asserts on the reschedule branch only.
    """
    branch = re.search(
        r'if _intent in \("reschedule", "cancel"\):(.*?)\n\s*else:',
        _handler_code(),
        re.DOTALL,
    )
    assert branch, "reschedule/cancel branch of the booking-ack handler not found"
    body = branch.group(1)

    assert "use this number" not in body, (
        "the reschedule ack is injecting the banned set-phrase question again"
    )
    assert "v3_awaiting_phone_confirm" not in body, (
        "arming the deterministic phone-confirm intercept assumes a question "
        "this flow no longer asks — it would swallow the caller's plain 'yes' "
        "to the readback"
    )
    assert "_next_q = None" in body, (
        "the reschedule ack must inject nothing — the model asks in its own turn"
    )


def test_location_gate_is_suppressed_for_reschedule_and_cancel():
    code = _handler_code()
    assert re.search(
        r'if _v3_gate_fired and _gate_intent in \(\s*\n?\s*"reschedule", "cancel"\s*\n?\s*\):',
        code,
    ), "the location gate no longer has its reschedule/cancel suppression"
    assert "Was your original appointment at" not in code, (
        "the discarded clinic question is back in the location gate"
    )


# ── 4. The not-found path must not dead-end on a transfer ───────────────────

def test_lookup_not_found_offers_alternatives_before_transferring():
    block = _reschedule_block(_theorem_prompt())
    assert (
        "Are you sure the number you're calling on is the one your "
        "booking is under?"
    ) in block
    assert "let me put you through to the team" not in block, (
        "exhausting one number's appointments must not transfer — the booking "
        "is usually just under a different number"
    )
