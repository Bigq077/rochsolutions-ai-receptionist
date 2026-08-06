"""
tests/obs/test_cost.py
----------------------
Unit tests for app/obs/cost.py. Pure arithmetic — no DB, no network.

Spec: docs/plan/COST_ROLLUP_SPEC.md §6.

The test that actually matters is test_capture_survives_a_cost_bug: everything
else here is arithmetic, but that one pins the invariant that a pricing bug must
never cost us a call record.
"""
from __future__ import annotations

import pytest

from app.obs import cost as cost_mod


# Deterministic rates for arithmetic tests. Chosen as round numbers so expected
# values can be hand-checked in the assertions rather than recomputed by the
# same code under test.
_TEST_RATES = {
    "twilio_inbound_per_min": 0.01,      # $0.01/min
    "twilio_stream_per_min": 0.01,       # $0.01/min  -> $0.02/min combined
    "assemblyai_per_hour": 0.36,         # $0.36/hr   -> $0.0001/s
    "elevenlabs_per_1k_chars": 0.10,     # $0.10/1k chars
    "llm_input_per_1m_tokens": 3.00,
    "llm_output_per_1m_tokens": 15.00,
    "usd_gbp": 0.50,                     # 1 USD = £0.50 -> 50 pence
}


@pytest.fixture
def rates(monkeypatch):
    """Install known rates and a version, restored automatically."""
    for k, v in _TEST_RATES.items():
        monkeypatch.setitem(cost_mod.RATES, k, v)
    monkeypatch.setattr(cost_mod, "RATE_TABLE_VERSION", "test-1")
    return _TEST_RATES


def _transcript(*pairs):
    return [{"role": r, "text": t} for r, t in pairs]


# ---------------------------------------------------------------------------
# Guard: the module refuses to invent numbers before the invoices are in
# ---------------------------------------------------------------------------

def test_rates_are_unconfigured_by_default():
    """Shipping placeholders must not silently produce plausible-looking costs."""
    assert cost_mod.rates_configured() is False
    result = cost_mod.estimate_call_cost(duration_s=240, transcript=None)
    assert result["total_pence"] is None
    assert result["error"] == "rates_not_configured"


def test_unconfigured_returns_none_not_zero():
    """None means 'not costed'; 0 would be a real (and wrong) cost of nothing."""
    assert cost_mod.estimate_call_cost(60, _transcript(("assistant", "hi")))["total_pence"] is None


# ---------------------------------------------------------------------------
# Twilio: per STARTED minute
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "duration_s,expected_minutes",
    [(1, 1), (59, 1), (60, 1), (61, 2), (120, 2), (121, 3), (240, 4)],
)
def test_twilio_bills_whole_started_minutes(rates, duration_s, expected_minutes):
    """61s bills 2 minutes, not 1.02. This is the single most common costing bug."""
    result = cost_mod.estimate_call_cost(duration_s, transcript=None)
    # $0.02/min * 50 pence/$ = 1 pence per minute
    assert result["breakdown"]["twilio"] == expected_minutes * 1


def test_zero_duration_costs_no_twilio(rates):
    assert cost_mod.estimate_call_cost(0, None)["breakdown"]["twilio"] == 0


# ---------------------------------------------------------------------------
# AssemblyAI: audio duration, not speech duration
# ---------------------------------------------------------------------------

def test_assemblyai_prices_on_call_duration(rates):
    # 3600s at $0.36/hr = $0.36 = 18 pence
    assert cost_mod.estimate_call_cost(3600, None)["breakdown"]["assemblyai"] == 18


# ---------------------------------------------------------------------------
# ElevenLabs: assistant characters only
# ---------------------------------------------------------------------------

def test_only_assistant_turns_are_billed_for_tts(rates):
    """The caller's speech is not synthesised, so it must not be billed."""
    t = _transcript(("assistant", "a" * 1000), ("user", "b" * 5000), ("caller", "c" * 5000))
    assert cost_mod.assistant_chars(t) == 1000
    # 1000 chars = $0.10 = 5 pence
    assert cost_mod.estimate_call_cost(0, t)["breakdown"]["elevenlabs"] == 5


def test_caller_role_is_written_two_ways(rates):
    """brain.py writes 'user', routes/twilio.py writes 'caller'. Neither is TTS."""
    assert cost_mod.assistant_chars(_transcript(("user", "x" * 100))) == 0
    assert cost_mod.assistant_chars(_transcript(("caller", "x" * 100))) == 0


def test_assistant_role_matching_is_case_insensitive(rates):
    assert cost_mod.assistant_chars([{"role": "ASSISTANT", "text": "abc"}]) == 3


# ---------------------------------------------------------------------------
# LLM: real token counts preferred, estimates flagged
# ---------------------------------------------------------------------------

def test_real_token_usage_is_used_and_not_flagged(rates):
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    r = cost_mod.estimate_call_cost(0, None, llm_usage=usage)
    # ($3 + $15) * 50 pence = 900 pence
    assert r["breakdown"]["llm"] == 900
    assert r["breakdown"]["llm_estimated"] is False


def test_missing_token_usage_falls_back_and_flags_the_estimate(rates):
    r = cost_mod.estimate_call_cost(60, _transcript(("assistant", "hello there")))
    assert r["breakdown"]["llm_estimated"] is True
    assert r["total_pence"] is not None


def test_partial_token_usage_is_treated_as_estimated(rates):
    """Half a usage dict is not a measurement."""
    r = cost_mod.estimate_call_cost(60, None, llm_usage={"input_tokens": 100})
    assert r["breakdown"]["llm_estimated"] is True


# ---------------------------------------------------------------------------
# Totals, rounding and versioning
# ---------------------------------------------------------------------------

def test_total_is_the_sum_of_components(rates):
    r = cost_mod.estimate_call_cost(
        3600, _transcript(("assistant", "a" * 1000)),
        llm_usage={"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    )
    b = r["breakdown"]
    # Rounding happens once on the unrounded sum, so allow 1p of slack against
    # the sum of individually-rounded components.
    assert abs(r["total_pence"] - (b["twilio"] + b["assemblyai"] + b["elevenlabs"] + b["llm"])) <= 1


def test_total_is_an_integer(rates):
    r = cost_mod.estimate_call_cost(137, _transcript(("assistant", "x" * 333)))
    assert isinstance(r["total_pence"], int)


def test_version_is_stamped_on_every_result(rates):
    assert cost_mod.estimate_call_cost(60, None)["version"] == "test-1"


def test_version_is_stamped_even_when_unconfigured():
    assert cost_mod.estimate_call_cost(60, None)["version"] == cost_mod.RATE_TABLE_VERSION


# ---------------------------------------------------------------------------
# Degenerate inputs — teardown must never blow up
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "duration_s,transcript",
    [
        (None, None),
        (None, []),
        (0, []),
        (60, [{"role": "assistant"}]),          # missing text
        (60, [{"text": "no role"}]),            # missing role
        (60, ["not a dict"]),                   # junk turn
        (60, [{"role": "assistant", "text": None}]),
    ],
)
def test_degenerate_inputs_do_not_raise(rates, duration_s, transcript):
    r = cost_mod.estimate_call_cost(duration_s, transcript)
    assert r["error"] is None
    assert isinstance(r["total_pence"], int)


def test_negative_duration_is_not_billed(rates):
    b = cost_mod.estimate_call_cost(-5, None)["breakdown"]
    assert b["twilio"] == 0 and b["assemblyai"] == 0


# ---------------------------------------------------------------------------
# The invariant that actually matters
# ---------------------------------------------------------------------------

def test_a_pricing_bug_never_raises_out_of_estimate(rates, monkeypatch):
    """cost.py runs at teardown on every call. It must swallow its own bugs."""
    def _boom(*_a, **_k):
        raise ValueError("simulated pricing bug")

    monkeypatch.setattr(cost_mod, "_twilio_pence", _boom)
    r = cost_mod.estimate_call_cost(60, _transcript(("assistant", "hi")))
    assert r["total_pence"] is None
    assert "simulated pricing bug" in r["error"]


def test_capture_survives_a_cost_bug(monkeypatch):
    """A broken cost module must leave the columns NULL, not lose the call."""
    from app.obs import store

    monkeypatch.setattr(
        cost_mod, "estimate_call_cost",
        lambda **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    fields = store._cost_fields({"duration_s": 60}, [{"role": "assistant", "text": "hi"}])
    assert fields == {"cost_pence": None, "cost_breakdown": None, "cost_version": None}
