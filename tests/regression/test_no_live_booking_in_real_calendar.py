"""
Regression: a test must NEVER create a live Acuity booking in a real
practitioner's calendar.

Guards tests/auto/test_acuity_booking_smoke.py — the only in-process test that
calls the live Acuity create-booking API. Proves, WITHOUT touching Acuity:

  1. By default NO calendar is "test safe", so the smoke test refuses to book
     even though live credentials are present in the environment (conftest.py
     loads the real .env on every run).
  2. A calendar becomes bookable ONLY when its exact id is listed in
     ACUITY_TEST_BOOKING_CALENDAR_ALLOWLIST; a real calendar id never qualifies
     unless someone deliberately adds it (which must only ever be the demo cal).
  3. The opt-in flag defaults OFF, so a plain `pytest` never even enters the
     booking path.

Context: 60 stray "Test Booking" appointments reached Mark's live Theorem
Acuity account because the smoke test ran on a plain pytest against the root
.env (real credentials). The branch was irrelevant — the environment decides
the calendar. These gates make a real-calendar booking impossible by default.
"""
import importlib.util
from pathlib import Path

_SMOKE = Path(__file__).resolve().parents[1] / "auto" / "test_acuity_booking_smoke.py"

# A real practitioner calendar id (the Alcester fallback shipped in the smoke
# test). It must never be bookable by default.
REAL_CAL_ID = "4256627"


def _load_smoke():
    """Import the smoke-test module by path (no package assumptions)."""
    spec = importlib.util.spec_from_file_location("_smoke_under_test", _SMOKE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_no_calendar_is_bookable_by_default(monkeypatch):
    monkeypatch.delenv("ACUITY_TEST_BOOKING_CALENDAR_ALLOWLIST", raising=False)
    mod = _load_smoke()
    assert mod._calendar_is_test_safe(REAL_CAL_ID) is False
    assert mod._calendar_is_test_safe("") is False
    assert mod._calendar_is_test_safe("literally-any-id") is False


def test_only_explicitly_allowlisted_calendar_is_bookable(monkeypatch):
    monkeypatch.setenv("ACUITY_TEST_BOOKING_CALENDAR_ALLOWLIST", "DEMO123, DEMO456")
    mod = _load_smoke()
    assert mod._calendar_is_test_safe("DEMO123") is True
    assert mod._calendar_is_test_safe("DEMO456") is True
    # A real calendar not on the list stays unbookable.
    assert mod._calendar_is_test_safe(REAL_CAL_ID) is False


def test_real_calendar_never_bookable_even_alongside_a_demo_allowlist(monkeypatch):
    # Allow-listing the demo calendar must not incidentally enable a real one.
    monkeypatch.setenv("ACUITY_TEST_BOOKING_CALENDAR_ALLOWLIST", "DEMO123")
    mod = _load_smoke()
    assert mod._calendar_is_test_safe(REAL_CAL_ID) is False


def test_booking_optin_flag_defaults_off(monkeypatch):
    # Under a plain pytest run the opt-in flag is unset, so the booking tests are
    # marked skip and never reach the booking code — regardless of creds.
    monkeypatch.delenv("RUN_LIVE_ACUITY_BOOKING_TESTS", raising=False)
    mod = _load_smoke()
    assert mod._LIVE_BOOKING_OPT_IN is False
    would_skip = not (
        mod._LIVE_BOOKING_OPT_IN and mod.ACUITY_USER_ID and mod.ACUITY_API_KEY
    )
    assert would_skip is True


# ── Call-runner target gate ──────────────────────────────────────────────────
# The automated call harness (tests/auto/run_tests.py + call_runner.py) drives a
# full conversation against a REAL deployed service, which books into that
# service's Acuity calendar — in both direct-WS and --real-calls modes. These
# tests prove it refuses to contact any target that isn't an explicit demo one.
_CONFIG = Path(__file__).resolve().parents[1] / "auto" / "config.py"

# Real Theorem targets that must never be drivable by default.
REAL_CALL_NUMBER = "+447426779875"  # SUSIE_NUMBER — the live Theorem line
REAL_SERVICE_URL = "https://rochsolutions-ai-receptionist.onrender.com"


def _load_call_config():
    spec = importlib.util.spec_from_file_location("_call_config_under_test", _CONFIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_call_runner_refuses_all_targets_by_default(monkeypatch):
    monkeypatch.delenv("RUN_LIVE_CALL_TESTS", raising=False)
    monkeypatch.delenv("CALL_TEST_TARGET_ALLOWLIST", raising=False)
    cfg = _load_call_config()
    assert cfg.call_target_is_allowed(REAL_CALL_NUMBER) is False
    assert cfg.call_target_is_allowed(REAL_SERVICE_URL) is False
    assert cfg.call_target_is_allowed("") is False


def test_call_runner_optin_without_allowlist_still_refuses(monkeypatch):
    monkeypatch.setenv("RUN_LIVE_CALL_TESTS", "1")
    monkeypatch.delenv("CALL_TEST_TARGET_ALLOWLIST", raising=False)
    cfg = _load_call_config()
    assert cfg.call_target_is_allowed(REAL_CALL_NUMBER) is False


def test_call_runner_allows_only_explicit_demo_target(monkeypatch):
    monkeypatch.setenv("RUN_LIVE_CALL_TESTS", "1")
    monkeypatch.setenv(
        "CALL_TEST_TARGET_ALLOWLIST", "+441111000000, https://demo.example.com"
    )
    cfg = _load_call_config()
    assert cfg.call_target_is_allowed("+441111000000") is True
    assert cfg.call_target_is_allowed("https://demo.example.com") is True
    # Real targets stay refused even with a demo allowlist set.
    assert cfg.call_target_is_allowed(REAL_CALL_NUMBER) is False
    assert cfg.call_target_is_allowed(REAL_SERVICE_URL) is False
