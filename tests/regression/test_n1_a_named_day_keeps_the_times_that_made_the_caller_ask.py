# tests/regression/test_n1_a_named_day_keeps_the_times_that_made_the_caller_ask.py
"""
N1 - "what about Monday?" withdrew every time the caller was offered for Monday.

CA12036a4529eaf8e46919432a9ebc1a6a, 10 Sep 2026 21:57, northgate, build
dfc8b91454b8, outcome=abandoned. Reproduced on demand by
CA91d1f12332f6230ed51ad1a427f91f5c, 11 Sep 23:10, build 6e556ad0:

    Susie : I've got a few days - Number 1, Monday 14th September - eight in
            the morning, or ten past five in the evening. Number 2, Tuesday
            the 15th - ten to nine, or twenty past four. Number 3, Wednesday
            the 16th - twenty to ten, or half past three.
    caller: um what about monday
    Susie : Monday 14th September - half past ten, twenty past eleven,
            twenty to three.

Zero overlap. 6 of 6 corpus re-readouts on days with slots to spare.

THE OWNER, corrected by a live log line: B-116's own pick was already
08:50/09:40/16:20 -- it subtracts by dated ISO start, so Monday's 08:00 and
17:10 were "heard on this day" and gone before S-2 was consulted. Reverting S-2
would not have fixed it.

THE THREE QUESTIONS, and this file asserts all three so a fix for one cannot
quietly break the other two:

    "what else have you got?"  -> withhold what they heard      B-116
    "anything sooner?"         -> the earliest, repeats and all  B-142
    "what about Monday?"       -> the day, offered times kept    N1

Written against the traps of 9-11 Sep, each named where it applies:

  * DRIVE THE REAL ENTRY POINT. A test that called the unit passed all week
    while the live path was dead (S-13). The behavioural tests go through
    `try_unspoken_followup_speech` with the caller's words.
  * A STEP THAT PASSES WITHOUT ITS MECHANISM FIRING IS NOT A PASS. The log
    line N1 emits is asserted, not assumed.
  * NEGATIVE ASSERTIONS GO VACUOUS UNDER A WORDING CHANGE. Everything here
    compares the RECORD (`last_offered_slots` ISO starts), never the prose.
  * A MOCK OF THE LOOP CANNOT SEE THE LOOP. The spread is recorded through
    `record_spoken_slots`, the real writer.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

from app.obs.slot_offers import offers_block, record_offer
from app.tools.slot_followup import (
    _choose_presented_times,
    _keep_times_heard_on_named_day,
    _pin_accepted_index,
    _pin_requested_time_index,
    _prefer_unheard_clock_times,
    choose_presented_indices,
    part_of_day,
    record_spoken_slots,
    speak_one_day_from_payload,
    try_unspoken_followup_speech,
)

MON, TUE, WED, THU, FRI, SAT = (
    "2026-09-14", "2026-09-15", "2026-09-16",
    "2026-09-17", "2026-09-18", "2026-09-19",
)
LABELS = {
    MON: "Monday 14th September", TUE: "Tuesday 15th September",
    WED: "Wednesday 16th September", THU: "Thursday 17th September",
    FRI: "Friday 18th September", SAT: "Saturday 19th September",
}

#: northgate's real grid: 50-minute steps from 08:00.
GRID = [
    "08:00", "08:50", "09:40", "10:30", "11:20", "12:10",
    "13:00", "13:50", "14:40", "15:30", "16:20", "17:10",
]

#: The spread exactly as the 21:57 call read it.
SPREAD = {MON: ["08:00", "17:10"], TUE: ["08:50", "16:20"], WED: ["09:40", "15:30"]}
HEARD_CLOCKS = {t for times in SPREAD.values() for t in times}
LIMIT = 3


def _slots(day_iso, times):
    return [
        {"start": f"{day_iso}T{t}:00+01:00", "end": f"{day_iso}T{t}:50+01:00"}
        for t in times
    ]


def _day(day_iso, times=None):
    times = list(GRID if times is None else times)
    return {
        "date": day_iso,
        "day_label": LABELS[day_iso],
        "slot_times": times,
        "slot_times_spoken": [f"spoken-{day_iso}-{t}" for t in times],
        "slots": _slots(day_iso, times),
        "times_found_on_day": len(times),
        "times_not_shown": 0,
    }


def _payload():
    return [_day(d) for d in (MON, TUE, WED, THU, FRI, SAT)]


def _after_the_spread(**extra):
    """The session as the 21:57 call left it: a six-day lookup, three days read
    out, two times on each, recorded by the real writer."""
    days = _payload()
    offered = [
        {"start": f"{d}T{t}:00+01:00", "end": "", "date": d}
        for d, times in SPREAD.items() for t in times
    ]
    session = {
        "available_days": days,
        "_slot_presentation_mode": "multi_day",
        "last_offered_slots": offered,
        "slot_labels": [LABELS[d] for d in SPREAD],
        "v3_dtmf_slot_map": {"1": LABELS[MON], "2": LABELS[TUE], "3": LABELS[WED]},
        **extra,
    }
    record_spoken_slots(session, offered)
    return session


def _offered(session):
    """(date, clock) pairs from the RECORD -- never from the prose."""
    return [
        (str(s.get("start"))[:10], str(s.get("start"))[11:16])
        for s in (session.get("last_offered_slots") or [])
    ]


def _clocks_on(session, date):
    return [c for d, c in _offered(session) if d == date]


def _idx_clocks(day, idx):
    return [day["slot_times"][i] for i in idx]


def _old_composition(session, day, limit):
    """`choose_presented_indices` exactly as it was before N1."""
    return _pin_accepted_index(
        session, day,
        _pin_requested_time_index(
            session, day,
            _prefer_unheard_clock_times(
                session, day, _choose_presented_times(session, day, limit),
                limit, None,
            ),
            limit,
        ),
        limit,
    )


# ---------------------------------------------------------------------------
# The defect, through the live entry point
# ---------------------------------------------------------------------------
def test_the_exhibit_keeps_both_of_mondays_offered_times():
    """The 21:57 call, word for word. Before the fix: 10:30 / 11:20 / 14:40."""
    session = _after_the_spread()

    spoken = try_unspoken_followup_speech(session, "um what about monday")

    assert spoken, "the named-day producer did not take the turn"
    monday = _clocks_on(session, MON)
    assert {"08:00", "17:10"} <= set(monday), monday


def test_the_exhibit_reads_exactly_the_readout_limit():
    """lost_a_slot's rule, live: keeping two old times must not cost the caller
    a place in the readout."""
    session = _after_the_spread()
    try_unspoken_followup_speech(session, "um what about monday")

    assert len(_offered(session)) == LIMIT, _offered(session)


def test_the_readout_narrows_to_monday_only():
    """It is one day's readout: nothing from Tuesday or Wednesday rides along."""
    session = _after_the_spread()
    try_unspoken_followup_speech(session, "um what about monday")

    assert {d for d, _ in _offered(session)} == {MON}, _offered(session)


def test_the_extra_time_is_new_to_the_caller_on_every_day():
    """T1b's lesson is not undone: the place that is not a kept time goes to a
    clock time the caller has not heard on ANY day (S-2 still at work)."""
    session = _after_the_spread()
    try_unspoken_followup_speech(session, "um what about monday")

    extra = set(_clocks_on(session, MON)) - {"08:00", "17:10"}
    assert extra, _offered(session)
    assert not extra & HEARD_CLOCKS, (extra, HEARD_CLOCKS)


def test_the_readout_still_spans_the_day():
    """`_spread` (owner, 1 Sep 2026): a morning, an afternoon and an evening,
    not two slots fifty minutes apart."""
    session = _after_the_spread()
    try_unspoken_followup_speech(session, "um what about monday")

    parts = {part_of_day(f"{MON}T{c}:00") for c in _clocks_on(session, MON)}
    assert parts == {"morning", "afternoon", "evening"}, _offered(session)


def test_every_time_spoken_is_a_real_slot_on_that_day():
    """invented_a_slot's rule, live. A kept time is re-admitted by INDEX from
    three parallel arrays; a mis-index would name a slot the caller cannot book."""
    session = _after_the_spread()
    try_unspoken_followup_speech(session, "um what about monday")

    for date, clock in _offered(session):
        assert date == MON and clock in GRID, (date, clock)


def test_the_mechanism_fired(caplog):
    """A pass without its log line is not a pass (10 Sep 13:43, D8)."""
    session = _after_the_spread()
    with caplog.at_level(logging.INFO, logger="app.tools.slot_followup"):
        try_unspoken_followup_speech(session, "um what about monday")

    assert any("(N1)" in r.getMessage() for r in caplog.records), [
        r.getMessage() for r in caplog.records
    ]


@pytest.mark.parametrize("utterance", [
    "um what about monday",            # 21:57, live
    "how about monday",                # 23:10, live
    "can you tell me about monday",    # T1b, live
    "uh yeah check for monday please",
    "have you got anything on monday",
    "is monday free",
])
def test_every_wording_of_the_request_keeps_the_offered_times(utterance):
    """The fix is keyed on WHICH producer answered, not on a phrase -- so every
    request wording that reaches it gets the same answer."""
    session = _after_the_spread()

    assert try_unspoken_followup_speech(session, utterance), utterance
    assert {"08:00", "17:10"} <= set(_clocks_on(session, MON)), (
        utterance, _offered(session),
    )


def test_an_acceptance_of_the_day_keeps_them_too():
    """B-145 -- "yeah monday works" -- goes through the same producer. A caller
    who ACCEPTED Monday having heard 08:00 and 17:10 is the last person who
    should find both gone from the list that follows."""
    session = _after_the_spread()

    assert try_unspoken_followup_speech(session, "yeah monday works")
    assert {"08:00", "17:10"} <= set(_clocks_on(session, MON)), _offered(session)


def test_asking_about_a_second_day_keeps_that_days_offered_times():
    """The 21:57 caller's next line: "uh and what about tuesday". After the
    Monday readout the offer is single_day, so this drives the producer behind
    both named-day callers directly, as T1b does."""
    session = _after_the_spread()
    try_unspoken_followup_speech(session, "um what about monday")
    monday = set(_clocks_on(session, MON))

    speak_one_day_from_payload(session, session["available_days"], TUE, why="D-B")

    tuesday = set(_clocks_on(session, TUE))
    assert {"08:50", "16:20"} <= tuesday, tuesday
    assert len(tuesday) == LIMIT, tuesday
    assert not (tuesday - {"08:50", "16:20"}) & monday, (monday, tuesday)


def test_the_keypad_map_names_only_what_was_spoken():
    """D-B failure 3. The keypad is built from the offer the producer recorded,
    so a re-admitted time must be in the map AND in the sentence."""
    session = _after_the_spread()
    text = try_unspoken_followup_speech(session, "um what about monday")

    values = set((session.get("v3_dtmf_slot_map") or {}).values())
    assert values, "the keypad map was not rewritten"
    assert not any("September" in v for v in values), values
    for v in values:
        assert v in text, (v, text)


def test_it_still_says_there_are_others_that_day():
    """B-97. Keeping two old times still leaves nine unspoken; the readout must
    not sound like the whole day."""
    session = _after_the_spread()
    text = try_unspoken_followup_speech(session, "um what about monday")

    assert "a few others" in (text or "").lower(), text


def test_the_obs_row_names_its_producer():
    """The replay harness can only replay N1 honestly if the row says which
    producer spoke it. Gate 5 rows stay ""; absent means unknown."""
    session = _after_the_spread()
    try_unspoken_followup_speech(session, "um what about monday")
    row = (offers_block(session) or [])[-1]
    assert row["source"] == "producer", row
    assert row["producer"] == "D-B", row

    accepted = _after_the_spread()
    try_unspoken_followup_speech(accepted, "yeah monday works")
    assert (offers_block(accepted) or [])[-1]["producer"] == "B-145"

    gate5 = {}
    record_offer(gate5, payload_days=[_day(MON)], offer=None)
    assert (offers_block(gate5) or [{}])[-1].get("producer") == ""


# ---------------------------------------------------------------------------
# The other two questions -- must NOT change
# ---------------------------------------------------------------------------
def test_what_else_after_the_monday_readout_still_withholds_what_they_heard():
    """B-116's own question, on the day under discussion. After "what about
    monday" has been answered (08:00 / afternoon / 17:10), "what else have you
    got" must hand back NONE of those three -- the named-day flag belongs to the
    turn that asked about the day, never to the turn after it."""
    session = _after_the_spread()
    assert try_unspoken_followup_speech(session, "um what about monday")
    first = set(_clocks_on(session, MON))
    assert {"08:00", "17:10"} <= first, first

    assert try_unspoken_followup_speech(session, "what else have you got")

    offered = _offered(session)
    assert offered, offered
    assert not {c for d, c in offered if d == MON} & first, (first, offered)


@pytest.mark.xfail(strict=True, reason=(
    "N5, PRE-EXISTING -- verified on the untouched base 5bfdface, 11 Sep 2026. "
    "After a multi-day spread, 'what else have you got on monday' (and even "
    "'... on monday the 14th') is answered by more_days_speech with Thursday, "
    "Friday and Saturday: day_named_by_caller does not resolve a bare weekday, "
    "so the more-slots branch treats the request as unscoped. Same family as "
    "N4 -- the day the caller named is dropped. Recorded, not fixed here. "
    "strict=True: when it is fixed this XPASSes and fails, so the marker cannot "
    "outlive the defect."
))
def test_n5_what_else_on_a_named_day_stays_on_that_day():
    session = _after_the_spread()

    assert try_unspoken_followup_speech(session, "what else have you got on monday")

    assert {d for d, _ in _offered(session)} == {MON}, _offered(session)


def test_what_else_unscoped_still_gets_the_days_not_heard():
    """more_days_speech: after a three-day spread, "what else" is three MORE
    days -- Monday is not one of them."""
    session = _after_the_spread()

    assert try_unspoken_followup_speech(session, "what else have you got")
    assert MON not in {d for d, _ in _offered(session)}, _offered(session)


def test_a_refused_day_is_still_not_read_back():
    """B-147. "monday doesn't work" names Monday in order to RULE IT OUT; the
    named-day producer must not take it, with or without N1."""
    session = _after_the_spread()

    assert try_unspoken_followup_speech(session, "monday doesn't work")
    assert MON not in {d for d, _ in _offered(session)}, _offered(session)


def test_sooner_still_leads_a_named_day_with_its_earliest():
    """B-142. For a caller who asked for the soonest, the spare place goes to the
    day's EARLIEST time, not to the part of the day the kept times miss.

    Tuesday was offered at 08:50 and 16:20. The spread rule alone would fill
    with an evening; this caller asked for the soonest, and 08:00 is it."""
    session = _after_the_spread(day_preference="as soon as possible")

    speak_one_day_from_payload(session, session["available_days"], TUE, why="D-B")

    tuesday = _clocks_on(session, TUE)
    assert "08:00" in tuesday, tuesday
    assert {"08:50", "16:20"} <= set(tuesday), tuesday


def test_the_default_selection_is_byte_identical_to_before():
    """Every caller except the named-day producer passes nothing, and gets
    exactly the old wrapper chain -- the tool caps, the refusals, and the
    'what else on this day' readout."""
    session = _after_the_spread()
    for day in _payload():
        assert choose_presented_indices(session, day, LIMIT) == \
            _old_composition(session, day, LIMIT), day["date"]
        assert choose_presented_indices(session, day, 2) == \
            _old_composition(session, day, 2), day["date"]


# ---------------------------------------------------------------------------
# Where it must stand down
# ---------------------------------------------------------------------------
def test_a_day_named_cold_is_unchanged():
    """B-148. Thursday was never read out, so there is nothing to keep and the
    existing rules are already right for it."""
    session = _after_the_spread()
    thu = _day(THU)

    assert choose_presented_indices(session, thu, LIMIT, named_day=True) == \
        choose_presented_indices(session, thu, LIMIT)


def test_a_day_already_heard_in_full_is_left_to_b116():
    """Once the caller has had a full readout of the day, "what about Monday"
    again is closer to "what else" -- re-reading all of it would add nothing."""
    session = _after_the_spread()
    record_spoken_slots(session, _slots(MON, ["10:30", "13:00"]))
    mon = _day(MON)

    assert choose_presented_indices(session, mon, LIMIT, named_day=True) == \
        choose_presented_indices(session, mon, LIMIT)


def test_a_short_day_is_spoken_whole():
    """A day holding `limit` or fewer: everything, heard or not -- B-116's
    "never starves a repeat", unchanged."""
    session = _after_the_spread()
    short = _day(MON, ["08:00", "12:10", "17:10"])

    assert choose_presented_indices(session, short, LIMIT, named_day=True) == [0, 1, 2]


def test_a_desynchronised_day_is_not_reordered():
    """A day that cannot prove its arrays are parallel gets a chronological
    readout. Re-admitting an index into mismatched arrays would name a slot
    the caller cannot book."""
    session = _after_the_spread()
    broken = _day(MON)
    broken["slot_times_spoken"] = broken["slot_times_spoken"][:-1]

    assert choose_presented_indices(session, broken, LIMIT, named_day=True) == [0, 1, 2]


def test_a_single_place_readout_is_untouched():
    """limit 1 has no spare place to fill after a kept time; stand down."""
    session = _after_the_spread()
    mon = _day(MON)

    assert choose_presented_indices(session, mon, 1, named_day=True) == \
        choose_presented_indices(session, mon, 1)


def test_nothing_heard_at_all_is_unchanged():
    """The first lookup of a call has no spoken record to keep from."""
    session = {"available_days": _payload()}
    mon = _day(MON)

    assert choose_presented_indices(session, mon, LIMIT, named_day=True) == \
        choose_presented_indices(session, mon, LIMIT)


def test_a_requested_time_still_wins_a_place():
    """D8 runs AFTER N1: a caller who names a time gets it, even at the cost of
    one of the kept times."""
    session = _after_the_spread()

    speak_one_day_from_payload(
        session, session["available_days"], MON, why="D-B",
        user_text="what about monday at one in the afternoon",
    )

    monday = _clocks_on(session, MON)
    assert "13:00" in monday, monday
    assert "08:00" in monday, monday
    assert len(monday) == LIMIT, monday


def test_it_never_raises_on_junk():
    """A readout preference must never fail a lookup."""
    day = _day(MON)
    for session, d, chosen, limit in (
        (None, day, [0, 1, 2], 3),
        ({}, None, [0], 3),
        (_after_the_spread(), {"slots": "nonsense"}, [0], 3),
        (_after_the_spread(), day, "nonsense", 3),
        (_after_the_spread(), day, [99, -4], 3),
        ({"_spoken_slot_starts": 12}, day, [0, 1, 2], 3),
    ):
        _keep_times_heard_on_named_day(session, d, chosen, limit)
    assert choose_presented_indices({}, day, LIMIT, named_day=True)


# ---------------------------------------------------------------------------
# Wiring -- only ONE caller may say "named day"
# ---------------------------------------------------------------------------
def _calls_passing_named_day():
    """(file, function) for every call in app/ passing named_day=True. AST, so
    a docstring that quotes the keyword cannot count; substring pre-filter so
    flow.py is never parsed for nothing (S-5 census, 26 min -> 4 s)."""
    root = Path(__file__).resolve().parents[2] / "app"
    hits = []
    for path in root.rglob("*.py"):
        src = path.read_text(encoding="utf-8", errors="replace")
        if "named_day" not in src:
            continue
        tree = ast.parse(src)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and any(
                    k.arg == "named_day"
                    and isinstance(k.value, ast.Constant) and k.value.value is True
                    for k in node.keywords
                ):
                    hits.append((path.name, fn.name))
    return hits


def test_only_the_named_day_producer_passes_the_flag():
    """If the tool caps, a refusal or the 'what else on this day' readout ever
    passed it, B-116 would stop answering "what else" for everyone."""
    assert set(_calls_passing_named_day()) == {
        ("slot_followup.py", "speak_one_day_from_payload"),
    }, _calls_passing_named_day()
