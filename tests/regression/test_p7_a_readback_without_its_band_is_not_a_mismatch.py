"""
P7: a read-back that drops "in the afternoon" is still the time that was offered.

CAabe1acabf5eddee255fa53e681773034, 1 Sep 2026, northgate, build ebdd9759.
Friday was offered at eight in the morning and half past three in the
afternoon. The caller took the latter and Susie read it back correctly:

    So that's Friday the 4th of September at half past three — could I take
    your first name and surname?

```
23:51:30 [ms_gate5] read-back time NOT in the offer and not safely correctable:
         read-back names Friday 4th September but not one of the times offered
         on it ['eight in the morning', 'half past three in the afternoon']
```

The check was whole-label containment, and the label carries a part-of-day tail
the sentence does not. Once the DAY has been named the tail adds nothing, so
dropping it is the natural way to say it.

No caller-facing effect — `turn_handler.py` leaves the text alone on a mismatch
— but this is the B-95 net (a caller asked to agree to a slot that did not
exist), and a net that fires on correct read-backs cannot be escalated on.

THE UNIQUENESS CONDITION IS THE POINT, and the reason this is not just a looser
match. "half past three" is ambiguous between 03:30 and 15:30 in general and
unambiguous only when the day offers one of them. A day offering both must stay
a mismatch — nobody can tell which she meant either, and trading a false alarm
for a missed one is the wrong direction for this guard.
"""
from __future__ import annotations

import pytest

from app.tools.slot_followup import reconcile_readback_time, record_spoken_slots


def _day(date, label, times, spoken):
    return {
        "date": date,
        "day_label": label,
        "slot_times": list(times),
        "slot_times_spoken": list(spoken),
        "times_not_shown": 0,
        "slots": [
            {"start": "{}T{}:00+01:00".format(date, t), "end": ""} for t in times
        ],
    }


def _session(day):
    """A caller who has heard every time on `day`."""
    session = {"available_days": [day]}
    record_spoken_slots(session, [
        {"start": "{}T{}:00+01:00".format(day["date"], t),
         "spoken": spoken, "date": day["date"]}
        for t, spoken in zip(day["slot_times"], day["slot_times_spoken"])
    ])
    return session


# The offer from the call, exactly.
FRIDAY = _day("2026-09-04", "Friday 4th September", ["08:00", "15:30"],
              ["eight in the morning", "half past three in the afternoon"])

# A day holding BOTH half past threes — the case that must stay a mismatch.
AMBIGUOUS = _day("2026-09-04", "Friday 4th September", ["03:30", "15:30"],
                 ["half past three in the morning",
                  "half past three in the afternoon"])


@pytest.mark.asyncio
async def test_the_call_that_found_it():
    """Fails before the fix."""
    text = ("So that's Friday the 4th of September at half past three — "
            "could I take your first name and surname?")
    out, action, why = reconcile_readback_time(text, _session(FRIDAY))

    assert action == "unchanged", (
        "a correct read-back was reported as {}: {}".format(action, why)
    )
    assert out == text, "a correct read-back must not be rewritten"


@pytest.mark.asyncio
async def test_the_full_label_still_passes():
    text = ("So that's Friday the 4th of September at half past three in the "
            "afternoon — could I take your name?")
    _, action, _why = reconcile_readback_time(text, _session(FRIDAY))
    assert action == "unchanged"


@pytest.mark.asyncio
async def test_a_morning_time_without_its_band_also_passes():
    text = "So that's Friday the 4th of September at eight — your name?"
    _, action, _why = reconcile_readback_time(text, _session(FRIDAY))
    assert action == "unchanged"


@pytest.mark.asyncio
async def test_a_genuinely_wrong_time_is_still_caught():
    """The guard must keep doing its job — this is what it exists for."""
    text = "So that's Friday the 4th of September at half past six — your name?"
    _, action, why = reconcile_readback_time(text, _session(FRIDAY))
    assert action == "mismatch", "the B-95 net stopped catching a wrong time"
    assert "half past six" not in (why or "")


@pytest.mark.asyncio
async def test_a_day_offering_both_half_past_threes_stays_a_mismatch():
    """The uniqueness condition. Ambiguous means ambiguous — do not guess."""
    text = "So that's Friday the 4th of September at half past three — your name?"
    out, action, _why = reconcile_readback_time(text, _session(AMBIGUOUS))

    assert action == "mismatch", (
        "a bare 'half past three' was accepted on a day holding 03:30 AND "
        "15:30 — the fix has traded a false alarm for a missed one"
    )
    assert out == text, "and it must still not rewrite anything"
