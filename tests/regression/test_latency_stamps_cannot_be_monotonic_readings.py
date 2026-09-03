"""An unstamped clock must read as "not measured", never as a duration.

`TurnTiming.t0` is typed `float`, not `Optional[float]`, so a caller with no
clock reading passes 0 — and `as_record`'s old `d()` returned `t4 - 0`, which is
`time.monotonic()`: seconds since boot, rendered as a plausible integer.

Measured against the obs store on 2026-09-02: **1,934 of 3,066 stored turns**
carried exactly that, e.g. `ttfa_ms=5831410519` (~67 days), concentrated on
22-23 August. Nothing downstream could tell them from real readings, so every
percentile ever taken from `calls.latency` was silently wrong — including the
`scripted` median, which printed as 11,855,281,945ms.

Both halves are pinned here: the emitter must not produce them, and the parser
must not consume the ones already stored.
"""
import time

import pytest

from app.media_streams.latency_timing import TurnTiming, _MAX_PLAUSIBLE_MS

import lat_parse


def _turn(**kw):
    now = time.monotonic()
    t = TurnTiming(turn_seq=1, t0=kw.pop("t0", now), t_dispatch=now)
    for k, v in kw.items():
        setattr(t, k, v)
    return t, now


# ── the emitter ──────────────────────────────────────────────────────────────

def test_unstamped_t0_reads_as_not_measured():
    """The exact shape of the 1,934 corrupt rows."""
    t, now = _turn(t0=0.0)
    t.t4 = now + 1.2
    t.content_t4 = now + 1.2
    rec = t.as_record()
    assert rec["ttfa_ms"] == -1
    assert rec["content_ttfa_ms"] == -1


def test_a_real_turn_still_measures():
    # +/-1ms, not exact equality. `now` is time.monotonic(), which on a machine
    # that has been up a while is a large float -- 534,513s when this was
    # found. At that magnitude float64 cannot hold `now + 1.2` exactly, so the
    # delta comes back 1.19999999995 and as_record's int() truncation reads
    # 1199. The assertion therefore failed as a function of MACHINE UPTIME:
    # green after a reboot, red a week later, with no code change. It cost a
    # baseline of 98 -> 99 and an investigation that started by suspecting an
    # unrelated commit.
    #
    # The production behaviour is right and is not being changed to suit a
    # test: losing at most 1ms off a latency figure is immaterial, and int()
    # truncating rather than rounding is a deliberate reading of "how long did
    # this take". The exactness was the test's mistake.
    t, now = _turn()
    t.t4 = now + 1.2
    t.content_t4 = now + 2.0
    rec = t.as_record()
    assert abs(rec["ttfa_ms"] - 1200) <= 1
    assert abs(rec["content_ttfa_ms"] - 2000) <= 1


@pytest.mark.parametrize("delta", [-5.0, 601.0, 5_000_000.0])
def test_implausible_durations_are_refused(delta):
    """A turn does not take ten minutes, and cannot take negative time."""
    t, now = _turn()
    t.t4 = now + delta
    assert t.as_record()["ttfa_ms"] == -1


def test_zero_is_a_real_reading_and_survives():
    """0ms is a measurement; only the sentinel -1 means "never reached"."""
    t, now = _turn()
    t.content_t3 = t.content_t4 = now + 1.0
    t.t2 = now + 1.0
    assert t.as_record()["audio_wire_ms"] == 0


# ── the parser, for rows already in the table ────────────────────────────────

def test_parser_discards_stored_monotonic_readings_and_says_so():
    before = lat_parse.DISCARDED["count"]
    s = lat_parse.summarize([1200, 1400, 5831410519, 1600])
    assert s["n"] == 3, "the monotonic reading must not reach the percentile"
    assert s["p50"] == 1400
    assert s["discarded"] == 1
    assert lat_parse.DISCARDED["count"] == before + 1, "discards must be reportable"


def test_parser_ceiling_matches_the_emitter():
    """One rule, two enforcement points — they must not drift apart."""
    assert lat_parse.MAX_PLAUSIBLE_MS == _MAX_PLAUSIBLE_MS


# ── voice-to-voice: the metric the bar should be read against ────────────────

def test_voice_to_voice_sums_per_turn_not_per_percentile():
    recs = [
        {"endpoint_wait_ms": 1000, "ttfa_ms": 2000},
        {"endpoint_wait_ms": 500,  "ttfa_ms": 1000},
    ]
    assert sorted(lat_parse._v2v(recs, "ttfa_ms")) == [1500, 3000]


def test_voice_to_voice_skips_turns_missing_either_half():
    recs = [
        {"endpoint_wait_ms": -1,   "ttfa_ms": 2000},
        {"endpoint_wait_ms": 1000, "ttfa_ms": -1},
        {"endpoint_wait_ms": 900,  "ttfa_ms": 1100},
    ]
    assert lat_parse._v2v(recs, "ttfa_ms") == [2000]
