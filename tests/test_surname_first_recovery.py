"""Surname-first STT recovery (theorem_v3 name-first flow).

STT sometimes clips the front off a compound name answer ("my first name is
Quentin, my surname is Rock" -> "surname is rock").  The system must PARK that
surname and attach it to the first name when it lands on the re-ask, instead of
throwing it away and re-collecting the whole name.

These tests pin the detection helper and the park/consume behaviour in
_v3_try_persist_name.  They are self-contained (pure session dicts) so they run
without the media-stream websocket stack.
"""
import pytest

from app.media_streams.connection import _v3_surname_only, _v3_try_persist_name


class TestSurnameOnlyDetection:
    @pytest.mark.parametrize("utterance,expected", [
        ("surname is rock", "Rock"),
        ("my surname is rock", "Rock"),
        ("surname rock", "Rock"),
        ("my surname's green", "Green"),
        ("my last name is o'brien", "O'Brien"),
        ("family name smith-jones", "Smith-Jones"),
        ("second name is taylor", "Taylor"),
    ])
    def test_bare_surname_captured(self, utterance, expected):
        assert _v3_surname_only(utterance) == expected

    @pytest.mark.parametrize("utterance", [
        # Has a first-name clause -> normal path handles it, do NOT park.
        "my first name is quentin and my surname is rock",
        # Garbled self-correction contains a first-name clause -> not parked.
        "yeah so my surname is quench i said my first name is quincey and my family was",
        # No surname marker.
        "quentin rock",
        "rock",
        # Marker followed by filler/politeness -> not a surname.
        "surname is please",
        "surname is thanks",
    ])
    def test_not_surname_only(self, utterance):
        assert _v3_surname_only(utterance) == ""

    def test_trailing_politeness_ignored(self):
        assert _v3_surname_only("my surname is green please") == "Green"


class TestParkAndConsume:
    def test_clipped_surname_reattaches_to_first_name(self):
        """surname arrives first (first name clipped), then first name lands."""
        sess = {"v3_name_collection_active": True}
        # Turn A: STT delivered only the surname; LLM re-asks the first name.
        persisted_a = _v3_try_persist_name(
            sess,
            "Sorry, I didn't quite catch your first name — could you say it again?",
            post_slot_pending=True,
            caller_utterance="surname is rock",
        )
        assert persisted_a is False
        assert sess.get("v3_pending_surname") == "Rock"
        assert sess.get("patient_name") is None
        # Turn B: first name arrives, LLM reads it back.
        persisted_b = _v3_try_persist_name(
            sess,
            "Thanks Quentin — which clinic were you thinking of?",
            post_slot_pending=True,
            caller_utterance="quentin",
        )
        assert persisted_b is True
        assert sess.get("patient_name") == "Quentin Rock"
        assert "v3_pending_surname" not in sess  # cleared on consume

    def test_same_breath_surname_wins_over_parked(self):
        sess = {"v3_name_collection_active": True, "v3_pending_surname": "Rock"}
        _v3_try_persist_name(
            sess, "Thanks Quentin — which clinic?",
            post_slot_pending=True, caller_utterance="quentin green",
        )
        assert sess.get("patient_name") == "Quentin Green"
        assert "v3_pending_surname" not in sess

    def test_parked_surname_backfills_first_name_only_lock(self):
        """First name stored alone (e.g. via collect_and_store); parked surname
        must back-fill it (Stage 2 path)."""
        sess = {
            "v3_name_collection_active": True,
            "patient_name": "Quentin",
            "collected": {"name": "Quentin"},
            "v3_pending_surname": "Rock",
            "v3_awaiting_surname": True,
        }
        persisted = _v3_try_persist_name(
            sess, "Thanks Quentin — which clinic?",
            post_slot_pending=True, caller_utterance="",
        )
        assert persisted is True
        assert sess.get("patient_name") == "Quentin Rock"
        assert "v3_pending_surname" not in sess

    def test_one_breath_full_name_unaffected(self):
        """Regression: the normal 'Quentin Rock' one-breath answer still works."""
        sess = {"v3_name_collection_active": True}
        _v3_try_persist_name(
            sess, "Thanks Quentin — which clinic?",
            post_slot_pending=True, caller_utterance="quentin rock",
        )
        assert sess.get("patient_name") == "Quentin Rock"

    def test_garbled_correction_does_not_park(self):
        """Regression: a correction utterance must not park a wrong surname."""
        sess = {"v3_name_collection_active": True}
        _v3_try_persist_name(
            sess, "Sorry, I didn't quite catch your first name — say it again?",
            post_slot_pending=True,
            caller_utterance=(
                "yeah so my surname is quench i said my first name is "
                "quincey and my family was"
            ),
        )
        assert sess.get("v3_pending_surname") is None

    def test_no_park_outside_name_phase(self):
        """A surname-shaped utterance outside the name phase is not parked."""
        sess = {}
        _v3_try_persist_name(
            sess, "So that's Tuesday at nine — does that work?",
            post_slot_pending=False, caller_utterance="surname is rock",
        )
        assert sess.get("v3_pending_surname") is None
