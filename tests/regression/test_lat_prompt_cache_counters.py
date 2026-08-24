"""B2 — the [LAT] line now carries Anthropic's prompt-cache counters.

The first LLM turn of a call runs ~0.7-1.0s slower than later turns on the same
call (`CAd075ea9673`: 2333 vs ~1631; `CAfcb3130c`: 2744 vs ~1753). Two
explanations fit that shape — a cold httpx pool, or the 5-minute prompt cache
expiring between sparse calls — and picking between them by argument is how the
wrong lever gets pulled. `llm_stream` sends the ~27K-token system block with
`cache_control: ephemeral`, and the API reports per-call what that cache did;
nothing was logging it, so hit/miss was inferred rather than known.

The distinction these tests exist to protect:

    None -> -1   the counter was NOT OBSERVED
    0    ->  0   the counter WAS observed and the cache was COLD

Collapsing those two makes the field worthless — a cold cache is precisely the
reading we are hunting, and it must not be indistinguishable from "no data".
The rest of the record already uses -1 for "stage not reached"; this keeps that
convention while carving out real zero.
"""
from __future__ import annotations

import logging

from app.media_streams.latency_timing import TurnTiming


def _turn(**kw):
    return TurnTiming(turn_seq=1, t0=0.0, t_dispatch=0.0, call_sid="CAtest", **kw)


def test_unobserved_counters_report_minus_one():
    rec = _turn().as_record()
    assert rec["cache_read_tokens"] == -1
    assert rec["cache_write_tokens"] == -1
    assert rec["prompt_input_tokens"] == -1


def test_an_observed_cold_cache_is_zero_not_minus_one():
    """The reading we are actually hunting. A cold cache is 0, not "missing"."""
    rec = _turn(cache_read_tokens=0, cache_write_tokens=27000).as_record()
    assert rec["cache_read_tokens"] == 0
    assert rec["cache_write_tokens"] == 27000


def test_a_warm_cache_is_reported_as_read():
    rec = _turn(cache_read_tokens=27000, cache_write_tokens=0).as_record()
    assert rec["cache_read_tokens"] == 27000
    assert rec["cache_write_tokens"] == 0


def test_emit_formats_the_counters(caplog):
    """The log line and the stored record must not drift — emit formats FROM
    as_record(), so a field added to one and not the other raises KeyError."""
    t = _turn(cache_read_tokens=27000, cache_write_tokens=0, prompt_input_tokens=412)
    with caplog.at_level(logging.INFO, logger="susie.latency"):
        t.emit()
    line = "\n".join(r.getMessage() for r in caplog.records)
    assert "cache_read=27000" in line
    assert "cache_write=0" in line
    assert "in_tok=412" in line


def test_emit_survives_unobserved_counters(caplog):
    """A turn that never saw a message_start must still emit a valid line."""
    with caplog.at_level(logging.INFO, logger="susie.latency"):
        _turn().emit()
    line = "\n".join(r.getMessage() for r in caplog.records)
    assert "cache_read=-1" in line
    assert "[LAT] turn_seq=1" in line


def test_emit_is_still_idempotent():
    t = _turn(cache_read_tokens=5)
    t.emit()
    assert t._emitted is True
    t.emit()  # must not raise


# ── the producer side: reading one counter off an Anthropic usage object ────
#
# Extracted to a module-level function precisely so it can be tested. Inside
# _one_streaming_call it was a closure, and an untestable branch here is how a
# silent permanent "cold cache" reading would ship.

from app.media_streams.llm_stream import _usage_token


class _Usage:
    cache_read_input_tokens = 27000
    cache_creation_input_tokens = None      # present, but nothing written
    input_tokens = 412


def test_a_present_integer_is_returned():
    assert _usage_token(_Usage(), "cache_read_input_tokens") == 27000
    assert _usage_token(_Usage(), "input_tokens") == 412


def test_present_but_none_is_a_real_zero():
    """"The call cached nothing" is a reading, not a gap."""
    assert _usage_token(_Usage(), "cache_creation_input_tokens") == 0


def test_a_missing_attribute_is_none_not_zero():
    """The guard that matters. If the SDK renames a field, this must report
    "not observed" (-1) — never 0, which would be indistinguishable from a
    permanently cold cache and would quietly confirm the hypothesis B2 exists
    to test."""
    assert _usage_token(_Usage(), "renamed_by_a_future_sdk") is None


def test_an_unparseable_value_is_not_observed():
    class Odd:
        input_tokens = "not-a-number"
    assert _usage_token(Odd(), "input_tokens") is None


def test_the_sdk_still_reports_the_fields_this_depends_on():
    """Pins the real dependency. If the Anthropic SDK drops or renames these,
    the counters silently go to -1 and B2 becomes unmeasurable again — this
    fails loudly instead."""
    from anthropic.types import Usage
    for field in (
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "input_tokens",
    ):
        assert field in Usage.model_fields, f"anthropic SDK no longer reports {field}"
