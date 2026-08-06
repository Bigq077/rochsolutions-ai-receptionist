"""B-15 — capture_phase answers the question on the table, not a stale flag.

`v3_awaiting_surname` is sticky by design. It has three assignment sites, all in
`_v3_try_capture_name`, and both False-sites require a surname to have actually
been found — connection.py:1790 says so outright: "nothing clears it when the
conversation moves on". That stickiness is load-bearing: it is what lets a later
bare straggler word ("rock") be back-filled as the surname, which 96417a9 and
aa0b3bd exist to protect. It is NOT the thing to fix.

What was wrong is that capture_phase tested it BEFORE the prompt, so a caller who
gave a first name only was in phase "name" for the rest of the call — the slot
choice, the phone step, the booking confirmation, the closing.

The cost was audible, and not in the latency instrumentation (LATENCY_TIMING and
WS_C_SEMANTIC_ENDPOINT both default off). The dead-air re-ask at
connection.py:13500 is live regardless of either flag and picks its wording from
this function, so a caller who went quiet at the booking-confirm step was
answered with "could I take your first name and surname again?" — a question they
had already answered, about a step they had already passed.

Second defect closed in the same pass: the phone branch had only hard flags where
the name branch had a prompt fallback, and v3_awaiting_phone_confirm is set in
exactly one place (connection.py:5292, the reschedule/cancel DTMF path). On an
ordinary booking call the phone step therefore never resolved to "phone".
"""
import pytest

from app.media_streams.latency_timing import capture_phase

# _PHONE_QUESTION_MARKERS is imported inside the two drift guards that need it,
# not here. At module scope it would turn a behavioural regression into a
# collection error, which reports as one broken file rather than as the specific
# assertions that no longer hold.

# Real wordings, lifted from the template prompt and the runtime prompts rather
# than invented, so a prompt reword breaks these tests loudly instead of leaving
# them passing against text no caller ever hears.
PHONE_Q = (
    "Thanks Quentin — I've got you on oh seven five oh two, two one one, "
    "two oh seven — is that the best number for the booking?"
)
NAME_Q = "Lovely — could I take your first name and surname?"
SURNAME_Q = "And could I take your surname as well?"
BOOKING_CONFIRM_Q = (
    "So that's Quentin, Tuesday the 4th of August at half past six — "
    "shall I go ahead and book that in?"
)
SLOT_Q = "I've got quarter to seven and half past seven — which suits you best?"
LOCATION_KEYPAD_Q = (
    "No problem at all — on your keypad, just press 1 for Awlstuh, "
    "or 2 for Redditch."
)
PHONE_KEYPAD_Q = (
    "No problem — go ahead and type the number on your keypad. "
    "You can press the star key to reset at any time."
)


def _session(**kw):
    return dict(kw)


# ── The defect: a stuck flag hijacking the rest of the call ─────────────────

@pytest.mark.parametrize("prompt,expected", [
    (BOOKING_CONFIRM_Q, "conversation"),
    (SLOT_Q,            "conversation"),
    (PHONE_Q,           "phone"),
])
def test_a_stuck_surname_flag_no_longer_owns_the_rest_of_the_call(prompt, expected):
    """The caller gave a first name only; the flag is stuck True from there on."""
    phase = capture_phase(_session(
        v3_awaiting_surname=True,
        last_bot_prompt=prompt,
    ))
    assert phase == expected, (
        f"a stale surname flag beat the live question {prompt!r}"
    )


def test_the_booking_confirm_turn_is_not_a_name_turn():
    """The exact shape behind the audible symptom.

    Dead air here used to be answered with "could I take your first name and
    surname again?" — mid-booking-confirmation, to a caller who had already
    given their name.
    """
    assert capture_phase(_session(
        v3_awaiting_surname=True,
        last_bot_prompt=BOOKING_CONFIRM_Q,
    )) != "name"


# ── The second defect: the phone step was never "phone" ─────────────────────

def test_the_ordinary_booking_phone_step_is_phone():
    """v3_awaiting_phone_confirm is set ONLY on the reschedule/cancel path
    (connection.py:5292), so on a booking call the phone step had no flag at all
    and the phone re-ask wording was unreachable."""
    assert capture_phase(_session(last_bot_prompt=PHONE_Q)) == "phone"


def test_the_phone_keypad_invitation_is_phone():
    assert capture_phase(_session(last_bot_prompt=PHONE_KEYPAD_Q)) == "phone"


def test_a_clipped_phone_readback_is_still_phone():
    """A turn cut off before "best number" reaches last_bot_prompt — the case
    the read-back opener was added to _PHONE_STEP_MARKERS for (A1, 26 Jul)."""
    assert capture_phase(_session(
        last_bot_prompt="Thanks Quentin — I've got you on oh seven five oh two"
    )) == "phone"


# ── The false-positive half, which matters more ─────────────────────────────

def test_the_location_keypad_question_is_not_phone():
    """"on your keypad" appears in the LOCATION rung-3 prompt too, which is why
    it is excluded from _PHONE_QUESTION_MARKERS. Keying on it would answer a
    location question with "is the number you're calling on the best one?"."""
    assert capture_phase(_session(last_bot_prompt=LOCATION_KEYPAD_Q)) != "phone"


@pytest.mark.parametrize("prompt", [NAME_Q, SURNAME_Q])
def test_a_genuine_name_question_is_still_name(prompt):
    """The half that would be easy to break while fixing the other half."""
    assert capture_phase(_session(last_bot_prompt=prompt)) == "name"


def test_a_name_question_is_name_even_with_no_flag_set():
    assert capture_phase(_session(
        last_bot_prompt=NAME_Q, v3_awaiting_surname=False
    )) == "name"


def test_last_question_is_read_as_well_as_last_bot_prompt():
    """Both fields feed the prompt; only one may be populated."""
    assert capture_phase(_session(last_question=NAME_Q)) == "name"
    assert capture_phase(_session(last_question=PHONE_Q)) == "phone"


# ── Hard flags still win, and the fallback still exists ─────────────────────

def test_active_dtmf_entry_beats_any_prompt_text():
    assert capture_phase(_session(
        v3_phone_dtmf_active=True, last_bot_prompt=NAME_Q
    )) == "phone"


def test_the_reschedule_phone_flag_beats_any_prompt_text():
    assert capture_phase(_session(
        v3_awaiting_phone_confirm=True, last_bot_prompt=SLOT_Q
    )) == "phone"


def test_the_sticky_flag_is_still_the_fallback_when_no_prompt_was_recorded():
    """Deliberately preserved. With no prompt to read, the flag is the best
    evidence there is — and this is the case it was added for."""
    assert capture_phase(_session(v3_awaiting_surname=True)) == "name"
    assert capture_phase(_session(
        v3_awaiting_surname=True, last_bot_prompt="", last_question=""
    )) == "name"


def test_no_prompt_and_no_flag_is_conversation():
    assert capture_phase(_session()) == "conversation"


def test_an_empty_session_is_conversation():
    assert capture_phase({}) == "conversation"
    assert capture_phase(None) == "conversation"


def test_phone_wins_when_a_turn_carries_both_markers():
    """Matches the precedence the hard flags already established. Asserted so
    the ordering is a decision rather than an accident of line order."""
    assert capture_phase(_session(
        last_bot_prompt="Could I take your surname? And I've got you on 07502."
    )) == "phone"


# ── Drift guard: one vocabulary, two copies ─────────────────────────────────

def test_phone_markers_stay_a_subset_of_the_prompt_modules_list():
    """_PHONE_QUESTION_MARKERS is copied from _PHONE_STEP_MARKERS to keep
    latency_timing stdlib-only on the per-turn hot path. Copies of one
    vocabulary drifting apart is this codebase's standing failure — see
    DEFECT_REGISTER.md §A4, where an affirmative list lived in four places.
    This is the assertion that makes the copy safe.
    """
    from app.prompts.clinic_template_prompt import _PHONE_STEP_MARKERS
    from app.media_streams.latency_timing import _PHONE_QUESTION_MARKERS

    extra = set(_PHONE_QUESTION_MARKERS) - set(_PHONE_STEP_MARKERS)
    assert not extra, (
        f"markers present here but not in the prompt module's vetted list: "
        f"{sorted(extra)}. Add them there, or drop them here."
    )


def test_the_two_lists_now_agree_exactly():
    """
    They used to differ by one member. `_PHONE_QUESTION_MARKERS` excluded
    "on your keypad" because it also appears in the LOCATION rung-3 prompt —
    the right call, made for B-15 — but the list it was copied FROM kept it,
    so the false positive stayed live everywhere else that reads it: the
    book_appointment phone backstop and the phone-confirm-unsettled ladder.

    Removing it from `_PHONE_STEP_MARKERS` collapses the difference. Any new
    divergence now means someone edited one copy and not the other.
    """
    from app.prompts.clinic_template_prompt import _PHONE_STEP_MARKERS
    from app.media_streams.latency_timing import _PHONE_QUESTION_MARKERS

    missing = set(_PHONE_STEP_MARKERS) - set(_PHONE_QUESTION_MARKERS)
    assert not missing, (
        f"unreviewed difference from the prompt module's list: {sorted(missing)}. "
        "Each marker must be checked against the LOCATION rung-3 prompt and any "
        "other keypad context before it is added to either list."
    )


def test_the_location_keypad_prompt_is_not_a_phone_question():
    """
    The regression itself, stated against the two real prompts. The location
    rung must not read as the phone step in EITHER list — that false positive
    disarms the book_appointment backstop that blocks a phoneless booking.
    """
    from app.prompts.clinic_template_prompt import _PHONE_STEP_MARKERS
    from app.media_streams.latency_timing import _PHONE_QUESTION_MARKERS
    from app.media_streams.connection import _LOC_RUNG3_DTMF

    location = _LOC_RUNG3_DTMF.lower()
    for name, markers in (
        ("_PHONE_STEP_MARKERS", _PHONE_STEP_MARKERS),
        ("_PHONE_QUESTION_MARKERS", _PHONE_QUESTION_MARKERS),
    ):
        hit = [mk for mk in markers if mk in location]
        assert not hit, (
            f"{name} matches the LOCATION keypad prompt via {hit} — a clinic "
            f"question counts as the phone question having been asked"
        )


def test_the_real_phone_prompt_still_matches_both_lists():
    """Guard against removing so much that the phone step stops registering."""
    from app.prompts.clinic_template_prompt import _PHONE_STEP_MARKERS
    from app.media_streams.latency_timing import _PHONE_QUESTION_MARKERS

    phone_prompt = (
        "No problem — go ahead and type the number on your keypad. "
        "You can press the star key to reset at any time."
    ).lower()
    assert any(mk in phone_prompt for mk in _PHONE_STEP_MARKERS)
    assert any(mk in phone_prompt for mk in _PHONE_QUESTION_MARKERS)
