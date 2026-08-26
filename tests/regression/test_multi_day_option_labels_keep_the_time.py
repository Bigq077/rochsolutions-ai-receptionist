"""
Regression: a multi-day numbered readout never updated the offer record.

Seen on three consecutive live calls on Marcus's line (25 Aug 2026), on every
turn where the requested day was empty and the search widened:

    Susie: "Wednesday 26th August is fully booked, I'm afraid — here's what
            we've got coming up - Number 1, Thursday 27th August - half past
            seven in the evening. Number 2, Saturday 29th August - quarter to
            twelve in the morning. Either of those suit you?"

    log:   slot buf: could not resolve spoken option(s)
           ['Thursday 27th August', 'Saturday 29th August'] against
           available_days - offer record left unchanged

The times were spoken. They are missing from the labels because
`_OPTION_LABEL_STOP_RE` splits on an em dash and keeps the FIRST segment, so
"Thursday 27th August - half past seven in the evening" becomes
"Thursday 27th August". `_resolve_within` matches a label against a slot's
`spoken` field by normalised EQUALITY, and a slot's spoken field is a time
("half past seven in the evening"). A day label can therefore never match, and
resolution fails every time - all-or-nothing, so nothing is recorded.

WHY THE SINGLE-DAY FORM NEVER SHOWED THIS: there the day sits in the preamble
("Tuesday 1st September - Number 1, five in the evening. Number 2, ...") so
each option label is already a bare time. Only the MULTI-DAY form puts a day
inside the option, and that form is produced by the widened
`requested_day_empty` path - which is why this fired on every widened turn and
on no other.

TWO THINGS DEPEND ON THE RECORD BEING WRITTEN:

  1. `record_spoken_slots` - the cumulative record of what the caller has
     actually heard (B-78b). Never updated here, which is the defect its own
     call-site comment describes: the caller asks for "the others" and is
     re-offered times they heard forty seconds earlier.
  2. `session["last_offered_slots"]` - which `_resolve_slot_iso` indexes into
     BY POSITION for an ordinal choice ("the first one", "2"). That path does
     no verification against what was spoken; it trusts the record to match.

WHAT MUST NOT CHANGE: the DTMF map. `extract_slot_options` feeds both the
resolver and `v3_dtmf_slot_map`, and the map's label is injected as a synthetic
transcript on a keypress. Changing it would change what pressing "1" says. The
map keeps the day label; only the resolver learns to look past the dash.
"""
from __future__ import annotations

from app.tools.slot_followup import (
    extract_slot_options,
    option_label_candidates,
    resolve_spoken_options,
)

_SPOKEN = {
    "19:30": "half past seven in the evening",
    "20:15": "quarter past eight in the evening",
    "11:45": "quarter to twelve in the morning",
    "17:00": "five in the evening",
}


def _day(date: str, times: list, label: str) -> dict:
    return {
        "date": date,
        "day_label": label,
        "slot_times": list(times),
        "slot_times_spoken": [_SPOKEN[t] for t in times],
        "slots": [
            {"start": f"{date}T{t}:00", "end": f"{date}T{t}:59"} for t in times
        ],
    }


THURSDAY = _day("2026-08-27", ["19:30", "20:15"], "Thursday 27th August")
SATURDAY = _day("2026-08-29", ["11:45"], "Saturday 29th August")

LIVE_MULTI_DAY = (
    "Wednesday 26th August is fully booked, I'm afraid — here's what we've got "
    "coming up — Number 1, Thursday 27th August — half past seven in the "
    "evening. Number 2, Saturday 29th August — quarter to twelve in the "
    "morning. Either of those suit you?"
)


def _candidates(text):
    return list(option_label_candidates(text).values())


# ---------------------------------------------------------------------------
# The live readout
# ---------------------------------------------------------------------------
def test_the_live_multi_day_readout_resolves():
    got = resolve_spoken_options([THURSDAY, SATURDAY], _candidates(LIVE_MULTI_DAY))
    assert got is not None, "the offer record is still left unchanged"
    assert [s["start"] for s in got] == [
        "2026-08-27T19:30:00",
        "2026-08-29T11:45:00",
    ]


def test_the_day_label_alone_still_cannot_resolve():
    """Proves the fix is the TIME being recovered, not a looser matcher."""
    assert resolve_spoken_options(
        [THURSDAY, SATURDAY],
        ["Thursday 27th August", "Saturday 29th August"],
    ) is None


def test_the_candidates_carry_both_halves():
    cands = option_label_candidates(LIVE_MULTI_DAY)
    assert set(cands) == {"1", "2"}
    assert "Thursday 27th August" in cands["1"]
    assert "half past seven in the evening" in cands["1"]


# ---------------------------------------------------------------------------
# Containment — the keypad map is a separate consumer and must not move
# ---------------------------------------------------------------------------
def test_the_dtmf_map_is_unchanged():
    """`extract_slot_options` feeds `v3_dtmf_slot_map`, whose label is injected
    as a synthetic transcript on a keypress. Pressing 1 must still say the day
    it said before this change."""
    assert extract_slot_options(LIVE_MULTI_DAY) == {
        "1": "Thursday 27th August",
        "2": "Saturday 29th August",
    }


def test_the_single_day_form_still_resolves():
    """The form that always worked. Day in the preamble, bare time per option."""
    text = (
        "Thursday 27th August — Number 1, half past seven in the evening. "
        "Number 2, quarter past eight in the evening. Any of those work?"
    )
    got = resolve_spoken_options([THURSDAY], _candidates(text))
    assert [s["start"] for s in got] == [
        "2026-08-27T19:30:00",
        "2026-08-27T20:15:00",
    ]


def test_a_plain_string_label_still_works():
    """resolve_spoken_options is public and tested elsewhere with bare strings."""
    got = resolve_spoken_options([THURSDAY], ["half past seven in the evening"])
    assert [s["start"] for s in got] == ["2026-08-27T19:30:00"]


# ---------------------------------------------------------------------------
# Deny by default is the whole posture of this resolver
# ---------------------------------------------------------------------------
def test_a_day_only_readout_resolves_nothing():
    """A genuine day-selection readout carries no time. There is no slot to
    record, and leaving the offer record alone is correct."""
    text = "Number 1, Thursday 27th August. Number 2, Saturday 29th August."
    assert resolve_spoken_options([THURSDAY, SATURDAY], _candidates(text)) is None


def test_an_ambiguous_time_IS_resolved_when_the_option_names_its_day():
    """This asserted None, and that was the over-broad behaviour.

    The same time on two days is only unresolvable if you throw the day away —
    and the option carries it. Live on CA9bd4ecf0 the candidates held both
    halves and resolution still returned nothing, because the time was looked
    up across every day at once. `prefer_day` cannot cover this: it is ONE day
    and a multi-day readout presents several.
    """
    other = _day("2026-08-28", ["19:30"], "Friday 28th August")
    text = "Number 1, Thursday 27th August — half past seven in the evening."
    got = resolve_spoken_options([THURSDAY, other], _candidates(text))
    assert [s["start"] for s in got] == ["2026-08-27T19:30:00"], (
        "the option named Thursday; Friday was never a candidate"
    )


def test_an_ambiguous_time_with_no_day_is_still_refused():
    """Deny-by-default survives where it is actually load-bearing: with no day
    in the option there is nothing to tell the two apart, and picking one is
    how a caller is booked into a day they never heard."""
    other = _day("2026-08-28", ["19:30"], "Friday 28th August")
    text = "Number 1, half past seven in the evening."
    assert resolve_spoken_options([THURSDAY, other], _candidates(text)) is None


def test_a_day_the_payload_does_not_know_falls_back_to_the_global_lookup():
    """An unrecognised day must not silently scope to nothing — it falls back,
    and the global ambiguity rule still applies."""
    other = _day("2026-08-28", ["19:30"], "Friday 28th August")
    text = "Number 1, Someday 40th Smarch — half past seven in the evening."
    assert resolve_spoken_options([THURSDAY, other], _candidates(text)) is None


def test_the_day_label_survives_the_filler_the_model_adds():
    """Payload says "Monday 31st August"; the model says "Monday the 31st of
    August". Both must key the same day or the scoping stops applying."""
    monday = _day("2026-08-31", ["19:30"], "Monday 31st August")
    other = _day("2026-09-01", ["19:30"], "Tuesday 1st September")
    text = "Number 1, Monday the 31st of August — half past seven in the evening."
    got = resolve_spoken_options([monday, other], _candidates(text))
    assert [s["start"] for s in got] == ["2026-08-31T19:30:00"]


def test_all_or_nothing_survives():
    """One unresolvable option must discard the whole set — a partial record
    would disagree with the speech, which is worse than not writing."""
    text = (
        "Number 1, Thursday 27th August — half past seven in the evening. "
        "Number 2, Saturday 29th August — midnight."
    )
    assert resolve_spoken_options([THURSDAY, SATURDAY], _candidates(text)) is None


def test_empty_inputs():
    assert option_label_candidates("") == {}
    assert option_label_candidates(None) == {}
    assert resolve_spoken_options([THURSDAY], _candidates("no options here")) is None


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------
def test_the_flush_resolves_from_candidates_not_truncated_labels():
    import inspect

    from app.media_streams import llm_stream as ls

    src = inspect.getsource(ls)
    assert "option_label_candidates" in src, (
        "the resolver is still being fed labels truncated at the em dash"
    )


def test_the_dtmf_map_still_built_from_extract_slot_options():
    import inspect

    from app.media_streams import llm_stream as ls

    src = inspect.getsource(ls)
    assert "extract_slot_options(_joined)" in src, (
        "the keypad map must keep its own extraction"
    )


def test_a_multi_day_offer_is_recorded_as_heard_but_not_as_the_offer():
    """The asymmetry is deliberate and both halves matter.

    RECORDED: the caller heard those times, so the cumulative spoken record has
    to learn them or "what else have you got?" re-offers them. That record is a
    flat set of ISO starts and `unspoken_remain_on_day` filters by day itself,
    so spanning days is fine for it.

    NOT RECORDED: `last_offered_slots`, because `_resolve_slot_iso` indexes it
    BY POSITION for an ordinal choice, and `slot_labels`, which is times-only
    and therefore ambiguous to a caller across two days.
    """
    import inspect

    from app.media_streams import llm_stream as ls

    src = inspect.getsource(ls)
    i = src.index("spoken options span")
    window = src[i - 1400:i + 200]
    assert "record_spoken_slots(session, _r)" in window, (
        "a multi-day readout still teaches the spoken record nothing"
    )
    assert 'session["last_offered_slots"] = [' not in window, (
        "the positional offer record was widened to multiple days without "
        "auditing _resolve_slot_iso's index path"
    )


# ---------------------------------------------------------------------------
# prefer_day must not collapse a cross-day readout onto one day
#
# Live on CA0453bd85 (26 Aug, vital_edge, build 324174dc):
#
#   Susie: "Number 1, Monday 7th September — nine in the morning.
#           Number 2, Tuesday 8th September — nine in the morning."
#   log:   2 spoken option(s) recorded as offered —
#          ['2026-09-07T09:00:00+01:00', '2026-09-07T09:00:00+01:00']
#
# BOTH recorded as Monday. `resolve_spoken_options` tries a pool filtered to
# `prefer_day` first; the per-option `by_day` index is built from that filtered
# pool, so option 2's "Tuesday 8th September" found no Tuesday, fell through to
# the pool's global map — which held only Monday — and matched Monday's "nine
# in the morning". Both options resolved, so the scoped attempt was accepted and
# the correct unscoped pass never ran.
#
# The consequence is worse than a bad record: both slots then shared one day, so
# the SINGLE-DAY branch ran and wrote `last_offered_slots`, which
# `_resolve_slot_iso` indexes BY POSITION. "The second one" would have booked
# Monday when the caller meant Tuesday.
#
# Introduced by the day-scoping commit. Before it, option 2's label was
# day-only, resolved to nothing, and the whole set was discarded — so this
# turned "record nothing" into "record the wrong slot".
# ---------------------------------------------------------------------------
def test_prefer_day_does_not_collapse_a_cross_day_readout():
    text = (
        "Number 1, Monday 7th September — nine in the morning. "
        "Number 2, Tuesday 8th September — nine in the morning."
    )
    monday = _day("2026-09-07", ["19:30"], "Monday 7th September")
    tuesday = _day("2026-09-08", ["19:30"], "Tuesday 8th September")
    monday["slot_times_spoken"] = ["nine in the morning"]
    tuesday["slot_times_spoken"] = ["nine in the morning"]
    got = resolve_spoken_options(
        [monday, tuesday], _candidates(text), prefer_day="2026-09-07",
    )
    assert [s["start"] for s in got] == [
        "2026-09-07T19:30:00",
        "2026-09-08T19:30:00",
    ], "prefer_day collapsed Tuesday onto Monday"


def test_prefer_day_still_scopes_a_single_day_readout():
    """The behaviour prefer_day exists for must survive: a clinic running the
    same rota every evening has the same spoken time on several days, and the
    day being presented is what tells them apart."""
    text = "Number 1, half past seven in the evening. Number 2, quarter past eight in the evening."
    other = _day("2026-08-28", ["19:30", "20:15"], "Friday 28th August")
    got = resolve_spoken_options(
        [THURSDAY, other], _candidates(text), prefer_day="2026-08-27",
    )
    assert [s["start"] for s in got] == [
        "2026-08-27T19:30:00",
        "2026-08-27T20:15:00",
    ]


def test_an_option_naming_an_unknown_day_is_not_forced_into_the_scope():
    """A day the payload has never heard of must not silently resolve to the
    preferred day's slot of the same time."""
    text = "Number 1, Someday 40th Smarch — half past seven in the evening."
    assert resolve_spoken_options(
        [THURSDAY], _candidates(text), prefer_day="2026-08-27",
    ) is not None  # unknown day is not in known_days -> falls back, resolves

