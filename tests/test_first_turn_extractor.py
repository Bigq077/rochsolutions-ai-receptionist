"""
Unit tests for the deterministic first-turn signal extractor.
Pure extraction only — no routing, no TTS, no LLM.
"""
from __future__ import annotations

import pytest

from app.media_streams.first_turn_extractor import (
    extract_first_turn_signals,
    apply_first_turn_signals,
)


# ---------------------------------------------------------------------------
# 1. Booking signal
# ---------------------------------------------------------------------------

class TestBookingSignal:
    def test_plain_booking(self):
        s = extract_first_turn_signals("I'd like to book an appointment please")
        assert s["first_turn_booking_signal"] is True

    def test_book_word_boundary(self):
        # "Facebook" must not trigger booking
        s = extract_first_turn_signals("I found you on Facebook")
        assert s["first_turn_booking_signal"] is False

    def test_schedule_word(self):
        s = extract_first_turn_signals("I want to schedule a session")
        assert s["first_turn_booking_signal"] is True

    def test_come_in_multi(self):
        s = extract_first_turn_signals("I'd like to come in and see someone")
        assert s["first_turn_booking_signal"] is True

    def test_no_booking_signal_on_faq(self):
        s = extract_first_turn_signals("What are your opening hours?")
        assert s["first_turn_booking_signal"] is False


# ---------------------------------------------------------------------------
# 2. FAQ / info signal
# ---------------------------------------------------------------------------

class TestFAQSignal:
    def test_do_you_treat(self):
        s = extract_first_turn_signals("Do you treat sports injuries?")
        assert s["first_turn_faq_signal"] is True

    def test_just_wondering(self):
        s = extract_first_turn_signals("I was just wondering if you do physio")
        assert s["first_turn_faq_signal"] is True

    def test_want_to_know(self):
        s = extract_first_turn_signals("I wanted to know whether you see ankle injuries")
        assert s["first_turn_faq_signal"] is True

    def test_no_faq_on_plain_booking(self):
        s = extract_first_turn_signals("I'd like to book for Friday morning please")
        assert s["first_turn_faq_signal"] is False


# ---------------------------------------------------------------------------
# 3. Service-fit signal
# ---------------------------------------------------------------------------

class TestServiceFitSignal:
    def test_could_you_treat_child(self):
        s = extract_first_turn_signals(
            "could you treat my little boy he hurt his ankle"
        )
        assert s["first_turn_service_fit"] is True

    def test_is_this_something_you_treat(self):
        s = extract_first_turn_signals("my son hurt his ankle is this something you treat")
        assert s["first_turn_service_fit"] is True

    def test_no_service_fit_on_plain_booking(self):
        s = extract_first_turn_signals("I'd like to book an appointment")
        assert s["first_turn_service_fit"] is False


# ---------------------------------------------------------------------------
# 4. Patient relationship + patient_is_caller
# ---------------------------------------------------------------------------

class TestPatientRelationship:
    def test_my_son(self):
        s = extract_first_turn_signals("my son hurt his ankle playing football")
        assert s["first_turn_patient_relationship"] == "son"
        assert s["first_turn_patient_is_caller"] is False

    def test_my_daughter(self):
        s = extract_first_turn_signals("my daughter needs physio for her knee")
        assert s["first_turn_patient_relationship"] == "daughter"

    def test_my_wife(self):
        s = extract_first_turn_signals("my wife has a bad shoulder")
        assert s["first_turn_patient_relationship"] == "wife"
        assert s["first_turn_patient_is_caller"] is False

    def test_caller_is_patient(self):
        s = extract_first_turn_signals("I hurt my ankle playing football")
        assert s["first_turn_patient_is_caller"] is True
        assert "first_turn_patient_relationship" not in s

    def test_my_mother(self):
        s = extract_first_turn_signals("it's for my mother she has hip pain")
        assert s["first_turn_patient_relationship"] == "mother"

    def test_my_partner(self):
        s = extract_first_turn_signals("my partner twisted their ankle")
        assert s["first_turn_patient_relationship"] == "partner"


# ---------------------------------------------------------------------------
# 5. Child-related flag
# ---------------------------------------------------------------------------

class TestChildRelated:
    def test_son_is_child_related(self):
        s = extract_first_turn_signals("my son hurt his ankle")
        assert s["first_turn_child_related"] is True

    def test_daughter_is_child_related(self):
        s = extract_first_turn_signals("my daughter's knee is sore")
        assert s["first_turn_child_related"] is True

    def test_teenager_is_child_related(self):
        s = extract_first_turn_signals("my teenager hurt their knee")
        assert s["first_turn_child_related"] is True

    def test_little_boy_direct(self):
        s = extract_first_turn_signals("I have a little boy with a sprained ankle")
        assert s["first_turn_child_related"] is True

    def test_paediatric_direct(self):
        s = extract_first_turn_signals("do you offer paediatric physiotherapy")
        assert s["first_turn_child_related"] is True

    def test_wife_not_child_related(self):
        s = extract_first_turn_signals("my wife has back pain")
        assert s["first_turn_child_related"] is False

    def test_caller_self_not_child_related(self):
        s = extract_first_turn_signals("I hurt my ankle at the gym")
        assert s["first_turn_child_related"] is False


# ---------------------------------------------------------------------------
# 6. Explicit age extraction
# ---------------------------------------------------------------------------

class TestAgeExtraction:
    def test_years_old(self):
        s = extract_first_turn_signals("my son is 10 years old and hurt his ankle")
        assert s.get("first_turn_age") == 10

    def test_hes_age(self):
        s = extract_first_turn_signals("he's 8 and still limping")
        assert s.get("first_turn_age") == 8

    def test_aged(self):
        s = extract_first_turn_signals("my daughter aged 15 hurt her shoulder")
        assert s.get("first_turn_age") == 15

    def test_no_age_when_absent(self):
        s = extract_first_turn_signals("my son hurt his ankle playing football")
        assert "first_turn_age" not in s

    def test_age_rejected_if_out_of_range(self):
        # 0 or >= 100 should not be stored
        s = extract_first_turn_signals("she is 0 years old")
        assert "first_turn_age" not in s


# ---------------------------------------------------------------------------
# 7. Reason / injury phrase
# ---------------------------------------------------------------------------

class TestReasonExtraction:
    def test_ankle_body_part(self):
        s = extract_first_turn_signals("my son hurt his ankle playing football")
        assert s["first_turn_reason_captured"] is True
        assert "ankle" in s["first_turn_reason"]

    def test_shoulder_body_part(self):
        s = extract_first_turn_signals("I've had shoulder pain for three weeks")
        assert s["first_turn_reason_captured"] is True
        assert "shoulder" in s["first_turn_reason"]

    def test_sprained_verb(self):
        s = extract_first_turn_signals("I sprained something last week")
        assert s["first_turn_reason_captured"] is True

    def test_greeting_only_no_reason(self):
        s = extract_first_turn_signals("Hello, good morning")
        assert s["first_turn_reason_captured"] is False
        assert "first_turn_reason" not in s


# ---------------------------------------------------------------------------
# 8. Location clue
# ---------------------------------------------------------------------------

class TestLocationClue:
    def test_alcester_detected(self):
        s = extract_first_turn_signals("I saw you were recommended in alcester")
        assert s.get("first_turn_location_clue") == "alcester"

    def test_redditch_detected(self):
        s = extract_first_turn_signals("I'm near redditch and looking for physio")
        assert s.get("first_turn_location_clue") == "redditch"

    def test_greig_maps_to_alcester(self):
        s = extract_first_turn_signals("I heard about you through the greig road clinic")
        assert s.get("first_turn_location_clue") == "alcester"

    def test_no_location_on_unrelated(self):
        s = extract_first_turn_signals("I hurt my ankle at the gym")
        assert "first_turn_location_clue" not in s


# ---------------------------------------------------------------------------
# 9. Urgency clue
# ---------------------------------------------------------------------------

class TestUrgencyClue:
    def test_as_soon_as_possible(self):
        s = extract_first_turn_signals("I need to be seen as soon as possible")
        assert s.get("first_turn_urgency") == "as soon as possible"

    def test_today(self):
        s = extract_first_turn_signals("I was hoping to come in today if possible")
        assert s.get("first_turn_urgency") == "today"

    def test_no_urgency_on_plain_booking(self):
        s = extract_first_turn_signals("I'd like to book for next week")
        assert "first_turn_urgency" not in s


# ---------------------------------------------------------------------------
# 10. Clinic discovery clue
# ---------------------------------------------------------------------------

class TestClinicDiscovery:
    def test_saw_online(self):
        s = extract_first_turn_signals("I saw you online and thought I'd call")
        assert s["first_turn_clinic_discovery"] is True

    def test_recommended(self):
        s = extract_first_turn_signals("You were recommended to me by a friend")
        assert s["first_turn_clinic_discovery"] is True

    def test_no_discovery_on_plain_booking(self):
        s = extract_first_turn_signals("I'd like to book an appointment")
        assert s["first_turn_clinic_discovery"] is False


# ---------------------------------------------------------------------------
# 11. Mixed utterance — all signals in one sentence
# ---------------------------------------------------------------------------

class TestMixedUtterance:
    def test_full_mixed(self):
        text = (
            "basically my little boy was playing football and he hurt his ankle "
            "while running and i saw online you were recommended in alcester "
            "i just want to know could you treat my little boy"
        )
        s = extract_first_turn_signals(text)
        assert s["first_turn_child_related"] is True
        assert s["first_turn_service_fit"] is True
        assert s["first_turn_reason_captured"] is True
        assert "ankle" in s["first_turn_reason"]
        assert s.get("first_turn_location_clue") == "alcester"
        assert s["first_turn_clinic_discovery"] is True
        assert s["first_turn_patient_is_caller"] is False

    def test_booking_plus_child(self):
        text = "I want to book my son in for his ankle injury as soon as possible"
        s = extract_first_turn_signals(text)
        assert s["first_turn_booking_signal"] is True
        assert s["first_turn_child_related"] is True
        assert s["first_turn_reason_captured"] is True
        assert s.get("first_turn_urgency") == "as soon as possible"


# ---------------------------------------------------------------------------
# 12. Greeting-only — must not over-extract
# ---------------------------------------------------------------------------

class TestWeakInput:
    def test_greeting_only(self):
        s = extract_first_turn_signals("Hello")
        assert s["first_turn_booking_signal"] is False
        assert s["first_turn_faq_signal"] is False
        assert s["first_turn_service_fit"] is False
        assert s["first_turn_child_related"] is False
        assert s["first_turn_patient_is_caller"] is True
        assert s["first_turn_reason_captured"] is False

    def test_noise_only(self):
        s = extract_first_turn_signals("um yeah hi there")
        assert s["first_turn_booking_signal"] is False
        assert s["first_turn_reason_captured"] is False


# ---------------------------------------------------------------------------
# 13. apply_first_turn_signals — conservative write rules
# ---------------------------------------------------------------------------

class TestApplySignals:
    def test_writes_to_session(self):
        s = extract_first_turn_signals("my son hurt his ankle")
        session: dict = {}
        apply_first_turn_signals(s, session)
        assert session.get("_first_turn_extracted") is True
        assert session.get("first_turn_child_related") is True

    def test_does_not_overwrite_existing_reason(self):
        s = extract_first_turn_signals("my son hurt his ankle")
        session: dict = {"reason": "pre-existing reason"}
        apply_first_turn_signals(s, session)
        assert session["reason"] == "pre-existing reason"

    def test_sets_reason_when_empty(self):
        s = extract_first_turn_signals("I hurt my ankle playing tennis")
        session: dict = {}
        apply_first_turn_signals(s, session)
        assert "ankle" in session.get("reason", "")

    def test_does_not_overwrite_existing_session_fields(self):
        s = extract_first_turn_signals("my son hurt his ankle")
        session: dict = {"first_turn_child_related": False}
        apply_first_turn_signals(s, session)
        # existing value must not be overwritten
        assert session["first_turn_child_related"] is False

    def test_sets_first_turn_extracted_flag(self):
        s = extract_first_turn_signals("hi I want to book")
        session: dict = {}
        apply_first_turn_signals(s, session)
        assert session["_first_turn_extracted"] is True
