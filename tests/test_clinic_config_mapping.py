"""
tests/test_clinic_config_mapping.py
------------------------------------
TDD red-phase test for adding the staging test number to TWILIO_TO_CLINIC.

New Render service (investigate/susie-call-flows branch, staging deploy)
needs a Twilio number that resolves to "theorem_v3" -- the same clinic_id
the real production line (+447380841468) uses -- so a test call exercises
the exact code paths under test (e.g. the v3-only greeting disclosure
guard in _inject_greeting, and the theorem_v3-only caller-concerns /
canonical-facts layer the full 14-call sweep is written against).

+14342787781 was an unused number already on the Twilio account (still on
Twilio's default demo.twilio.com webhooks -- confirmed nothing in this
codebase referenced it before this change). Deliberately NOT reusing
+447366530580 ("theorem_v2") for this: that number is load-bearing for an
existing, separate automated test suite (tests/auto/scenarios/
two_clinic_scenarios.py, tests/auto/run_tests.py Phases 15-19) that
depends on it resolving to "theorem_v2" specifically -- remapping it would
regress that tooling.

Covers:
  1. The new staging number resolves to theorem_v3 (expected to FAIL until
     the TWILIO_TO_CLINIC entry is added -- that failure is the red phase)
  2. Regression guard: theorem_v2's number is untouched by this change
  3. Regression guard: the real production number is untouched
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.clinic_config import clinic_id_from_twilio_to


class TestStagingNumberResolvesToTheoremV3:
    def test_new_staging_number_maps_to_theorem_v3(self):
        assert clinic_id_from_twilio_to("+14342787781") == "theorem_v3"


class TestExistingMappingsUntouched:
    def test_theorem_v2_test_line_still_resolves_to_theorem_v2(self):
        """Regression guard: tests/auto/'s two-clinic suite depends on this
        exact mapping -- must not be disturbed by adding the new number."""
        assert clinic_id_from_twilio_to("+447366530580") == "theorem_v2"

    def test_production_line_still_resolves_to_theorem_v3(self):
        assert clinic_id_from_twilio_to("+447380841468") == "theorem_v3"

    def test_unrecognised_number_still_falls_back_to_demo(self):
        assert clinic_id_from_twilio_to("+10000000000") == "demo"
