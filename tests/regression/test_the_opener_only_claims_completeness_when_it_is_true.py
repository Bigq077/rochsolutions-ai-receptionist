"""The read-back opener claimed completeness and then took it straight back.

`build_slot_offer` opened every single-day readout with

    "The available slots for Tuesday 8th September are — Number 1, …"

whatever `more_times` said, and then appended

    "And I've a few others that day."

in the same breath. The first sentence says the list is the day's availability;
the second says it is not.

THIS IS NOT A NEW RULE. `SLOT_FORMATTER_SYSTEM_PROMPT` has specified the split
since it was written, and the builder simply used the wrong case for half its
inputs:

    3) no lead_in AND first_day.more_times is FALSE  (the numbered list is that
       day's COMPLETE set — tell the caller so nothing seems held back):
       "The available slots for [day_label] are — Number 1, …"
    4) no lead_in AND first_day.more_times is TRUE:
       "[day_label] — Number 1, …"

The P10 note already living in that function calls the "are —" wording a
COMPLETENESS claim, and suppressed it on the CONTINUATION path ("On Tuesday I
also have —"). It left the FIRST reading of a day making the claim wrongly.

── and it is where the LAT-1 seconds are ─────────────────────────────────────
CA8b40d1ed, northgate, 7 Sep 2026. The caller barged in over the tail, having
heard all three options. Measured on that call and rebuilt from its payload:

    before   47 words   12.7 s   247 chars
    after    39 words   10.2 s   197 chars

That 197 is the other half of the point. `last_bot_prompt` is capped at 200
characters, and on that call the readout overflowed it, lost its "?" and sent
clinical screening to the `last_question` fallback (B-31):

    last_bot_prompt truncated at 200 chars and lost its '?'
    bot="n the evening. And I've a few others tha"

Shortening the tail alone left it at 225 — still over. The opener is what
brings this shape under the cap.
"""

import pytest

from app.tools.receptionist_tools import _spoken_slot_time
from app.tools.slot_followup import MORE_TIMES_TAIL
from app.tools.slot_offer import build_slot_offer

_DAY = "2026-09-08"
_LABEL = "Tuesday 8th September"
_COMPLETENESS = "The available slots for"


def _payload(times, hidden=0):
    return [{
        "date": _DAY, "day_label": _LABEL,
        "slot_times": list(times),
        "slot_times_spoken": [_spoken_slot_time(t) for t in times],
        "times_not_shown": hidden,
        "slots": [{"start": "%sT%s:00" % (_DAY, t), "date": _DAY,
                   "day_label": _LABEL, "time": t,
                   "spoken": _spoken_slot_time(t)} for t in times],
    }]


def _spoken(times, hidden=0, **kw):
    off = build_slot_offer(_payload(times, hidden), **kw)
    assert off is not None
    return " ".join(off.chunks)


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------

def test_more_times_means_no_completeness_claim():
    out = _spoken(["08:50", "16:20", "17:10"], more_times=True)
    assert _COMPLETENESS not in out, (
        "the opener claims the list is the day's availability, and the tail "
        "in the same reply says it is not:\n  %s" % out
    )
    assert out.startswith(_LABEL + " —"), out
    assert MORE_TIMES_TAIL in out


def test_a_single_time_with_more_behind_it_makes_no_claim_either():
    """"THE slot I have on Tuesday is …" is the same false claim without the
    numbering."""
    out = _spoken(["08:50"], more_times=True)
    assert "The slot I have on" not in out, out
    assert MORE_TIMES_TAIL in out


def test_hidden_band_times_also_suppress_the_claim():
    """`times_not_shown` is the B-97 count — times a band filter removed before
    the session ever saw them. They are "more" even though no walk over the
    slots can see them, and the opener must respect that."""
    out = _spoken(["08:50", "16:20"], hidden=4)
    assert _COMPLETENESS not in out, out


# ---------------------------------------------------------------------------
# What must NOT change — the claim is CORRECT when the list is complete
# ---------------------------------------------------------------------------

def test_a_complete_list_still_says_so():
    """Case 3 exists for a reason: with nothing held back, saying so is how the
    caller knows nothing is being kept from them."""
    out = _spoken(["08:50", "16:20", "17:10"], more_times=False)
    assert out.startswith(_COMPLETENESS), out
    assert MORE_TIMES_TAIL not in out


def test_a_single_complete_time_still_says_so():
    out = _spoken(["08:50"], more_times=False)
    assert "The slot I have on %s is" % _LABEL in out, out


@pytest.mark.parametrize("lead_in,opening", [
    ("earliest", "The earliest I have is"),
    ("also", "On %s I also have —" % _LABEL),
])
def test_the_other_two_openers_are_untouched(lead_in, opening):
    """`earliest` is a ranking claim guarded by `earliest_lead_in_is_true`, and
    `also` is P10's continuation wording. Neither is a completeness claim and
    neither is in scope here."""
    out = _spoken(["08:50", "16:20"], lead_in=lead_in, more_times=True)
    assert opening in out, out


# ---------------------------------------------------------------------------
# The measurements this change exists for
# ---------------------------------------------------------------------------

def test_the_readback_fits_under_the_last_bot_prompt_cap():
    """B-31 truncates `last_bot_prompt` at 200 chars and the readout lost its
    "?" to it on CA8b40d1ed. Shortening the tail alone left 225."""
    out = _spoken(["08:50", "16:20", "17:10"], more_times=True)
    assert len(out) <= 200, "%d chars:\n  %s" % (len(out), out)


def test_the_readback_is_materially_shorter():
    """39 words against the 47 measured at 12.7 s on the call."""
    out = _spoken(["08:50", "16:20", "17:10"], more_times=True)
    assert len(out.split()) <= 40, "%d words:\n  %s" % (len(out.split()), out)


# ---------------------------------------------------------------------------
# Every option must survive the trim — the readout is still the keypad map
# ---------------------------------------------------------------------------

def test_no_slot_or_keypad_entry_is_lost():
    off = build_slot_offer(_payload(["08:50", "16:20", "17:10"]), more_times=True)
    text = " ".join(off.chunks)
    assert len(off.slots) == 3
    assert sorted(off.dtmf_map) == ["1", "2", "3"]
    for i, s in enumerate(off.slots, start=1):
        assert s["spoken"] in text, s
        assert "Number %d," % i in text
    assert _LABEL in text, "the day the record is keyed on must still be spoken"


# ---------------------------------------------------------------------------
# The same rule in multi_day — where there is no tail to soften it
# ---------------------------------------------------------------------------

def _days(n):
    """`n` days, two times each, so the sweep can hold more than are named."""
    out = []
    for i in range(n):
        d = "2026-09-%02d" % (7 + i)
        label = "Day %d" % (i + 1)
        times = ["09:00", "17:00"]
        out.append({
            "date": d, "day_label": label,
            "slot_times": times,
            "slot_times_spoken": [_spoken_slot_time(t) for t in times],
            "slots": [{"start": "%sT%s:00" % (d, t), "date": d,
                       "day_label": label, "time": t,
                       "spoken": _spoken_slot_time(t)} for t in times],
        })
    return out


_COMING_UP = "Here's what we've got coming up —"


def test_multi_day_does_not_claim_to_be_the_days_coming_up_when_it_is_not():
    """Four days found, three named.

    multi_day gets NO tail — B-99 suppresses "a few others that day" here
    correctly, because "that day" has no referent once three days have been
    named. So the opener is the only thing the caller has to go on, and it was
    the confident version whatever the sweep held.
    """
    out = " ".join(build_slot_offer(_days(4)).chunks)
    assert _COMING_UP not in out, out
    assert out.startswith("I've got a few days —"), out
    assert "others that day" not in out, "B-99: no day-less tail on multi_day"


def test_multi_day_still_says_so_when_those_are_the_days():
    """Exactly three days found and three named — the claim is true."""
    out = " ".join(build_slot_offer(_days(3)).chunks)
    assert out.startswith(_COMING_UP), out


def test_every_day_and_keypad_entry_survives_the_multi_day_opener():
    off = build_slot_offer(_days(4))
    text = " ".join(off.chunks)
    assert off.mode == "multi_day"
    assert sorted(off.dtmf_map) == ["1", "2", "3"]
    for i in (1, 2, 3):
        assert "Number %d," % i in text
        assert off.dtmf_map[str(i)] in text
