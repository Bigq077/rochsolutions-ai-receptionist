"""
Regression: Susie confirmed a slot the diary has never held, three times.

`CA7ebc00839bf773bcf7cbaa52d7c60f7e` (11 Sep 2026 00:04, northgate, build
66f7dec3). The caller said "10 to 12 works". Susie replied:

    "So that's Monday the 14th of September at twenty to twelve —
     could I take your first name and surname?"

and said it again on each of the next two turns. Twenty to twelve is 11:40.
northgate runs a 50-minute grid from 08:00 — 08:00, 08:50, 09:40, 10:30, 11:20,
12:10, 13:00 — so 11:40 is not bookable on any day, in any diary, at that
clinic. There was no offer behind the sentence: the turn ran no tool, so Gate 5,
the reverse-parse layer and every read-back guard were somewhere else.

WHAT THE FIX IS. `app/tools/slot_fact_guard.py` reads every outgoing chunk in
`_tts_loop` and asks one question of the data: does this sentence name a clock
time that no payload on this call holds. It is invariant 1 of
`docs/plan/SLOT_PRESENTATION_SPEC.md` and the only one enforceable without
touching a selection rule.

WHAT THE FIX IS NOT, asserted below so nobody has to re-derive the boundary.
The FIRST defect on that same call —

    "the nearest to twelve o'clock is twenty to three on Monday the 14th, or
     twenty past four on Tuesday the 15th"

— named 14:40 and 16:20. Both are on northgate's grid and both are genuinely
bookable, so the guard passes them, correctly. Answering "as close as possible
to 12" with a quarter to three is a RELEVANCE failure (invariant 4) and belongs
to the decision table. A guard believed to cover selection is worse than no
guard.

THE FALSE-POSITIVE SURFACE is the whole risk here, because in `enforce` mode a
false positive replaces a sentence a caller needed. Every exemption below is a
real shape from the prompts or the corpus: a negated clause, opening hours, an
appointment duration, a window the caller named, and the caller's own requested
time echoed back per Step 5's out-of-window rule.
"""
from __future__ import annotations

import logging

import pytest

from app.tools import slot_fact_guard as guard

# northgate's real grid, and the times that are NOT on it.
GRID = ["08:00", "08:50", "09:40", "10:30", "11:20", "12:10", "13:00",
        "13:50", "14:40", "15:30", "16:20"]
MON, TUE = "2026-09-14", "2026-09-15"


def _spoken(hhmm: str) -> str:
    from app.tools.receptionist_tools import _spoken_slot_time
    return _spoken_slot_time(hhmm)


def _day(date: str, label: str, times: list[str]) -> dict:
    return {
        "date": date,
        "day_label": label,
        "slot_times": list(times),
        "slot_times_spoken": [_spoken(t) for t in times],
        "slots": [{"start": f"{date}T{t}:00"} for t in times],
    }


def _session(days=None) -> dict:
    return {
        "clinic_id": "northgate",
        "available_days": days if days is not None else [
            _day(MON, "Monday 14th September", GRID),
            _day(TUE, "Tuesday 15th September", GRID),
        ],
    }


@pytest.fixture(autouse=True)
def _default_mode(monkeypatch):
    """Every test states its own mode. Unset means `log`, which is the default
    the code ships with, so the fixture asserts that rather than assuming it."""
    monkeypatch.delenv("SLOT_FACT_GUARD", raising=False)
    assert guard.guard_mode() == guard.MODE_LOG


# ── the exhibit ────────────────────────────────────────────────────────────

def test_the_confirmation_that_named_eleven_forty_is_caught():
    s = _session()
    v = guard.check_outgoing(
        s,
        "So that's Monday the 14th of September at twenty to twelve — "
        "could I take your first name and surname?",
    )
    assert [x["kind"] for x in v.violations] == ["unknown_time"]
    assert v.violations[0]["candidates"] == ["11:40", "23:40"]
    # The phrase is the FOLDED form -- "20 to 12". Folding changes lengths, so
    # there is no honest mapping back to the original offsets; the full
    # sentence is logged verbatim next to it for the operator.
    assert v.violations[0]["phrase"] == "20 to 12"


def test_in_log_mode_the_words_are_unchanged():
    """The default must observe and not intervene: this same commit reaches
    three live patient lines by fast-forward."""
    s = _session()
    text = "So that's Monday the 14th of September at twenty to twelve."
    v = guard.check_outgoing(s, text)
    assert v.text == text
    assert v.violations
    assert not v.blocked


def test_in_enforce_mode_the_sentence_is_replaced_and_the_turn_stops(monkeypatch):
    monkeypatch.setenv("SLOT_FACT_GUARD", "enforce")
    s = _session()
    v = guard.check_outgoing(
        s, "So that's Monday the 14th of September at twenty to twelve.")
    assert v.text == guard.RECOVERY_SENTENCE
    assert v.blocked
    # No time, no day, no promise — the recovery line cannot itself be a
    # violation, or the guard would loop on its own output.
    assert guard.check_outgoing(_session(), guard.RECOVERY_SENTENCE).clean

    # The rest of the paragraph is dropped: a caller must not hear the tail of
    # an offer that has just been retracted.
    tail = guard.check_outgoing(s, "Could I take your first name and surname?")
    assert tail.text == ""
    assert tail.blocked

    # ...until the next caller turn.
    guard.turn_boundary(s)
    again = guard.check_outgoing(s, "Could I take your first name and surname?")
    assert again.text == "Could I take your first name and surname?"


# ── the boundary: what it must NOT claim to catch ──────────────────────────

def test_a_bookable_time_answered_to_the_wrong_request_is_not_this_guards_business():
    """Defect 1 of the same call. 14:40 and 16:20 are real, and answering
    "closest to 12" with them is invariant 4, not invariant 1.

    The caller's own utterance is folded in first, exactly as the wiring does
    on a live turn -- without it the echoed "twelve o'clock" is itself a
    violation, because northgate holds 12:10 and not 12:00. That dependency is
    the reason `note_caller_speech` is wired at the transcript boundary and not
    left to the producers."""
    s = _session()
    guard.note_caller_speech(s, "as close as possible to 12 please")
    v = guard.check_outgoing(
        s,
        "The nearest to twelve o'clock is twenty to three on Monday the 14th, "
        "or twenty past four on Tuesday the 15th.",
    )
    assert not v.violations


def test_every_label_the_generator_emits_for_a_real_slot_passes():
    """The strongest available statement about the false-positive rate: the
    generator's own wording for every bookable time on the grid, in a
    sentence shaped like a readout, must be clean."""
    s = _session()
    for hhmm in GRID:
        text = f"Monday the 14th of September — {_spoken(hhmm)}. Does that work?"
        v = guard.check_outgoing(s, text)
        assert not v.violations, (hhmm, text, v.violations)


# ── the exemptions, each a real shape ──────────────────────────────────────

def test_a_time_susie_says_she_does_not_have_is_not_an_offer():
    """B-139's clause split, reused. "Monday doesn't have twenty to twelve" is
    an honest sentence about a time the diary lacks — which is the entire
    point of saying it."""
    s = _session()
    v = guard.check_outgoing(
        s, "Monday doesn't have twenty to twelve, I'm afraid.")
    assert not v.violations


def test_opening_hours_are_not_a_claim_about_a_slot():
    s = _session()
    for text in (
        "We're open from nine in the morning until half past five.",
        "Our opening hours are eight till six on weekdays.",
        "The clinic closes at half past seven on a Thursday.",
    ):
        v = guard.check_outgoing(s, text)
        assert not v.violations, (text, v.violations)


def test_a_window_the_caller_named_is_not_a_slot_time():
    """Step 5's out-of-window rule REQUIRES naming the window before the
    alternative. "from half five to nine" has "five to nine" inside it, which
    is a perfectly good 08:55 to a regex and nothing at all to a listener."""
    s = _session()
    v = guard.check_outgoing(
        s, "I haven't got anything from half five to nine — I do have half past four.")
    assert not v.violations


def test_a_duration_is_not_a_clock_time():
    s = _session()
    for text in (
        "The appointment runs about fifty minutes.",
        "I'll need about ten minutes to get that sorted.",
    ):
        assert not guard.check_outgoing(s, text).violations, text


def test_a_time_the_caller_asked_for_may_be_named_back():
    """The caller said "around eleven forty". Echoing it while offering a real
    alternative is honest, and the alternative is what has to be bookable."""
    s = _session()
    guard.note_caller_speech(s, "have you got anything around 11:40")
    v = guard.check_outgoing(
        s, "I haven't got 11:40 — the closest is twenty past eleven. Does that work?")
    assert not v.violations


def test_a_date_is_never_read_as_a_time():
    """B-126 and D8, one layer down. "Monday the 14th of September" holds a 14
    and a 9 and this repo has twice turned a date into a time."""
    s = _session()
    v = guard.check_outgoing(
        s, "That's Monday the 14th of September, and Tuesday the 15th.")
    assert not v.violations
    assert guard.spoken_time_mentions("Monday the 14th of September") == []


def test_nothing_is_judged_before_the_first_lookup():
    """The guard's whole authority is the payload. With none, it has no
    opinion — and must not acquire one from the absence."""
    s = _session(days=[])
    v = guard.check_outgoing(
        s, "So that's Monday the 14th of September at twenty to twelve.")
    assert v.clean
    assert v.text.startswith("So that's")


# ── the cumulative set, and the wrong-day severity ─────────────────────────

def test_a_band_filtered_refetch_does_not_retract_what_was_bookable():
    """`available_days` is one fetch and may be a filtered VIEW of a day. A
    later mornings-only payload must not make an afternoon slot the caller was
    already offered into a violation — absence from one fetch is not absence
    from the diary (B-102's sentence)."""
    s = _session()
    guard.check_outgoing(s, "Monday the 14th — twenty to three in the afternoon.")
    s["available_days"] = [_day(MON, "Monday 14th September", ["08:00", "08:50"])]
    v = guard.check_outgoing(
        s, "Monday the 14th — twenty to three in the afternoon. Still free?")
    assert not v.violations


def test_a_real_time_on_the_wrong_day_is_recorded_and_never_enforced(monkeypatch):
    """Tuesday holds 09:40; a Monday that does not must not be told it does.
    Recorded only: attributing a day to a clause is reverse-parsing, and this
    project has measured that as unreliable."""
    monkeypatch.setenv("SLOT_FACT_GUARD", "enforce")
    s = _session(days=[
        _day(MON, "Monday 14th September", ["08:00", "08:50"]),
        _day(TUE, "Tuesday 15th September", ["09:40"]),
    ])
    v = guard.check_outgoing(
        s, "Monday the 14th of September — twenty to ten in the morning.")
    assert not v.violations
    assert [w["kind"] for w in v.warnings] == ["wrong_day"]
    assert v.warnings[0]["day"] == MON
    assert v.text.startswith("Monday")       # spoken anyway
    assert not v.blocked


# ── mode plumbing and fail-open ────────────────────────────────────────────

def test_off_does_no_work(monkeypatch):
    monkeypatch.setenv("SLOT_FACT_GUARD", "off")
    s = _session()
    v = guard.check_outgoing(
        s, "So that's Monday the 14th at twenty to twelve.")
    assert v.clean and v.text.startswith("So that's")


def test_an_unrecognised_mode_reads_as_log_not_enforce(monkeypatch):
    """A typo in a Render env var must not start replacing live speech."""
    monkeypatch.setenv("SLOT_FACT_GUARD", "enforced")
    assert guard.guard_mode() == guard.MODE_LOG
    monkeypatch.setenv("SLOT_FACT_GUARD", "ENFORCE")
    assert guard.guard_mode() == guard.MODE_ENFORCE


@pytest.mark.parametrize("session", [None, "not a session", 42, {}])
def test_a_malformed_session_speaks_the_words_anyway(session):
    """Fail open, always. This sits between the words and the caller."""
    v = guard.check_outgoing(session, "So that's Monday at twenty to twelve.")
    assert v.text == "So that's Monday at twenty to twelve."
    assert not v.blocked


def test_a_violation_is_greppable_in_the_render_log(caplog):
    """The log line is the primary instrument: there is no `calls` column for
    these rows yet, and this repo's only proof of what production is doing is a
    greppable line in the Render log."""
    s = _session()
    with caplog.at_level(logging.ERROR, logger="app.tools.slot_fact_guard"):
        guard.check_outgoing(
            s, "So that's Monday the 14th of September at twenty to twelve.")
    assert any("[slot_guard]" in r.message or "[slot_guard]" in r.getMessage()
               for r in caplog.records)


# ── the wiring ─────────────────────────────────────────────────────────────
#
# `app/obs/slot_offers.py`'s own docstring names the failure these cover: "a fix
# whose call-site wiring was never exercised", one of three defects in the week
# to 3 Sep that a phone call found and 7,945 tests did not. A guard nobody calls
# is worth less than no guard, because it is believed in.

def test_the_tts_loop_calls_the_guard_after_every_suppression_check():
    import inspect
    from app.media_streams.connection import WebSocketCallHandler

    src = inspect.getsource(WebSocketCallHandler._tts_loop)
    assert "slot_fact_guard" in src
    assert "check_outgoing" in src
    assert "turn_boundary" in src
    # Order is the contract: the dedup guard's `continue` must run first, or the
    # guard logs violations for speech nobody heard, and the obs transcript
    # must be written after, or it claims words that were replaced.
    assert src.index("_last_tts_chunk = chunk_text.strip()") < src.index("check_outgoing")
    assert src.index("check_outgoing") < src.index("record_assistant")


def test_the_callers_own_times_are_folded_in_at_turn_start():
    import inspect
    from app.media_streams import llm_stream

    src = inspect.getsource(llm_stream)
    assert "note_caller_speech" in src
    # Before the reply is built, not beside `record_user` at turn end.
    assert src.index("note_caller_speech") < src.index("fp_result = try_fast_path")


def test_the_rows_survive_on_the_session_for_the_call_record():
    s = _session()
    assert guard.guard_block(s) is None      # never checked != checked and clean
    guard.check_outgoing(s, "So that's Monday the 14th at twenty to twelve.")
    rows = guard.guard_block(s)
    assert rows and rows[0]["violations"][0]["phrase"]
