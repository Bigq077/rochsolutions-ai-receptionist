"""S-11 — the [LAT] row records whether the token stream FLOWED or STALLED.

`SLOT_PRESENTATION_FINISH_2026-09-10.md` §4.1 costed the obvious fix for S-11
(watch the first CHUNK, not the first token) at three deadlines and found it a
bad trade at all of them: at 3000ms it buys 76% of the harm and makes 19.6% of
ALL turns newly speak, 175 of them within 300ms of the content. It closes by
naming the predicate that would separate the two populations — **time since the
LAST token**: a streaming turn will release soon, a stalled one will not — and
then parks the whole thing:

    "The corpus does not store inter-token times, so it cannot be measured, and
     shipping an unmeasurable predicate onto the hot path is the trap this
     codebase keeps falling into. Left open deliberately."

These fields are that measurement. **No predicate ships on them** — the ladder
does not read them and no deadline moved — so what needs protecting is the
MEASUREMENT's meaning, which is where the last three of these tests aim.

The distinctions that make the field worth having, all of which a careless
reader collapses:

    no tokens at all        token_count 0, last_token_ms -1, max_gap -1
    exactly ONE token       token_count 1, last_token_ms set, max_gap **-1**
                            -- there was no second token to measure against,
                               which is NOT a gap of zero
    a steady stream         max_gap small   -> predicate stays silent, no cost
    a genuine stall         max_gap large   -> predicate fires, real benefit
    a tool round trip       NOT counted     -> or every tool turn looks stalled

See `SCOPE_STALL_LADDER_2026-09-12.md` §4.
"""
from __future__ import annotations

from app.media_streams.latency_timing import TurnTiming


def _turn(**kw):
    return TurnTiming(turn_seq=1, t0=0.0, t_dispatch=0.0, call_sid="CAtest", **kw)


# ── not observed vs observed ────────────────────────────────────────────────

def test_a_turn_with_no_tokens_reports_zero_and_two_minus_ones():
    """0 tokens is a REAL reading; the two timings are not measurements."""
    rec = _turn().as_record()
    assert rec["token_count"] == 0
    assert rec["last_token_ms"] == -1
    assert rec["max_inter_token_ms"] == -1


def test_one_token_has_no_gap_and_that_is_not_a_gap_of_zero():
    """The distinction this test exists for.

    A single-token turn has no inter-token interval at all. Reporting 0 would
    score it as a perfectly steady stream — the exact opposite of "unknown" —
    and a cost estimate built on that would count every one-token turn as
    evidence that a predicate stays silent.
    """
    t = _turn()
    t.note_token(now=1.0)
    rec = t.as_record()
    assert rec["token_count"] == 1
    assert rec["last_token_ms"] == 1000
    assert rec["max_inter_token_ms"] == -1


# ── the measurement itself ─────────────────────────────────────────────────

def test_the_largest_gap_wins_not_the_last_one():
    t = _turn()
    for now in (1.0, 1.25, 5.5, 5.75, 6.0):  # one 4.25s stall among 250ms ticks
        t.note_token(now=now)
    rec = t.as_record()
    assert rec["token_count"] == 5
    assert rec["max_inter_token_ms"] == 4250
    assert rec["last_token_ms"] == 6000


def test_a_steady_stream_and_a_stalled_one_are_distinguishable():
    """The whole point: these two turns must not produce the same row.

    Both reach their last token at 3.0s, so `last_token_ms` alone cannot tell
    them apart — which is why the gap is stored as well as the timing.
    """
    steady = _turn()
    for i in range(1, 25):
        steady.note_token(now=i * 0.125)     # 125ms apart, ends at 3.0s

    stalled = _turn()
    stalled.note_token(now=0.125)
    stalled.note_token(now=3.0)             # one 2.875s hole

    assert steady.as_record()["last_token_ms"] == 3000
    assert stalled.as_record()["last_token_ms"] == 3000
    assert steady.as_record()["max_inter_token_ms"] < 200
    assert stalled.as_record()["max_inter_token_ms"] == 2875


def test_a_tool_round_trip_is_not_scored_as_the_model_going_quiet():
    """Without note_stream_break every tool turn carries Acuity's round trip as
    a `max_inter_token_gap`, and the flowing-vs-stalled split becomes noise."""
    # Quarter-seconds throughout: exact in binary, so the assertion cannot fail
    # on float representation. `as_record` TRUNCATES (int(), as `d` does), so a
    # gap written as 1.2 - 1.0 reads 199ms, not 200 — real, harmless, and not
    # something a test should pin.
    t = _turn()
    t.note_token(now=1.0)
    t.note_token(now=1.25)
    t.note_stream_break()                   # tool call: 4 seconds pass
    t.note_token(now=5.25)
    t.note_token(now=5.5)
    rec = t.as_record()
    # The 4s tool wait is absent; only the real inter-token gaps are measured.
    assert rec["max_inter_token_ms"] == 250
    # ...and the turn still owns every token it produced.
    assert rec["token_count"] == 4
    assert rec["last_token_ms"] == 5500


# ── conventions the rest of the record already keeps ───────────────────────

def test_note_token_is_last_write_wins_unlike_stamp():
    """`t1` answers when the stream STARTED and is first-write-wins; this
    answers whether it is STILL GOING, so a later call must move it."""
    t = _turn()
    t.note_token(now=1.0)
    t.note_token(now=2.0)
    assert t.as_record()["last_token_ms"] == 2000
    t.stamp("t1", now=1.0)
    t.stamp("t1", now=9.0)
    assert t.as_record()["llm_ttft_ms"] == 1000     # first write still stands


def test_a_monotonic_clock_reading_cannot_leak_through_as_a_duration():
    """The defect `d`'s docstring records — 1,934 of 3,066 stored turns carrying
    `ttfa_ms=5831410519` — reached the table because a raw monotonic value is
    indistinguishable from a reading. `max_inter_token_ms` takes a duration
    rather than a subtraction, so it needs the same ceiling in its own right.
    """
    t = _turn()
    t.note_token(now=0.0)
    t.note_token(now=600_000.0)             # ~7 days: never a real gap
    assert t.as_record()["max_inter_token_ms"] == -1


def test_the_lat_line_carries_the_new_fields():
    """Stored-only was the S-9 mistake: the tool-vs-plain split was answerable
    solely from the obs corpus, and a read-only OBS_DATABASE_URL is the standing
    blocker. The Render log must be enough to collect S-11 evidence."""
    t = _turn()
    t.note_token(now=1.0)
    t.note_token(now=3.0)
    rec = t.as_record()
    for key in ("tool_calls", "token_count", "last_token_ms", "max_inter_token_ms"):
        assert key in rec
    # emit() formats the line FROM as_record, so a missing key would raise here.
    t.emit()
