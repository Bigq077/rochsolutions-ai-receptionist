"""A second, different callback lead on one call was silently dropped.

`_queue_owner_callback_sms` opened with a per-CALL latch:

    if session.get("_waitlist_pinged"):
        return True

So a caller who asked for a callback and then asked for a SECOND, DIFFERENT
person to be rung back had that lead dropped — and `return True` reports
success, so the tool told the model "Clinic notified" and the call ended
normally. Nothing sounded wrong. It surfaced only when somebody was not rung
back.

FOUND BY THE 7 SEP AUDIT, not by a call. It was flagged on 1 Sep inside the P4
write-up, in a block quote under a **FIXED** heading —

    "Adjacent defect this exposed, NOT fixed: because that SMS dedup is
     per-CALL rather than per-LEAD, a caller who asks for a second, different
     person to be rung back has that lead silently dropped — the tool still
     returns 'Clinic notified'. Pre-existing and untouched by this fix."

— never given a row of its own, and so never carried forward.

TWO HALVES OF ONE RULE, DISAGREEING. `_same_callback_lead` (llm_stream, B-121)
had already decided this correctly, in these words: *"A caller may legitimately
ask for a second, different person to be rung back, and refusing that would be
the B-62 mistake in a new place: a real write suppressed because an earlier one
succeeded."* That gate let the second lead through, and the SMS queue's latch
then swallowed the text behind it. They now share `callback_lead_matches`, and
the last test here pins that they cannot drift apart again.
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, patch

import pytest

from app.tools.receptionist_tools import (
    _MAX_CALLBACK_LEADS,
    _exec_request_callback,
    callback_lead_matches,
)

_ALICE = {"patient_name": "Alice Brennan", "phone": "+447383262949",
          "notes": "wants a word with Jonathan"}
_DYLAN = {"patient_name": "Dylan Wilson", "phone": "+447700900123",
          "notes": "wants a quick chat before booking"}


def _session():
    return {"clinic_id": "vital_edge", "call_sid": "CAtest",
            "phone_confirmed": True}


async def _ask(session, lead, sent):
    async def _fake_send(*, to, message, **_kw):
        sent.append(message)
        return "SMfake"

    with patch("app.notifications.sms.send_sms",
               new=AsyncMock(side_effect=_fake_send)):
        result = await _exec_request_callback(dict(lead), session)
        await asyncio.sleep(0)          # let create_task run
    return result


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_second_different_lead_is_texted_too():
    session, sent = _session(), []

    first = await _ask(session, _ALICE, sent)
    second = await _ask(session, _DYLAN, sent)

    assert first.get("success") is True
    assert second.get("success") is True
    assert len(sent) == 2, (
        "the second lead was dropped — the caller was told the clinic had been "
        "notified and nobody will ring Dylan back. Sent: %r" % sent
    )
    assert any("Alice Brennan" in m for m in sent)
    assert any("Dylan Wilson" in m for m in sent), sent


@pytest.mark.asyncio
async def test_the_same_lead_twice_is_texted_once():
    """The farewell-turn re-fire B-121 was written for. Still one text."""
    session, sent = _session(), []
    await _ask(session, _ALICE, sent)
    await _ask(session, _ALICE, sent)
    assert len(sent) == 1, sent


@pytest.mark.asyncio
async def test_the_same_lead_is_remembered_after_a_different_one():
    """A, B, then A again.

    `callback_lead` holds only the LAST lead, so comparing against it alone
    would re-text Alice here. The list is what makes this one text each.
    """
    session, sent = _session(), []
    await _ask(session, _ALICE, sent)
    await _ask(session, _DYLAN, sent)
    await _ask(session, _ALICE, sent)
    assert len(sent) == 2, sent


@pytest.mark.asyncio
async def test_the_number_of_leads_per_call_is_bounded():
    """An owner-billed SMS on a path a model can re-enter.

    The per-call latch used to bound this at one, and removing it removes the
    bound — so the bound is now explicit. Reports success rather than failure:
    earlier leads DID reach the owner, and telling the model the write failed
    sends it round again on a path that is already looping.
    """
    session, sent = _session(), []
    for i in range(_MAX_CALLBACK_LEADS + 2):
        result = await _ask(
            session,
            {"patient_name": "Caller %d" % i, "phone": "+44770090010%d" % i,
             "notes": "n"},
            sent,
        )
        assert result.get("success") is True, i
    assert len(sent) == _MAX_CALLBACK_LEADS, sent


# ---------------------------------------------------------------------------
# What must NOT change — `_waitlist_pinged` keeps its meaning
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_teardown_latch_still_means_a_lead_was_texted():
    """`_waitlist_pinged` is read by the drop-off ping (connection.py:975),
    teardown (:18071) and the no-lead fallback — none of which asks about a
    PARTICULAR lead. Its meaning is untouched by making the dedup per-lead."""
    session, sent = _session(), []
    assert not session.get("_waitlist_pinged")
    await _ask(session, _ALICE, sent)
    assert session.get("_waitlist_pinged") is True
    await _ask(session, _DYLAN, sent)
    assert session.get("_waitlist_pinged") is True


@pytest.mark.asyncio
async def test_the_last_lead_is_still_recorded_for_the_duplicate_gate():
    """`_same_callback_lead` reads `callback_lead`, and a farewell-turn re-fire
    repeats the most RECENT lead — so that key must keep tracking the last one
    even though the list now tracks all of them."""
    session, sent = _session(), []
    await _ask(session, _ALICE, sent)
    await _ask(session, _DYLAN, sent)
    assert session["callback_lead"]["patient_name"] == "Dylan Wilson"
    assert len(session["callback_leads"]) == 2


def test_the_session_record_stays_serialisable():
    """The session is persisted, so the leads are a list and not a set."""
    from app.tools import receptionist_tools

    src = inspect.getsource(receptionist_tools._queue_owner_callback_sms)
    assert "callback_leads" in src
    assert "set(" not in src, "the session is serialised — a set will not survive"


# ---------------------------------------------------------------------------
# One owner for "is this the same lead"
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("a,b,same", [
    # the same person, written two ways
    (("Alice Brennan", "+447383262949"), ("alice  brennan", "07383 262949"), True),
    # different people
    (("Alice Brennan", "+447383262949"), ("Dylan Wilson", "+447700900123"), False),
    # same name, different number — a different lead
    (("Alice Brennan", "+447383262949"), ("Alice Brennan", "+447700900123"), False),
    # nothing to compare on either side: NOT a repeat, so it sends
    (("", ""), ("", ""), False),
    (("Alice Brennan", "+447383262949"), ("", ""), False),
])
def test_lead_identity_is_decided_in_one_place(a, b, same):
    prev = {"patient_name": a[0], "phone": a[1]}
    assert callback_lead_matches(prev, b[0], b[1]) is same


def test_the_llm_gate_delegates_rather_than_copying():
    """Two copies of this comparison is how a real second lead gets called a
    repeat — which is the defect this file exists for, in the other direction.
    """
    from app.media_streams import llm_stream

    src = inspect.getsource(llm_stream._same_callback_lead)
    assert "callback_lead_matches" in src, (
        "_same_callback_lead has its own copy of the comparison again")
    assert "isdigit()" not in src, "the normalisation has been re-inlined here"
