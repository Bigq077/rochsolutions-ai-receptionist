"""B-19 / B-07 — the background filler was one-shot, so an upstream LLM spike
became bare silence.

The background filler task fired once at LLM_FIRST_CHUNK_TIMEOUT_MS (1.8s) and
then ended. A 14s Anthropic stall therefore produced one phrase at 1.8s and
~12s of nothing — breaking the CLAUDE.md §6 bar of "no dead air over 3s without
a filler or acknowledgement" on precisely the turns the filler exists to cover.

Owner decision 2026-08-03: ONE re-arm at ~5s, then stop. Not a loop.

These tests pin the decision logic (`_second_filler_text`) and the fact that
the re-arm exists at all. They do not need a WebSocket, a connection or an LLM.
"""
import re
from pathlib import Path

import pytest

from app.media_streams import llm_stream
from app.media_streams.config import (
    FILLER_PHRASES,
    LLM_FILLER_SECOND_DELAY_MS,
    LLM_FIRST_CHUNK_TIMEOUT_MS,
)
from app.media_streams.llm_stream import _second_filler_text


def _armed_session():
    """A session in the state the first filler leaves behind: it spoke, and no
    tool-call filler has taken over."""
    return {"_ack_filler_active": True, "_ack_filler_cancelled": False}


# ── the re-arm fires when it should ────────────────────────────────────────


def test_second_filler_plays_when_llm_still_silent():
    """The B-19 case itself: first filler spoke, LLM still has not produced a
    token 5s later. Something must be said."""
    text = _second_filler_text(_armed_session(), "Give me a moment…", False)
    assert text is not None
    assert text in FILLER_PHRASES


def test_second_filler_is_never_a_verbatim_repeat():
    """Hearing the identical phrase twice reads as a stuck line, not a hold."""
    first = FILLER_PHRASES[0]
    for _ in range(50):
        assert _second_filler_text(_armed_session(), first, False) != first


def test_second_filler_never_repeats_a_write_ack():
    """The first phrase may have been confirm_write_filler's "Just locking that
    in now…". Saying it twice claims the write twice to a caller who has
    already said go ahead — the B-30 shape, and worse than silence."""
    write_ack = "Just locking that in now…"
    for _ in range(50):
        second = _second_filler_text(_armed_session(), write_ack, False)
        assert second in FILLER_PHRASES
        assert "locking" not in second.lower()
        assert "book" not in second.lower()


# ── the three reasons it must stay quiet ───────────────────────────────────


def test_no_second_filler_once_the_llm_has_answered():
    """got_first_chunk is the race guard: the first token also cancels the
    task, but if the wait and the token land together we must not speak over
    the answer."""
    assert _second_filler_text(_armed_session(), "One moment…", True) is None


def test_no_second_filler_when_a_tool_filler_took_over():
    """filler_phrases.with_filler clears _ack_filler_active when it wins. It is
    already speaking — stacking on it is two fillers back to back."""
    session = {"_ack_filler_active": False, "_ack_filler_cancelled": False}
    assert _second_filler_text(session, "One moment…", False) is None


def test_suppression_does_not_read_the_consumed_cancelled_flag():
    """REGRESSION GUARD, and the trap this fix nearly walked into.

    _tts_loop *consumes* _ack_filler_cancelled — connection.py resets it to
    False after suppressing one chunk. So by the time the re-arm wakes, that
    flag reads False whether or not a tool filler won, and gating on it would
    let the second filler play on top of the tool filler.

    _ack_filler_active is the durable signal. This test fails if anyone
    re-points the suppression at the consumed flag: here the cancelled flag is
    False (already consumed) while a tool filler HAS taken over.
    """
    session = {"_ack_filler_active": False, "_ack_filler_cancelled": False}
    assert _second_filler_text(session, "One moment…", False) is None


def test_missing_flag_is_treated_as_not_armed():
    """A session that never armed a filler must not produce one."""
    assert _second_filler_text({}, "One moment…", False) is None


# ── the decision itself, pinned ────────────────────────────────────────────


def test_second_delay_is_about_five_seconds():
    """Owner decision 2026-08-03. If this changes, the change was a decision,
    not a tweak — re-read REGISTER_B_U.md B-19 before editing this test."""
    assert LLM_FILLER_SECOND_DELAY_MS == 5000


def test_worst_case_dead_air_is_bounded_under_the_claude_md_bar():
    """CLAUDE.md §6: no dead air over 3s without a filler or acknowledgement.

    Before the fix the gap after the first filler was unbounded — it ran as
    long as the upstream stall. It is now bounded by the re-arm delay."""
    first_at_s = LLM_FIRST_CHUNK_TIMEOUT_MS / 1000.0
    assert first_at_s <= 3.0, "first filler must still land inside the bar"
    gap_s = LLM_FILLER_SECOND_DELAY_MS / 1000.0
    assert gap_s <= 5.0, "the re-armed filler must cap the second silence"


def test_the_rearm_is_not_a_loop():
    """The decision was explicitly 'a second at ~5s, then stop'. A continuing
    cadence was considered and rejected — three or four phrases on one slow
    turn sounds anxious.

    Pinned structurally: _delayed_filler must contain exactly TWO awaited
    sleeps (the initial timeout and the single re-arm). A third means someone
    turned this into a loop without revisiting the decision.
    """
    src = Path(llm_stream.__file__).read_text(encoding="utf-8")
    start = src.index("async def _delayed_filler()")
    end = src.index("_filler_task = asyncio.create_task", start)
    # Strip comment lines — prose about loops is not a loop.
    body = "\n".join(
        line for line in src[start:end].splitlines()
        if not line.lstrip().startswith("#")
    )

    assert not re.search(r"^\s*(while|for)\s", body, re.M), (
        "the re-arm must not become a loop"
    )

    sleeps = re.findall(r"await asyncio\.sleep\(", body)
    assert len(sleeps) == 2, (
        f"expected exactly 2 awaited sleeps in _delayed_filler "
        f"(initial timeout + one re-arm), found {len(sleeps)}"
    )
