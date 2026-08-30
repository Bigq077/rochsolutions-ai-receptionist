"""The booking flow, on whatever clinic THIS branch actually serves.

Written 2026-08-30, to close the gap that day exposed: **a flow change (O-1,
the session-length question moving ahead of the timing question) was pushed to
two live patient lines having been verified only on a third.** The demo line
runs `northgate` on `latency-eval`; the patients are on `jv_v2` and
`vitaledge-onboarding`, whose engines have diverged and whose services carry
different lengths (30/60 against Vital Edge's 60/90). Nothing exercised the
booking flow on the branch a patient would actually reach.

`tests/harness/` was canonical-only, which is why. It travels with the branch
now, and this file names no clinic: it discovers one from `app/clinics/` the
same way the code under test is gated, so it asks each branch about its own
config and its own engine.

WHAT IT PINS, and each one has cost a live call:

  1. The length question comes BEFORE the timing question. O-1, reported by the
     owner from the demo line: "it seems a bit awkward". The rung is a prompt
     instruction, so this is the only kind of test that can tell you the model
     obeys it.
  2. The caller's spoken length is CAPTURED by the engine. This runs in
     connection.py's transcript handler, not in run_turn, and the harness was
     blind to it until `0e9ad6c5` — see test_pre_dispatch_captures.
  3. The diary gets the length the caller ASKED FOR. CA86c320ef, 4 Aug: the
     caller chose 90 minutes and a 60-minute event went in, at the wrong price,
     leaving the following slot still offerable. `_resolve_duration_minutes`
     must prefer the captured choice over whatever `duration_minutes` the model
     passes.

NON-DETERMINISTIC BY NATURE — it drives a real model. Assertions are therefore
about FACTS the engine records (what was captured, what reached the diary) and
about ORDER, never about wording. A run that fails to complete the booking
inside the turn budget is reported as inconclusive rather than failed: that is
the script losing the thread, not the engine breaking, and a flaky red here
would get the whole file switched off.

Run it:  HARNESS_LIVE_LLM=1 pytest tests/harness/test_this_branchs_booking_flow.py -q
"""
from __future__ import annotations

import json
import os
import pathlib
import re
from datetime import datetime, timedelta

import pytest

from app.clinic_config import get_clinic
from tests.harness.driver import ConversationDriver
from tests.harness.fake_clinic import FakeDiary

live_llm = pytest.mark.skipif(
    os.getenv("HARNESS_LIVE_LLM") != "1",
    reason="spends model tokens and is non-deterministic; set HARNESS_LIVE_LLM=1",
)


# ── Which clinic does THIS branch have with a choice of lengths? ────────────

def _a_clinic_with_a_length_choice():
    """(clinic_id, service_dict) for a clinic this branch ships, or skip.

    Discovered, never listed. A hardcoded id is the trap this repo keeps paying
    for: `northgate` is the demo line's clinic and no patient branch has it, and
    `get_clinic` on an unknown id does not raise — it returns a shape whose
    `services` is a list of strings, and the failure surfaces as an
    AttributeError deep inside a renderer.
    """
    root = pathlib.Path(__file__).resolve().parents[2] / "app" / "clinics"
    for d in sorted(root.iterdir()):
        try:
            cfg = json.loads((d / "clinic.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        if cfg.get("prompt_engine") != "template_v1":
            continue
        try:
            clinic = get_clinic(d.name)
        except Exception:
            continue
        for svc in clinic.get("services") or []:
            if isinstance(svc, dict) and svc.get("typical_duration_minutes_options"):
                return d.name, svc
    pytest.skip(
        "this branch ships no template_v1 clinic with a multi-length service, "
        "so O-1 and the CA86c320ef guarantee are both N-A here"
    )


LENGTH_Q = re.compile(
    r"(thirty|sixty|ninety|30|60|90)[- ]?minute|how long|which length", re.I
)
TIMING_Q = re.compile(
    r"preference for when|when would|come in|what day|particular day", re.I
)
NAME_Q = re.compile(r"first name|your name|surname", re.I)
PHONE_Q = re.compile(r"phone|number to|keypad|best number", re.I)
SLOT_Q = re.compile(
    r"number 1|number one|which (one|of those)|either of those|does that work|suit you",
    re.I,
)
CONFIRM_Q = re.compile(
    r"shall i (put|book|go ahead)|is that right|all correct|confirm", re.I
)


class _Caller:
    """Answers whatever Susie actually asked.

    Deliberately NOT a fixed script. A fixed one encodes an expected order, so
    it would pass on the build it was written for and mis-answer every other —
    which is the very thing this file exists to detect. It also lets the same
    file run truthfully on a branch that has NOT yet taken O-1.
    """

    def __init__(self, longest: int) -> None:
        self.longest = longest
        self.done = {k: False for k in ("length", "time", "reason", "name", "phone")}

    def reply(self, said: str) -> str:
        s = said or ""
        if LENGTH_Q.search(s) and not self.done["length"]:
            self.done["length"] = True
            return f"the {self.longest} minute one please"
        if NAME_Q.search(s) and not self.done["name"]:
            self.done["name"] = True
            return "it's Quentin Rook"
        if PHONE_Q.search(s) and not self.done["phone"]:
            self.done["phone"] = True
            # CONFIRM the caller-ID number; do NOT recite digits. Reciting sends
            # Susie down the DTMF/keypad path, which `_pre_turn` documents as
            # deliberately not modelled (it needs digit events this driver
            # cannot send). The call then stalls on "type the number on your
            # keypad" and burns the turn budget, so the booking assertions skip
            # and the file quietly stops testing its own point. The verbal
            # confirm IS modelled, and is what these clinics ask for first.
            return "yes that's the best number for me"
        if CONFIRM_Q.search(s):
            return "yes please go ahead"
        if SLOT_Q.search(s):
            return "the first one please"
        if TIMING_Q.search(s) and not self.done["time"]:
            self.done["time"] = True
            return "anytime next week is fine"
        if not self.done["reason"]:
            self.done["reason"] = True
            return "my shoulder's been tight from training"
        return "yes"


async def _run_one():
    clinic_id, svc = _a_clinic_with_a_length_choice()
    opts = sorted(int(o) for o in svc["typical_duration_minutes_options"])
    longest = opts[-1]

    diary = FakeDiary.weekly(
        start=datetime.now() + timedelta(days=3), days=14,
        times=["09:00", "11:00", "14:00"],
    )
    caller = _Caller(longest)
    order: list = []

    async with ConversationDriver(clinic_id=clinic_id, diary=diary) as call:
        nxt = f"hi there i'd like to book a {svc.get('name', 'massage')}"
        for _ in range(14):
            said = (await call.say(nxt)).spoken
            if LENGTH_Q.search(said) and "length" not in order:
                order.append("length")
            if TIMING_Q.search(said) and "timing" not in order:
                order.append("timing")
            if diary.bookings:
                break
            nxt = caller.reply(said)
        session = dict(call.session)

    return {
        "clinic_id": clinic_id,
        "service": svc.get("name"),
        "options": opts,
        "longest": longest,
        "order": order,
        "captured": session.get("_service_duration_choice"),
        "bookings": list(diary.bookings),
    }


@pytest.fixture(scope="module")
def call_result():
    import asyncio
    return asyncio.run(_run_one())


# ── 1. O-1: the length question comes first ────────────────────────────────

@live_llm
def test_the_length_question_precedes_the_timing_question(call_result):
    """O-1. Rung 1c is a prompt instruction, so only a real model run can say
    whether it is obeyed. On the build before it, the length was asked only when
    the tool-time gate blocked — i.e. after the caller had already answered a
    timing question, which is what the owner heard and called awkward."""
    order = call_result["order"]
    if "length" not in order:
        pytest.fail(
            f"{call_result['clinic_id']}: the length question was never asked "
            f"for {call_result['service']!r}, which offers "
            f"{call_result['options']} — the slot grid is being built at a "
            f"length nobody agreed"
        )
    if "timing" not in order:
        pytest.skip("the timing question never came up — nothing to order against")
    assert order.index("length") < order.index("timing"), (
        f"{call_result['clinic_id']}: the caller was asked when they want to "
        f"come in, answered, and was then interrupted with the length question "
        f"— O-1, order seen: {order}"
    )


# ── 2. the engine hears the caller's length ────────────────────────────────

@live_llm
def test_the_engine_captured_the_length_the_caller_said(call_result):
    """`capture_duration_choice` runs in connection.py's transcript handler,
    before dispatch. It is the FIRST half of the CA86c320ef guarantee, and the
    harness was blind to it until 0e9ad6c5 — see test_pre_dispatch_captures for
    what that blindness looked like."""
    assert call_result["captured"] == call_result["longest"], (
        f"{call_result['clinic_id']}: the caller asked for "
        f"{call_result['longest']} minutes and the engine captured "
        f"{call_result['captured']!r}. Without the capture, "
        f"_resolve_duration_minutes has nothing to prefer and whatever the "
        f"model passes as duration_minutes wins by default"
    )


# ── 3. the diary gets what the caller asked for ────────────────────────────

@live_llm
def test_the_diary_gets_the_length_the_caller_asked_for(call_result):
    """CA86c320ef, 4 Aug 2026, live: the caller chose 90 minutes and a
    60-minute event went into the diary — wrong length, wrong price, and the
    slot after it still offerable to the next caller. The wrong END time
    survives every verbal read-back, so nothing in the conversation catches it.
    """
    if not call_result["bookings"]:
        pytest.skip(
            "the call did not reach a booking inside the turn budget — the "
            "scripted caller lost the thread, which is not an engine verdict"
        )
    b = call_result["bookings"][0]
    start = datetime.fromisoformat(b.start.replace("Z", ""))
    end = datetime.fromisoformat(b.end.replace("Z", ""))
    minutes = int((end - start).total_seconds() // 60)
    assert minutes == call_result["longest"], (
        f"{call_result['clinic_id']}: caller asked for "
        f"{call_result['longest']} minutes, diary got {minutes} "
        f"({b.start} -> {b.end}, duration_min={b.duration_min}). This is "
        f"CA86c320ef: wrong length, wrong price, and the following slot is "
        f"still bookable by someone else"
    )


@live_llm
def test_the_booking_names_the_service_the_caller_asked_for(call_result):
    """F-021's family: the caller asks for a sports massage and the diary says
    Deep Tissue. Cheap to check while a real booking is in hand."""
    if not call_result["bookings"]:
        pytest.skip("no booking to inspect")
    booked = (call_result["bookings"][0].service or "").lower().replace("_", " ")
    asked = (call_result["service"] or "").lower()
    head = asked.split()[0] if asked else ""
    assert head and head in booked, (
        f"{call_result['clinic_id']}: caller asked for {asked!r}, diary got "
        f"{booked!r}"
    )
