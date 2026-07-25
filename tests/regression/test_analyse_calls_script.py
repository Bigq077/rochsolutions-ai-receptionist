# tests/regression/test_analyse_calls_script.py
"""Tests for scripts/analyse_calls.py — the 30-call aggregation tool.

Every fixture line below is VERBATIM from the jv_v1 logs of 2026-07-24/25.
Invented log lines would only prove the parser matches my idea of the format;
real ones prove it matches the format Render actually emits.

The headline regression is the boundary bug found the first time this ran on
real data: a call's authoritative lines are emitted AFTER its cleanup line —

    [ms_conn] cleanup call_sid=X
    [ms_lost] CALL SUMMARY call_sid=X lost_total=1 ...
    Row built — outcome=... dur=...s

so splitting on `cleanup` attributed every call's own summary to the NEXT
call, double-counted `[ms_lost]` (once as an event, once in the summary) and
reported outcome one row late. A tool built to stop small-sample misreadings
must not itself misreport.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path("scripts").resolve()))
import analyse_calls  # noqa: E402


# Two complete calls, verbatim, including the epilogue ordering that broke it.
REAL_LOG = """\
2026-07-24 23:14:30,909 - susie.latency - INFO - [LAT] turn_seq=1 path=llm outcome=completed ttfa_ms=3941 content_ttfa_ms=3941 ep_dispatch_ms=0 llm_ttft_ms=1763 chunk_gate_ms=1868 tts_first_byte_ms=308 audio_wire_ms=0 flags=- model=claude-sonnet-4-6
2026-07-24 23:14:31,510 - app.media_streams.connection - INFO - [ms_silence] tts_finished in 17.4s: "I'm sorry to hear that — that sounds really painful. An infl"
2026-07-24 23:14:53,199 - susie.latency - INFO - [LAT] turn_seq=2 path=llm outcome=completed ttfa_ms=1927 content_ttfa_ms=1927 ep_dispatch_ms=0 llm_ttft_ms=1342 chunk_gate_ms=474 tts_first_byte_ms=110 audio_wire_ms=0 flags=- model=claude-sonnet-4-6
2026-07-24 23:15:06,198 - app.media_streams.connection - INFO - [ms_watchdog] WATCHDOG_FIRE q_gen=5 attempt=#1 state=GREETING
2026-07-24 23:16:27,705 - app.media_streams.connection - WARNING - [ms_safety_net] 10s dead-air — emitting safety re-ask (since=10.6s llm_busy=False tts_playing=False q_gen=11 inbound_audio=flowing media_gap=0.0s frames=7011)
2026-07-24 23:16:36,673 - app.media_streams.connection - WARNING - [ms_lost] reason=same_breath_straggler text='rock' (1311ms early) call_total=1
2026-07-24 23:17:11,198 - app.media_streams.connection - INFO - [ms_conn] cleanup call_sid=CA3c01aec81b54a47bc444b22b1d97b7c7 stable=True
2026-07-24 23:17:11,198 - app.media_streams.connection - WARNING - [ms_lost] CALL SUMMARY call_sid=CA3c01aec81b54a47bc444b22b1d97b7c7 lost_total=1 by_reason={'same_breath_straggler': 1} inbound_audio=flowing media_frames=9183
2026-07-24 23:17:13,032 - httpx - INFO - HTTP Request: GET https://api.elevenlabs.io/v1/models "HTTP/1.1 401 Unauthorized"
2026-07-24 23:17:17,292 - app.tools.actionable_summary - INFO - 📊 Row built — outcome=reached_confirmation name=Quentin phone=yes dur=186s source=llm
2026-07-25 02:30:19,314 - susie.latency - INFO - [LAT] turn_seq=1 path=llm outcome=completed ttfa_ms=3404 content_ttfa_ms=3404 ep_dispatch_ms=0 llm_ttft_ms=1777 chunk_gate_ms=1434 tts_first_byte_ms=192 audio_wire_ms=0 flags=- model=claude-sonnet-4-6
2026-07-25 02:30:19,859 - app.media_streams.connection - INFO - [ms_silence] tts_finished in 12.9s: "I'm sorry to hear that — that sounds really uncomfortable. A"
2026-07-25 02:30:36,156 - susie.latency - INFO - [LAT] turn_seq=2 path=llm outcome=completed ttfa_ms=1257 content_ttfa_ms=1257 ep_dispatch_ms=0 llm_ttft_ms=1028 chunk_gate_ms=121 tts_first_byte_ms=106 audio_wire_ms=0 flags=- model=claude-sonnet-4-6
2026-07-25 02:30:47,388 - app.media_streams.connection - WARNING - [ms_safety_net] 10s dead-air — emitting safety re-ask (since=10.6s llm_busy=False tts_playing=False q_gen=3 inbound_audio=flowing media_gap=0.0s frames=2118)
2026-07-25 02:31:00,454 - susie.latency - INFO - [LAT] turn_seq=3 path=llm outcome=completed ttfa_ms=1933 content_ttfa_ms=3050 ep_dispatch_ms=0 llm_ttft_ms=2332 chunk_gate_ms=595 tts_first_byte_ms=123 audio_wire_ms=0 flags=- model=claude-sonnet-4-6
2026-07-25 02:31:04,843 - app.media_streams.connection - INFO - [ms_conn] cleanup call_sid=CA089994da35afd0eb8e8311dab70534b4 stable=True
2026-07-25 02:31:04,843 - app.media_streams.connection - INFO - [ms_lost] CALL SUMMARY call_sid=CA089994da35afd0eb8e8311dab70534b4 lost_total=0 by_reason={} inbound_audio=flowing media_frames=2988
2026-07-25 02:31:08,843 - app.tools.actionable_summary - INFO - 📊 Row built — outcome=abandoned name=None phone=yes dur=60s source=llm
"""


@pytest.fixture(scope="module")
def calls():
    return analyse_calls.parse(REAL_LOG)


@pytest.fixture(scope="module")
def agg(calls):
    return analyse_calls.summarise(calls)


# ---------------------------------------------------------------------------
# The boundary regression.
# ---------------------------------------------------------------------------
def test_two_calls_are_found(calls):
    assert len(calls) == 2, f"expected 2 calls, got {[c['call_sid'] for c in calls]}"


def test_summary_attributes_to_the_call_it_names(calls):
    """CALL SUMMARY is emitted AFTER cleanup — it must not slide one row on."""
    by_sid = {c["call_sid"]: c for c in calls}
    assert by_sid["CA3c01aec81b54a47bc444b22b1d97b7c7"]["media_frames"] == 9183
    assert by_sid["CA089994da35afd0eb8e8311dab70534b4"]["media_frames"] == 2988


def test_outcome_and_duration_land_on_the_right_call(calls):
    by_sid = {c["call_sid"]: c for c in calls}
    first = by_sid["CA3c01aec81b54a47bc444b22b1d97b7c7"]
    second = by_sid["CA089994da35afd0eb8e8311dab70534b4"]
    assert (first["outcome"], first["duration_s"]) == ("reached_confirmation", 186)
    assert (second["outcome"], second["duration_s"]) == ("abandoned", 60)


def test_lost_utterances_are_not_double_counted(agg):
    """'rock' appears as an event AND in the summary. It is ONE loss."""
    assert agg["lost_total"] == 1, (
        f"double-counted: {agg['lost_by_reason']} — the [ms_lost] event line "
        "and the CALL SUMMARY describe the same utterance"
    )
    assert agg["lost_by_reason"] == {"same_breath_straggler": 1}


# ---------------------------------------------------------------------------
# Numbers cross-checked by hand against the source logs.
# ---------------------------------------------------------------------------
def test_latency_matches_hand_calculation(agg):
    # ttfa values present: 3941, 1927 | 3404, 1257, 1933
    assert agg["turns"] == 5
    assert agg["ttfa_max_ms"] == 3941
    assert agg["turns_over_bar"] == 4, "1257 is the only turn under 1500 ms"
    assert agg["turns_over_bar_pct"] == 80.0


def test_longest_turn_is_the_129s_and_174s_pair(calls, agg):
    assert agg["longest_turn_s"] == pytest.approx(17.4)
    by_sid = {c["call_sid"]: c for c in calls}
    assert by_sid["CA089994da35afd0eb8e8311dab70534b4"]["longest_turn_s"] == pytest.approx(12.9)


def test_recovery_and_audio_counters(agg):
    assert agg["watchdog_fires"] == 1
    assert agg["safety_net_fires"] == 2
    assert agg["inbound_audio"] == {"flowing": 2}
    assert agg["tts_auth_401"] == 1
    assert agg["outcomes"] == {"reached_confirmation": 1, "abandoned": 1}


# ---------------------------------------------------------------------------
# New instrumentation this session must be counted.
# ---------------------------------------------------------------------------
def test_backstop_and_dead_end_lines_are_counted():
    log = (
        "2026-07-25 03:00:00,000 - app.media_streams.connection - INFO - "
        "[ms_watchdog] BACKSTOP armed — turn asked nothing ('Right —') but a "
        "question is still outstanding: 'Would you like to book?'\n"
        "2026-07-25 03:00:10,000 - app.media_streams.connection - INFO - "
        "[ms_watchdog] Spec W: turn asked nothing and no question is "
        "outstanding — nothing to re-ask: 'Thanks, bye.'\n"
        "2026-07-25 03:00:20,000 - app.media_streams.connection - INFO - "
        "[ms_conn] cleanup call_sid=CATEST stable=True\n"
    )
    a = analyse_calls.summarise(analyse_calls.parse(log))
    assert a["backstop_arms"] == 1
    assert a["dead_ends"] == 1


def test_inbound_audio_fault_is_visible():
    log = (
        "[ms_lost] CALL SUMMARY call_sid=CAX lost_total=0 by_reason={} "
        "inbound_audio=stalled media_frames=2118\n"
        "[ms_conn] cleanup call_sid=CAX stable=True\n"
    )
    a = analyse_calls.summarise(analyse_calls.parse(log))
    assert a["inbound_audio"] == {"stalled": 1}


def test_clinical_screening_events_are_counted():
    log = (
        "[clinical_screening] screen dvt ARMED by: 'my calf is in a lot of pain'\n"
        "[clinical_screening] screen dvt clear: 'no its not swollen'\n"
        "[ms_conn] cleanup call_sid=CAY stable=True\n"
    )
    a = analyse_calls.summarise(analyse_calls.parse(log))
    assert a["screens"] == {"dvt:ARMED": 1, "dvt:clear": 1}


# ---------------------------------------------------------------------------
# Robustness — a wrong number is worse than no number.
# ---------------------------------------------------------------------------
def test_truncated_paste_is_flagged_not_silently_clean():
    """A paste cut mid-call must not read as a completed call."""
    truncated = "\n".join(REAL_LOG.splitlines()[:5])
    parsed = analyse_calls.parse(truncated)
    assert len(parsed) == 1
    assert parsed[0]["call_sid"] == "<unterminated>"


def test_empty_input_yields_no_calls():
    assert analyse_calls.parse("") == []


def test_unrelated_text_is_not_mistaken_for_a_call():
    assert analyse_calls.parse("hello\nworld\n") == []


def test_negative_latency_sentinels_are_excluded():
    """-1 means 'not applicable this turn' (scripted path), not 0 ms."""
    log = (
        "[LAT] turn_seq=3 path=scripted ttfa_ms=139 llm_ttft_ms=-1 "
        "chunk_gate_ms=-1 tts_first_byte_ms=-1\n"
        "[ms_conn] cleanup call_sid=CAZ stable=True\n"
    )
    c = analyse_calls.parse(log)[0]
    assert c["ttfa_ms"] == [139]
    assert c["llm_ttft_ms"] == [], "-1 sentinel counted as a real measurement"


def test_percentile_is_nearest_rank():
    assert analyse_calls._percentile([], 95) is None
    assert analyse_calls._percentile([5], 95) == 5
    assert analyse_calls._percentile([1, 2, 3, 4, 5], 100) == 5
    assert analyse_calls._percentile([1, 2, 3, 4, 5], 50) == 3


# ---------------------------------------------------------------------------
# CLI contract.
# ---------------------------------------------------------------------------
def test_cli_json_mode_round_trips(tmp_path):
    p = tmp_path / "paste.log"
    p.write_text(REAL_LOG, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "scripts/analyse_calls.py", str(p), "--json"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["summary"]["calls"] == 2
    assert data["summary"]["lost_total"] == 1


def test_cli_reports_no_calls_without_crashing(tmp_path):
    p = tmp_path / "junk.log"
    p.write_text("nothing to see here\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "scripts/analyse_calls.py", str(p)],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 1
    assert "no calls found" in proc.stderr


# ---------------------------------------------------------------------------
# Screen states — a sweep is only readable if every terminal state is counted.
# ORPHAN and TRUNCATED shipped 2026-07-25 (2485229 / 188e478) and the parser
# was blind to them; 'unclear' had never been captured at all.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("line,expect", [
    ("[clinical_screening] screen trauma_fracture ARMED by: 'i fell'",
     ("trauma_fracture", "ARMED")),
    ("[clinical_screening] screen dvt clear: 'no'",
     ("dvt", "clear")),
    ("[clinical_screening] screen dvt POSITIVE (block=True): 'swollen'",
     ("dvt", "POSITIVE")),
    ("[clinical_screening] screen cauda_equina answer unclear: 'eh?'",
     ("cauda_equina", "unclear")),
    ("[clinical_screening] screen trauma_fracture ORPHAN — asked by the model, "
     "never armed by Layer 1; grading this turn as the answer: 'x'",
     ("trauma_fracture", "ORPHAN")),
    ("[clinical_screening] screen trauma_fracture answer TRUNCATED — endpointed "
     "mid-clause, not treating as clear; re-asking: 'x'",
     ("trauma_fracture", "TRUNCATED")),
])
def test_every_screen_state_is_parsed(line, expect):
    m = analyse_calls._SCREEN_RE.search(line)
    assert m is not None, line
    assert (m.group(1), m.group(2)) == expect
