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
    "vital_edge": (
        "OWNER decision (Quentin), 2026-09-01, as the first patient line. "
        "JONATHAN HAS NOT HEARD IT — the owner chose to go first and told "
        "the practitioner after, which is a weaker standard than this list "
        "was written to expect, and is recorded that way on purpose. "
        "Chosen first of the three because VE is provisional: "
        "confirm_write_kind(provisional=True) returns PENDING_REQUEST, so its "
        "write heads are 'Sending that over to Jonathan -', not the "
        "WRITE_BOOK pool that would claim a booking VE never makes "
        "(test_one_hold_phrase_that_never_lies pins that). Evidence it works "
        "at all is northgate's, not VE's — CA54f4a6b5, 2026-09-01, where the "
        "situational head fired and was heard. Revert = set "
        "operational.hold_speech false and drop this entry."
    ),
    "jv_v1": (
        "OWNER decision (Quentin), 2026-09-04, completing the roll-out — the "
        "holiday plan's item 4, 'port filler phrase work to joint venture and "
        "vital edge'. THE JV PRACTITIONER HAS NOT HEARD IT. That is the same "
        "weaker standard vital_edge was taken on three days earlier, and it is "
        "written down here rather than smoothed over, because this list exists "
        "to make the standard visible and not to certify it. "
        "WHY JV IS THE LOW-RISK ONE OF THE THREE AT THE TIME OF WRITING: its "
        "configuration is "
        "identical to northgate's in the only two respects the arbiter reads — "
        "booking_system is google_calendar so confirm_write_kind(provisional="
        "False) returns WRITE_BOOK, and it has a named practitioner. northgate "
        "is the shape the arbiter has run longest and the one every demo-line "
        "call this week exercised, so JV draws the pool with the most listening "
        "behind it. It can never draw VE's PENDING_REQUEST, which names the "
        "practitioner and would claim a request where JV writes a real "
        "booking. "
        "NOT YET AUDIBLE TO PATIENTS AT THE TIME OF WRITING: JV runs on "
        "`production`, so this reaches its callers only on the next "
        "fast-forward. That push is the decision point, not this commit. "
        "Revert = set operational.hold_speech false and drop this entry."
    ),
    "theorem_v2": (
        "OWNER decision (Quentin), 2026-09-06, completing the roll-out — the "
        "last of the four lines. MARK HAS NOT HEARD IT, the same weaker "
        "standard vital_edge and jv_v1 were taken on, recorded here rather "
        "than smoothed over. "
        "⚠️ THE KEY IS TOP-LEVEL FOR THEOREM AND ONLY FOR THEOREM. Theorem is "
        "a legacy CLINICS entry in clinic_config.py; `get_clinic` returns "
        "`dict(CLINICS[cid])` verbatim and never calls "
        "`_map_json_to_clinic_contract`, so `operational.hold_speech` here "
        "would be dead config — the exact inverse of jv_v1 and vital_edge, "
        "where a TOP-LEVEL key is overwritten with False. Two mechanisms, "
        "opposite answers, and both fail inaudibly. "
        "WHICH POOL IT DRAWS: `booking_system` is 'acuity', so `clinic_facts` "
        "reports provisional=False and the write head is WRITE_BOOK, 'Right, "
        "booking you in -' — true, because Theorem writes a real Acuity "
        "appointment. It can never draw PENDING_REQUEST, which would be doubly "
        "wrong here: it claims a request Theorem does not make, and Theorem "
        "carries no `practitioner` key to render into it. "
        "Revert = set CLINICS['theorem']['hold_speech'] False and drop this "
        "entry and theorem_v3's."
    ),
    "theorem_v3": (
        "OWNER decision (Quentin), 2026-09-06 — Mark's LIVE line, "
        "+447380841468. Not a separate decision from theorem_v2: "
        "`CLINICS['theorem_v3'] = deepcopy(CLINICS['theorem_v2'])`, itself a "
        "deepcopy of `CLINICS['theorem']`, so all three ids move together on "
        "one key and cannot be opted in separately. Listed separately anyway "
        "because this list is read as the record of who chose what, and a "
        "reader looking up the id that answers Mark's phone must find it. "
        "Same standard, same pool, same revert as theorem_v2."
    ),
}


def test_no_patient_line_hears_the_arbiter_without_someone_choosing_it():
    """Opting in is a real change to what a caller hears, so it is recorded.

    northgate is the demo clinic and exists to be experimented on. jv_v1,
    vital_edge and theorem_v2/_v3 carry real patients, and each is a separate
    conversation with a practitioner. As of 2026-09-06 all four are on by OWNER
    decision, and NOT ONE of the three practitioners has heard the arbiter
    first. That is the standard actually used, and the entries say so —
    this list exists to make it visible, not to certify it.
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
    """`demo` is the off example now — jv_v1, then theorem, then this.

    jv_v1 was the stand-in for "a real clinic that has not opted in" until it
    opted in on 2026-09-04; theorem took the role until 2026-09-06. Re-aimed
    each time rather than deleted, because the point of the first line is that
    the switch reads a REAL config and returns False for it — an unknown id and
    an empty session cannot show that, since both would pass against a function
    that returned False unconditionally.

    `demo` is the last one left and is not going to opt in: it answers no
    Twilio number (`TWILIO_TO_CLINIC` has no entry for it) and is `get_clinic`'s
    fallback for an unrecognised id. If it ever does, this line needs a
    different real clinic, not deleting.
    """
    assert "demo" not in set(cc.TWILIO_TO_CLINIC.values()), (
        "demo now answers a real line — pick a different off example"
    )
    assert hold_speech_enabled({"clinic_id": "demo"}) is False
    assert hold_speech_enabled({}) is False
    assert hold_speech_enabled({"clinic_id": "no_such_clinic"}) is False


def test_every_live_line_is_accounted_for():
    """The list must cover the lines, not merely agree with them.

    `test_no_patient_line_hears_the_arbiter_without_someone_choosing_it` only
    catches a clinic that is ON and unlisted. It would say nothing if a new
    Twilio number were added and left off — which is the shape of the next
    mistake, now that all four existing lines are on and the check has no
    negative case left to fire on.
    """
    live = set(cc.TWILIO_TO_CLINIC.values())
    unaccounted = sorted(live - set(HOLD_SPEECH_OPT_IN))
    assert not unaccounted, (
        f"{unaccounted} answer a real number and are in neither state anyone "
        "recorded. Either opt them in with who and when, or — if they are "
        "deliberately keeping the pre-arbiter behaviour — say so here."
    )


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

def test_the_clip_reports_that_it_spoke_and_never_speaks_twice():
    """FillerGuard plays a recorded CLIP, so it cannot ask the arbiter for
    wording -- but it must not be able to stack either.

    It used to do both halves wrongly. It called note_filler_played(), which
    sets `_hold_head_spoken`, so it TOLD the arbiter it had spoken and every
    gated producer afterwards correctly stayed quiet. What it never did was
    ASK, so its own 2.5s "second clip" escalation sat outside the
    one-head-per-turn rule entirely. Seen live on CAc46c00705bc1ad81
    (2026-08-29, northgate, hold_speech on): clip at 350ms, clip at 2.5s, then
    the tool. hold_speech.py's docstring claims stacking is "unrepresentable by
    construction", and with that producer outside the arbiter it was
    representable again.

    Resolved 2026-08-29 by deleting the second clip rather than by teaching it
    to ask: the owner rule is that the recorded filler belongs to the one moment
    before slots are read out, so two clips in 2.5 seconds breached it
    independently of the arbiter. One clip cannot stack with itself, so the
    guarantee is now structural rather than negotiated.

    Asserted on the module's SIGNATURE and behaviour, not by scanning its text.
    The previous version of this test scanned for the string "decide_hold" and
    was broken by a COMMENT explaining why the call is absent -- a text scan
    cannot tell coupling from prose.
    """
    import ast
    import inspect
    import textwrap

    from app.media_streams import filler_guard as fg

    # 1. There is no second clip to schedule.
    params = inspect.signature(fg.FillerGuard.__init__).parameters
    assert "clip_path_2" not in params, (
        "the second clip is back; it is the producer that never asked the "
        "arbiter, which makes one-head-per-turn a slogan again")
    assert "second_delay_ms" not in params

    # 2. _fire() sends audio exactly once. Counted on the AST so a second
    #    `await _send(...)` cannot creep back in behind a condition.
    src = inspect.getsource(fg.FillerGuard.arm)
    tree = ast.parse(textwrap.dedent(src))
    sends = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_send"
    ]
    assert len(sends) == 1, f"the clip is sent {len(sends)} times in one turn"

    # 3. It still REPORTS. Every gated producer reads _hold_head_spoken, so
    #    without this the arbiter speaks on top of the clip.
    guard_src = inspect.getsource(fg)
    assert "note_filler_played" in guard_src

