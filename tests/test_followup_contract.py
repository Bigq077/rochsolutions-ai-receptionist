# tests/test_followup_contract.py
"""
Focused tests for the deterministic pending follow-up contract resolver.

Covers:
- service_fit_pending proceed intent (various phrasing)
- service_fit_pending info follow-up questions
- service_fit_pending decline
- service_fit_pending unknown / fallthrough
- age extraction utility
"""
import pytest
from app.media_streams.followup_contract import (
    resolve_service_fit_followup,
    extract_age,
    RESOLVE_PROCEED,
    RESOLVE_INFO,
    RESOLVE_DECLINE,
    RESOLVE_UNKNOWN,
    KIND_SERVICE_FIT_PENDING,
)


# ── resolve_service_fit_followup ───────────────────────────────────────────────

class TestProceedIntent:
    def test_yes(self):
        assert resolve_service_fit_followup("yes") == RESOLVE_PROCEED

    def test_yes_please(self):
        assert resolve_service_fit_followup("yes please") == RESOLVE_PROCEED

    def test_yeah(self):
        assert resolve_service_fit_followup("yeah") == RESOLVE_PROCEED

    def test_okay(self):
        assert resolve_service_fit_followup("okay") == RESOLVE_PROCEED

    def test_okay_lets_do_that(self):
        assert resolve_service_fit_followup("okay let's do that") == RESOLVE_PROCEED

    def test_take_a_few_details(self):
        # Echo-of-bridge phrase — caller parrots what assistant said
        assert resolve_service_fit_followup("take a few details") == RESOLVE_PROCEED

    def test_take_a_few(self):
        assert resolve_service_fit_followup("take a few") == RESOLVE_PROCEED

    def test_go_ahead(self):
        assert resolve_service_fit_followup("go ahead") == RESOLVE_PROCEED

    def test_sounds_good(self):
        assert resolve_service_fit_followup("sounds good") == RESOLVE_PROCEED

    def test_lets_do_that(self):
        assert resolve_service_fit_followup("let's do that then") == RESOLVE_PROCEED

    def test_book_that_in(self):
        assert resolve_service_fit_followup("book that in") == RESOLVE_PROCEED

    def test_arrange_that(self):
        assert resolve_service_fit_followup("arrange that") == RESOLVE_PROCEED

    def test_please_do(self):
        assert resolve_service_fit_followup("please do") == RESOLVE_PROCEED

    def test_id_like_to(self):
        assert resolve_service_fit_followup("I'd like to") == RESOLVE_PROCEED

    def test_sure(self):
        assert resolve_service_fit_followup("sure") == RESOLVE_PROCEED

    def test_short_book(self):
        assert resolve_service_fit_followup("book") == RESOLVE_PROCEED

    def test_get_booked(self):
        assert resolve_service_fit_followup("get booked") == RESOLVE_PROCEED


class TestInfoFollowup:
    def test_what_would_happen_first(self):
        assert resolve_service_fit_followup("what would happen first?") == RESOLVE_INFO

    def test_what_exactly_would_you_do(self):
        assert resolve_service_fit_followup("what exactly would you do?") == RESOLVE_INFO

    def test_what_do_you_do_for_ankle(self):
        assert resolve_service_fit_followup("what do you do for an ankle injury?") == RESOLVE_INFO

    def test_how_does_it_work(self):
        assert resolve_service_fit_followup("how does it work?") == RESOLVE_INFO

    def test_would_it_be_assessment(self):
        assert resolve_service_fit_followup("would it be an assessment first?") == RESOLVE_INFO

    def test_first_step(self):
        assert resolve_service_fit_followup("what's the first step?") == RESOLVE_INFO

    def test_what_happens_next(self):
        assert resolve_service_fit_followup("what happens next?") == RESOLVE_INFO

    def test_yes_but_what_would_happen(self):
        # Long utterance with yes-token AND info phrase → INFO wins
        assert resolve_service_fit_followup(
            "yes but what would happen first exactly?"
        ) == RESOLVE_INFO

    def test_what_is_involved(self):
        assert resolve_service_fit_followup("what is involved?") == RESOLVE_INFO

    def test_tell_me_more(self):
        assert resolve_service_fit_followup("tell me more about it") == RESOLVE_INFO

    def test_do_you_treat_swelling(self):
        assert resolve_service_fit_followup("do you treat swelling?") == RESOLVE_INFO

    def test_how_long_does_it_take(self):
        assert resolve_service_fit_followup("how long does it take?") == RESOLVE_INFO


class TestDeclineIntent:
    def test_no_thanks(self):
        assert resolve_service_fit_followup("no thanks") == RESOLVE_DECLINE

    def test_not_right_now(self):
        assert resolve_service_fit_followup("not right now") == RESOLVE_DECLINE

    def test_maybe_later(self):
        assert resolve_service_fit_followup("maybe later") == RESOLVE_DECLINE

    def test_just_wanted_to_know(self):
        assert resolve_service_fit_followup("just wanted to know") == RESOLVE_DECLINE

    def test_just_checking(self):
        assert resolve_service_fit_followup("just checking") == RESOLVE_DECLINE

    def test_never_mind(self):
        assert resolve_service_fit_followup("never mind") == RESOLVE_DECLINE

    def test_not_today(self):
        assert resolve_service_fit_followup("not today") == RESOLVE_DECLINE


class TestUnknownFallthrough:
    def test_unrelated_question(self):
        # Should not match any category
        result = resolve_service_fit_followup("do you have parking?")
        # parking is not a service-fit info phrase — falls through
        assert result == RESOLVE_UNKNOWN

    def test_very_short_noise(self):
        result = resolve_service_fit_followup("hmm")
        assert result == RESOLVE_UNKNOWN

    def test_age_response_falls_through(self):
        # "he's 13" is not PROCEED/INFO/DECLINE — handled by child_age gate, not here
        result = resolve_service_fit_followup("he's 13")
        assert result == RESOLVE_UNKNOWN

    def test_bare_age(self):
        result = resolve_service_fit_followup("8")
        assert result == RESOLVE_UNKNOWN


# ── extract_age ────────────────────────────────────────────────────────────────

class TestExtractAge:
    def test_hes_13(self):
        assert extract_age("he's 13") == 13

    def test_hes_8(self):
        assert extract_age("he's 8") == 8

    def test_he_is_15(self):
        assert extract_age("he is 15") == 15

    def test_shes_10(self):
        assert extract_age("she's 10") == 10

    def test_15_years_old(self):
        assert extract_age("15 years old") == 15

    def test_8_years_old(self):
        assert extract_age("8 years old") == 8

    def test_just_turned_13(self):
        assert extract_age("he just turned 13") == 13

    def test_bare_number_short(self):
        assert extract_age("8") == 8

    def test_bare_number_short_2(self):
        assert extract_age("13") == 13

    def test_my_son_is_11(self):
        assert extract_age("my son is 11") == 11

    def test_my_daughter_is_9(self):
        assert extract_age("my daughter is 9") == 9

    def test_no_age_returns_none(self):
        assert extract_age("I don't know") is None

    def test_empty_returns_none(self):
        assert extract_age("") is None

    def test_out_of_range_ignored(self):
        # 0 and 200 are not valid ages
        assert extract_age("0 years old") is None
        assert extract_age("200 years old") is None


# ── contract kind constant ────────────────────────────────────────────────────

def test_kind_constant_value():
    assert KIND_SERVICE_FIT_PENDING == "service_fit_pending"
