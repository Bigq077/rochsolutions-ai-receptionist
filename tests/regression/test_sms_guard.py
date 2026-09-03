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


# ---------------------------------------------------------------------------
# End to end, through SMSService.send_sms
# ---------------------------------------------------------------------------
# NOTE: the `sg` module above is loaded by FILE PATH, so it is a DIFFERENT
# module object from the `app.notifications.sms_guard` that send_sms imports.
# Its cache and its inbox are not the ones the wiring uses. These tests must
# talk to the real module or they assert on the wrong globals — which would
# pass while proving nothing.
# NO await ) here. pytest.ini sets asyncio_mode = auto, so an
# `async def` test is driven by pytest-asyncio's own loop. Calling
# await ) from a sync test creates AND CLOSES a loop underneath
# that machinery, and later async tests then fail in ways that depend on
# collection order while passing in isolation -- which is exactly what it
# did here, differently on each full-suite run. Match the house style used
# by test_sms_log_names_the_real_destination.py: async def + await.
import pytest as _pytest

from app.notifications import sms_guard as real_guard
from app.notifications.sms import SMSService

TESTER = "+447700900123"     # stands in for the developer's own handset
CALLER = "+447476952176"     # a patient who rang in


class _FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        return type("_Sent", (), {
            "sid": "SMreal000000000000000000000000001",
            "status": "queued",
        })()


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessages()


def _service():
    """An SMSService with no Twilio behind it.

    Built with object.__new__ so __init__'s credential check and real Client
    construction are skipped -- the same harness shape the media-streams
    regression tests use.
    """
    s = object.__new__(SMSService)
    s.account_sid, s.auth_token = "AC" + "0" * 32, "x"
    s.from_number = "+447380841468"
    s.client = _FakeClient()
    return s


@_pytest.fixture(autouse=True)
def _guard_state(monkeypatch):
    real_guard.reset_cache()
    real_guard.clear_inbox()
    monkeypatch.setenv("SMS_ENABLED", "true")
    monkeypatch.setenv("SMS_TEST_NUMBERS", TESTER)
    monkeypatch.delenv("SMS_SEGMENT_STRICT", raising=False)
    monkeypatch.delenv("EVAL_STAFF_SMS_TO", raising=False)
    yield
    real_guard.reset_cache()
    real_guard.clear_inbox()


class TestEndToEnd:
    async def test_the_test_handset_never_reaches_twilio(self):
        """The whole point: 83% of a month's spend went to one handset."""
        svc = _service()
        sid = await svc.send_sms(TESTER, "Your appointment is confirmed.")

        assert sid, "a SID must come back — call sites read None as FAILURE"
        assert sid.startswith("SMfake")
        assert svc.client.messages.calls == [], "Twilio was billed for a test SMS"
        assert len(real_guard.inbox()) == 1

    async def test_a_real_number_still_reaches_twilio(self):
        """The guard must not become a second SMS_ENABLED."""
        svc = _service()
        sid = await svc.send_sms(CALLER, "Your appointment is confirmed.")

        assert sid == "SMreal000000000000000000000000001"
        assert len(svc.client.messages.calls) == 1
        assert svc.client.messages.calls[0]["to"] == CALLER
        assert real_guard.inbox() == [], "a real send must not be captured"

    async def test_the_body_that_reaches_twilio_is_gsm7(self):
        """The sanitiser runs on the real path, not only the fake one — that is
        the half of the saving that reaches patients."""
        svc = _service()
        await svc.send_sms(
            CALLER,
            "Hi Quentin 👋 — your appointment is confirmed … see you then!",
        )

        body = svc.client.messages.calls[0]["body"]
        assert real_guard.is_gsm7(body), real_guard.offenders(body)
        assert "—" not in body and "…" not in body and "👋" not in body
        assert "appointment is confirmed" in body, "meaning was not preserved"

    async def test_sms_enabled_false_still_suppresses_everything(self, monkeypatch):
        """Unchanged behaviour. The kill switch runs BEFORE the guard, so a
        suppressed run must not reach Twilio and must not be captured either --
        capturing would make the inbox claim a message that was never sent."""
        monkeypatch.setenv("SMS_ENABLED", "false")
        svc = _service()

        assert await svc.send_sms(CALLER, "Your appointment is confirmed.") is None
        assert await svc.send_sms(TESTER, "Your appointment is confirmed.") is None
        assert svc.client.messages.calls == []
        assert real_guard.inbox() == []

    async def test_an_over_long_message_warns_but_still_sends(self, monkeypatch):
        """check_budget must not become a blocker on a live service: a fat
        template is a cost problem, not a reason to drop a patient's text."""
        monkeypatch.setenv("SMS_SEGMENT_LIMIT", "1")
        svc = _service()
        sid = await svc.send_sms(CALLER, "word " * 200)

        assert sid == "SMreal000000000000000000000000001"
        assert len(svc.client.messages.calls) == 1
