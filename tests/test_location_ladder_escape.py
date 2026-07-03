"""
tests/test_location_ladder_escape.py
------------------------------------
Clinic-question loop escape hatch (sweep Call 12, restarted v2 sweep).

Once Susie asks "Awlstuh or Redditch?" the location ladder is:
  reask_count 0 → rung 2 biased confirm
  reask_count 1 → rung 3 DTMF keypad
…and previously, reask_count >= 1 re-fired the keypad FOREVER on every
unrecognized utterance — a caller asking anything that wasn't a clinic name got
trapped ("press 1 for Awlstuh, or 2 for Redditch" on loop), with no escape.

_location_ladder_exhausted() is the escape predicate: once the keypad has been
offered (reask_count >= 2), a further unrecognized utterance must break out to
the LLM instead of re-firing the keypad.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.media_streams.connection import _location_ladder_exhausted


def test_not_exhausted_before_keypad():
    # rung 2 (0) and rung 3/keypad-about-to-fire (1) must still use the ladder
    assert _location_ladder_exhausted({"v3_location_reask_count": 0}) is False
    assert _location_ladder_exhausted({"v3_location_reask_count": 1}) is False


def test_exhausted_after_keypad():
    # keypad already offered (>= 2) → escape to the LLM, do not loop
    assert _location_ladder_exhausted({"v3_location_reask_count": 2}) is True
    assert _location_ladder_exhausted({"v3_location_reask_count": 5}) is True


def test_missing_key_is_not_exhausted():
    assert _location_ladder_exhausted({}) is False
