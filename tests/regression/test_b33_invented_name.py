# tests/regression/test_b33_invented_name.py
"""
B-33 — a patient name was invented from Susie's own utterance.

`CAc3c4e6619660fa69416e8545c9d5674a`, 3 Aug 2026 00:20:05. The caller had said
exactly one thing — *"i've hurt my ankle"* — and had given no name:

    [ms_conn v3] name persisted (normal path): 'Rehab'
    v3_phone_dtmf_active = True (name confirmed — phone collection phase)
    📊 Row built — outcome=abandoned name=Rehab phone=yes dur=33s

DTMF phone collection armed behind the invented name, so a caller typing digits
at that moment would have had the appointment written under "Rehab".

**The register's recorded mechanism was inferred and wrong in its specifics** —
it said "a capitalised word inside a long clinical explanation was read as a
confirmation". Reproducing it found three separate faults, and the capitalisation
is *manufactured by our own pipeline*:

  1. **Gate 5 creates the shape.** It strips a banned opener ("Of course — ")
     and then RE-CAPITALISES the next word (`turn_handler`, "Fix A"). An ordinary
     mid-sentence noun becomes a sentence-initial title-case word — exactly what
     the readback patterns hunt for.
  2. **Pattern 1d was in the ANCHORED list and did not belong there.** That
     list's own criterion is "an explicit acknowledgement verb or readback opener
     PRECEDES the captured word", and ANCHORED bypasses the phase gate entirely.
     Pattern 1d has no leading lexeme at all — only a trailing hint — so
     *"Massage — if you'd prefer something gentler…"* was read as a name.
  3. **Pattern 1c matched "right" mid-sentence.** Unanchored, it captured
     whatever followed: a practitioner's name or the clinic's town, neither of
     which any false-positive list catches.

The fix that closes the observed call is the phase gate: **a reply that ASKS for
the name cannot also read one back**, because the caller has not answered yet.
"""
from __future__ import annotations

import pytest

from app.media_streams import connection as c
from app.media_streams import turn_handler as th


def _persist(last_bot: str, caller: str = "", post_slot: bool = False):
    """Returns the stored patient_name, or None if nothing was persisted."""
    session: dict = {}
    stored = c._v3_try_persist_name(
        session, last_bot, post_slot_pending=post_slot, caller_utterance=caller
    )
    return session.get("patient_name") if stored else None


# ── The observed call, end to end through Gate 5 ──────────────────────────
def test_the_verbatim_b33_shape_persists_nothing():
    """The reply explains the injury AND asks for the name, so the caller
    cannot have answered yet. Nothing in it may be stored as a name."""
    raw = (
        "Of course — rehab, if you would like to come in Marcus can take a "
        "look. Could I take your first name?"
    )
    spoken = th.sanitise_response(raw, {"_clinical_depth_cache": ""})
    assert spoken.startswith("Rehab"), (
        "fixture drift: Gate 5 no longer manufactures the title-case opener, "
        "which is half of what made B-33 possible"
    )
    assert _persist(spoken, "i've hurt my ankle") is None


def test_gate5_still_manufactures_the_shape_this_guards_against():
    """Pins fault 1 explicitly. The name layer must stay safe against its own
    pipeline's re-capitalisation, not against a hypothetical model quirk."""
    for opener in ("Of course — ", "Absolutely — ", "Certainly, "):
        spoken = th.sanitise_response(
            opener + "massage, if you would prefer something gentler.",
            {"_clinical_depth_cache": ""},
        )
        assert spoken[:1].isupper() and spoken.lower().startswith("massage")


# ── Fault 2 — pattern 1d must not bypass the phase gate ───────────────────
@pytest.mark.parametrize(
    "reply",
    [
        "Massage — if you would prefer something gentler we offer that.",
        "Rehab — if you would like to come in we can take a look.",
        "Physio — could I check something first?",
        "Sports — got it, we do cover that.",
    ],
)
def test_service_words_are_not_names_when_no_name_was_asked(reply):
    assert _persist(reply, "my ankle hurts") is None


def test_pattern_1d_is_no_longer_in_the_anchored_set():
    """Structural. ANCHORED runs on every turn with no phase signal, so a
    pattern with no leading acknowledgement lexeme cannot live there."""
    anchored = [p.pattern for p in c._V3_NAME_CONFIRM_PATTERNS_ANCHORED]
    assert not any("got it|noted|perfect" in p for p in anchored), (
        "pattern 1d is back in ANCHORED — it will bypass the phase gate again"
    )
    bare = [p.pattern for p in c._V3_NAME_CONFIRM_PATTERNS_BARE]
    assert any("got it|noted|perfect" in p for p in bare)


# ── Fault 3 — "right" as an ordinary adjective ────────────────────────────
@pytest.mark.parametrize(
    "reply,would_have_captured",
    [
        ("If that does not feel right Marcus can take a look on Tuesday.", "Marcus"),
        ("That is not quite right Leanne usually covers the evenings.", "Leanne"),
        ("Yes that is right Bolton is our only site.", "Bolton"),
    ],
)
def test_mid_sentence_right_captures_nothing(reply, would_have_captured):
    """None of these are in any false-positive list, so before the fix a
    practitioner's name or the clinic's town became the PATIENT's name."""
    for lst in (
        c._V3_NAME_FALSE_POSITIVES,
        c._V3_SLOT_LEAD_WORDS,
        c._V3_NAME_LEAD_STOPWORDS,
    ):
        assert would_have_captured.lower() not in lst, (
            "fixture drift: this word is now filtered, so it no longer "
            "demonstrates the gap"
        )
    assert _persist(reply, "ok") is None


def test_right_as_a_genuine_opener_still_works():
    """The pattern exists for "Right Sarah — …". Anchoring must not kill it."""
    assert _persist("Right Sarah — could I take your number?", "sarah") == "Sarah"
    assert _persist("Okay. Right Sarah — and your surname?", "sarah") == "Sarah"


# ── The legitimate captures this function exists for ──────────────────────
def test_anchored_readback_still_works_with_no_phase_signal():
    """CA8f9c5578: the readback and the name request never co-occur, so
    "Thanks Sarah" must persist without post_slot_pending. That is the whole
    reason ANCHORED is ungated, and it must survive this change."""
    assert _persist(
        "Thanks Sarah — I have got you on oh seven five.", "uh sarah jenkins"
    ) == "Sarah Jenkins"


def test_so_thats_readback_still_works():
    assert _persist("So that's Quentin, Tuesday at ten.", "quentin rock") == "Quentin Rock"


@pytest.mark.parametrize(
    "reply",
    [
        "Sarah — got it, and your surname?",
        "Sarah — if you would like to use this number.",
        "Sarah, noted.",
    ],
)
def test_bare_readback_works_once_the_caller_has_actually_answered(reply):
    """post_slot_pending means the PREVIOUS turn asked, so this caller
    utterance IS the name. Bare patterns are legitimate here."""
    assert _persist(reply, "sarah", post_slot=True) == "Sarah"


def test_the_gate_now_turns_on_post_slot_pending_alone():
    """The precise change. Same reply, same caller utterance — the only
    difference is whether the caller had been asked yet."""
    reply = "Rehab — if you would like to come in we can take a look."
    assert _persist(reply, "i've hurt my ankle", post_slot=False) is None
    # With post_slot_pending the bare patterns are in play again by design;
    # this asserts the switch is the gate and nothing else.
    assert _persist(reply, "i've hurt my ankle", post_slot=True) == "Rehab"
