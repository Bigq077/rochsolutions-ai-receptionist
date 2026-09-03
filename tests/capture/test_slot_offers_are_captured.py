"""The availability payload is stored, so the offer can be replayed.

WHY. Three defects in the week to 2026-09-03 were found by a phone call and
none by the suite -- the 8pm/10am wrong-time acceptance, an apology matcher
covering one of its head's two wordings, and a fix whose call-site wiring was
never exercised. Each was a mismatch between components, which a unit test
structurally cannot see.

The answer both plans give is a replay harness over the stored corpus, and it
could not be built. `replay_slot_readouts.py` says why in its first paragraph:
obs keeps transcripts, not availability payloads. Four of the harness's five
predicates take a payload or a session record, and neither was stored anywhere;
the Render log truncates the payload mid-array at ~200 characters.

This pins the half that was missing.

FORWARD-ONLY. Nothing here back-fills. Rows written before this column exists
are NULL by absence, not by measurement.
"""
from __future__ import annotations

import pytest

from app.obs.slot_offers import _MAX_OFFERS, offers_block, record_offer
from app.tools.slot_offer import build_slot_offer


def _day(date, label, times, spoken, hidden=0):
    return {
        "date": date,
        "day_label": label,
        "slot_times": list(times),
        "slot_times_spoken": list(spoken),
        "times_not_shown": hidden,
        "slots": [{"start": f"{date}T{t}:00+01:00", "end": ""} for t in times],
        # Provider detail no predicate reads. Must NOT be stored.
        "raw_provider_blob": {"anything": "at all"},
    }


# Monday as the diary really returned it on 2026-09-03: twelve bookable times.
PAYLOAD = [
    _day("2026-09-07", "Monday 7th September",
         ["08:00", "08:50", "09:40", "10:30", "11:20", "12:10",
          "13:00", "13:50", "14:40", "15:30", "16:20", "17:10"],
         ["eight in the morning", "ten to nine in the morning",
          "twenty to ten in the morning", "half past ten in the morning",
          "twenty past eleven in the morning", "ten past twelve",
          "one in the afternoon", "ten to two in the afternoon",
          "twenty to three in the afternoon", "half past three in the afternoon",
          "twenty past four in the afternoon", "ten past five in the evening"],
         hidden=10),
]

# What the caller was actually read: two of the twelve.
PRESENTED = [
    _day("2026-09-07", "Monday 7th September", ["08:00", "17:10"],
         ["eight in the morning", "ten past five in the evening"]),
]


def _record_one(session=None):
    session = {} if session is None else session
    offer = build_slot_offer(PRESENTED, more_times=True)
    record_offer(session, payload_days=PAYLOAD, offer=offer,
                 presented_days=PRESENTED)
    return session, offer


# ── What is captured ────────────────────────────────────────────────────────

def test_the_payload_and_the_offer_are_both_stored():
    """Both halves, because the GAP between them is the measurement.

    The payload held twelve Monday times and the caller was read two. That
    difference is the `presented != bookable` split (B-95) and the false
    completeness claim in OPEN_DEFECTS_2026-09-03 §2.2 -- neither of which can
    be counted from a transcript, because the transcript only ever contained
    the two.
    """
    session, offer = _record_one()
    block = offers_block(session)

    assert block and len(block) == 1
    entry = block[0]

    assert len(entry["payload"][0]["slot_times"]) == 12, "the diary's real answer"
    assert len(entry["presented"][0]["slot_times"]) == 2, "what was spoken"
    assert [s["start"] for s in entry["offer"]["slots"]] == [
        "2026-09-07T08:00:00+01:00", "2026-09-07T17:10:00+01:00",
    ]
    assert entry["offer"]["chunks"], "the sentence a replay diffs against"
    assert entry["offer"]["more_times"] is True
    assert entry["mode"] == offer.mode


def test_provider_detail_is_not_stored():
    """The payload is trimmed to the fields the pure predicates read. Anything
    else only makes a column that lives on every call bigger."""
    session, _ = _record_one()
    import json

    assert "raw_provider_blob" not in json.dumps(offers_block(session))


def test_nothing_stored_is_personal():
    """A slot is a date and a time. Name, number and reason live in `collected`
    and are governed by the Phase 4 redactor; if a field here ever needed
    redacting it would not belong in this column."""
    import json

    session, _ = _record_one()
    text = json.dumps(offers_block(session))
    for field in ("caller", "phone", "first_name", "last_name", "reason"):
        assert field not in text


def test_a_call_that_never_looked_up_availability_stores_nothing():
    """None, not [] -- so NULL keeps meaning "no data" rather than "measured,
    and empty". The convention _screening_summary and _latency_block set."""
    assert offers_block({}) is None
    assert offers_block(None) is None


def test_repeated_lookups_accumulate_but_are_capped():
    """A caller asking for a different day six times is the interesting shape.
    A runaway loop must not grow a session that is serialised to Redis every
    turn."""
    session = {}
    for _ in range(_MAX_OFFERS + 5):
        _record_one(session)
    block = offers_block(session)
    assert len(block) == _MAX_OFFERS
    assert [e["seq"] for e in block] == list(range(_MAX_OFFERS))


# ── It can never break a call ───────────────────────────────────────────────

@pytest.mark.parametrize("session", [None, "not a dict", 42, []])
def test_a_bad_session_is_survived(session):
    record_offer(session, payload_days=PAYLOAD, offer=None)


def test_a_bad_offer_is_survived():
    """This runs on the live path at the moment the offer is built. An
    observability layer must not be able to cost a caller their booking."""
    session = {}
    record_offer(session, payload_days=PAYLOAD, offer=object())
    record_offer(session, payload_days="not days", offer=None)
    record_offer(session, payload_days=None, offer=None)
    assert offers_block(session) is not None  # entries written, none raised


# ── The column reaches the database, including on an OLD store ──────────────

def test_the_column_round_trips_through_the_store(sqlite_store, fixture_record):
    session, _ = _record_one()
    record = dict(fixture_record)
    record["call_sid"] = "CAslotoffers01"
    record["slot_offers"] = offers_block(session)

    assert sqlite_store.capture_call(record, [{"role": "assistant", "text": "hi"}])
    got = sqlite_store.get_call("CAslotoffers01")
    assert got["slot_offers"], "the column did not survive the round trip"
    assert len(got["slot_offers"][0]["payload"][0]["slot_times"]) == 12


def test_an_existing_store_self_heals_and_keeps_capturing(tmp_path, monkeypatch):
    """THE hazard this change had to clear.

    `session.merge` SELECTs every mapped column, so a model column with no
    database column stops ALL capture -- not just this field -- and does it
    silently. That is a recorded defect in this codebase, not a hypothetical.

    So: build a store the way it exists in production TODAY (no slot_offers
    column), then let the schema check run and confirm both that the column
    appears AND that a call still captures.
    """
    from sqlalchemy import create_engine, inspect, text

    from app import config
    from app.obs import store

    db_path = tmp_path / "old.db"
    url = f"sqlite:///{db_path}"

    # A store created before the column existed.
    engine = create_engine(url)
    store.Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE calls DROP COLUMN slot_offers"))
    assert "slot_offers" not in {
        c["name"] for c in inspect(engine).get_columns("calls")
    }
    engine.dispose()

    monkeypatch.setattr(config, "DATABASE_URL", url)
    monkeypatch.setattr(config, "OBS_CAPTURE_ENABLED", True)
    store.reset_engine()
    assert store.init_db() is True

    engine2 = store._get_engine()
    assert "slot_offers" in {
        c["name"] for c in inspect(engine2).get_columns("calls")
    }, "the column did not self-heal onto an existing table"

    session, _ = _record_one()
    assert store.capture_call(
        {"call_sid": "CAheal0001", "clinic_id": "northgate",
         "slot_offers": offers_block(session)},
        [{"role": "assistant", "text": "hi"}],
    ), "capture stopped working after the column was added"
    assert store.get_call("CAheal0001")["slot_offers"]
    store.reset_engine()
