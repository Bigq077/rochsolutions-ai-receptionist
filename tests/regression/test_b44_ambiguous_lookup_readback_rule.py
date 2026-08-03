# tests/regression/test_b44_ambiguous_lookup_readback_rule.py
"""
B-44 — the B-42 gate is in the right place for safety and the wrong place for
the conversation.

`CAdbc84848ce5f62a4ee7964116a9b1d14`, 3 Aug 2026 10:55, build `0dc510d196f1`.
The B-42 gate worked perfectly and the call still took **89 seconds and seven
turns**, with the caller stating an intention to cancel **four times**:

    10:54:25  "i'd like to cancel my appointment please"
    10:55:04  "i'd like to cancel it"
    10:55:16  "uh yes"                       (to "is that you?")
    10:55:26  "oh i'd like to cancel it altogether"

Because identity was settled *after* the retention question, the retention
question had to be asked again. Neither gate was wrong in isolation — the
`"uh yes"` genuinely answered *"is that you?"* and not *"do you want to
cancel?"*. The **ordering** was wrong.

The natural place to say the name is the read-back, where Susie already recites
the day and time. This layer puts it there.

**Delivered on the tool result, not in the prompt.** Same reasoning as B-36's
Layer 2, which on `CA9cc1a23e` steered the model correctly and meant Gate 5f
never had to fire: the rule arrives at the moment of use, cannot drift out of
step with the gate, and reaches every clinic without touching a 24k-line prompt
or any `clinic.json`.

**Steering only.** If the model ignores it, the B-42 gate still refuses the
write. That division of labour is the point, and `test_the_rule_does_not_itself
_satisfy_the_gate` pins it.
"""
from __future__ import annotations

import inspect

import pytest

from app.media_streams import llm_stream as ls
from app.tools import receptionist_tools as rt
from app.tools.receptionist_tools import (
    _LOOKUP_AMBIGUOUS_RULE,
    _note_lookup_ambiguity,
    _with_ambiguity_rule,
)


def _result(name: str = "Quentin Rock") -> dict:
    return {"found": True, "patient_name": name, "match_count": 0}


# ── Attached when ambiguous, and only then ────────────────────────────────
@pytest.mark.parametrize("total", [2, 3, 12, 13])
def test_an_ambiguous_lookup_carries_the_readback_rule(total):
    out = _with_ambiguity_rule(_result(), "Quentin Rock", total)
    rule = out.get("caller_message_rule") or ""
    assert rule, "the model is given no instruction to name the patient"
    assert "Quentin Rock" in rule, "the rule must name the specific patient"


def test_a_single_match_carries_no_rule():
    """The overwhelmingly common case. Naming the patient here would add a turn
    to every ordinary cancel for no safety gain — the same reason
    book_appointment sits outside the B-42 gate."""
    assert "caller_message_rule" not in _with_ambiguity_rule(_result(), "Q", 1)


def test_a_missing_name_does_not_crash_the_format():
    out = _with_ambiguity_rule({}, "", 5)
    assert "that patient" in out["caller_message_rule"]


# ── What the rule actually has to say ─────────────────────────────────────
def test_the_rule_instructs_the_four_things_that_fix_the_ordering():
    r = _LOOKUP_AMBIGUOUS_RULE.lower()
    # 1. say the name
    assert "say the name" in r
    # 2. as an identity question, not "is that the right one?" — which is what
    #    the caller answered on CAe74ceae7 while confirming a DATE, not a PERSON
    assert "is that you?" in r
    # 3. BEFORE the retention question — this is the whole B-44 fix
    assert "reschedule or cancel" in r
    # 4. the escape hatch when it is not them
    assert "next=true" in r


def test_the_rule_and_the_gate_message_agree():
    """Two texts telling the model what to do about the same situation. If they
    diverge, one of them is training the model out of the other."""
    gate_src = inspect.getsource(ls.LLMStream._execute_tools)
    gate_msg_region = gate_src[
        gate_src.find("identity_confirmation_required"):
        gate_src.find("identity_confirmation_required") + 1600
    ]
    for token in ("next=true", "is that you"):
        assert token in gate_msg_region, f"gate message lost {token!r}"
        assert token in _LOOKUP_AMBIGUOUS_RULE, f"readback rule lost {token!r}"


# ── The division of labour — steering must not become permission ──────────
def test_the_rule_does_not_itself_satisfy_the_gate():
    """The load-bearing property. The rule is an INSTRUCTION to say the name;
    only the name actually reaching TTS may open the write gate. If attaching
    the rule released the gate, B-44 would silently undo B-42."""
    session = {"_lookup_patient_name": "Quentin Rock"}
    _note_lookup_ambiguity(session, 12)
    _with_ambiguity_rule(_result(), "Quentin Rock", 12)
    assert ls._lookup_identity_unconfirmed(session) is True


def test_following_the_rule_is_what_opens_the_gate():
    """The intended happy path: the model obeys, the name is spoken in the
    read-back, and the gate never fires on the write."""
    session = {"_lookup_patient_name": "Quentin Rock"}
    _note_lookup_ambiguity(session, 12)
    ls._note_lookup_name_spoken(
        session,
        "I've got an appointment for Quentin Rock on Wednesday the 5th at "
        "seven — is that you?",
    )
    assert ls._lookup_identity_unconfirmed(session) is False


def test_the_old_nameless_readback_still_does_not_open_the_gate():
    """Verbatim from CAe74ceae7 and CAdbc84848 — day and time, no name."""
    session = {"_lookup_patient_name": "Quentin Rock"}
    _note_lookup_ambiguity(session, 12)
    ls._note_lookup_name_spoken(
        session,
        "I can see an appointment on Wednesday the 5th of August at seven. "
        "Is that the right one?",
    )
    assert ls._lookup_identity_unconfirmed(session) is True


# ── Wiring: both back-ends, or one clinic runs without it ─────────────────
def test_both_lookup_backends_attach_the_rule():
    for fn in (rt._lookup_patient_gcal, rt._exec_lookup_patient):
        assert "_with_ambiguity_rule" in inspect.getsource(fn), (
            f"{fn.__name__} does not attach the read-back rule — that clinic "
            f"keeps the 89-second flow"
        )


def test_the_rule_rides_on_the_same_key_the_model_already_reads():
    """`caller_message_rule` is the key B-36 Layer 2 established for
    tool-result steering. Reusing it means no new contract for the model."""
    out = _with_ambiguity_rule(_result(), "Quentin Rock", 4)
    assert "caller_message_rule" in out


def test_the_lookup_rule_is_not_disturbed_by_the_write_recorder():
    """_note_write_result must keep passing lookup results through untouched —
    it is scoped to write TOOLS by name, and a lookup is not one."""
    out = _with_ambiguity_rule(_result(), "Quentin Rock", 4)
    same = ls._note_write_result({}, "lookup_patient", out)
    assert same is out
    assert "Quentin Rock" in same["caller_message_rule"]
