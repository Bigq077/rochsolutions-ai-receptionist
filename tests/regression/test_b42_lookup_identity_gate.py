# tests/regression/test_b42_lookup_identity_gate.py
"""
B-42 — a cancellation was confirmed against the wrong person's appointment.

`CAe74ceae7002d6cff1ba8a324f04cf134`, 3 Aug 2026 10:39, build `aa964ff8d173`.
The caller rang from `+447502211207`:

    10:39:01.704  lookup_patient (gcal): match 1/13 name='Sarah Jenkins'
    10:39:04.040  tts 'I can see an appointment on Wednesday the 5th of August at q…'
    10:39:04.251  tts 'is that the right one?'          <- NO NAME SPOKEN
    10:39:12.265  FINAL 'yes'
    10:39:26.890  {"success": true, "cancelled_event": "… — Sarah Jenkins"}

The caller confirmed a **date**, never a **person**.

On that call it was test-data contamination — 13 future appointments sat under
one phone number from a morning of testing. The mechanism is not test-specific:
a shared phone number is ordinary in physiotherapy (a couple, a parent booking
for a child, a carer), so the same path cancels the wrong family member's
appointment with nobody aware.

`_lookup_patient_gcal` returns `matches[0]` after sorting by start time. It
already reports `match_count` and `has_more` and supports `next=true` — nothing
was forcing them to be used.

The fix is a gate, not prompt wording, because the write is destructive and
invisible to the caller, and because B-36 cause 1 is the standing evidence that
a guarantee expressed only in the prompt evaporates the moment the model rewords.
"""
from __future__ import annotations

import inspect

import pytest

from app.media_streams import llm_stream as ls
from app.tools import receptionist_tools as rt
from app.tools.receptionist_tools import (
    LOOKUP_AMBIGUOUS_KEY,
    LOOKUP_NAME_SPOKEN_KEY,
    _note_lookup_ambiguity,
)


# The date the emitted match sits on. B-54 added a second latch on the
# appointment axis, so a session that only carries a name is no longer a
# realistic post-lookup session — both back-ends set this key at _emit.
_WHEN = "2026-08-05T18:15:00+01:00"          # Wednesday the 5th
_SAID = "Wednesday the 5th"                   # what satisfies the B-54 latch


def _looked_up(name: str, total: int, when: str = _WHEN) -> dict:
    """A session immediately after a lookup returned `total` matches."""
    s = {"_lookup_patient_name": name, "_lookup_appointment_datetime": when}
    _note_lookup_ambiguity(s, total)
    return s


def _say(session: dict, spoken: str) -> None:
    """Both latches read the same released-to-TTS text, so the tests must feed
    both or they assert against a state the engine can never be in."""
    ls._note_lookup_name_spoken(session, spoken)
    ls._note_lookup_slot_spoken(session, spoken)


# ── The observed failure ──────────────────────────────────────────────────
def test_the_verbatim_call_is_now_blocked():
    """13 matches, and the readback Susie actually gave carried no name."""
    s = _looked_up("Sarah Jenkins", 13)
    ls._note_lookup_name_spoken(
        s,
        "I can see an appointment on Wednesday the 5th of August at quarter "
        "past six. Is that the right one?",
    )
    assert ls._lookup_identity_unconfirmed(s) is True, (
        "the caller confirmed a date, not a person — the write must not proceed"
    )


def test_saying_the_name_and_the_date_releases_the_gate():
    s = _looked_up("Sarah Jenkins", 13)
    _say(s, f"I've got an appointment under Sarah Jenkins on {_SAID} — "
            "is that you? And is that the one you mean?")
    assert ls._lookup_identity_unconfirmed(s) is False


def test_the_name_alone_no_longer_releases_the_gate():
    """B-54, and the reason this file's expectations moved.

    Saying the name used to be sufficient. On CA156fa25 all 15 matches were the
    SAME person, so the name settled nothing and the earliest match was
    cancelled. The B-42 guarantee is untouched — a nameless read-back still
    blocks, asserted above. What changed is that the name is no longer the
    whole of the check.
    """
    s = _looked_up("Sarah Jenkins", 13)
    _say(s, "I've got an appointment under Sarah Jenkins — is that you?")
    assert ls._lookup_identity_unconfirmed(s) is True, (
        "the caller confirmed a person, not an appointment — with several on "
        "the number the write still must not proceed"
    )


def test_a_first_name_alone_is_enough():
    """Among people sharing a number the first name is the discriminator.
    Requiring the surname would loop the caller on a natural readback."""
    s = _looked_up("Sarah Jenkins", 4)
    _say(s, f"That one's under Sarah, on {_SAID} — is that you?")
    assert ls._lookup_identity_unconfirmed(s) is False


# ── Ambiguity marking ─────────────────────────────────────────────────────
@pytest.mark.parametrize("total,expected", [(1, False), (2, True), (13, True)])
def test_ambiguity_is_marked_from_the_match_count(total, expected):
    s = _looked_up("Sarah Jenkins", total)
    assert s[LOOKUP_AMBIGUOUS_KEY] is expected


def test_a_single_match_never_blocks():
    """The common case — one appointment on the number — must be untouched, or
    every ordinary cancel grows an extra turn."""
    s = _looked_up("Quentin Rock", 1)
    assert ls._lookup_identity_unconfirmed(s) is False


def test_stepping_to_the_next_match_re_arms_the_gate():
    """`next=true` changes WHO we are discussing, so the name must be said again."""
    s = _looked_up("Sarah Jenkins", 13)
    _say(s, f"That's under Sarah Jenkins, on {_SAID} — is that you?")
    assert ls._lookup_identity_unconfirmed(s) is False
    _note_lookup_ambiguity(s, 13)          # what _emit does on the next match
    s["_lookup_patient_name"] = "Quentin Rock"
    assert ls._lookup_identity_unconfirmed(s) is True, (
        "stepping to another person's appointment left the gate open"
    )


# ── False positives are the dangerous direction ───────────────────────────
@pytest.mark.parametrize(
    "spoken",
    [
        "You live near Brockley, is that right?",     # 'rock' inside a word
        "We're on Jenkinson Street.",                 # 'jenkins' inside a word
        "I can see an appointment on Wednesday.",     # no name at all
        "",
    ],
)
def test_a_lookalike_does_not_satisfy_the_gate(spoken):
    """A FALSE POSITIVE re-opens B-42; a false negative only re-asks. Word
    boundaries, not substrings."""
    s = _looked_up("Rock Jenkins", 13)
    ls._note_lookup_name_spoken(s, spoken)
    assert ls._lookup_identity_unconfirmed(s) is True


def test_the_latch_holds_once_set():
    s = _looked_up("Sarah Jenkins", 13)
    ls._note_lookup_name_spoken(s, "That's Sarah Jenkins' appointment, yes?")
    ls._note_lookup_name_spoken(s, "Right, let me sort that.")   # no name here
    assert s[LOOKUP_NAME_SPOKEN_KEY] is True


def test_no_looked_up_name_does_not_crash_or_release():
    s = {}
    _note_lookup_ambiguity(s, 5)
    ls._note_lookup_name_spoken(s, "Some speech.")
    assert ls._lookup_identity_unconfirmed(s) is True


# ── Wiring ────────────────────────────────────────────────────────────────
def test_both_lookup_backends_mark_ambiguity():
    """Template clinics use the Google Calendar path, Theorem uses Acuity.
    A gate that only covers one back-end is a gate one clinic does not have."""
    for fn in (rt._lookup_patient_gcal, rt._exec_lookup_patient):
        assert "_note_lookup_ambiguity" in inspect.getsource(fn), (
            f"{fn.__name__} does not mark lookup ambiguity"
        )


def test_the_gate_covers_both_destructive_writes():
    src = inspect.getsource(ls.LLMStream._execute_tools)
    assert '"reschedule_appointment", "cancel_appointment"' in src
    assert "identity_confirmation_required" in src


def test_the_identity_gate_runs_before_the_confirmation_gates():
    """There is no point asking "shall I move it?" while we do not know whose
    "it" is — and the identity message must be the one the model reads."""
    src = inspect.getsource(ls.LLMStream._execute_tools)
    identity = src.find("identity_confirmation_required")
    resched = src.find("reschedule_confirmation_required")
    cancel = src.find("cancellation_confirmation_required")
    assert identity != -1 and resched != -1 and cancel != -1
    assert identity < resched, "the reschedule CTA gate now pre-empts identity"
    assert identity < cancel, "the cancel retention gate now pre-empts identity"


def test_the_name_signal_reads_spoken_text_not_the_capped_prompt():
    """last_bot_prompt is capped at 200 chars (B-31/B-38). A readback long
    enough to lose the name to that cap is the turn where the caller most needs
    it, so the signal must come from the full spoken text."""
    src = inspect.getsource(ls.LLMStream.run_turn)
    assert "_note_lookup_name_spoken(session, _display_reply)" in src


def test_booking_is_not_affected():
    """book_appointment creates a NEW appointment for whoever is on the phone —
    there is no other patient's record to destroy, and it has its own surname
    and phone backstops."""
    src = inspect.getsource(ls.LLMStream._execute_tools)
    identity_block = src[src.find("identity_confirmation_required") - 2000:
                         src.find("identity_confirmation_required")]
    assert "book_appointment" not in identity_block.split("elif")[-1]


# ── Composition with B-36 ─────────────────────────────────────────────────
def test_the_block_arms_the_false_confirmation_guard():
    """The refusal carries no `success` key, so _note_write_result must still
    read it as a refusal — arming Gate 5f and attaching the do-not-claim rule.
    Otherwise a blocked identity check could still be narrated as done."""
    from app.media_streams import turn_handler as th
    s = {}
    out = ls._note_write_result(
        s, "cancel_appointment", {"status": "identity_confirmation_required"}
    )
    # B-58 reworded this rule to constrain speech rather than assert calendar
    # state; what this test guards is that a rule is attached at all, and that
    # it is the no-claim one rather than B-58's duplicate-write rule.
    assert out.get("caller_message_rule") == ls._WRITE_NO_CLAIM_RULE[
        th.WRITE_FAMILY_CANCEL
    ]
    assert th._armed_write_families(s) == [th.WRITE_FAMILY_CANCEL]
