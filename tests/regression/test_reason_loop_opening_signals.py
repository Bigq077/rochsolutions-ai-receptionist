"""B-128 - the caller's opening sentence was thrown away three times over.

Measured over 683 stored obs calls on 2026-09-01, all on the two clinics that
opt into a reason question AND ship a condition library (jv_v1, northgate):

  D1  33 calls  opened with the reason and were asked "What's the appointment
                for?" anyway  ("i'd like to book please it's for knee pain")
  D2  36 calls  opened by asking to book and were asked "would you like to
                book?" anyway, before any slot was on the table
  D3  17 calls  answered the reason question and were asked it again in fresh
                wording (a further 16 re-asks were the silence watchdog, which
                is correct behaviour and is NOT covered here)

The three had one shape in common: nothing on the live path read the opening
utterance. `apply_first_turn_signals` has only ever been reachable through
FlowEngine, which is bypassed on every live clinic, so `session["reason"]` was
written for the first time by `book_appointment`'s A2 gate - many turns after
the question had already been asked.

Each test below fails on the parent commit.
"""
import pytest

from app.media_streams.connection import (
    _reason_already_known,
    _next_question_after_booking_ack,
    _TIMING_QUESTION_AFTER_BOOKING_ACK,
)
from app.media_streams.first_turn_extractor import (
    opening_reason,
    opening_had_booking_intent,
)
from app.media_streams.turn_handler import sanitise_response


def _session(clinic_id="jv_v1", opening="", **kw):
    s = {"clinic_id": clinic_id, "opening_utterance": opening, "collected": {}}
    s.update(kw)
    return s


# -- D1: the reason arrived in the opening and was asked for anyway ----------

class TestOpeningReasonIsHeard:
    def test_condition_led_opening_is_not_asked_the_reason(self):
        """CAd15c3af6fc: "i'd like to book please it's for knee pain" produced
        "What's the appointment for?". The injector must ask TIMING instead."""
        s = _session(opening="hi um i'd like to book please it's for knee pain")
        assert _reason_already_known(s) is True
        assert _next_question_after_booking_ack(s) == _TIMING_QUESTION_AFTER_BOOKING_ACK

    def test_the_reason_is_recorded_not_merely_suppressed(self):
        """The write is the safety half. Suppressing without recording hands the
        caller to the A2 gate, which refuses the booking and instructs the model
        to ask the very question Gate 5b-r then strips - the documented
        deadlock that made Theorem bookings impossible."""
        s = _session(opening="yeah i'd like to book an appointment my left ankle is sore")
        _reason_already_known(s)
        assert (s.get("reason") or "").strip(), "A2 would refuse this booking"
        assert (s["collected"].get("reason") or "").strip()

    def test_an_opening_with_no_reason_is_still_asked(self):
        s = _session(opening="hi i'd like to book an appointment please")
        assert _reason_already_known(s) is False
        assert _next_question_after_booking_ack(s) == "What's the appointment for?"

    def test_two_complaints_fail_open_and_are_asked(self):
        """_extract_reason returns None when it cannot tell which is THE reason.
        Guessing would record the wrong one; asking is the safe direction."""
        s = _session(opening="hi i'd like to book my knee and my shoulder are both sore")
        assert opening_reason(s) is None
        assert _reason_already_known(s) is False

    def test_a_correction_fails_open(self):
        s = _session(opening="hi it's not my knee it's my hip")
        assert opening_reason(s) is None

    def test_an_existing_reason_is_never_overwritten(self):
        """A reason stated deliberately later outranks an opening aside."""
        s = _session(opening="hi i'd like to book my ankle is sore",
                     reason="lower back spasm")
        _reason_already_known(s)
        assert s["reason"] == "lower back spasm"


# -- D2: they already asked to book, and were asked whether they wanted to ---

class TestOpeningBookingIntentIsHeard:
    def test_booking_intent_in_the_opening_is_detected(self):
        s = _session(
            opening="yeah i'd like to book an appointment um just my left ankle nothing serious"
        )
        assert opening_had_booking_intent(s) is True

    def test_a_pure_enquiry_carries_no_booking_intent(self):
        s = _session(opening="hi how much is a sports massage")
        assert opening_had_booking_intent(s) is False

    def test_the_prompt_tells_the_model_not_to_re_offer(self):
        """CAa270bed18a: "i'd like to book ... my left ankle" produced "Shall I
        get you booked in with Marcus for an assessment?" - asking the caller to
        agree to something they had themselves proposed."""
        from app.prompts.susie_system_prompt import build_system_prompt_parts
        s = _session(opening="yeah i'd like to book an appointment my left ankle is sore")
        _static, dynamic = build_system_prompt_parts(s)
        assert "ASKED TO BOOK" in dynamic
        assert "do NOT ask whether they would" in dynamic

    def test_confirming_a_specific_slot_stays_allowed(self):
        """"Shall I book that in?" after reading a slot out is a different
        question and must not be swept up with the redundant offer."""
        from app.prompts.susie_system_prompt import build_system_prompt_parts
        s = _session(opening="yeah i'd like to book an appointment my ankle is sore")
        _static, dynamic = build_system_prompt_parts(s)
        assert "remains correct" in dynamic


# -- D3: asked, answered, asked again ---------------------------------------

class TestReasonQuestionAskedOnce:
    def test_a_second_reason_question_is_stripped_once_the_latch_is_set(self):
        """CAb4e2cf4b05: "Right - what's the appointment for?" / "my shoulder" /
        "Got it - can you tell me a bit more about what's been going on with
        it?". The question must not leave the system a second time."""
        s = _session(
            opening="um yeah i'd like to book an appointment for my shoulder please",
            _reason_question_asked=True,
        )
        out = sanitise_response(
            "Got it - can you tell me a bit more about what's been going on with it?", s
        )
        assert "going on with it" not in out.lower()

    def test_the_stripped_turn_still_asks_the_caller_something(self):
        """A turn that asks nothing is dead air, and dead air on a live call
        reads as a broken system. Gate 5b-r substitutes the outstanding step."""
        s = _session(
            opening="um yeah i'd like to book an appointment for my shoulder please",
            _reason_question_asked=True,
        )
        out = sanitise_response("Got it - what's the appointment for?", s)
        assert "?" in out, "turn asks nothing: {0!r}".format(out)

    def test_the_first_ask_is_untouched(self):
        """The clinic asks on purpose - once. Before the latch, nothing strips."""
        s = _session(opening="hi i'd like to book an appointment please")
        out = sanitise_response("Right - what's the appointment for?", s)
        assert "appointment for" in out.lower()

    def test_a_slot_readback_is_never_stripped(self):
        """The widened arm must not reach a readback: every one opens "So that's
        Wednesday the 19th..." and a connector-only rule deleted it once."""
        s = _session(opening="hi i'd like to book my ankle is sore",
                     _reason_question_asked=True)
        line = ("So that's Wednesday the 19th of August at ten in the morning "
                "- shall I go ahead and book that in?")
        assert sanitise_response(line, s) == line


# -- clinic scoping ---------------------------------------------------------

class TestClinicScoping:
    @pytest.mark.parametrize("cid", ["theorem", "theorem_v3"])
    def test_a_clinic_that_never_asks_renders_no_new_state(self, cid):
        """Theorem opts out, so none of this applies and it must not appear."""
        from app.prompts.susie_system_prompt import build_system_prompt_parts
        s = _session(clinic_id=cid, opening="hi i'd like to book my knee is sore")
        _static, dynamic = build_system_prompt_parts(s)
        assert "ASKED TO BOOK" not in dynamic
        assert "ALREADY said what this is about" not in dynamic

    def test_no_opening_utterance_is_harmless(self):
        """Before the first turn there is nothing to read, and the guard must
        fall through to its old behaviour rather than raise."""
        s = _session(opening="")
        assert opening_reason(s) is None
        assert opening_had_booking_intent(s) is False
        assert _reason_already_known(s) is False


# -- the opening must survive a greeting, and must not freeze early ----------

class TestOpeningLatch:
    def test_a_bare_greeting_is_not_the_opening(self):
        """77 of the 556 in-scope stored openings are "hi" / "hi there" / an STT
        fragment. Latching one spends the mechanism on a turn that says
        nothing."""
        from app.media_streams.first_turn_extractor import opening_is_substantive
        assert opening_is_substantive("hi") is False
        assert opening_is_substantive("hi there") is False
        assert opening_is_substantive("yeah i'd like to book my ankle is sore") is True

    def test_the_answer_is_not_frozen_before_the_opening_arrives(self):
        """A None cached at turn 0 would outlive the turn that fills it in, and
        the guard would be starved again by its own memo."""
        s = _session(opening="")
        assert opening_reason(s) is None
        assert opening_had_booking_intent(s) is False
        s["opening_utterance"] = "hi i'd like to book my ankle is sore"
        assert opening_reason(s) is not None
        assert opening_had_booking_intent(s) is True


# -- the strip must never become a loop -------------------------------------

class TestStripIsBounded:
    def test_a_second_ask_is_suppressed_when_no_reason_is_on_record(self):
        s = _session(opening="hi i'd like to book an appointment please",
                     _reason_question_asked=True)
        out = sanitise_response("Got it - what's the appointment for?", s)
        assert "appointment for" not in out.lower()

    def test_a_THIRD_ask_is_allowed_through_when_no_reason_is_on_record(self):
        """A2 refuses a booking with no reason and its error text ORDERS the
        model to ask. A gate that never yields leaves tool and gate fighting for
        the rest of the call - the Theorem deadlock. One suppression, then the
        question is allowed through."""
        s = _session(opening="hi i'd like to book an appointment please",
                     _reason_question_asked=True)
        sanitise_response("Got it - what's the appointment for?", s)
        out = sanitise_response("Sorry - what's the appointment for?", s)
        assert "appointment for" in out.lower(), (
            "the reason question can never be asked again: A2 will refuse the "
            "booking forever"
        )

    def test_the_strip_stays_unlimited_once_the_reason_is_on_record(self):
        """With a reason recorded A2 passes, so no instruction to re-ask can
        arrive and there is nothing to yield to."""
        s = _session(opening="hi i'd like to book my ankle is sore",
                     _reason_question_asked=True, reason="sore left ankle")
        for _ in range(3):
            out = sanitise_response("Got it - what's the appointment for?", s)
            assert "appointment for" not in out.lower()


# -- the reason must be recorded whichever path the turn takes --------------

class TestReasonIsRecordedPathIndependently:
    """CAa23b1ed5 / CA52dc5ea1, the first two live calls on this fix.

    Both ended "pre-summary reason: collected=None session=None". The first is
    the interesting one: RC-2 told the model NOT to say "Right -", so the
    booking-ack injector never ran, and the injector was the ONLY caller of
    `commit_opening_reason`. The two halves of the fix were fighting - the
    better the question is suppressed, the less often the reason is written.

    A2 itself is not at risk (it reads the model's tool argument first), but an
    abandoned call that stated a reason leaves the operator nothing to act on.
    """

    def test_the_reason_is_recorded_for_an_opt_in_clinic(self):
        from app.media_streams.first_turn_extractor import commit_opening_reason
        s = _session(opening="um yeah hi there i'd like to book an appointment "
                             "my left shoulder's been really sore for a couple of weeks")
        assert commit_opening_reason(s) is True
        assert "shoulder" in (s.get("reason") or "").lower()
        assert "shoulder" in (s["collected"].get("reason") or "").lower()

    def test_a_clinic_that_never_asks_is_not_given_a_reason(self):
        """Theorem records a reason only when volunteered through its own
        mechanism; an empty reason is a correct outcome there."""
        from app.media_streams.turn_handler import (
            _clinic_asks_its_own_reason_question,
        )
        s = _session(clinic_id="theorem_v3",
                     opening="hi i'd like to book my knee is sore")
        assert _clinic_asks_its_own_reason_question(s) is False

    def test_run_turn_commits_the_reason_and_not_only_the_injector(self):
        """Source-level, because the defect was a MISSING call site: the helper
        worked perfectly and simply was not reached on the condition-led path.
        A behavioural test of the helper cannot catch that, and did not."""
        import inspect
        from app.media_streams import llm_stream
        src = inspect.getsource(llm_stream)
        assert "commit_opening_reason" in src, (
            "run_turn no longer commits the opening reason; the only remaining "
            "call site is the booking-ack injector, which the condition-led "
            "rung deliberately bypasses"
        )
