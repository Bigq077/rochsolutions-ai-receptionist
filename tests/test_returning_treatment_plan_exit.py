"""
Tests for the RETURNING_TREATMENT_PLAN hard-exit-to-standard-booking-flow fix.

Covers the production bug where "no it's been a while" (and related utterances)
were not matched by the deterministic NO gate, causing the caller to remain in
the returning-patient identity flow (COLLECT_NAME_RETURNING / CONFIRM_PHONE_RETURNING)
rather than the standard new-patient booking flow (COLLECT_NAME / CONFIRM_PHONE).

Root cause:
  1. _RTP_NO was missing "been a while", "a while ago", etc.
  2. Even when NO was stored (via Haiku fallback), new_or_returning stayed
     "returning", causing COLLECT_NAME_RETURNING to NOT be skipped.

Fixed by:
  1. Expanding _RTP_NO with all "been a while / not ongoing / new episode" signals.
  2. In the deterministic NO branch AND the Haiku-NO safety net, resetting
     new_or_returning="new" so the standard skip chain routes through
     COLLECT_NAME (step 10) instead of COLLECT_NAME_RETURNING (step 7).

Cases tested:
  1. YES ("still coming in regularly") → stays in returning path, on_treatment_plan=True
  2. "no it's been a while" → new_or_returning="new", goes to standard booking flow
  3. "actually it's been a while" → same (discourse-marker prefix tolerated)
  4. "this is a new episode" → same (existing signal still works)
  5. After exit, state must never be COLLECT_NAME_RETURNING or CONFIRM_PHONE_RETURNING
  6. selected_location (clinic) is preserved across the branch
  7. Already-captured reason is preserved across the branch

Run with:
  pytest tests/test_returning_treatment_plan_exit.py -v
"""
from __future__ import annotations

import asyncio
import copy
from typing import Any, Dict

import pytest

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeTTS:
    def __init__(self):
        self.items: list[str] = []

    async def put(self, text: str) -> None:
        self.items.append(text)

    def empty(self) -> bool:
        return len(self.items) == 0

    def get_nowait(self):
        if self.items:
            return self.items.pop(0)
        raise asyncio.QueueEmpty()

    def last(self) -> str:
        return self.items[-1] if self.items else ""

    def all_text(self) -> str:
        return " | ".join(self.items)


async def _noop_llm(instruction: str, allow_tools: bool = True) -> str:
    return ""


def _make_rtp_engine(
    extra_session: Dict[str, Any] | None = None,
    reason: str | None = None,
) -> tuple:
    """
    Return (engine, tts) positioned at RETURNING_TREATMENT_PLAN (step 2).

    Session is set up as if:
      - caller said they had been before → new_or_returning="returning"
      - recency was classified as "recent" → returning_recency="recent"
      - location has already been selected → selected_location="alcester"
      - reason optionally pre-captured
    """
    from app.media_streams.flow import FlowEngine, BOOKING_FLOW
    from app.media_streams.session import DEFAULT_MS_SESSION

    # Locate the step index for RETURNING_TREATMENT_PLAN dynamically
    rtp_idx = next(
        i for i, s in enumerate(BOOKING_FLOW)
        if s["state"] == "RETURNING_TREATMENT_PLAN"
    )

    session = copy.deepcopy(DEFAULT_MS_SESSION)
    session.update({
        "new_or_returning":   "returning",
        "returning_recency":  "recent",
        "selected_location":  "alcester",
        "flow_step":          rtp_idx,
        "state":              "RETURNING_TREATMENT_PLAN",
        "flow_state":         "RETURNING_TREATMENT_PLAN",
        "_intent_detected":   True,
    })
    if reason is not None:
        session["reason"] = reason
    if extra_session:
        session.update(extra_session)

    tts = _FakeTTS()
    engine = FlowEngine(session, tts, _noop_llm)
    engine._active_flow = BOOKING_FLOW
    engine._intent_detected = True
    return engine, tts


# ---------------------------------------------------------------------------
# 1. YES branch — caller is still on an active treatment plan
# ---------------------------------------------------------------------------

class TestReturningTreatmentPlanYes:

    async def test_yes_stays_in_returning_path(self):
        """'still coming in regularly' → on_treatment_plan=True, returns plan path."""
        engine, _ = _make_rtp_engine()
        await engine.handle_transcript("yes i'm still coming in regularly")

        assert engine.session.get("on_treatment_plan") is True, (
            "on_treatment_plan must be True for active-plan YES"
        )
        # new_or_returning must remain "returning" — the caller IS a returning patient
        assert engine.session.get("new_or_returning") == "returning", (
            "new_or_returning must NOT be changed for an active-plan YES"
        )
        # State must NOT be COLLECT_NAME (new patient path)
        assert engine.session.get("state") not in ("COLLECT_NAME",), (
            "YES should NOT route to COLLECT_NAME (new patient path)"
        )

    async def test_yes_does_not_reset_returning_recency(self):
        """YES must not clear returning_recency."""
        engine, _ = _make_rtp_engine()
        await engine.handle_transcript("i am yes")

        assert engine.session.get("on_treatment_plan") is True
        # returning_recency should NOT be wiped on YES
        assert engine.session.get("returning_recency") is not None, (
            "returning_recency must not be cleared when caller confirms active plan"
        )


# ---------------------------------------------------------------------------
# 2–4. NO branch — various "been a while / not ongoing / new episode" utterances
# ---------------------------------------------------------------------------

class TestReturningTreatmentPlanNo:

    # ── helper ───────────────────────────────────────────────────────────────

    async def _assert_exits_to_standard_flow(self, utterance: str) -> None:
        engine, _ = _make_rtp_engine()
        await engine.handle_transcript(utterance)

        state = engine.session.get("state")
        assert engine.session.get("new_or_returning") == "new", (
            f"new_or_returning must be 'new' after NO ({utterance!r}), "
            f"got {engine.session.get('new_or_returning')!r}"
        )
        assert engine.session.get("on_treatment_plan") is False, (
            f"on_treatment_plan must be False after NO ({utterance!r})"
        )
        assert state not in (
            "COLLECT_NAME_RETURNING",
            "CONFIRM_PHONE_RETURNING",
            "COLLECT_PHONE_RETURNING",
            "RETURNING_PLAN_CONFIRM_PHONE",
            "RETURNING_PLAN_COLLECT_PHONE",
            "RETURNING_PLAN_LOOKUP",
        ), (
            f"State must not be in returning-specific states after NO ({utterance!r}), "
            f"got {state!r}"
        )

    # ── test 2: "no it's been a while" (the failing production case) ─────────

    async def test_no_its_been_a_while(self):
        """Core production failure case: deterministic NO must fire."""
        await self._assert_exits_to_standard_flow("no it's been a while")

    # ── test 3: discourse-marker prefix ──────────────────────────────────────

    async def test_actually_its_been_a_while(self):
        """Leading 'actually' must not prevent the NO match."""
        await self._assert_exits_to_standard_flow("actually it's been a while")

    async def test_no_actually_its_been_a_while(self):
        """Double prefix tolerated."""
        await self._assert_exits_to_standard_flow("no actually it's been a while")

    async def test_well_its_been_a_while(self):
        await self._assert_exits_to_standard_flow("well it's been a while")

    async def test_i_mean_its_been_a_while(self):
        await self._assert_exits_to_standard_flow("i mean it's been a while")

    # ── test 4: "new episode" (existing signal, must still exit) ─────────────

    async def test_new_episode(self):
        """'this is a new episode' must exit returning path."""
        await self._assert_exits_to_standard_flow("this is a new episode")

    async def test_new_issue(self):
        await self._assert_exits_to_standard_flow("it's a new issue")

    # ── additional "been a while" variants ───────────────────────────────────

    async def test_a_while_ago(self):
        await self._assert_exits_to_standard_flow("it was a while ago")

    async def test_ages_ago(self):
        await self._assert_exits_to_standard_flow("it was ages ago")

    async def test_not_recently(self):
        await self._assert_exits_to_standard_flow("not recently")

    async def test_long_time_ago(self):
        await self._assert_exits_to_standard_flow("that was a long time ago")

    async def test_not_anymore(self):
        await self._assert_exits_to_standard_flow("no not anymore")

    async def test_this_is_different(self):
        await self._assert_exits_to_standard_flow("this is different")

    async def test_no_longer_coming_in(self):
        # "no longer" is a clean NO signal that doesn't overlap with YES signals.
        await self._assert_exits_to_standard_flow("no i'm no longer coming in")


# ---------------------------------------------------------------------------
# 5. After NO exit, state must never be COLLECT_NAME_RETURNING
# ---------------------------------------------------------------------------

class TestNoReturningNameCollection:

    async def test_no_collect_name_returning_after_exit(self):
        """
        After 'been a while' NO, the skip chain must NOT land on
        COLLECT_NAME_RETURNING.  The first name-collection state reached must
        be COLLECT_NAME (the new-patient path) or a step that follows it.
        """
        engine, _ = _make_rtp_engine()
        await engine.handle_transcript("no it's been a while")

        # Simulate the skip cascade by calling ask_current_question once more
        # (flow.py's NO branch already calls it; check the final resting state).
        state = engine.session.get("state")
        assert state != "COLLECT_NAME_RETURNING", (
            "COLLECT_NAME_RETURNING must be skipped for 'been a while' NO — "
            f"got state={state!r}"
        )
        assert state != "CONFIRM_PHONE_RETURNING", (
            "CONFIRM_PHONE_RETURNING must be skipped for 'been a while' NO — "
            f"got state={state!r}"
        )

    async def test_not_on_active_plan_no_surname_lookup_flags(self):
        """
        After NO, stale returning-lookup flags must be absent so that
        cancel/reschedule surname lookup cannot be triggered later in the call.
        """
        engine, _ = _make_rtp_engine(extra_session={
            # Simulate residual flags from a prior cancel/reschedule session
            "rc_name_surname":       "Smith",
            "lookup_correction_mode": True,
            "rc_recovery_step":      "surname",
            "rc_phone_first":        True,
        })
        await engine.handle_transcript("no it's been a while")

        assert engine.session.get("rc_name_surname") is None, (
            "rc_name_surname must be cleared after NO exit"
        )
        assert engine.session.get("lookup_correction_mode") is None or \
               engine.session.get("lookup_correction_mode") is False, (
            "lookup_correction_mode must be cleared after NO exit"
        )
        assert engine.session.get("rc_recovery_step") is None, (
            "rc_recovery_step must be cleared after NO exit"
        )


# ---------------------------------------------------------------------------
# 6. Selected clinic (location) is preserved
# ---------------------------------------------------------------------------

class TestLocationPreservedAcrossExit:

    async def test_alcester_preserved(self):
        engine, _ = _make_rtp_engine()
        await engine.handle_transcript("no it's been a while")

        assert engine.session.get("selected_location") == "alcester", (
            "selected_location must be preserved across RETURNING_TREATMENT_PLAN NO exit"
        )

    async def test_redditch_preserved(self):
        engine, _ = _make_rtp_engine(extra_session={"selected_location": "redditch"})
        await engine.handle_transcript("it's been a while")

        assert engine.session.get("selected_location") == "redditch", (
            "selected_location redditch must be preserved"
        )


# ---------------------------------------------------------------------------
# 7. Already-captured reason is preserved
# ---------------------------------------------------------------------------

class TestReasonPreservedAcrossExit:

    async def test_reason_preserved_if_already_known(self):
        """
        If reason was captured before RETURNING_TREATMENT_PLAN, it must
        still be present after the NO exit.  COLLECT_REASON should be skipped.
        """
        engine, _ = _make_rtp_engine(reason="knee pain after running")
        await engine.handle_transcript("no it's been a while")

        assert engine.session.get("reason") == "knee pain after running", (
            "reason must be preserved across RETURNING_TREATMENT_PLAN NO exit"
        )
        # COLLECT_REASON should be skipped (reason already known)
        state = engine.session.get("state")
        assert state != "COLLECT_REASON", (
            f"COLLECT_REASON must be skipped when reason already captured, got {state!r}"
        )

    async def test_without_reason_lands_at_collect_reason(self):
        """
        If reason is NOT yet captured and not on plan, the next step after NO
        must be COLLECT_REASON (the standard booking reason question).
        """
        engine, _ = _make_rtp_engine()  # no reason
        await engine.handle_transcript("no it's been a while")

        state = engine.session.get("state")
        assert state == "COLLECT_REASON", (
            f"Without pre-captured reason, state after NO exit must be "
            f"COLLECT_REASON, got {state!r}"
        )
