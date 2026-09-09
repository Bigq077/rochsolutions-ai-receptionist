"""LAT-1: "in the morning" is 27% of a multi-day read-out, and it is optional.

`operational.speak_part_of_day`, DEFAULT TRUE. Every clinic keeps the wording it
has until its own clinic.json opts out, because a change to what patients hear
belongs to the clinic and not to the engine.

Measured on CA8d5b2e3e's payload, at the 3.82 words/sec that call ran at:

    multi-day    66 words  17.3 s   ->  48 words  12.6 s
    single-day   39 words  10.2 s   ->  30 words   7.9 s

WHY IT IS SAFE TO DROP, all of it measured rather than argued:

  * over 236 real day-offers in the obs store carrying 2262 spoken labels,
    dropping the suffix created ZERO pairs that sound alike;
  * no clinic offers an hour together with its twelve-hour twin — measured
    spreads are northgate 08-18 and theorem 09-18, both narrower than twelve
    hours, so a clock face names exactly one time inside a working day;
  * `slot_accepted_by_caller` has ALWAYS stripped the suffix before matching
    (`_strip_part_of_day`), so nothing about what a caller may SAY changes.
    That is the half this file spends most of its assertions on, because it is
    the half that books appointments.

WHAT IS NOT MEASURABLE, and why this is a flag and not a default: the suffix
also CONFIRMS to a caller that "eight" is the morning. No corpus can price
that.

REVERSED 9 Sep 2026 — the judge priced it. northgate ran bare for two days and
CA0b217e710b9a3957a384186794af4149 (9 Sep 10:32) came back tagged
`caller_frustration`, evidence "the slot read-outs were garbled and ambiguous",
quoting the readout this flag produces: "Number 1, eight. Number 2, one.
Number 3, ten past five." One call in 13, so a signal and not a verdict — but
the demo line is the most expensive place to hold a wording experiment, so it
goes back and the lever stays.

So NO clinic opts out today. The lever is still fully covered below: every
wording, resolution and read-out assertion drives `part_of_day` directly rather
than through a clinic, which is what lets the flag be turned on again without
rediscovering whether it works.
"""

import inspect

import pytest

from app.tools.receptionist_tools import _spoken_slot_time, speaks_part_of_day
from app.tools.slot_followup import slot_accepted_by_caller
from app.tools.slot_offer import apply_offer_to_session, build_slot_offer

_DAY = "2026-09-08"
_LABEL = "Tuesday 8th September"
_TIMES = ["08:50", "16:20", "17:10"]


def _payload(part_of_day):
    spoken = [_spoken_slot_time(t, part_of_day) for t in _TIMES]
    return [{
        "date": _DAY, "day_label": _LABEL,
        "slot_times": list(_TIMES), "slot_times_spoken": spoken,
        "times_not_shown": 0,
        "slots": [{"start": "%sT%s:00" % (_DAY, t), "date": _DAY,
                   "day_label": _LABEL, "time": t, "spoken": s}
                  for t, s in zip(_TIMES, spoken)],
    }]


def _armed_session(part_of_day):
    """A session holding the offer, armed through the real writer."""
    payload = _payload(part_of_day)
    offer = build_slot_offer(payload, more_times=True)
    session = {"available_days": payload}
    apply_offer_to_session(
        session,
        {"slots": [dict(s) for s in offer.slots],
         "dtmf_map": offer.dtmf_map, "mode": offer.mode},
        list(offer.chunks),
    )
    session["available_days"] = payload
    return session


# ---------------------------------------------------------------------------
# The flag — default is today's wording, always
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("clinic_id", ["jv_v1", "vital_edge", "theorem_v3",
                                       "theorem", "demo", "northgate"])
def test_every_clinic_keeps_the_suffix(clinic_id):
    """Including northgate, from 9 Sep. See the reversal note in the docstring:
    the demo line went bare for two days and a caller heard "Number 2, one"."""
    assert speaks_part_of_day({"clinic_id": clinic_id}) is True, clinic_id


def test_the_lever_still_reads_a_false_from_config(monkeypatch):
    """The reversal removed the only clinic that exercised the OFF path, and a
    flag no config sets is a flag that can quietly stop being read. This drives
    it from a stub so turning it back on stays a one-key change.

    `speaks_part_of_day` imports `get_clinic` inside the function, so the patch
    has to land on the source module rather than on a name bound at import.
    """
    import app.clinic_config as cc
    monkeypatch.setattr(
        cc, "get_clinic",
        lambda cid: {"operational": {"speak_part_of_day": False}}, raising=False)
    assert speaks_part_of_day({"clinic_id": "anything"}) is False


@pytest.mark.parametrize("junk", [None, "", {}, "a string", 0, [],
                                  {"clinic_id": None},
                                  {"clinic_id": "no-such-clinic"}])
def test_an_unreadable_config_keeps_the_wording_it_has(junk):
    """Fail towards the status quo. A clinic whose config cannot be read must
    not silently change what its patients hear."""
    assert speaks_part_of_day(junk) is True


# ---------------------------------------------------------------------------
# The wording itself
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hhmm,with_band,without", [
    ("08:00", "eight in the morning", "eight"),
    ("08:50", "ten to nine in the morning", "ten to nine"),
    ("09:15", "quarter past nine in the morning", "quarter past nine"),
    ("12:10", "ten past twelve in the afternoon", "ten past twelve"),
    ("13:30", "half past one in the afternoon", "half past one"),
    ("16:45", "quarter to five in the afternoon", "quarter to five"),
    ("17:10", "ten past five in the evening", "ten past five"),
])
def test_both_wordings(hhmm, with_band, without):
    assert _spoken_slot_time(hhmm) == with_band
    assert _spoken_slot_time(hhmm, part_of_day=False) == without


@pytest.mark.parametrize("hhmm,label", [("12:00", "midday"), ("00:00", "midnight")])
def test_the_two_that_never_had_a_band_are_unchanged(hhmm, label):
    assert _spoken_slot_time(hhmm) == label
    assert _spoken_slot_time(hhmm, part_of_day=False) == label


@pytest.mark.parametrize("hhmm", ["08:00", "08:50", "09:15", "12:10", "13:30",
                                  "16:45", "17:10", "12:00"])
def test_no_stray_whitespace_either_way(hhmm):
    """The band carries its own leading space, so removing it must not leave a
    trailing one — a label with a stray space is a label that stops matching."""
    for pod in (True, False):
        out = _spoken_slot_time(hhmm, pod)
        assert out == out.strip(), repr(out)
        assert "  " not in out, repr(out)


# ---------------------------------------------------------------------------
# THE HALF THAT BOOKS APPOINTMENTS — a pick must still resolve
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("utterance,expected", [
    # what the caller now HEARS, said back
    ("ten to nine", "08:50"),
    ("twenty past four", "16:20"),
    ("ten past five", "17:10"),
    ("ten to nine works", "08:50"),
    ("yeah ten past five please", "17:10"),
    # and the long form they may say from habit, which they were NOT read
    ("ten to nine in the morning", "08:50"),
    ("ten past five in the evening", "17:10"),
    # positions are unaffected by the wording
    ("number two", "16:20"),
    ("the first one", "08:50"),
    ("the last one", "17:10"),
])
def test_a_pick_resolves_without_the_suffix(utterance, expected):
    got = slot_accepted_by_caller(_armed_session(part_of_day=False), utterance)
    assert got == "%sT%s:00" % (_DAY, expected), (
        "%r resolved to %r on a band-less offer" % (utterance, got))


@pytest.mark.parametrize("utterance,expected", [
    ("ten to nine in the morning", "08:50"),
    ("ten past five in the evening", "17:10"),
    ("number two", "16:20"),
])
def test_the_suffixed_offer_still_resolves_exactly_as_before(utterance, expected):
    got = slot_accepted_by_caller(_armed_session(part_of_day=True), utterance)
    assert got == "%sT%s:00" % (_DAY, expected), utterance


def test_a_contradicting_band_is_still_caught_without_the_suffix():
    """`_time_contradicts` reads the START time, never the label, so the guard
    survives the label losing its band. "number two in the morning" against a
    16:20 slot is still a contradiction."""
    assert slot_accepted_by_caller(
        _armed_session(part_of_day=False), "number two in the morning") is None


# ---------------------------------------------------------------------------
# It must reach the read-out, and it must reach EVERY availability path
# ---------------------------------------------------------------------------

def test_the_readout_is_materially_shorter_without_the_suffix():
    long_ = " ".join(build_slot_offer(_payload(True), more_times=True).chunks)
    short = " ".join(build_slot_offer(_payload(False), more_times=True).chunks)
    assert "in the morning" not in short, short
    assert "in the morning" in long_, long_
    assert len(short.split()) <= len(long_.split()) - 8, (
        "expected ~9 words back, got %d -> %d"
        % (len(long_.split()), len(short.split())))


def test_every_availability_path_resolves_the_flag_from_its_own_session():
    """Structural, and the reason is containment.

    `_build_days_data` defaults `part_of_day=True`, so a path that forgets to
    pass it does not crash — it silently gives that clinic the other wording.
    With seven call sites across the Acuity, Google, diary and published
    readers, one clinic reading two ways on two paths is exactly the failure
    this pins.
    """
    from app.tools import receptionist_tools

    src = inspect.getsource(receptionist_tools)
    calls = [ln for ln in src.split("\n")
             if "_build_days_data(" in ln and "def _build_days_data" not in ln]
    assert len(calls) >= 7, "call sites moved — re-aim this test (%d)" % len(calls)

    # the flag is passed on the same call, which may wrap onto the next line.
    # The DEFINITION is dropped first — it contains the parameter, not the
    # argument, and would pass this test for the wrong reason.
    body = src.split("def _build_days_data(", 1)[1]
    body = body[body.index("return days_data"):]
    blocks = (src.split("def _build_days_data(", 1)[0] + body).split(
        "_build_days_data(")[1:]
    for b in blocks:
        # walk to the call's OWN closing paren -- the first ")" belongs to
        # speaks_part_of_day(session), which is the thing being looked for
        depth, end = 1, len(b)
        for _i, _ch in enumerate(b):
            if _ch == "(":
                depth += 1
            elif _ch == ")":
                depth -= 1
                if depth == 0:
                    end = _i
                    break
        head = b[:end]
        assert "part_of_day=speaks_part_of_day(session)" in head, (
            "an availability path builds days without resolving the clinic's "
            "wording:\n  _build_days_data(%s" % head[:120])
