"""Defect B: a one-slot offer is an OFFER, and a retracted one is nothing.

`CA5c69c585`, northgate demo line, 12 Sep 2026 17:36:16, build 35f06eb6:

    [ms_gate5] the caller named a time (['16:00']) -- ONE slot (DT-7/8)
    offer built: "The nearest I've got to four in the afternoon is twenty past
                  four in the afternoon. Shall I book that in for you?"
    [slot_guard] SPOKEN SLOT FACT NOT IN THE DIARY (enforce): '4 in the
                  afternoon' reads as 16:00 ... -> REPLACED
    [ms_conn] slot_guard REPLACED a slot fact -> 'Sorry — let me just
                  double-check that one for you.'

and obs stored, for that call:

    slot_offers[0]  mode one_slot  spoken: true   (the transcript holds only
                                                    the recovery line)
    collected.selected_slot = 2026-09-15T16:20     (no one had said yes)

Two faults, one producer. `apply_resolved_time_to_session` wrote
`selected_slot` at BUILD time -- an offer that ends "shall I book that in?"
recorded as the caller's choice before they answered -- and when the fact
guard replaced the sentence at TTS nothing unwound `last_offered_slots`, the
spoken record (inv. 16) or the D-o readout, so the slot stayed on the table
for the next utterance to resolve against (defect A) and "say that again"
would have re-spoken the retracted sentence.

This pins: (1) the producer no longer writes `selected_slot`; (2)
`retract_offer` on the replaced sentence leaves nothing on the table, nothing
recorded as heard, and the obs row `spoken: false`; (3) a model sentence that
is NOT the offer retracts nothing; (4) a yes after a SPOKEN offer still pins.
Payload is the call's own `calls.slot_offers[0].payload`.
"""
from __future__ import annotations

from app.obs.slot_offers import offers_block, record_offer
from app.tools.slot_fact_guard import RECOVERY_SENTENCE
from app.tools.slot_followup import (
    ACCEPTED_SLOT_RECORD_KEY,
    LAST_READOUT_KEY,
    apply_resolved_time_to_session,
    retract_offer,
    slot_accepted_by_caller,
    spoken_starts_for_offer,
)

TUESDAY = {
    "date": "2026-09-15",
    "day_label": "Tuesday 15th September",
    "slot_times": ["08:00", "08:50", "09:40", "10:30", "11:20", "12:10",
                   "13:00", "13:50", "14:40", "15:30", "16:20", "17:10"],
    "slot_times_spoken": [
        "eight in the morning", "ten to nine in the morning",
        "twenty to ten in the morning", "half past ten in the morning",
        "twenty past eleven in the morning", "ten past twelve in the afternoon",
        "one in the afternoon", "ten to two in the afternoon",
        "twenty to three in the afternoon", "half past three in the afternoon",
        "twenty past four in the afternoon", "ten past five in the evening",
    ],
    "times_not_shown": 0,
}
SLOT_1620 = {
    "start": "2026-09-15T16:20:00+01:00",
    "end": "2026-09-15T17:05:00+01:00",
    "date": "2026-09-15",
    "time": "16:20",
    "spoken": "twenty past four in the afternoon",
    "day_label": "Tuesday 15th September",
}


def _build():
    session = {"available_days": [TUESDAY]}
    speech = apply_resolved_time_to_session(session, SLOT_1620, asked=["16:00"])
    # The obs row the producer writes beside the offer, as gate5 does.
    record_offer(
        session, payload_days=[TUESDAY],
        offer={"chunks": [speech], "slots": [SLOT_1620], "dtmf_map": {}},
        source="gate5", spoken=True,
    )
    return session, speech


def test_the_one_slot_offer_does_not_write_selected_slot():
    session, speech = _build()
    assert "twenty past four" in speech
    assert "selected_slot" not in session, (
        "the one-slot OFFER recorded itself as the caller's choice before "
        "they answered -- collected.selected_slot = 16:20 on CA5c69c585"
    )
    assert session["last_offered_slots"] == [
        {"start": SLOT_1620["start"], "end": SLOT_1620["end"]}
    ]
    assert "2026-09-15T16:20:00" in spoken_starts_for_offer(session)


def test_a_guard_replacement_takes_the_offer_off_the_table():
    session, speech = _build()
    # What `_tts_loop` does when `check_outgoing` returns RECOVERY_SENTENCE
    # for this sentence.
    assert retract_offer(session, speech) is True
    assert RECOVERY_SENTENCE  # the caller hears this instead; not our concern
    assert not session.get("last_offered_slots"), "the retracted slot was still on the table"
    assert not session.get("slot_labels")
    assert LAST_READOUT_KEY not in session, "'say that again' would re-speak a retracted offer"
    assert "_slot_readout_chunks" not in session
    assert "2026-09-15T16:20:00" not in spoken_starts_for_offer(session), (
        "inv. 16: a slot the guard stopped being spoken is recorded as heard"
    )
    rows = offers_block(session)
    assert rows and rows[-1]["spoken"] is False
    assert rows[-1]["retracted"] == "slot_fact_guard"
    # And the exact call-3 turn, after the retraction: nothing to accept.
    assert slot_accepted_by_caller(session, "hello you still there") is None
    assert slot_accepted_by_caller(session, "yes go for it") is None, (
        "a yes to an offer the caller never heard pinned a slot"
    )
    assert ACCEPTED_SLOT_RECORD_KEY not in session


def test_the_tts_splitter_hands_the_guard_one_sentence_of_the_offer():
    """The guard sees sentences; the readout stores the whole chunk."""
    session, speech = _build()
    first_sentence = speech.split(". ")[0] + "."
    assert first_sentence != speech
    assert retract_offer(session, first_sentence) is True
    assert not session.get("last_offered_slots")


def test_a_replaced_model_sentence_is_not_a_retraction():
    """The guard also replaces a MODEL sentence carrying a stray time (11 Sep
    00:04, "twenty to twelve"). That must not wipe an offer the caller heard."""
    session, speech = _build()
    stray = "So that's Monday the 14th at twenty to twelve — shall I book that in?"
    assert retract_offer(session, stray) is False
    assert session["last_offered_slots"] == [
        {"start": SLOT_1620["start"], "end": SLOT_1620["end"]}
    ]
    assert "2026-09-15T16:20:00" in spoken_starts_for_offer(session)
    assert offers_block(session)[-1]["spoken"] is True


def test_a_yes_after_a_spoken_offer_still_pins_the_slot():
    """Nothing here may cost the ordinary acceptance."""
    session, _ = _build()
    got = slot_accepted_by_caller(session, "uh yes go for it")
    assert (got or "")[:19] == "2026-09-15T16:20:00"


def test_retract_never_raises_on_a_bad_session():
    assert retract_offer(None, "anything") is False  # type: ignore[arg-type]
    assert retract_offer({}, "") is False
    assert retract_offer({LAST_READOUT_KEY: "not a dict"}, "x") is False
