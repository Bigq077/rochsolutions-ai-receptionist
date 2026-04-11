"""
tests/test_name_collector.py
============================
Unit tests for the unified NameCollector engine.

Each test builds a fresh session dict and exercises NameCollector.handle()
directly — no FastAPI, no Redis, no LLM.  Tests are grouped by scenario type.
"""
from __future__ import annotations

import pytest
from app.media_streams.name_collector import (
    NameCollector,
    NC_FN_NORMAL, NC_FN_SPELLING, NC_SN_NORMAL, NC_SN_SPELLING, NC_SN_CONFIRM,
    _parse_spelled_letters,
    _extract_leading_token,
    _is_spelling_offer,
    _is_repair_request,
    _META_LANGUAGE,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def sess() -> dict:
    """Return a fresh, empty session dict."""
    return {}


def nc(session=None) -> NameCollector:
    return NameCollector(session if session is not None else sess())


# ── 1. Clean full name in a single utterance ─────────────────────────────────

class TestCleanFullName:
    def test_two_word_name(self):
        action, payload = nc().handle("john smith", "John Smith")
        assert action == "accept"
        assert payload == "John Smith"

    def test_two_word_name_with_prefix(self):
        s = sess()
        action, payload = NameCollector(s).handle("my name is sarah jones", "My name is Sarah Jones")
        assert action == "accept"
        assert payload == "Sarah Jones"

    def test_three_word_name_rejects(self):
        # Three+ tokens: NameCollector should fail or not accept a three-part name
        # (fn_normal sees >2 tokens → _fn_fail)
        action, _ = nc().handle("john michael smith", "John Michael Smith")
        assert action in ("ask", "repair")

    def test_full_name_stored_in_session(self):
        s = sess()
        action, payload = NameCollector(s).handle("emma wilson", "Emma Wilson")
        assert action == "accept"
        assert s.get("full_name") == "Emma Wilson"
        assert s.get("collected", {}).get("full_name") == "Emma Wilson"

    def test_name_fragment_cleared_on_accept(self):
        s = sess()
        s["name_fragment"] = "Emma"  # simulate a stale fragment
        NameCollector(s).handle("emma wilson", "Emma Wilson")
        assert "name_fragment" not in s


# ── 2. First name only → ask surname ─────────────────────────────────────────

class TestFirstNameOnly:
    def test_single_token_asks_surname(self):
        action, payload = nc().handle("sarah", "Sarah")
        assert action == "ask"
        assert "surname" in payload.lower()

    def test_first_name_stored(self):
        s = sess()
        NameCollector(s).handle("james", "James")
        assert s["_nc"]["first_name"] == "James"
        assert s["name_fragment"] == "James"
        assert s["_nc"]["substate"] == "sn_normal"

    def test_function_word_alone_rejected(self):
        for word in ("yes", "ok", "no", "sure", "please"):
            action, _ = nc().handle(word, word)
            assert action in ("ask", "repair"), f"'{word}' should be rejected"

    def test_domain_word_alone_rejected(self):
        for word in ("booking", "physiotherapy", "clinic", "appointment"):
            action, _ = nc().handle(word, word)
            assert action in ("ask", "repair"), f"'{word}' should be rejected"


# ── 3. First name → surname → accept ─────────────────────────────────────────

class TestTwoTurnCollection:
    def test_normal_two_turn(self):
        s = sess()
        col = NameCollector(s)
        a1, _ = col.handle("matt", "Matt")
        assert a1 == "ask"
        a2, payload = NameCollector(s).handle("slater", "Slater")
        assert a2 == "accept"
        assert payload == "Matt Slater"

    def test_full_name_in_second_turn_accepted(self):
        """Caller says 'Matt Slater' as the surname response."""
        s = sess()
        NameCollector(s).handle("matt", "Matt")
        # Caller repeats full name on second turn
        action, payload = NameCollector(s).handle("matt slater", "Matt Slater")
        assert action == "accept"

    def test_wrapper_prefix_on_surname_turn(self):
        s = sess()
        NameCollector(s).handle("david", "David")
        action, payload = NameCollector(s).handle("my surname is jones", "My surname is Jones")
        # sn_normal sees 1 valid token after any prefix stripping from _strip_prefixes
        assert action == "accept"
        assert "Jones" in payload


# ── 4. Noise / meta-language rejection ───────────────────────────────────────

class TestNoiseMeta:
    def test_you_repeat_rejected(self):
        action, _ = nc().handle("you repeat", "You repeat")
        assert action in ("ask", "repair")

    def test_you_are_repeating_rejected(self):
        action, _ = nc().handle("you are repeating", "You are repeating")
        assert action in ("ask", "repair")

    def test_do_you_need_help_rejected(self):
        action, _ = nc().handle("do you need help spelling that", "Do you need help spelling that")
        assert action in ("ask", "repair")

    def test_need_help_spelling_rejected(self):
        action, _ = nc().handle("need help spelling", "Need help spelling")
        assert action in ("ask", "repair")

    def test_can_you_say_rejected(self):
        action, _ = nc().handle("can you say that again", "Can you say that again")
        assert action in ("ask", "repair")


# ── 5. Leading-token salvage from noise utterances ───────────────────────────

class TestLeadingTokenSalvage:
    def test_name_before_spelling_offer(self):
        """'Slater do you need help spelling that' → salvage 'Slater'."""
        s = sess()
        s["_nc"] = {
            "substate": "sn_normal",
            "first_name": "Matt",
            "surname_candidate": None,
            "fn_retries": 0,
            "sn_retries": 0,
        }
        s["name_fragment"] = "Matt"
        action, payload = NameCollector(s).handle(
            "slater do you need help spelling that",
            "Slater do you need help spelling that",
        )
        # Should enter sn_confirm with "Slater"
        assert action == "ask"
        assert "S" in payload and "L" in payload  # spaced readback

    def test_first_name_before_meta(self):
        """'Matt do you need help' → salvage 'Matt' in fn_normal."""
        s = sess()
        action, payload = NameCollector(s).handle(
            "matt do you need help", "Matt do you need help"
        )
        assert action == "ask"
        assert s["_nc"]["first_name"] == "Matt"


# ── 6. Short surname → sn_confirm ────────────────────────────────────────────

class TestShortSurname:
    def _at_sn_normal(self, first="Matt"):
        s = sess()
        s["_nc"] = {
            "substate": "sn_normal",
            "first_name": first,
            "surname_candidate": None,
            "fn_retries": 0,
            "sn_retries": 0,
        }
        s["name_fragment"] = first
        return s

    def test_three_char_surname_enters_confirm(self):
        s = self._at_sn_normal()
        action, payload = NameCollector(s).handle("hew", "Hew")
        assert action == "ask"
        assert s["_nc"]["substate"] == NC_SN_CONFIRM
        assert s["_nc"]["surname_candidate"] == "Hew"

    def test_two_char_surname_enters_confirm(self):
        s = self._at_sn_normal()
        action, _ = NameCollector(s).handle("li", "Li")
        assert s["_nc"]["substate"] == NC_SN_CONFIRM

    def test_normal_surname_accepted_directly(self):
        s = self._at_sn_normal()
        action, payload = NameCollector(s).handle("slater", "Slater")
        assert action == "accept"
        assert payload == "Matt Slater"


# ── 7. sn_confirm substate ───────────────────────────────────────────────────

class TestSnConfirm:
    def _at_sn_confirm(self, candidate="Hew", first="Matt"):
        s = sess()
        s["_nc"] = {
            "substate": NC_SN_CONFIRM,
            "first_name": first,
            "surname_candidate": candidate,
            "fn_retries": 0,
            "sn_retries": 0,
        }
        s["name_fragment"] = first
        s["spelling_confirm_surname"] = candidate
        return s

    def test_yes_accepts_candidate(self):
        s = self._at_sn_confirm("Hew", "Matt")
        action, payload = NameCollector(s).handle("yes", "Yes")
        assert action == "accept"
        assert "Matt" in payload and "Hew" in payload

    def test_correct_accepts_candidate(self):
        s = self._at_sn_confirm("Slater", "Matt")
        action, payload = NameCollector(s).handle("correct", "Correct")
        assert action == "accept"

    def test_no_enters_spelling(self):
        s = self._at_sn_confirm("Hew", "Matt")
        action, payload = NameCollector(s).handle("no", "No")
        assert action == "ask"
        assert s["_nc"]["substate"] == NC_SN_SPELLING

    def test_spelled_correction_updates_candidate(self):
        s = self._at_sn_confirm("Hew", "Matt")
        action, payload = NameCollector(s).handle("h e w i t s o n", "H E W I T S O N")
        # Spelled letters parsed → new candidate "Hewitson"
        assert s["_nc"]["substate"] == NC_SN_CONFIRM
        assert s["_nc"]["surname_candidate"] == "Hewitson"

    def test_clean_word_in_confirm_accepted(self):
        s = self._at_sn_confirm("Hew", "Matt")
        action, payload = NameCollector(s).handle("hewitson", "Hewitson")
        assert action == "accept"
        assert "Hewitson" in payload


# ── 8. Spelling mode ─────────────────────────────────────────────────────────

class TestSpellingMode:
    def test_spelling_offer_switches_mode(self):
        s = sess()
        action, payload = NameCollector(s).handle("shall i spell it", "Shall I spell it")
        assert action == "ask"
        assert s["_nc"]["substate"] == NC_FN_SPELLING

    def test_spelled_first_name(self):
        s = sess()
        s["_nc"] = {
            "substate": NC_FN_SPELLING,
            "first_name": None,
            "surname_candidate": None,
            "fn_retries": 0,
            "sn_retries": 0,
        }
        action, payload = NameCollector(s).handle("m a t t", "M A T T")
        assert action == "ask"
        assert s["_nc"]["first_name"] == "Matt"

    def test_nato_phonetics_first_name(self):
        s = sess()
        s["_nc"] = {
            "substate": NC_FN_SPELLING,
            "first_name": None,
            "surname_candidate": None,
            "fn_retries": 0,
            "sn_retries": 0,
        }
        action, _ = NameCollector(s).handle(
            "sierra alpha mike", "Sierra Alpha Mike"
        )
        assert action == "ask"
        assert s["_nc"]["first_name"] == "Sam"

    def test_spelled_surname_enters_confirm(self):
        s = sess()
        s["_nc"] = {
            "substate": NC_SN_SPELLING,
            "first_name": "Matt",
            "surname_candidate": None,
            "fn_retries": 0,
            "sn_retries": 0,
        }
        s["name_fragment"] = "Matt"
        action, payload = NameCollector(s).handle("s l a t e r", "S L A T E R")
        assert action == "ask"
        assert s["_nc"]["substate"] == NC_SN_CONFIRM
        assert s["_nc"]["surname_candidate"] == "Slater"

    def test_normal_word_accepted_in_spelling_mode(self):
        """Caller changed mind — says whole name instead of spelling."""
        s = sess()
        s["_nc"] = {
            "substate": NC_FN_SPELLING,
            "first_name": None,
            "surname_candidate": None,
            "fn_retries": 0,
            "sn_retries": 0,
        }
        action, payload = NameCollector(s).handle("sarah", "Sarah")
        assert action == "ask"
        assert s["_nc"]["first_name"] == "Sarah"


# ── 9. Retry escalation ───────────────────────────────────────────────────────

class TestRetryEscalation:
    def test_two_fn_failures_escalate_to_spelling(self):
        s = sess()
        col = NameCollector(s)
        # First failure
        col.handle("uh um", "Uh um")
        # Manually set retries to simulate second failure
        s["_nc"]["fn_retries"] = 1
        NameCollector(s).handle("uh um", "Uh um")
        assert s["_nc"]["substate"] == NC_FN_SPELLING

    def test_two_sn_failures_escalate_to_sn_spelling(self):
        s = sess()
        s["_nc"] = {
            "substate": NC_SN_NORMAL,
            "first_name": "Matt",
            "surname_candidate": None,
            "fn_retries": 0,
            "sn_retries": 1,
        }
        s["name_fragment"] = "Matt"
        NameCollector(s).handle("uh um er", "Uh um er")
        assert s["_nc"]["substate"] == NC_SN_SPELLING


# ── 10. Repair request replay ─────────────────────────────────────────────────

class TestRepairRequest:
    def test_repair_replays_current_question(self):
        s = sess()
        action, payload = NameCollector(s).handle("pardon", "Pardon")
        assert action == "repair"
        assert "first name" in payload.lower()

    def test_repair_in_sn_normal_replays_surname_question(self):
        s = sess()
        s["_nc"] = {
            "substate": NC_SN_NORMAL,
            "first_name": "Matt",
            "surname_candidate": None,
            "fn_retries": 0,
            "sn_retries": 0,
        }
        action, payload = NameCollector(s).handle("sorry what was that", "Sorry what was that")
        assert action == "repair"
        assert "surname" in payload.lower()

    def test_spelling_offer_not_caught_as_repair(self):
        """'Shall I spell it' is a spelling offer, not a repair request."""
        s = sess()
        action, _ = NameCollector(s).handle("shall i spell it", "Shall I spell it")
        assert s["_nc"]["substate"] == NC_FN_SPELLING  # entered spelling, not repaired


# ── 11. Name negation ─────────────────────────────────────────────────────────

class TestNameNegation:
    def test_im_not_sarah(self):
        s = sess()
        action, payload = NameCollector(s).handle("i'm not sarah it's emma", "I'm not Sarah it's Emma")
        assert action == "ask"
        # Should store Emma
        assert s["_nc"]["first_name"] == "Emma"

    def test_negation_without_correction(self):
        s = sess()
        action, payload = NameCollector(s).handle("i'm not called that", "I'm not called that")
        assert action == "ask"
        assert "first name" in payload.lower()


# ── 12. reset() and reset_to_surname() ───────────────────────────────────────

class TestReset:
    def test_full_reset_clears_everything(self):
        s = sess()
        s["_nc"] = {
            "substate": NC_SN_CONFIRM,
            "first_name": "Matt",
            "surname_candidate": "Slater",
            "fn_retries": 2,
            "sn_retries": 1,
        }
        s["name_fragment"] = "Matt"
        s["full_name"] = "Matt Slater"
        s["spelling_confirm_surname"] = "Slater"
        NameCollector(s).reset()
        nc_state = s["_nc"]
        assert nc_state["substate"] == NC_FN_NORMAL
        assert nc_state["first_name"] is None
        assert nc_state["surname_candidate"] is None
        assert "name_fragment" not in s
        assert "full_name" not in s
        assert "spelling_confirm_surname" not in s

    def test_reset_to_surname_keeps_first_name(self):
        s = sess()
        s["_nc"] = {
            "substate": NC_SN_CONFIRM,
            "first_name": "Matt",
            "surname_candidate": "Hew",
            "fn_retries": 0,
            "sn_retries": 2,
        }
        s["name_fragment"] = "Matt"
        s["spelling_confirm_surname"] = "Hew"
        NameCollector(s).reset_to_surname()
        nc_state = s["_nc"]
        assert nc_state["substate"] == NC_SN_NORMAL
        assert nc_state["first_name"] == "Matt"
        assert nc_state["sn_retries"] == 0
        assert "spelling_confirm_surname" not in s
        assert s.get("name_fragment") == "Matt"


# ── 13. Helper: _parse_spelled_letters ───────────────────────────────────────

class TestParseSpelledLetters:
    def test_space_separated(self):
        assert _parse_spelled_letters("S L A T E R") == "Slater"

    def test_hyphen_separated(self):
        assert _parse_spelled_letters("S-L-A-T-E-R") == "Slater"

    def test_nato_phonetics(self):
        result = _parse_spelled_letters("Sierra Lima Alpha Tango Echo Romeo")
        assert result == "Slater"

    def test_mixed_letter_and_nato(self):
        result = _parse_spelled_letters("S Lima A T E Romeo")
        assert result == "Slater"

    def test_single_letter_returns_none(self):
        assert _parse_spelled_letters("S") is None

    def test_non_letter_word_returns_none(self):
        # "Smith" is not a single letter or NATO word
        assert _parse_spelled_letters("S M I T H hello") is None

    def test_two_letters_accepted(self):
        result = _parse_spelled_letters("L I")
        assert result == "Li"


# ── 14. Helper: _extract_leading_token ───────────────────────────────────────

class TestExtractLeadingToken:
    def test_single_token_before_stop(self):
        result = _extract_leading_token(
            "slater do you need help spelling that",
            ("do you need", "need help", "help spelling"),
        )
        assert result == "Slater"

    def test_two_tokens_before_stop_returns_none(self):
        result = _extract_leading_token(
            "matt slater do you need help",
            ("do you need",),
        )
        assert result is None  # ambiguous

    def test_no_stop_phrase_returns_none(self):
        result = _extract_leading_token("just a normal sentence", ("do you need",))
        assert result is None

    def test_stop_at_position_zero_returns_none(self):
        result = _extract_leading_token("do you need help with that slater", ("do you need",))
        assert result is None


# ── 15. Legacy session compatibility ─────────────────────────────────────────

class TestLegacyCompat:
    def test_name_fragment_set_after_first_name(self):
        s = sess()
        NameCollector(s).handle("peter", "Peter")
        assert s["name_fragment"] == "Peter"

    def test_spelling_confirm_surname_set_on_sn_confirm(self):
        s = sess()
        s["_nc"] = {
            "substate": NC_SN_NORMAL,
            "first_name": "Peter",
            "surname_candidate": None,
            "fn_retries": 0,
            "sn_retries": 0,
        }
        s["name_fragment"] = "Peter"
        NameCollector(s).handle("hew", "Hew")  # short surname → sn_confirm
        assert s["spelling_confirm_surname"] == "Hew"

    def test_collected_dict_updated_on_accept(self):
        s = sess()
        NameCollector(s).handle("anna schmidt", "Anna Schmidt")
        assert s.get("collected", {}).get("full_name") == "Anna Schmidt"
        assert s.get("collected", {}).get("name") == "Anna Schmidt"


# ── 16. Double-barrelled surname ─────────────────────────────────────────────

class TestDoubleBarrelledSurname:
    def test_two_token_surname_combined(self):
        s = sess()
        s["_nc"] = {
            "substate": NC_SN_NORMAL,
            "first_name": "Sarah",
            "surname_candidate": None,
            "fn_retries": 0,
            "sn_retries": 0,
        }
        s["name_fragment"] = "Sarah"
        action, payload = NameCollector(s).handle("smith jones", "Smith Jones")
        assert action == "accept"
        assert "Smith" in payload and "Jones" in payload


# ── 17. Edge cases ────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_string(self):
        action, _ = nc().handle("", "")
        assert action in ("ask", "repair")

    def test_booking_context_wrapper_stripped(self):
        """'booking in john smith' — prefix 'booking in ' stripped."""
        s = sess()
        action, payload = NameCollector(s).handle("booking in john smith", "booking in John Smith")
        assert action == "accept"
        assert "John" in payload and "Smith" in payload

    def test_it_s_prefix_stripped(self):
        s = sess()
        action, payload = NameCollector(s).handle("it's john smith", "It's John Smith")
        assert action == "accept"

    def test_unknown_substate_resets(self):
        s = sess()
        s["_nc"] = {
            "substate": "broken_state",
            "first_name": None,
            "surname_candidate": None,
            "fn_retries": 0,
            "sn_retries": 0,
        }
        action, _ = NameCollector(s).handle("john", "John")
        # Should reset gracefully and ask for first name
        assert action in ("ask", "repair")
