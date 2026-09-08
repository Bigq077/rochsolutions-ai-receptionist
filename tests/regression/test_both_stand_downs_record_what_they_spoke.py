"""B-134 had a second door, and it was the one a patient line went through.

`_flush_slot_buf` has TWO stand-down branches. Both suppress the deterministic
offer and speak the MODEL instead, so both leave `last_offered_slots`
describing an offer the caller can no longer hear:

    P6   the model numbered no options and an offer is already standing
    P6b  the caller's acceptance resolved this turn and the model names it

B-134 taught P6 to record the slots its sentence actually spoke. **P6b was
never given the same treatment**, and nothing said why -- the two branches sit
eight lines apart and only one of them repairs the record.

-- THE LIVE CALL ------------------------------------------------------------
`CA4215ab7f6c28a49f89a8f87ce781edd4`, theorem_v3, 2026-09-08 01:15, build
`08e99fab`.

    01:15:18  offer: Wednesday 9th September -- nine, ten, three.
    01:15:32  caller: "yeah three works"      -> ACCEPTED 2026-09-09T15:00
    01:15:39  [ms_gate5] STOOD DOWN ... (P6b)
              model: "here's what we've got coming up -- Number 1, Friday 11th
                      September ... Number 2, Monday 14th ... Number 3,
                      Tuesday 15th ..."
    01:15:56  tts_finished in 16.1s
    01:16:01  caller: "let me if the last one works for me"
    01:16:10  caller hung up.   outcome=abandoned  score=2

Six genuine payload times were read out and **not one was recorded**. "The last
one" therefore resolved against the PREVIOUS offer -- the Wednesday slot he had
already accepted -- rather than the last of what he had just been read. Exactly
B-134's failure, one branch over.

-- WHY FIXING THE STAND-DOWN DOES NOT CLOSE THIS ----------------------------
`8b97f1e4` stops P6b firing on this call, and it is right to. But P6b standing
down is a legitimate thing that will keep happening, and a sentence that
confirms a pick may also offer an alternative:

    "That's Wednesday at three -- or I've also got Friday at nine."

That names the accepted slot, so P6b fires CORRECTLY, and strands the
alternative in precisely the same way. Two defects, one call; this is the
second, and it outlives the first.

-- WHAT THIS FILE PINS ------------------------------------------------------
B-134's own tests reimplement the branch body locally, so they pass whether or
not any call site invokes it -- the failure mode B-134's commit message named
out loud. So the source check below is not decoration: it is the only thing
that would have caught this omission, and the only thing that will catch the
next branch added beside these two.
"""
from __future__ import annotations

import inspect
import re

import pytest

from app.media_streams import llm_stream
from app.tools.receptionist_tools import _spoken_slot_time
from app.tools.slot_followup import payload_slots_named_in, slot_accepted_by_caller
from app.tools.slot_offer import apply_offer_to_session

# -- the call's payload ------------------------------------------------------
_DAYS = [
    ("2026-09-09", "Wednesday 9th September", ["09:00", "10:00", "15:00"]),
    ("2026-09-11", "Friday 11th September", ["09:00", "14:00"]),
    ("2026-09-14", "Monday 14th September", ["09:00", "18:00"]),
    ("2026-09-15", "Tuesday 15th September", ["09:00", "16:00"]),
]

#: Verbatim from the Gate 5 log, and 16.1 seconds long in the caller's ear.
MODEL_SENTENCE = (
    "here's what we've got coming up - Number 1, Friday 11th September - "
    "nine in the morning or two in the afternoon. Number 2, Monday 14th "
    "September - nine in the morning or six in the evening. Number 3, "
    "Tuesday 15th September - nine in the morning or four in the afternoon. "
    "Any of those suit you?"
)


def _day(date, label, times):
    return {
        "date": date, "day_label": label,
        "slot_times": list(times),
        "slot_times_spoken": [_spoken_slot_time(t) for t in times],
        "slots": [{"start": "%sT%s:00+01:00" % (date, t), "end": "",
                   "date": date, "day_label": label, "time": t,
                   "spoken": _spoken_slot_time(t)} for t in times],
    }


def _session():
    """As it stood when P6b fired: Wednesday is the record, and the caller has
    accepted three o'clock on it."""
    return {
        "available_days": [_day(*d) for d in _DAYS],
        "_accepted_slot_iso": "2026-09-09T15:00:00+01:00",
        "last_offered_slots": [
            {"start": "2026-09-09T%s:00+01:00" % t, "end": ""}
            for t in ("09:00", "10:00", "15:00")
        ],
        "slot_labels": [_spoken_slot_time(t) for t in ("09:00", "10:00", "15:00")],
        "slot_starts_spoken": ["2026-09-09T%s:00" % t
                               for t in ("09:00", "10:00", "15:00")],
        "v3_dtmf_slot_map": {"1": "Wednesday 9th September"},
        "v3_awaiting_slot_selection": True,
    }


def _record(session, sentence=MODEL_SENTENCE):
    """The helper's contract, stated where a test can read it."""
    named = payload_slots_named_in(session, sentence)
    already = {str((s or {}).get("start") or "")[:19]
               for s in (session.get("last_offered_slots") or [])
               if isinstance(s, dict)}
    fresh = [s for s in named if str(s.get("start") or "")[:19] not in already]
    if fresh:
        apply_offer_to_session(
            session, {"slots": named, "dtmf_map": {}, "mode": "single_day"},
            [sentence])
    return fresh


# ---------------------------------------------------------------------------
# The call site -- the only check that would have caught the omission
# ---------------------------------------------------------------------------

def _flush_source():
    src = inspect.getsource(llm_stream)
    i = src.index("    async def _flush_slot_buf(")
    j = src.index("\n    async def ", i + 10)
    return src[i:j]


def test_every_stand_down_records_before_it_speaks():
    """Each branch that speaks the model INSTEAD of the deterministic offer must
    first record what that sentence names. Finding a third one that does not is
    this test doing its job -- record there too, do not relax the rule."""
    body = _flush_source()
    speaks = [m.start() for m in re.finditer(r"\n +for _c in _stand_down:", body)]
    assert len(speaks) >= 2, (
        "expected both stand-down branches to speak _stand_down; found %d -- "
        "re-aim this test rather than deleting it" % len(speaks))
    for at in speaks:
        window = body[max(0, at - 400):at]
        assert "_record_stood_down_slots(" in window, (
            "a stand-down speaks the model without recording what it says:\n%s"
            % body[max(0, at - 200):at + 120])


def test_the_recorder_is_shared_and_not_copied():
    """One body, two call sites. Two copies drift, and this defect IS the two
    copies having drifted."""
    body = _flush_source()
    assert body.count("def _record_stood_down_slots(") == 1
    assert body.count("_record_stood_down_slots(_model_text") == 2
    assert body.count("_spoken_slots = payload_slots_named_in") == 1, (
        "the recording logic appears more than once -- share it, do not copy it")


def test_the_recorder_never_raises():
    """A record that cannot be written must not cost the caller the sentence."""
    body = _flush_source()
    i = body.index("def _record_stood_down_slots(")
    j = body.index("from app.tools.slot_followup import accepted_slot_is_named_in", i)
    assert "except Exception:" in body[i:j], body[i:j][-400:]


# ---------------------------------------------------------------------------
# The behaviour
# ---------------------------------------------------------------------------

def test_the_six_spoken_times_were_recorded_nowhere():
    """Pins the defect, so this file cannot quietly stop testing anything."""
    session = _session()
    starts = {str(s["start"])[:19] for s in session["last_offered_slots"]}
    assert not any(str(s["start"])[:19] in starts
                   for s in payload_slots_named_in(session, MODEL_SENTENCE))


def test_all_six_reach_the_record():
    session = _session()
    fresh = _record(session)
    assert len(fresh) == 6, [str(s["start"])[:19] for s in fresh]
    assert {str(s["start"])[:16] for s in session["last_offered_slots"]} == {
        "2026-09-11T09:00", "2026-09-11T14:00",
        "2026-09-14T09:00", "2026-09-14T18:00",
        "2026-09-15T09:00", "2026-09-15T16:00",
    }


@pytest.mark.parametrize("said,iso", [
    ("tuesday at four in the afternoon works", "2026-09-15T16:00:00+01:00"),
    ("monday at six in the evening please", "2026-09-14T18:00:00+01:00"),
    ("friday at two in the afternoon", "2026-09-11T14:00:00+01:00"),
])
def test_a_pick_from_the_spoken_list_now_resolves(said, iso):
    """`slot_accepted_by_caller` takes a time "only among times the caller was
    actually READ". All three were read out and none was recorded, so all three
    resolved to nothing."""
    session = _session()
    assert slot_accepted_by_caller(session, said) is None
    _record(session)
    assert slot_accepted_by_caller(session, said) == iso


# ---------------------------------------------------------------------------
# The guards -- each is a way to make this a worse defect than the one it fixes
# ---------------------------------------------------------------------------

def test_a_plain_confirmation_leaves_the_record_alone():
    """P6b's REAL case. A confirmation names a slot the record already holds,
    so nothing is fresh and nothing is rewritten -- otherwise every confirmation
    renumbers the offer under a caller mid-sentence."""
    session = _session()
    before = list(session["last_offered_slots"])
    fresh = _record(
        session,
        "So that's Wednesday 9th September at three in the afternoon - shall I "
        "book that in?")
    assert fresh == []
    assert session["last_offered_slots"] == before


def test_a_confirmation_that_also_offers_an_alternative_records_only_it():
    """The shape that survives 8b97f1e4: P6b fires correctly and the
    alternative must still be reachable."""
    session = _session()
    _record(session,
            "That's Wednesday at three in the afternoon - or I've also got "
            "Friday 11th September at nine in the morning.")
    assert slot_accepted_by_caller(
        session, "actually friday at nine in the morning"
    ) == "2026-09-11T09:00:00+01:00"


def test_a_sentence_naming_no_times_changes_nothing():
    session = _session()
    before = list(session["last_offered_slots"])
    assert _record(session, "No rush at all - take your time.") == []
    assert session["last_offered_slots"] == before


def test_the_standing_keypad_map_is_left_alone():
    """Written as an assertion the opposite way round on the first attempt, and
    the code was right: `apply_offer_to_session` writes `v3_dtmf_slot_map` only
    for a map of two or more, so an empty one leaves the standing map in place.

    That is deliberate and load-bearing (B-78/B-80): popping it would hand the
    next turn permission to wipe `last_offered_slots`, which is the record this
    fix exists to protect. The recording must not buy a resolvable pick at the
    price of the thing that makes it resolvable."""
    session = _session()
    _record(session)
    assert session["v3_dtmf_slot_map"] == {"1": "Wednesday 9th September"}
    assert session.get("v3_awaiting_slot_selection") is True
