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

def test_reschedule_flow_asks_which_clinic_first():
    """Owner's call, 2026-08-05: the clinic question is asked, and asked FIRST.

    It is source (2) of the STRICT RULE — an explicit statement from the caller
    naming the clinic — and on a two-site clinic it is the natural opening
    question. What changed in a1c4593 is only WHO asks it: the model, in the
    same turn as the ack, rather than code injecting a separate turn.
    """
    block = _reschedule_block(_theorem_prompt())
    assert "TURN 1" in block
    assert "Was your original appointment at our Awlstuh or Redditch clinic?" in block, (
        "the reschedule flow no longer asks which clinic"
    )
    assert "Was the appointment you'd like to cancel at our Awlstuh or Redditch clinic?" in block, (
        "the cancel flow no longer asks which clinic"
    )


def test_the_clinic_answer_is_stored():
    """Asking is only half of it — the answer has to reach the session.

    collect_and_store(field="location") syncs selected_location and is
    persisted to Redis by save_session; without it the answer is spoken into
    the void and downstream tools fall back to the alcester default.
    """
    block = _reschedule_block(_theorem_prompt())
    assert 'collect_and_store(field="location"' in block
    for value in ("alcester", "redditch"):
        assert value in block, f"no stored value given for {value}"


def test_the_phone_readback_shares_the_turn_with_the_clinic_ack():
    """The dead-air shape, one step later.

    "Right, Awlstuh." on its own is a bare acknowledgement with nothing for the
    caller to answer — exactly what left three calls in silence at the ack.
    """
    block = _reschedule_block(_theorem_prompt())
    assert "is that the number the appointment was booked under?" in block
    assert "NEVER end turn 2 on the clinic acknowledgement alone" in block


def test_the_appointment_beats_the_callers_memory():
    """Both sources can now exist and disagree. The booking record wins."""
    block = _reschedule_block(_theorem_prompt())
    assert "WHEN BOTH SOURCES EXIST, THE APPOINTMENT WINS" in block
    assert "Never move or cancel an appointment at a site it is not at" in block


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


def test_exactly_one_asker_owns_the_clinic_question():
    """The flow asks which clinic; the MODEL asks it, not code.

    Either can ask. Both must not. When code owned it, the question arrived as
    an injected turn and its re-queue collapsed the 00:08:43 call, and the
    injection depended on literal-matching the model's ack, which left three
    calls in dead air. The prompt now carries the question; this pins the code
    side to silence so they cannot both fire.
    """
    code = _handler_code()
    assert re.search(
        r'if _v3_gate_fired and _gate_intent in \(\s*\n?\s*"reschedule", "cancel"\s*\n?\s*\):',
        code,
    ), "the code location gate is asking on this flow again — it would double-ask"
    assert "Was your original appointment at" not in code, (
        "the clinic question is back in the location gate; it belongs to the "
        "prompt now, and two askers means the caller is asked twice"
    )


# ── 3b. No OTHER block may re-teach the code-driven contract ────────────────
#
# The port fixed the RESCHEDULE / CANCEL FLOW block and the code, and the flow
# still died on a bare ack for three more live calls, because three other
# blocks were still teaching the old behaviour. Editing the block you are
# looking at is not enough in a 100k-char prompt: what reaches the model is the
# UNION of every block, and the model obeys the most emphatic one it finds.


def test_no_block_claims_the_system_finishes_the_turn():
    """The model owns its whole turn now — nothing is appended by code.

    The banned-openers block used to exempt "Of course, let's get that moved
    for you." with "— the system handles that automatically". Both halves were
    wrong: Gate 5 strips that opener, and no system injection remains. Left in,
    it told the model to ack and stop.
    """
    p = _theorem_prompt()
    assert "the system handles that automatically" not in p, (
        "a block still tells the model the system finishes its reschedule "
        "turn — it will ack and stop, and the caller hears silence"
    )
    assert "No acknowledgement is handled for you" in p
    # "The system injects the next question" is deliberately NOT asserted away:
    # it is still true of the new-booking flow's first step, which really is
    # code-injected. Only the reschedule ack lost its injection.


def test_ack_and_stop_is_not_the_global_default():
    """Two blocks used to contradict each other outright.

    ONE QUESTION PER TURN said "the acknowledgement is its own turn — the next
    question goes on the following turn". The ACKNOWLEDGEMENT RULE said "The
    acknowledgement and the next question are delivered in the same turn —
    never as separate turns". The model resolved it by stopping.
    """
    p = _theorem_prompt()
    assert "acknowledgement is its own turn" not in p, (
        "ONE QUESTION PER TURN has gone back to making ack-and-stop the "
        "default, contradicting the ACKNOWLEDGEMENT RULE"
    )
    assert "The acknowledgement and the next question are delivered in the same turn" in p
    assert "An acknowledgement is NOT a turn of its own" in p


def test_reschedule_intent_escapes_the_new_booking_flow_first_step():
    """Booking step 1 is the first thing an existing patient hits.

    Until 5 Aug only a near-miss of "cancel" escaped it; "I'd like to
    reschedule my appointment" fell through to 'acknowledge simply "Right —"
    and NOTHING ELSE'. The reschedule flow's opening turn was never reached.
    """
    p = _theorem_prompt()
    step1 = p[p.index("1. Caller signals booking intent"):]
    step1 = step1[:step1.index('acknowledge simply')]
    assert "MOVED" in step1, (
        "booking step 1 no longer hands reschedule intent to the reschedule "
        "flow — a bare 'Right —' will swallow it again"
    )
    for word in ("reschedule", "rearrange", "move", "change"):
        assert word in step1.lower(), f"{word!r} does not escape booking step 1"
    assert "do NOT stop after an acknowledgement" in step1


def test_the_reschedule_ack_is_taught_without_the_banned_opener():
    """Gate 5 deletes a leading "Of course, ". The taught ack must not have one."""
    p = _theorem_prompt()
    banned = _banned_opener_re()
    assert not banned.match("Let's get that moved for you.")
    # The old form may appear only as an explicit negative example.
    for m in re.finditer(r"[^.\n]*Of course, let's get that moved[^.\n]*", p):
        assert "never" in m.group().lower(), (
            f"the banned-opener form is still taught as the ack: {m.group()[:120]!r}"
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
