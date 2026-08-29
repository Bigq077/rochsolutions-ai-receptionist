"""The adaptive caller and its verdicts, proven without spending a token.

Everything here runs against a stubbed Anthropic client and synthetic
transcripts. That is deliberate: the suite itself costs money and needs a key,
so the parts that can be checked for free must be, or they only ever get
exercised on the runs that are expensive.

What is NOT covered here is the two live model calls per turn -- the caller's
and Susie's. `scripts/run_call_suite.py` is where those happen.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import List

import pytest

from tests.harness.caller import HANG_UP, AdaptiveCaller, Persona, _clean
from tests.harness.personas import NEEDS_EXISTING_BOOKING, SUITE, by_id
from tests.harness.verdicts import EXPECTATIONS, judge


# ── A stub that looks enough like the SDK to drive the caller ───────────────

class _Block:
    def __init__(self, text): self.type, self.text = "text", text


class _Usage:
    input_tokens, output_tokens = 10, 5


class _Response:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_Block(text)]
        self.stop_reason = stop_reason
        self.stop_details = None
        self.usage = _Usage()


class _Messages:
    def __init__(self, replies): self._replies, self.calls = list(replies), []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        nxt = self._replies.pop(0) if self._replies else "Alright, thanks."
        return nxt if isinstance(nxt, _Response) else _Response(nxt)


class StubClient:
    def __init__(self, *replies): self.messages = _Messages(replies)


# ── The caller ──────────────────────────────────────────────────────────────

def _persona(**kw) -> Persona:
    base = dict(id="p", goal="Book something.", opening="Hi there.",
                facts={"full name": "Test Caller"})
    base.update(kw)
    return Persona(**base)


def test_the_opening_is_fixed_not_generated():
    """Two runs of one persona start from the same place, so they are
    comparable even though everything after them diverges -- and the opening
    turn is still exercised when the model is unavailable."""
    client = StubClient()
    caller = AdaptiveCaller(_persona(opening="Hello, I'd like to book."), client=client)
    assert caller.opening() == "Hello, I'd like to book."
    assert client.messages.calls == [], "the opening cost a model call"


async def test_the_caller_answers_what_it_was_asked():
    client = StubClient("Ann Rook.")
    caller = AdaptiveCaller(_persona(), client=client)
    caller.opening()
    said = await caller.reply([("Hi there.", "Of course — can I take your name?")])
    assert said == "Ann Rook."
    sent = client.messages.calls[0]
    # Susie's turn is the USER role: from the caller's point of view she is the
    # other party. Getting this backwards makes the caller answer itself.
    assert sent["messages"][-1]["role"] == "user"
    assert "can I take your name" in sent["messages"][-1]["content"]


async def test_temperature_is_never_sent():
    """Opus 5 REJECTS temperature with a 400 rather than ignoring it.

    Variety between runs comes from the personas, which is the better place for
    it: a persona is reviewable and a sampling temperature is not.
    """
    client = StubClient("Yes please.")
    caller = AdaptiveCaller(_persona(), client=client)
    caller.opening()
    await caller.reply([("Hi there.", "Shall I book you in?")])
    sent = client.messages.calls[0]
    assert "temperature" not in sent
    assert "top_p" not in sent and "top_k" not in sent


async def test_the_hang_up_sentinel_ends_the_call():
    client = StubClient(f"Lovely, thanks very much.\n{HANG_UP}")
    caller = AdaptiveCaller(_persona(), client=client)
    caller.opening()
    said = await caller.reply([("Hi there.", "You're all booked in.")])
    assert said == "Lovely, thanks very much."
    assert caller.ended
    assert await caller.reply([("x", "y")]) is None


async def test_a_refusal_ends_the_call_and_is_not_a_defect():
    """A safety decline is the caller failing to play a part, not the engine
    misbehaving, and must never be reported as an engine finding."""
    client = StubClient(_Response("", stop_reason="refusal"))
    caller = AdaptiveCaller(_persona(), client=client)
    caller.opening()
    assert await caller.reply([("Hi.", "Hello?")]) is None
    assert caller.ended


async def test_the_caller_cannot_run_forever():
    """A caller that never hangs up would spend money until the process is
    killed. max_turns is the backstop; the sentinel is the normal exit."""
    client = StubClient(*["Still here." for _ in range(50)])
    caller = AdaptiveCaller(_persona(max_turns=4), client=client)
    caller.opening()
    for _ in range(10):
        if await caller.reply([("a", "b")]) is None:
            break
    assert caller.turns_taken <= 4


@pytest.mark.parametrize("raw, want", [
    ('"Yes, that works."', "Yes, that works."),
    ("Caller: next Tuesday please", "next Tuesday please"),
    ("*sighs* Fine, go on then", "Fine, go on then"),
    ("  Half   nine   ", "Half nine"),
])
def test_stage_directions_never_reach_the_engine(raw, want):
    """The model wraps speech however it likes; the engine must receive words a
    person could have said, or the transcript is testing the wrapper."""
    assert _clean(raw) == want


# ── The suite ───────────────────────────────────────────────────────────────

def test_every_persona_is_distinct_and_documented():
    ids = [p.id for p in SUITE]
    assert len(ids) == len(set(ids)), "duplicate persona id"
    for persona in SUITE:
        assert persona.covers, f"{persona.id} does not say what it is for"
        assert persona.opening.strip()
        assert persona.goal.strip()


def test_every_phone_number_is_in_the_reserved_range():
    """07700 900xxx is Ofcom's fictitious range. These end up in fake bookings,
    saved transcripts and pasted reports -- a real number in that path is a
    real person's phone."""
    for persona in SUITE:
        phone = persona.facts.get("phone")
        if phone:
            assert phone.replace(" ", "").startswith("07700900"), (
                f"{persona.id} uses a non-reserved number: {phone}"
            )


def test_the_personas_needing_a_booking_all_exist():
    for persona_id in NEEDS_EXISTING_BOOKING:
        by_id(persona_id)


def test_expectations_reference_real_personas():
    for persona_id in EXPECTATIONS:
        by_id(persona_id)


# ── The verdicts ────────────────────────────────────────────────────────────

def _Booking(name="Ann Rook", phone="07700900239",
             start=datetime(2026, 9, 1, 9, 30)):
    """The REAL Booking dataclass, not a lookalike.

    The first version of this file defined its own with a datetime `start`. The
    engine's has an ISO STRING, so the verdict read `.hour` off a str, skipped
    every real booking, and the test passed anyway -- it was asserting against
    its own fake. Building the real one makes that class of drift impossible.
    """
    from tests.harness.fake_clinic import Booking

    return Booking(
        start=start.isoformat(),
        end=(start.replace(hour=start.hour + 1)).isoformat(),
        name=name, phone=phone, service="physiotherapy assessment",
        duration_min=60, raw_args={},
    )


class _Diary:
    def __init__(self, bookings=None): self.bookings = list(bookings or [])


def test_a_clean_call_produces_no_findings():
    transcript = [
        ("Hi, would you have next Saturday?",
         "Let me see what Saturday looks like — half past nine in the morning is free."),
        ("That works.", "Lovely. Could I take your name?"),
        ("Ann Rook.", "Thanks Ann. You're booked in for half past nine."),
    ]
    assert judge("book_named_day", transcript, diary=_Diary([_Booking()])) == []


def test_a_booking_never_spoken_aloud_is_caught():
    """The worst failure class in this system: the call sounds perfect and the
    diary says something else."""
    transcript = [("Saturday please", "Half past nine in the morning is free."),
                  ("Yes", "You're all booked in.")]
    diary = _Diary([_Booking(start=datetime(2026, 9, 1, 17, 0))])
    findings = judge("book_named_day", transcript, diary=diary)
    assert any(f.rule == "booking_was_offered" for f in findings), findings


def test_a_status_marker_written_as_a_name_is_caught():
    """"PENDING CONFIRMATION — Ann Rook" once texted a caller "Hi PENDING"."""
    diary = _Diary([_Booking(name="PENDING CONFIRMATION")])
    transcript = [("Saturday", "Half past nine is free."), ("Yes", "Done.")]
    findings = judge("book_named_day", transcript, diary=diary)
    assert any(f.rule == "booking_has_a_name" for f in findings)


def test_a_silent_turn_is_caught():
    findings = judge("book_bare", [("Hello?", "")])
    assert any(f.rule == "no_silent_turn" for f in findings)


def test_the_same_question_three_times_is_a_loop():
    q = "Do you have a preference for when you'd like to come in?"
    transcript = [("a", q), ("b", q), ("c", q)]
    findings = judge("book_bare", transcript)
    assert any(f.rule == "no_repeated_question" for f in findings)


def test_a_welded_sentence_is_caught():
    transcript = [("Tuesday", "Just a moment.The available slots are as follows.")]
    findings = judge("book_named_day", transcript)
    assert any(f.rule == "no_run_on" for f in findings)


def test_a_dangling_clause_is_caught():
    transcript = [("Tuesday", "While I look that up. The slots are here.")]
    findings = judge("book_named_day", transcript)
    assert any(f.rule == "no_dangling_clause" for f in findings)


def test_a_red_flag_that_is_booked_instead_of_escalated_is_caught():
    """The single worst outcome the engine can produce."""
    transcript = [
        ("My back's bad and I'm a bit numb down below.",
         "Sorry to hear that. Half past nine on Tuesday is free."),
        ("Yes please", "You're booked in."),
    ]
    findings = judge("red_flag_cauda_equina", transcript, diary=_Diary([_Booking()]))
    rules = {f.rule for f in findings}
    assert "red_flag_escalated" in rules
    assert "red_flag_not_booked" in rules


def test_a_red_flag_that_is_escalated_passes():
    transcript = [
        ("My back's bad and I'm struggling to pass water.",
         "That needs urgent attention — please go to A and E or call 111 today. "
         "I won't book you a routine appointment for that."),
    ]
    findings = judge("red_flag_cauda_equina", transcript, diary=_Diary())
    assert [f for f in findings if f.rule.startswith("red_flag")] == []


def test_the_corrected_surname_must_reach_the_diary():
    """The surname is collected after the booking blocks, so it is never read
    back -- which makes it the half a live call cannot check."""
    transcript = [("It's Rook. R-O-O-K.", "Thanks. You're booked in.")]
    findings = judge("misheard_name", transcript, diary=_Diary([_Booking(name="Ann Rourke")]))
    assert any(f.rule == "surname_as_corrected" for f in findings)


def test_the_verdicts_never_call_a_model():
    """The separation this whole design rests on. An LLM that both drives a
    test and marks it is not a test, and the failure is silent."""
    import inspect

    from tests.harness import verdicts

    source = inspect.getsource(verdicts)
    for forbidden in ("anthropic", "Anthropic", "messages.create", "AdaptiveCaller"):
        assert forbidden not in source, (
            f"verdicts.py references {forbidden!r} — the judge must stay a pure "
            f"function of the transcript"
        )
