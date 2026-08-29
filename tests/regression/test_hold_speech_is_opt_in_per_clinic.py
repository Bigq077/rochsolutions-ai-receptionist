"""A clinic keeps the hold behaviour it runs today until someone opts it in.

WHY THIS EXISTS

`app/hold_speech.py` is canonical-only work. It replaced six independent
hold-phrase producers with one arbiter, and it is a real fix — the stored
corpus had 354 hold phrases across 98 calls, one call with 17, and 175 of 322
promising a lookup that never happened.

But it changes what a caller HEARS while waiting, and no patient line has heard
it. The clinic branches are meant to fold onto canonical, and a fold that
silently changes the sound of a live clinic's calls is not a fold anyone can
review. So the arbiter is now opt-in per clinic, defaulting to the behaviour
every live clinic already runs.

WHAT "OFF" MEANS, AND WHAT IT DOES NOT

Off is not silence, and it is not a tidied-up version of the old behaviour. It
is the pre-arbiter answer: speak, every time, from the same FILLER_PHRASES list
`connection.py` drew from before cbde450e, with no cross-producer latch and no
reasoning about the work in flight. A third behaviour that no clinic has ever
run would be worse than either of the two real ones, so the legacy branch
reproduces the old one rather than improving on it.

`test_every_producer_passes_the_switch` is the one that matters. The defect
class this whole module exists for was "six producers each deciding for
themselves"; a seventh producer that forgets the switch would be that defect
again, one clinic at a time.

Deterministic: no model, no network, no clinic files touched.
"""
from __future__ import annotations

import ast
from pathlib import Path

import app.clinic_config as cc
from app.hold_speech import WorkKind, decide_hold, hold_speech_enabled

REPO = Path(__file__).resolve().parents[2]
PRODUCERS = (
    REPO / "app" / "media_streams" / "connection.py",
    REPO / "app" / "media_streams" / "llm_stream.py",
    # filler_guard.py makes no decide_hold call today — it REPORTS rather than
    # asks (see the test at the bottom). Scanned anyway so that the day someone
    # routes it through the arbiter, they cannot forget the switch. Leaving it
    # out is how this pin claimed "every producer" while checking two of three.
    REPO / "app" / "media_streams" / "filler_guard.py",
)


# ---------------------------------------------------------------------------
# The default is what makes a fold neutral
# ---------------------------------------------------------------------------

def test_a_clinic_that_says_nothing_keeps_todays_behaviour():
    resolved = cc._map_json_to_clinic_contract({"operational": {}})
    assert resolved["hold_speech"] is False, (
        "hold speech must default OFF. It is canonical-only, it changes what a "
        "caller hears, and defaulting it on would make folding a clinic branch "
        "an audible change nobody signed off.")


# Clinics that have deliberately opted into the arbiter, and who decided.
HOLD_SPEECH_OPT_IN = {
    "northgate": "the demo tenant — no patients, opted in 2026-08-29 so the "
                 "arbiter can be heard before it is offered to anyone real",
}


def test_no_patient_line_hears_the_arbiter_without_someone_choosing_it():
    """Opting in is a real change to what a caller hears, so it is recorded.

    northgate is the demo clinic and exists to be experimented on. jv_v1,
    vital_edge and theorem carry real patients, and each is a separate
    conversation with a practitioner who has not yet heard it.
    """
    unlisted = [
        cid for cid in sorted(set(cc.TWILIO_TO_CLINIC.values()))
        if cc.get_clinic(cid).get("hold_speech") is True
        and cid not in HOLD_SPEECH_OPT_IN
    ]
    assert not unlisted, (
        f"{unlisted} opted into the arbiter with nobody recorded as choosing "
        "it. If their practitioner heard it and agreed, add them to "
        "HOLD_SPEECH_OPT_IN with who and when. If this arrived on a copied "
        "clinic.json, set operational.hold_speech false — the default.")


def test_the_demo_clinic_is_actually_on_so_it_can_be_heard():
    """The opt-in has to reach the resolver, or the listen proves nothing.

    Pinned because the first attempt to listen (2026-08-29) was made before any
    flag was committed: northgate was still False, so the call ran the legacy
    path and heard exactly the behaviour it already had.
    """
    assert cc.get_clinic("northgate").get("hold_speech") is True


def test_the_switch_reads_the_clinic_and_never_raises():
    assert hold_speech_enabled({"clinic_id": "jv_v1"}) is False
    assert hold_speech_enabled({}) is False
    assert hold_speech_enabled({"clinic_id": "no_such_clinic"}) is False


# ---------------------------------------------------------------------------
# Off is the OLD behaviour, not silence and not an improvement
# ---------------------------------------------------------------------------

def test_legacy_speaks_where_the_arbiter_would_suppress():
    """The arbiter's two suppression rules are exactly what legacy lacks."""
    for kwargs in (
        {"kind": WorkKind.PATIENT_LOOKUP, "head_already_spoken": True},
        {"kind": WorkKind.NONE, "head_already_spoken": False},
    ):
        assert decide_hold(legacy=False, **kwargs).speak is False
        legacy = decide_hold(legacy=True, **kwargs)
        assert legacy.speak is True, (
            "off must reproduce the pre-arbiter behaviour, which spoke on both "
            "of these — not stay silent, which no clinic has ever done")
        assert legacy.head


def test_legacy_draws_from_the_list_the_old_producers_used():
    from app.media_streams.config import FILLER_PHRASES

    heads = {decide_hold(kind=WorkKind.PATIENT_LOOKUP,
                         head_already_spoken=False, legacy=True).head
             for _ in range(40)}
    assert heads, "legacy produced no phrase at all"
    assert heads <= set(FILLER_PHRASES), (
        f"legacy invented wording the old producers never said: "
        f"{heads - set(FILLER_PHRASES)}")


def test_the_write_ack_override_survives_the_gate():
    """FM-25: an ambiguous reply must not become a booking claim.

    The pre-arbiter write-ack site tried confirm_write_filler FIRST and only
    then fell back to a neutral phrase. Losing that in the legacy branch would
    reintroduce a defect the gate is supposed to be neutral about.
    """
    d = decide_hold(kind=WorkKind.NONE, head_already_spoken=True, legacy=True,
                    legacy_override="Just locking that in now.")
    assert d.speak and d.head == "Just locking that in now."


# ---------------------------------------------------------------------------
# The structural pin
# ---------------------------------------------------------------------------

def test_every_producer_passes_the_switch():
    """A producer that calls the arbiter without `legacy=` ignores the clinic.

    Found mechanically rather than by review: the original defect was six
    producers each deciding for themselves, and a new one that forgets this
    argument is that defect returning one clinic at a time — silently, because
    it would simply use the arbiter everywhere.
    """
    missing = []
    for path in PRODUCERS:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in ("_hs_decide", "_decide_hold", "decide_hold"):
                continue
            if not any(kw.arg == "legacy" for kw in node.keywords):
                missing.append(f"{path.name}:{node.lineno}")

    assert not missing, (
        "these producers call the arbiter without passing the clinic's switch: "
        f"{missing}. Pass legacy=not hold_speech_enabled(session) — otherwise "
        "that producer uses the arbiter on every clinic regardless of whether "
        "its owner has opted in.")


# ---------------------------------------------------------------------------
# The limit of the guarantee, pinned so it is not mistaken for coverage
# ---------------------------------------------------------------------------

def test_filler_guard_reports_to_the_latch_but_does_not_ask_it():
    """FillerGuard is a producer the arbiter cannot suppress. Known, not fixed.

    It plays a recorded CLIP rather than TTS, and it calls note_filler_played(),
    which sets `_hold_head_spoken` — so it TELLS the arbiter it spoke, and every
    gated producer afterwards correctly stays quiet. What it never does is ASK,
    so its own 2.5s "second clip" escalation is outside the one-head-per-turn
    rule entirely.

    Seen live on CAc46c00705bc1ad81 (2026-08-29, northgate, hold_speech on):
    a clip at 350ms, a second at 2.5s, then the tool. The arbiter did its job —
    it suppressed the THIRD phrase that legacy would have spoken, because
    _FILLER_TOOLS["check_availability"] is a real list — but two clips still
    reached the caller, which is why the change is hard to hear.

    Pinned because hold_speech.py's docstring says stacking is "unrepresentable"
    by construction. With this producer outside the arbiter, it is representable
    again, and that gap should be a decision rather than a surprise.
    """
    guard = (REPO / "app" / "media_streams" / "filler_guard.py").read_text(
        encoding="utf-8")
    assert "decide_hold" not in guard, (
        "filler_guard now calls the arbiter — good, but this test documents the "
        "opposite. Update it, and make sure the call passes legacy=.")
    assert "note_filler_played" in guard, (
        "filler_guard stopped reporting that it spoke. Every gated producer "
        "reads _hold_head_spoken, so without this the arbiter will speak ON TOP "
        "of the clip.")
