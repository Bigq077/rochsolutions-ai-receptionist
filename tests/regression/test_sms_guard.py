"""SMS cost guard: encoding, segment counting, and the free test path.

Context: the account log showed 2,345 outbound messages billed as 7,637
segments — 3.26 each — because 85% were forced to UCS-2 by an em dash or an
emoji, and 83% of a month's spend went to one test handset.
"""

import importlib.util
import os
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "sms_guard", Path(__file__).resolve().parents[2] / "app" / "notifications" / "sms_guard.py"
)
sg = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sg)


class TestEncoding:
    def test_em_dash_triples_a_message_and_the_sanitiser_fixes_it(self):
        body = (
            "Hi, you called Theorem Physiotherapy — we missed you. Book online at "
            "example.com/book or reply BOOK and we will call you back. "
            "Opening hours Mon-Fri 8am-6pm."
        )
        assert sg.count_segments(body) == 3          # what you are billed today
        assert sg.count_segments(sg.to_gsm7(body)) == 1

    def test_emoji_are_stripped(self):
        body = "Hi Quentin 👋 Your appointment is confirmed 📅 Tuesday at 9am 📍 12 High Street."
        assert not sg.is_gsm7(body)
        assert sg.is_gsm7(sg.to_gsm7(body))

    @pytest.mark.parametrize("char", ["—", "–", "'", "'", '"', '"', "…", "•", "→", "😊", " "])
    def test_every_known_offender_is_removed(self, char):
        assert sg.is_gsm7(sg.to_gsm7(f"Appointment {char} confirmed"))

    def test_sanitiser_is_idempotent(self):
        body = "Tuesday — 9am 😊"
        assert sg.to_gsm7(sg.to_gsm7(body)) == sg.to_gsm7(body)

    def test_meaning_is_preserved(self):
        assert sg.to_gsm7("Tue — 9am") == "Tue - 9am"
        assert sg.to_gsm7("we're open") == "we're open"

    def test_pound_sign_survives(self):
        # £ is valid GSM-7 — do not let a sanitiser eat prices.
        assert sg.to_gsm7("Initial assessment is £55") == "Initial assessment is £55"
        assert sg.count_segments("Initial assessment is £55") == 1


class TestSegmentCounting:
    @pytest.mark.parametrize("length,expected", [(1, 1), (160, 1), (161, 2), (306, 2), (307, 3)])
    def test_gsm7_boundaries(self, length, expected):
        assert sg.count_segments("a" * length) == expected

    @pytest.mark.parametrize("length,expected", [(1, 1), (70, 1), (71, 2), (134, 2), (135, 3)])
    def test_ucs2_boundaries(self, length, expected):
        assert sg.count_segments("é" * (length - 1) + "—") == expected

    def test_astral_emoji_costs_two_units(self):
        assert sg.count_segments("😊" * 35) == 1     # 70 UTF-16 units
        assert sg.count_segments("😊" * 36) == 2


class TestTestNumberRouting:
    def test_listed_number_never_reaches_twilio(self, monkeypatch):
        monkeypatch.setenv("SMS_TEST_NUMBERS", "+447502211207,+447870166861")
        sg.reset_cache()
        assert sg.is_test_number("+447502211207")
        assert not sg.is_test_number("+447123456789")

    def test_fake_send_returns_a_sid_so_callers_see_success(self, monkeypatch):
        monkeypatch.setenv("SMS_TEST_NUMBERS", "+447502211207")
        sg.reset_cache()
        sg.clear_inbox()
        sid = sg.record_fake("+447502211207", "+447380841468", "Tuesday at 9am", 1)
        assert isinstance(sid, str) and sid.startswith("SM")
        assert sg.inbox()[0]["to"] == "+447502211207"

    def test_empty_env_means_everything_is_live(self, monkeypatch):
        monkeypatch.delenv("SMS_TEST_NUMBERS", raising=False)
        sg.reset_cache()
        assert not sg.is_test_number("+447502211207")


class TestBudget:
    def test_strict_mode_raises_and_names_the_character(self, monkeypatch):
        monkeypatch.setenv("SMS_SEGMENT_LIMIT", "1")
        monkeypatch.setenv("SMS_SEGMENT_STRICT", "true")
        with pytest.raises(ValueError, match="U\\+2014"):
            sg.check_budget("x" * 200 + "—", "+447123456789")

    def test_clean_single_segment_passes(self, monkeypatch):
        monkeypatch.setenv("SMS_SEGMENT_LIMIT", "1")
        monkeypatch.setenv("SMS_SEGMENT_STRICT", "true")
        assert sg.check_budget("Tuesday at 9am, see you then.", "+447123456789") == 1
