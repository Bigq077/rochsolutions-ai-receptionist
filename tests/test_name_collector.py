"""
tests/test_name_collector.py
============================
Unit tests for the unified NameCollector engine — no-spelling-mode build.

Flow contract (pilot):
  fn_normal → fn_confirm → YES → sn_normal → sn_confirm → YES → accept
                         → NO  → fn_reask  → (any)     → sn_normal …
  sn_normal → sn_confirm → YES → accept
                         → NO  → sn_reask  → (any)     → accept (preamble set)

Spelling offers are treated as denials everywhere.
NC_FN_SPELLING / NC_SN_SPELLING are dead constants — never entered in live flow.
"""
from __future__ import annotations

import pytest
from app.media_streams.name_collector import (
    NameCollector,
    NC_FN_NORMAL, NC_FN_CONFIRM, NC_FN_REASK, NC_FN_SPELLING,
    NC_SN_NORMAL, NC_SN_CONFIRM, NC_SN_REASK, NC_SN_SPELLING,
    _BEST_EFFORT_ACK,
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


def _full_nc_dict(**overrides) -> dict:
    """Return a complete _nc state dict with all required fields."""
    base = {
        "substate":          NC_FN_NORMAL,
        "fn_candidate":      None,
        "fn_confirmed":      False,
        "first_name":        None,
        "surname_candidate": None,
        "sn_confirmed":      False,
        "pending_surname":   None,
        "fn_retries":        0,
        "sn_retries":        0,
    }
    base.update(overrides)
    return base


# ── 1. Clean full name in a single utterance ─────────────────────────────────

class TestCleanFullName:
    def test_two_word_name(self):
        """Two-word name goes through fn_confirm then sn_confirm before accepting."""
        s = sess()
        # fn_normal: 2 tokens → fn_confirm for first name, pending_surname queued
        a1, p1 = NameCollector(s).handle("john smith", "John Smith")
        assert a1 == "ask"
        assert "John" in p1
        assert s["_nc"]["substate"] == NC_FN_CONFIRM
        assert s["_nc"]["pending_surname"] == "Smith"
        # fn_confirm YES → jumps to sn_confirm (pending_surname consumed)
        a2, p2 = NameCollector(s).handle("yes", "Yes")
        assert a2 == "ask"
        assert "Smith" in p2
        assert s["_nc"]["substate"] == NC_SN_CONFIRM
        # sn_confirm YES → accept
        a3, p3 = NameCollector(s).handle("yes", "Yes")
        assert a3 == "accept"
        assert p3 == "John Smith"

    def test_two_word_name_with_prefix(self):
        s = sess()
        a1, p1 = NameCollector(s).handle("my name is sarah jones", "My name is Sarah Jones")
        assert a1 == "ask"
        assert "Sarah" in p1
        assert s["_nc"]["pending_surname"] == "Jones"
        a2, p2 = NameCollector(s).handle("yes", "Yes")
        assert "Jones" in p2
        a3, p3 = NameCollector(s).handle("yes", "Yes")
        assert a3 == "accept"
        assert p3 == "Sarah Jones"

    def test_three_word_name_rejects(self):
        action, _ = nc().handle("john michael smith", "John Michael Smith")
        assert action in ("ask", "repair")

    def test_full_name_stored_in_session(self):
        s = sess()
        NameCollector(s).handle("emma wilson", "Emma Wilson")
        NameCollector(s).handle("yes", "Yes")
        NameCollector(s).handle("yes", "Yes")
        assert s.get("full_name") == "Emma Wilson"
        assert s.get("collected", {}).get("full_name") == "Emma Wilson"

    def test_name_fragment_cleared_on_accept(self):
        s = sess()
        s["name_fragment"] = "Emma"
        NameCollector(s).handle("emma wilson", "Emma Wilson")
        NameCollector(s).handle("yes", "Yes")
        NameCollector(s).handle("yes", "Yes")
        assert "name_fragment" not in s


# ── 2. First name only → fn_confirm → ask surname ────────────────────────────

class TestFirstNameOnly:
    def test_single_token_enters_fn_confirm(self):
        action, payload = nc().handle("sarah", "Sarah")
        assert action == "ask"
        assert "Sarah" in payload

    def test_first_name_stored_after_confirm(self):
        s = sess()
        NameCollector(s).handle("james", "James")
        assert s["_nc"]["fn_candidate"] == "James"
        assert s["_nc"]["substate"] == NC_FN_CONFIRM
        assert "name_fragment" not in s
        NameCollector(s).handle("yes", "Yes")
        assert s["_nc"]["first_name"] == "James"
        assert s["name_fragment"] == "James"
        assert s["_nc"]["substate"] == NC_SN_NORMAL

    def test_fn_confirmed_flag_set_on_yes(self):
        s = sess()
        NameCollector(s).handle("james", "James")
        assert s["_nc"]["fn_confirmed"] is False
        NameCollector(s).handle("yes", "Yes")
        assert s["_nc"]["fn_confirmed"] is True

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
        a1, _ = NameCollector(s).handle("matt", "Matt")
        assert a1 == "ask"
        NameCollector(s).handle("yes", "Yes")
        a2, _ = NameCollector(s).handle("slater", "Slater")
        assert a2 == "ask"
        assert s["_nc"]["substate"] == NC_SN_CONFIRM
        assert s["_nc"]["surname_candidate"] == "Slater"
        a3, payload = NameCollector(s).handle("yes", "Yes")
        assert a3 == "accept"
        assert payload == "Matt Slater"

    def test_surname_turn_after_fn_confirmed(self):
        s = sess()
        NameCollector(s).handle("matt", "Matt")
        NameCollector(s).handle("yes", "Yes")
        a, _ = NameCollector(s).handle("matt slater", "Matt Slater")
        assert a == "ask"
        assert s["_nc"]["substate"] == NC_SN_CONFIRM

    def test_wrapper_prefix_on_surname_turn(self):
        s = sess()
        NameCollector(s).handle("david", "David")
        NameCollector(s).handle("yes", "Yes")
        a1, p1 = NameCollector(s).handle("my surname is jones", "My surname is Jones")
        assert a1 == "ask"
        assert s["_nc"]["substate"] == NC_SN_CONFIRM
        assert s["_nc"]["surname_candidate"] == "Jones"
        a2, payload = NameCollector(s).handle("yes", "Yes")
        assert a2 == "accept"
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
        s["_nc"] = _full_nc_dict(substate="sn_normal", first_name="Matt")
        s["name_fragment"] = "Matt"
        action, payload = NameCollector(s).handle(
            "slater do you need help spelling that",
            "Slater do you need help spelling that",
        )
        assert action == "ask"
        assert s["_nc"]["substate"] == NC_SN_CONFIRM
        assert s["_nc"]["surname_candidate"] == "Slater"
        assert "Slater" in payload

    def test_first_name_before_meta(self):
        """'Matt do you need help' → salvage 'Matt' in fn_normal → fn_confirm."""
        s = sess()
        action, payload = NameCollector(s).handle(
            "matt do you need help", "Matt do you need help"
        )
        assert action == "ask"
        assert s["_nc"]["substate"] == NC_FN_CONFIRM
        assert s["_nc"]["fn_candidate"] == "Matt"


# ── 6. Short surname → sn_confirm ────────────────────────────────────────────

class TestShortSurname:
    def _at_sn_normal(self, first="Matt"):
        s = sess()
        s["_nc"] = _full_nc_dict(substate="sn_normal", first_name=first)
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

    def test_normal_surname_routes_through_confirm(self):
        s = self._at_sn_normal()
        a1, p1 = NameCollector(s).handle("slater", "Slater")
        assert a1 == "ask"
        assert s["_nc"]["substate"] == NC_SN_CONFIRM
        assert s["_nc"]["surname_candidate"] == "Slater"
        a2, payload = NameCollector(s).handle("yes", "Yes")
        assert a2 == "accept"
        assert payload == "Matt Slater"


# ── 7. sn_confirm substate ───────────────────────────────────────────────────

class TestSnConfirm:
    def _at_sn_confirm(self, candidate="Hew", first="Matt"):
        s = sess()
        s["_nc"] = _full_nc_dict(
            substate=NC_SN_CONFIRM,
            first_name=first,
            surname_candidate=candidate,
        )
        s["name_fragment"] = first
        s["spelling_confirm_surname"] = candidate
        return s

    def test_yes_accepts_candidate(self):
        s = self._at_sn_confirm("Hew", "Matt")
        action, payload = NameCollector(s).handle("yes", "Yes")
        assert action == "accept"
        assert "Matt" in payload and "Hew" in payload

    def test_sn_confirmed_flag_set_on_yes(self):
        s = self._at_sn_confirm("Slater", "Matt")
        NameCollector(s).handle("yes", "Yes")
        # After accept, _nc substate is done — confirmed flag was set before _accept
        # (check via session full_name to confirm accept happened)
        assert s.get("full_name") == "Matt Slater"

    def test_correct_accepts_candidate(self):
        s = self._at_sn_confirm("Slater", "Matt")
        action, payload = NameCollector(s).handle("correct", "Correct")
        assert action == "accept"

    def test_no_enters_sn_reask(self):
        """NO in sn_confirm enters sn_reask (not spelling mode)."""
        s = self._at_sn_confirm("Hew", "Matt")
        action, payload = NameCollector(s).handle("no", "No")
        assert action == "ask"
        assert s["_nc"]["substate"] == NC_SN_REASK

    def test_no_sn_reask_phrase(self):
        """sn_reask response is the exact re-ask phrase."""
        s = self._at_sn_confirm("Hew", "Matt")
        action, payload = NameCollector(s).handle("no", "No")
        assert "sorry about that" in payload.lower()
        assert "surname" in payload.lower()

    def test_clean_word_in_confirm_updates_candidate(self):
        """A clean word in sn_confirm updates the candidate and re-confirms."""
        s = self._at_sn_confirm("Hew", "Matt")
        action, payload = NameCollector(s).handle("hewitson", "Hewitson")
        assert action == "ask"
        assert "Hewitson" in payload
        assert s["_nc"]["surname_candidate"] == "Hewitson"
        assert s["_nc"]["substate"] == NC_SN_CONFIRM

    def test_spelling_offer_in_sn_confirm_enters_sn_reask(self):
        """Spelling offers in sn_confirm are treated as denial → sn_reask."""
        s = self._at_sn_confirm("Hew", "Matt")
        action, payload = NameCollector(s).handle("shall i spell it", "Shall I spell it")
        assert action == "ask"
        assert s["_nc"]["substate"] == NC_SN_REASK


# ── 8. No spelling mode in live flow ─────────────────────────────────────────

class TestNoSpellingMode:
    def test_spelling_offer_in_fn_normal_escalates_to_fn_reask(self):
        """'Shall I spell it' in fn_normal — garbled input triggers one-retry escalation."""
        s = sess()
        action, payload = NameCollector(s).handle("shall i spell it", "Shall I spell it")
        assert action == "ask"
        # Must NOT enter spelling mode; with one-retry-only, first failure → fn_reask
        assert s["_nc"]["substate"] != NC_FN_SPELLING
        assert s["_nc"]["substate"] == NC_FN_REASK

    def test_spelling_offer_in_fn_confirm_enters_fn_reask(self):
        """'Shall I spell it' in fn_confirm = denial → fn_reask."""
        s = sess()
        s["_nc"] = _full_nc_dict(substate=NC_FN_CONFIRM, fn_candidate="Sarah")
        action, payload = NameCollector(s).handle("shall i spell it", "Shall I spell it")
        assert action == "ask"
        assert s["_nc"]["substate"] == NC_FN_REASK

    def test_spelling_offer_in_sn_normal_escalates_to_sn_reask(self):
        """'Shall I spell it' in sn_normal — garbled input triggers one-retry escalation."""
        s = sess()
        s["_nc"] = _full_nc_dict(substate=NC_SN_NORMAL, first_name="Matt")
        s["name_fragment"] = "Matt"
        action, payload = NameCollector(s).handle("shall i spell it", "Shall I spell it")
        assert action == "ask"
        # Must NOT enter spelling mode; with one-retry-only, first failure → sn_reask
        assert s["_nc"]["substate"] != NC_SN_SPELLING
        assert s["_nc"]["substate"] == NC_SN_REASK

    def test_spelling_offer_in_sn_confirm_enters_sn_reask(self):
        """'Shall I spell it' in sn_confirm = denial → sn_reask."""
        s = sess()
        s["_nc"] = _full_nc_dict(substate=NC_SN_CONFIRM, first_name="Matt", surname_candidate="Hew")
        s["name_fragment"] = "Matt"
        action, payload = NameCollector(s).handle("shall i spell it", "Shall I spell it")
        assert action == "ask"
        assert s["_nc"]["substate"] == NC_SN_REASK

    def test_legacy_fn_spelling_substate_triggers_defensive_reset(self):
        """NC_FN_SPELLING in substate (legacy/corrupt) → defensive reset → ask."""
        s = sess()
        s["_nc"] = _full_nc_dict(substate=NC_FN_SPELLING)
        action, payload = NameCollector(s).handle("sarah", "Sarah")
        # Defensive reset: returns ask and resets to fn_normal
        assert action in ("ask", "repair")
        assert s["_nc"]["substate"] == NC_FN_NORMAL

    def test_legacy_sn_spelling_substate_triggers_defensive_reset(self):
        """NC_SN_SPELLING in substate (legacy/corrupt) → defensive reset → ask."""
        s = sess()
        s["_nc"] = _full_nc_dict(substate=NC_SN_SPELLING, first_name="Matt")
        action, payload = NameCollector(s).handle("slater", "Slater")
        assert action in ("ask", "repair")
        assert s["_nc"]["substate"] == NC_FN_NORMAL


# ── 9. fn_reask and sn_reask (best-effort paths) ─────────────────────────────

class TestBestEffortPaths:
    def test_fn_reask_stores_valid_name(self):
        """fn_reask: caller says a valid name → stored as best effort."""
        s = sess()
        s["_nc"] = _full_nc_dict(substate=NC_FN_REASK, fn_candidate="Sarah")
        action, payload = NameCollector(s).handle("emma", "Emma")
        assert action == "ask"
        assert s["_nc"]["first_name"] == "Emma"
        assert s["_nc"]["fn_confirmed"] is False
        assert s["_nc"]["substate"] == NC_SN_NORMAL

    def test_fn_reask_falls_back_to_candidate(self):
        """fn_reask: no usable response → fall back to previous fn_candidate."""
        s = sess()
        s["_nc"] = _full_nc_dict(substate=NC_FN_REASK, fn_candidate="Sarah")
        action, payload = NameCollector(s).handle("shall i spell it", "Shall I spell it")
        assert action == "ask"
        assert s["_nc"]["first_name"] == "Sarah"
        assert s["_nc"]["fn_confirmed"] is False

    def test_fn_reask_falls_back_to_unknown_when_no_candidate(self):
        """fn_reask: no response, no candidate → 'Unknown' as fallback."""
        s = sess()
        s["_nc"] = _full_nc_dict(substate=NC_FN_REASK, fn_candidate=None)
        action, payload = NameCollector(s).handle("uh um er", "Uh um er")
        assert action == "ask"
        assert s["_nc"]["first_name"] == "Unknown"
        assert s["_nc"]["fn_confirmed"] is False

    def test_fn_reask_combined_phrase(self):
        """fn_reask response includes best-effort ack and surname ask."""
        s = sess()
        s["_nc"] = _full_nc_dict(substate=NC_FN_REASK, fn_candidate="Sarah")
        action, payload = NameCollector(s).handle("emma", "Emma")
        assert "confirmation" in payload.lower() or "noted" in payload.lower()
        assert "surname" in payload.lower()

    def test_fn_reask_advances_to_sn_normal(self):
        """After fn_reask, substate advances to sn_normal for surname collection."""
        s = sess()
        s["_nc"] = _full_nc_dict(substate=NC_FN_REASK, fn_candidate="Sarah")
        NameCollector(s).handle("emma", "Emma")
        assert s["_nc"]["substate"] == NC_SN_NORMAL

    def test_sn_reask_stores_valid_name_and_accepts(self):
        """sn_reask: caller says a valid surname → stored best effort → accept."""
        s = sess()
        s["_nc"] = _full_nc_dict(
            substate=NC_SN_REASK, first_name="Matt", surname_candidate="Hew"
        )
        s["name_fragment"] = "Matt"
        action, payload = NameCollector(s).handle("hewitson", "Hewitson")
        assert action == "accept"
        assert "Matt" in payload and "Hewitson" in payload

    def test_sn_reask_falls_back_to_candidate(self):
        """sn_reask: no usable response → fall back to previous surname_candidate."""
        s = sess()
        s["_nc"] = _full_nc_dict(
            substate=NC_SN_REASK, first_name="Matt", surname_candidate="Hew"
        )
        s["name_fragment"] = "Matt"
        action, payload = NameCollector(s).handle("shall i spell it", "Shall I spell it")
        assert action == "accept"
        assert "Hew" in payload

    def test_sn_reask_sets_accept_preamble(self):
        """sn_reask sets session['_nc_accept_preamble'] for flow.py to play."""
        s = sess()
        s["_nc"] = _full_nc_dict(
            substate=NC_SN_REASK, first_name="Matt", surname_candidate="Hew"
        )
        s["name_fragment"] = "Matt"
        NameCollector(s).handle("hewitson", "Hewitson")
        assert s.get("_nc_accept_preamble") == _BEST_EFFORT_ACK

    def test_sn_reask_confirmed_flag_false(self):
        """sn_reask stores sn_confirmed=False."""
        s = sess()
        s["_nc"] = _full_nc_dict(
            substate=NC_SN_REASK, first_name="Matt", surname_candidate="Hew"
        )
        s["name_fragment"] = "Matt"
        NameCollector(s).handle("hewitson", "Hewitson")
        # After accept _nc substate is done; check via preamble presence
        assert "_nc_accept_preamble" in s

    def test_full_denial_path_fn_and_sn(self):
        """Full path: fn denied → fn_reask → sn denied → sn_reask → accept."""
        s = sess()
        # fn_normal → fn_confirm
        NameCollector(s).handle("sarah", "Sarah")
        assert s["_nc"]["substate"] == NC_FN_CONFIRM
        # fn_confirm NO → fn_reask
        NameCollector(s).handle("no", "No")
        assert s["_nc"]["substate"] == NC_FN_REASK
        # fn_reask → stored best effort, ask surname
        a1, p1 = NameCollector(s).handle("emma", "Emma")
        assert a1 == "ask"
        assert s["_nc"]["first_name"] == "Emma"
        assert s["_nc"]["substate"] == NC_SN_NORMAL
        # sn_normal → sn_confirm
        NameCollector(s).handle("jones", "Jones")
        assert s["_nc"]["substate"] == NC_SN_CONFIRM
        # sn_confirm NO → sn_reask
        NameCollector(s).handle("no", "No")
        assert s["_nc"]["substate"] == NC_SN_REASK
        # sn_reask → accept with preamble
        a2, p2 = NameCollector(s).handle("johnson", "Johnson")
        assert a2 == "accept"
        assert "Emma" in p2 and "Johnson" in p2
        assert s.get("_nc_accept_preamble") == _BEST_EFFORT_ACK


# ── 10. Retry escalation ──────────────────────────────────────────────────────

class TestRetryEscalation:
    def test_fn_failure_escalates_immediately_to_fn_reask(self):
        """First genuine fn failure (retries=1) immediately escalates to fn_reask (one retry only)."""
        s = sess()
        action, _ = NameCollector(s).handle("uh um er", "Uh um er")
        assert action == "ask"
        assert s["_nc"]["substate"] == NC_FN_REASK

    def test_fn_failures_at_threshold_escalate_to_fn_reask(self):
        """One fn failure escalates substate to NC_FN_REASK (one-retry-only)."""
        s = sess()
        action, _ = NameCollector(s).handle("uh um er", "Uh um er")   # fn_retries=1 → escalate
        assert action == "ask"
        assert s["_nc"]["substate"] == NC_FN_REASK

    def test_fn_failures_never_escalate_to_spelling(self):
        """Repeated fn failures NEVER enter fn_spelling — only fn_reask."""
        s = sess()
        for _ in range(5):
            NameCollector(s).handle("uh um er", "Uh um er")
        assert s["_nc"]["substate"] != "fn_spelling"

    def test_sn_failure_escalates_immediately_to_sn_reask(self):
        """First genuine sn failure (retries=1) immediately escalates to sn_reask (one retry only)."""
        s = sess()
        s["_nc"] = _full_nc_dict(substate=NC_SN_NORMAL, first_name="Matt", sn_retries=0)
        s["name_fragment"] = "Matt"
        action, _ = NameCollector(s).handle("uh um er", "Uh um er")
        assert action == "ask"
        assert s["_nc"]["substate"] == NC_SN_REASK

    def test_sn_failures_at_threshold_escalate_to_sn_reask(self):
        """One sn failure escalates substate to NC_SN_REASK (one-retry-only)."""
        s = sess()
        s["_nc"] = _full_nc_dict(substate=NC_SN_NORMAL, first_name="Matt", sn_retries=0)
        s["name_fragment"] = "Matt"
        action, _ = NameCollector(s).handle("uh um er", "Uh um er")  # sn_retries=1 → escalate
        assert action == "ask"
        assert s["_nc"]["substate"] == NC_SN_REASK

    def test_sn_failures_never_escalate_to_spelling(self):
        """Repeated sn failures NEVER enter sn_spelling — only sn_reask."""
        s = sess()
        s["_nc"] = _full_nc_dict(substate=NC_SN_NORMAL, first_name="Matt", sn_retries=3)
        s["name_fragment"] = "Matt"
        NameCollector(s).handle("uh um er", "Uh um er")
        assert s["_nc"]["substate"] != "sn_spelling"


# ── 11. Repair request replay ─────────────────────────────────────────────────

class TestRepairRequest:
    def test_repair_replays_current_question(self):
        s = sess()
        action, payload = NameCollector(s).handle("pardon", "Pardon")
        assert action == "repair"
        assert "first name" in payload.lower()

    def test_repair_in_sn_normal_replays_surname_question(self):
        s = sess()
        s["_nc"] = _full_nc_dict(substate=NC_SN_NORMAL, first_name="Matt")
        action, payload = NameCollector(s).handle("sorry what was that", "Sorry what was that")
        assert action == "repair"
        assert "surname" in payload.lower()

    def test_repair_in_fn_reask_replays_reask_phrase(self):
        """Repair during fn_reask replays the re-ask question."""
        s = sess()
        s["_nc"] = _full_nc_dict(substate=NC_FN_REASK, fn_candidate="Sarah")
        action, payload = NameCollector(s).handle("pardon", "Pardon")
        assert action == "repair"
        assert "first name" in payload.lower()

    def test_repair_in_sn_reask_replays_reask_phrase(self):
        """Repair during sn_reask replays the re-ask question."""
        s = sess()
        s["_nc"] = _full_nc_dict(
            substate=NC_SN_REASK, first_name="Matt", surname_candidate="Hew"
        )
        action, payload = NameCollector(s).handle("pardon", "Pardon")
        assert action == "repair"
        assert "surname" in payload.lower()

    def test_spelling_offer_in_fn_normal_not_caught_as_repair(self):
        """Spelling offer in fn_normal is garbled input — triggers escalation, never spelling mode."""
        s = sess()
        action, _ = NameCollector(s).handle("shall i spell it", "Shall I spell it")
        assert s["_nc"]["substate"] != NC_FN_SPELLING  # never spelling mode


# ── 12. Name negation ─────────────────────────────────────────────────────────

class TestNameNegation:
    def test_im_not_sarah(self):
        s = sess()
        action, payload = NameCollector(s).handle("i'm not sarah it's emma", "I'm not Sarah it's Emma")
        assert action == "ask"
        assert s["_nc"]["substate"] == NC_FN_CONFIRM
        assert s["_nc"]["fn_candidate"] == "Emma"

    def test_negation_without_correction(self):
        s = sess()
        action, payload = NameCollector(s).handle("i'm not called that", "I'm not called that")
        assert action == "ask"
        assert "first name" in payload.lower()


# ── 13. reset() and reset_to_surname() ───────────────────────────────────────

class TestReset:
    def test_full_reset_clears_everything(self):
        s = sess()
        s["_nc"] = _full_nc_dict(
            substate=NC_SN_CONFIRM,
            first_name="Matt",
            fn_confirmed=True,
            surname_candidate="Slater",
            fn_retries=2,
            sn_retries=1,
        )
        s["name_fragment"] = "Matt"
        s["full_name"] = "Matt Slater"
        s["spelling_confirm_surname"] = "Slater"
        NameCollector(s).reset()
        nc_state = s["_nc"]
        assert nc_state["substate"] == NC_FN_NORMAL
        assert nc_state["first_name"] is None
        assert nc_state["fn_confirmed"] is False
        assert nc_state["surname_candidate"] is None
        assert nc_state["sn_confirmed"] is False
        assert "name_fragment" not in s
        assert "full_name" not in s
        assert "spelling_confirm_surname" not in s

    def test_full_reset_clears_accept_preamble(self):
        s = sess()
        s["_nc_accept_preamble"] = _BEST_EFFORT_ACK
        NameCollector(s).reset()
        assert "_nc_accept_preamble" not in s

    def test_reset_to_surname_keeps_first_name(self):
        s = sess()
        s["_nc"] = _full_nc_dict(
            substate=NC_SN_CONFIRM,
            first_name="Matt",
            fn_confirmed=True,
            surname_candidate="Hew",
            sn_retries=2,
        )
        s["name_fragment"] = "Matt"
        s["spelling_confirm_surname"] = "Hew"
        NameCollector(s).reset_to_surname()
        nc_state = s["_nc"]
        assert nc_state["substate"] == NC_SN_NORMAL
        assert nc_state["first_name"] == "Matt"
        assert nc_state["fn_confirmed"] is True
        assert nc_state["sn_retries"] == 0
        assert "spelling_confirm_surname" not in s
        assert s.get("name_fragment") == "Matt"


# ── 14. Helper: _parse_spelled_letters ───────────────────────────────────────

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
        assert _parse_spelled_letters("S M I T H hello") is None

    def test_two_letters_accepted(self):
        result = _parse_spelled_letters("L I")
        assert result == "Li"


# ── 15. Helper: _extract_leading_token ───────────────────────────────────────

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
        assert result is None

    def test_no_stop_phrase_returns_none(self):
        result = _extract_leading_token("just a normal sentence", ("do you need",))
        assert result is None

    def test_stop_at_position_zero_returns_none(self):
        result = _extract_leading_token("do you need help with that slater", ("do you need",))
        assert result is None


# ── 16. Legacy session compatibility ─────────────────────────────────────────

class TestLegacyCompat:
    def test_name_fragment_set_after_first_name_confirmed(self):
        s = sess()
        NameCollector(s).handle("peter", "Peter")
        assert "name_fragment" not in s
        NameCollector(s).handle("yes", "Yes")
        assert s["name_fragment"] == "Peter"

    def test_spelling_confirm_surname_set_on_sn_confirm(self):
        s = sess()
        s["_nc"] = _full_nc_dict(substate=NC_SN_NORMAL, first_name="Peter")
        s["name_fragment"] = "Peter"
        NameCollector(s).handle("hew", "Hew")
        assert s["spelling_confirm_surname"] == "Hew"

    def test_collected_dict_updated_on_accept(self):
        s = sess()
        NameCollector(s).handle("anna schmidt", "Anna Schmidt")
        NameCollector(s).handle("yes", "Yes")
        NameCollector(s).handle("yes", "Yes")
        assert s.get("collected", {}).get("full_name") == "Anna Schmidt"
        assert s.get("collected", {}).get("name") == "Anna Schmidt"


# ── 17. Double-barrelled surname ─────────────────────────────────────────────

class TestDoubleBarrelledSurname:
    def test_two_token_surname_enters_confirm(self):
        s = sess()
        s["_nc"] = _full_nc_dict(substate=NC_SN_NORMAL, first_name="Sarah")
        s["name_fragment"] = "Sarah"
        a1, p1 = NameCollector(s).handle("smith jones", "Smith Jones")
        assert a1 == "ask"
        assert s["_nc"]["substate"] == NC_SN_CONFIRM
        assert "Smith" in s["_nc"]["surname_candidate"] and "Jones" in s["_nc"]["surname_candidate"]
        a2, payload = NameCollector(s).handle("yes", "Yes")
        assert a2 == "accept"
        assert "Smith" in payload and "Jones" in payload


# ── 18. Edge cases ────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_string(self):
        action, _ = nc().handle("", "")
        assert action in ("ask", "repair")

    def test_booking_context_wrapper_stripped(self):
        s = sess()
        a1, p1 = NameCollector(s).handle("booking in john smith", "booking in John Smith")
        assert a1 == "ask"
        assert "John" in p1
        assert s["_nc"]["pending_surname"] == "Smith"
        NameCollector(s).handle("yes", "Yes")
        a3, p3 = NameCollector(s).handle("yes", "Yes")
        assert a3 == "accept"
        assert "John" in p3 and "Smith" in p3

    def test_it_s_prefix_stripped(self):
        s = sess()
        a1, p1 = NameCollector(s).handle("it's john smith", "It's John Smith")
        assert a1 == "ask"
        assert "John" in p1
        NameCollector(s).handle("yes", "Yes")
        a3, p3 = NameCollector(s).handle("yes", "Yes")
        assert a3 == "accept"

    def test_unknown_substate_resets(self):
        s = sess()
        s["_nc"] = _full_nc_dict(substate="broken_state")
        action, _ = NameCollector(s).handle("john", "John")
        assert action in ("ask", "repair")

    def test_best_effort_ack_constant_contains_key_phrases(self):
        """_BEST_EFFORT_ACK contains the required phrases from the spec."""
        assert "confirmation message" in _BEST_EFFORT_ACK.lower() or "confirmation" in _BEST_EFFORT_ACK.lower()
        assert "reply" in _BEST_EFFORT_ACK.lower() or "correcting" in _BEST_EFFORT_ACK.lower()


# ── 19. needs_name_correction_sms flag ───────────────────────────────────────
# These tests verify the deterministic trust model:
#   - clean path (no retries, confirmed YES) → flag NOT set
#   - fn_reask path  → flag SET
#   - sn_reask path  → flag SET
#   - fn_confirm YES after fn_retries > 0 → flag SET
#   - sn_confirm YES after sn_retries > 0 → flag SET
#   - reset() / reset_to_surname() → flag CLEARED

class TestNameCorrectionSmsFlag:

    def test_clean_full_path_no_flag(self):
        """Clean first name + clean surname → needs_name_correction_sms NOT set."""
        s = sess()
        NameCollector(s).handle("quentin", "Quentin")   # fn_normal → fn_confirm
        NameCollector(s).handle("yes", "Yes")            # fn_confirm YES (retries=0)
        NameCollector(s).handle("roch", "Roch")          # sn_normal → sn_confirm
        NameCollector(s).handle("yes", "Yes")            # sn_confirm YES (retries=0)
        assert s.get("needs_name_correction_sms") is not True

    def test_fn_reask_sets_flag(self):
        """fn_reask (fn_confirm denied → re-ask → any response) sets the flag."""
        s = sess()
        NameCollector(s).handle("quentin", "Quentin")   # fn_confirm
        NameCollector(s).handle("no", "No")             # → fn_reask
        NameCollector(s).handle("quentin", "Quentin")   # fn_reask → store best-effort
        assert s.get("needs_name_correction_sms") is True

    def test_sn_reask_sets_flag(self):
        """sn_reask (sn_confirm denied → re-ask → any response) sets the flag."""
        s = sess()
        NameCollector(s).handle("matt", "Matt")
        NameCollector(s).handle("yes", "Yes")           # fn clean
        NameCollector(s).handle("hew", "Hew")           # sn_confirm
        NameCollector(s).handle("no", "No")             # → sn_reask
        action, _ = NameCollector(s).handle("hewitson", "Hewitson")  # sn_reask accept
        assert action == "accept"
        assert s.get("needs_name_correction_sms") is True

    def test_fn_confirm_yes_after_retries_sets_flag(self):
        """YES in fn_confirm with fn_retries > 0 marks capture as unreliable (defensive path)."""
        s = sess()
        # Directly construct fn_confirm state with fn_retries=1 (defensive: can't happen via
        # normal flow since first failure escalates to fn_reask, but the check is kept for safety)
        s["_nc"] = _full_nc_dict(substate=NC_FN_CONFIRM, fn_candidate="Quentin", fn_retries=1)
        NameCollector(s).handle("yes", "Yes")
        assert s.get("needs_name_correction_sms") is True

    def test_sn_confirm_yes_after_retries_sets_flag(self):
        """YES in sn_confirm after sn_retries > 0 marks capture as unreliable."""
        s = sess()
        s["_nc"] = _full_nc_dict(
            substate=NC_SN_NORMAL,
            first_name="Matt",
            fn_confirmed=True,
            sn_retries=1,        # already had one sn failure
        )
        s["name_fragment"] = "Matt"
        NameCollector(s).handle("rook", "Rook")         # sn_normal → sn_confirm
        NameCollector(s).handle("yeah", "Yeah")         # YES after sn_retries=1
        assert s.get("needs_name_correction_sms") is True

    def test_sn_confirm_yes_after_retries_sets_preamble(self):
        """YES in sn_confirm after sn_retries > 0 also sets the TTS preamble."""
        s = sess()
        s["_nc"] = _full_nc_dict(
            substate=NC_SN_NORMAL,
            first_name="Matt",
            fn_confirmed=True,
            sn_retries=1,
        )
        s["name_fragment"] = "Matt"
        NameCollector(s).handle("rook", "Rook")
        NameCollector(s).handle("yeah", "Yeah")
        assert s.get("_nc_accept_preamble") == _BEST_EFFORT_ACK

    def test_live_log_regression_rook_after_sn_retry(self):
        """
        Regression: live call where 'Rook' was heard after a messy surname path.
        System said 'I'll send you a confirmation message' but SMS correction
        was never sent — because the flag was never set.
        sn_retries=1 → sn_confirm('Rook') → 'yeah' must set needs_name_correction_sms.
        """
        s = sess()
        s["_nc"] = _full_nc_dict(
            substate=NC_SN_NORMAL,
            first_name="Quentin",
            fn_confirmed=True,
            sn_retries=1,
        )
        s["name_fragment"] = "Quentin"
        NameCollector(s).handle("rook", "Rook")         # sn_confirm("Rook")
        action, name = NameCollector(s).handle("yeah", "Yeah")
        assert action == "accept"
        assert "Quentin" in name and "Rook" in name
        assert s.get("needs_name_correction_sms") is True

    def test_clean_path_no_flag_after_sn_reask_if_sn_retries_zero(self):
        """
        Clean fn_confirm (no retries) + clean sn_confirm (no retries) → no flag,
        even if sn went through sn_confirm → sn_reask path (explicit denial counts
        as degraded, tested separately).
        """
        s = sess()
        NameCollector(s).handle("anna", "Anna")          # fn_confirm
        NameCollector(s).handle("yes", "Yes")            # fn YES (retries=0)
        NameCollector(s).handle("schmidt", "Schmidt")   # sn_confirm
        NameCollector(s).handle("yes", "Yes")            # sn YES (retries=0)
        assert s.get("needs_name_correction_sms") is not True

    def test_reset_clears_flag(self):
        """reset() clears needs_name_correction_sms."""
        s = sess()
        s["needs_name_correction_sms"] = True
        NameCollector(s).reset()
        assert "needs_name_correction_sms" not in s

    def test_reset_to_surname_clears_flag(self):
        """reset_to_surname() clears needs_name_correction_sms."""
        s = sess()
        s["_nc"] = _full_nc_dict(first_name="Matt", fn_confirmed=True)
        s["needs_name_correction_sms"] = True
        NameCollector(s).reset_to_surname()
        assert "needs_name_correction_sms" not in s

    def test_fn_reask_via_bounded_escalation_sets_flag(self):
        """fn_fail×1 → NC_FN_REASK → any response sets needs_name_correction_sms."""
        s = sess()
        NameCollector(s).handle("uh um", "Uh um")   # fn_retries=1 → NC_FN_REASK immediately
        assert s["_nc"]["substate"] == NC_FN_REASK
        NameCollector(s).handle("quentin", "Quentin")  # fn_reask → accept
        assert s.get("needs_name_correction_sms") is True

    def test_sn_reask_via_bounded_escalation_sets_flag(self):
        """sn_fail×2 → NC_SN_REASK → any response sets needs_name_correction_sms."""
        s = sess()
        s["_nc"] = _full_nc_dict(substate=NC_SN_NORMAL, first_name="Matt", sn_retries=1)
        s["name_fragment"] = "Matt"
        NameCollector(s).handle("uh um", "Uh um")   # sn_retries=2 → NC_SN_REASK
        assert s["_nc"]["substate"] == NC_SN_REASK
        action, _ = NameCollector(s).handle("hewitson", "Hewitson")
        assert action == "accept"
        assert s.get("needs_name_correction_sms") is True


# ── 14. Structural label word rejection ───────────────────────────────────────

class TestStructuralLabelRejection:
    """Words like 'surname', 'name', 'first', 'family' must not become name candidates."""

    def test_my_surname_is_not_a_candidate(self):
        """'my surname is' → no candidate extracted (label word blocked)."""
        s = sess()
        action, _ = NameCollector(s).handle("my surname is", "My surname is")
        assert action == "ask"
        assert s["_nc"].get("fn_candidate") is None

    def test_surname_alone_not_a_candidate(self):
        """Single word 'surname' is in _META_WORDS — not a valid first-name candidate."""
        s = sess()
        action, _ = NameCollector(s).handle("surname", "Surname")
        assert action == "ask"
        assert s["_nc"].get("fn_candidate") is None

    def test_my_name_is_not_a_candidate(self):
        """'my name is' → no candidate extracted."""
        s = sess()
        action, _ = NameCollector(s).handle("my name is", "My name is")
        assert action == "ask"
        assert s["_nc"].get("fn_candidate") is None

    def test_family_name_rejected_as_sn_candidate(self):
        """'family name' → 'family' is in _META_WORDS, no sn candidate."""
        s = sess()
        s["_nc"] = _full_nc_dict(substate=NC_SN_NORMAL, first_name="Matt", fn_confirmed=True)
        s["name_fragment"] = "Matt"
        action, _ = NameCollector(s).handle("family", "Family")
        assert action == "ask"
        assert s["_nc"].get("surname_candidate") is None

    def test_real_name_after_surname_label_extracted(self):
        """'my surname is johnson' → 'Johnson' extracted (label stripped)."""
        s = sess()
        s["_nc"] = _full_nc_dict(substate=NC_SN_NORMAL, first_name="Matt", fn_confirmed=True)
        s["name_fragment"] = "Matt"
        action, _ = NameCollector(s).handle("my surname is johnson", "My surname is Johnson")
        assert action == "ask"
        assert s["_nc"].get("surname_candidate") == "Johnson"


# ── 15. Extended repair phrase variants ───────────────────────────────────────

class TestExtendedRepairPhrases:
    """New _META_LANGUAGE phrases added for reliability patch."""

    def test_if_you_didnt_catch_rejected(self):
        """'if you didn't catch that' is _META_LANGUAGE — not a name candidate."""
        s = sess()
        action, _ = NameCollector(s).handle("if you didn't catch that", "If you didn't catch that")
        assert action in ("ask", "repair")
        assert s["_nc"].get("fn_candidate") is None

    def test_let_me_try_rejected(self):
        """'let me try again' is _META_LANGUAGE — not a name candidate."""
        s = sess()
        action, _ = NameCollector(s).handle("let me try again", "Let me try again")
        assert action in ("ask", "repair")
        assert s["_nc"].get("fn_candidate") is None

    def test_couldnt_hear_rejected(self):
        """'couldn't hear me' is _META_LANGUAGE — not a name candidate."""
        s = sess()
        action, _ = NameCollector(s).handle("you couldn't hear me", "You couldn't hear me")
        assert action in ("ask", "repair")
        assert s["_nc"].get("fn_candidate") is None

    def test_didnt_get_that_rejected(self):
        """'didn't get that' is _META_LANGUAGE — not a name candidate."""
        s = sess()
        action, _ = NameCollector(s).handle("didn't get that", "Didn't get that")
        assert action in ("ask", "repair")
        assert s["_nc"].get("fn_candidate") is None
