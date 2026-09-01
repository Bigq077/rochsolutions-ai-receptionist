"""B-19 / B-07 — the background filler was one-shot, so an upstream LLM spike
became bare silence.

The background filler task fired once at LLM_FIRST_CHUNK_TIMEOUT_MS (1.8s) and
then ended. A 14s Anthropic stall therefore produced one phrase at 1.8s and
~12s of nothing — breaking the CLAUDE.md §6 bar of "no dead air over 3s without
a filler or acknowledgement" on precisely the turns the filler exists to cover.

Owner decision 2026-08-03: ONE re-arm, then stop. Not a loop.
Owner decision 2026-09-01: it fires on a GENUINE stall (10s absolute from
dispatch), not 5s after the first phrase - two contentless phrases five
seconds apart sound worse to a caller than the silence they replace.

These tests pin the decision logic (`_second_filler_text`) and the fact that
the re-arm exists at all. They do not need a WebSocket, a connection or an LLM.
"""
import re
from pathlib import Path

import pytest

from app.media_streams import llm_stream
from app.media_streams.config import (
    FILLER_PHRASES,
    LLM_FILLER_SECOND_MIN_GAP_MS,
    LLM_FILLER_SECOND_STALL_MS,
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


def test_the_stall_threshold_is_absolute_not_relative():
    """The 2026-09-01 correction, and the shape of the bug it fixes.

    The delay used to be 5000ms measured FROM THE FIRST PHRASE. dc6f521e moved
    the situational head from 3000ms to 600ms and did not touch it, so the
    second phrase slid from 8.0s to 5.6s with nobody deciding to — 13.9% of
    turns instead of 4.8%, measured over 294 obs turns.

    An absolute deadline from dispatch cannot drift when the head timing moves
    again. If this becomes relative once more, that bug is back.
    """
    src = Path(llm_stream.__file__).read_text(encoding="utf-8")
    start = src.index("async def _delayed_filler()")
    end = src.index("_filler_task = asyncio.create_task", start)
    body = src[start:end]
    assert "_filler_t0 + LLM_FILLER_SECOND_STALL_MS" in body, (
        "the re-arm must wait to an absolute deadline measured from dispatch"
    )
    assert LLM_FILLER_SECOND_STALL_MS == 10000


def test_a_second_phrase_can_never_be_back_to_back():
    """MIN_GAP is the structural guard, not the tuning knob.

    In practice it never binds — a 600ms head is 9.4s clear of the 10s
    deadline. It exists so that the NEXT timing change cannot recreate the
    defect the way dc6f521e did. Four seconds is long enough that two phrases
    read as separate events rather than a stutter.
    """
    assert LLM_FILLER_SECOND_MIN_GAP_MS >= 4000
    src = Path(llm_stream.__file__).read_text(encoding="utf-8")
    start = src.index("async def _delayed_filler()")
    end = src.index("_filler_task = asyncio.create_task", start)
    assert "LLM_FILLER_SECOND_MIN_GAP_MS" in src[start:end], (
        "the re-arm must take the LATER of the stall deadline and the min gap"
    )


def test_silence_under_the_stall_threshold_is_a_deliberate_trade():
    """CLAUDE.md §6 says no dead air over 3s without a filler. Between the head
    and the 10s deadline we now knowingly break that, and this test exists so
    the trade is visible rather than accidental.

    Owner decision 2026-09-01, reversing the 2026-08-03 "second filler at ~5s":
    two contentless phrases 5s apart sound worse to a caller than the silence
    they replace. The corpus is why the line is at 10s and not lower — over
    5.6s is 13.9% of turns, over 10s is 2.0%, so this buys back 86% of the
    doubles while still covering the stalls B-19 was written for.
    """
    first_at_s = LLM_FIRST_CHUNK_TIMEOUT_MS / 1000.0
    assert first_at_s <= 3.0, "the FIRST phrase must still land inside the bar"
    assert LLM_FILLER_SECOND_STALL_MS >= 8000, (
        "below ~8s the second phrase stacks often enough to be the defect "
        "rather than the fix"
    )
    assert LLM_FILLER_SECOND_STALL_MS <= 15000, (
        "above ~15s the B-19 dead air returns — a 24.7s turn exists in the "
        "corpus and cannot be met with silence"
    )


# ── the live path goes through the refusals ────────────────────────────────


def test_the_live_candidate_goes_through_the_same_refusals():
    """Until 2026-09-01 the live re-arm rendered an UNKNOWN_SLOW head inline
    and never called this function, so all three refusals above were pinned by
    tests and enforced nowhere. The candidate parameter is what makes the
    tests and the running code the same decision."""
    head = "Still with you —"
    assert _second_filler_text(
        _armed_session(), "Sorry to hear that —", False, candidate=head
    ) == head
    assert _second_filler_text(
        _armed_session(), head, False, candidate=head
    ) is None
    assert _second_filler_text(
        _armed_session(), "One moment…", True, candidate=head
    ) is None
    assert _second_filler_text(
        {"_ack_filler_active": False}, "One moment…", False, candidate=head
    ) is None
    assert _second_filler_text(
        _armed_session(), "One moment…", False, candidate="   "
    ) is None


def test_a_candidate_may_not_claim_a_write_twice():
    """Rule 4, generalised to whatever the arbiter hands us. Two phrases in a
    row that both claim the booking is being written is the B-30 shape."""
    write_ack = "Just locking that in now…"
    assert _second_filler_text(
        _armed_session(), write_ack, False, candidate=write_ack
    ) is None


def test_the_live_path_actually_calls_this_function():
    """The regression that made the guards decorative. If the re-arm renders a
    head inline again, this fails."""
    src = Path(llm_stream.__file__).read_text(encoding="utf-8")
    start = src.index("async def _delayed_filler()")
    end = src.index("_filler_task = asyncio.create_task", start)
    assert "_second_filler_text(" in src[start:end], (
        "the live re-arm must take its decision from _second_filler_text"
    )


# ── the five stored doubles that prompted this ─────────────────────────────


@pytest.mark.parametrize("head", [
    "Sorry to hear that —",        # CAc119b8838f556ac2 — the reported one
    "Let's get you booked in —",   # CA52dc5ea104909d79
    "Popping that in for you —",   # CA320e6b1cb782173f
    "Right with you…",             # CA8522b3e23fc64293, Vital Edge
    "Right, booking you in —",     # CAa4231548cafb7b83
])
def test_the_stored_doubles_were_not_stopped_by_wording(head):
    """Each of these five was heard on a real call: a head, then
    "Still with you —" five seconds later.

    This test pins WHY the fix is a deadline and not a phrase blocklist. The
    wording refusal does not stop any of them — the phrases differ, so
    _second_filler_text allows every pair. What stops them is that all five
    turns produced content well under 10s. If someone later "fixes" this by
    banning wordings instead, this test still passes and the defect returns.
    """
    slow = "Still with you —"
    assert head != slow
    assert _second_filler_text(
        _armed_session(), head, False, candidate=slow
    ) == slow, "wording refusal is not what prevents these — the deadline is"


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
