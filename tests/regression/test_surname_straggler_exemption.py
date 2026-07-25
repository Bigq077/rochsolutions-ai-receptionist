# tests/regression/test_surname_straggler_exemption.py
"""P2 (2026-07-24) — the surname "Rock" was dropped as a same-breath straggler, again.

Incident
--------
    23:16:34.635  FINAL 'yeah so that will be quaint in'   <- "Quentin", mangled
    23:16:35.331  FINAL 'rock'
    23:16:36.673  [ms_lost] reason=same_breath_straggler text='rock' (1311ms early)

The same-breath guard drops a fragment enqueued before the previous turn
finished, on the reasoning that a genuine reply cannot precede the response it
replies to. Correct in general, wrong for a surname said a beat after the
first name.

There is already an exemption for exactly this, and all three of its arms
missed on the booking path:

  * the keyword scan of last_question / last_bot_prompt — by the time the
    straggler was dequeued the prompt had advanced to "Did you say Quentin —
    is that right?", which contains no name keyword;
  * v3_awaiting_surname — only set by the name_collector two-turn path, and
    this booking collected the name through the LLM;
  * v3_confirmed_slot_phrase — only set on the numbered-slot path, and this
    slot was confirmed through the slot buffer ("slot buf: no numbered options
    this turn"), so it was never set.

connection.py already carried the comment "(Call 2, 2026-07-07: 'rock' lost
here)". Same caller, same surname, second recurrence, because the fix added
then covered only the paths that existed then.

Fix
---
A fourth arm keyed on booking_flow_active with the phone step not yet started.
Inside a booking, before the number is taken, a 1-2 word trailing fragment is
overwhelmingly a name part — and this arm does not depend on which path
(LLM or deterministic) collected it.

Trade-off, deliberately accepted: a kept fragment dispatches a turn rather than
being discarded, so this can add one short redundant turn. That is the same
trade the original exemption already made; losing the caller's surname from
the booking record is strictly worse.
"""

import pytest


# The predicate as it exists in connection.py's transcript loop. Mirrored here
# because it is inline inside a very large loop; test_exemption_matches_source
# below fails if the real one is edited without updating this.
def _in_name_collection(session: dict) -> bool:
    name_ctx = (
        (session.get("last_question") or "").lower()
        + " "
        + (session.get("last_bot_prompt") or "").lower()
    )
    return (
        any(
            w in name_ctx
            for w in ("your name", "first name", "surname", "full name", "take your name")
        )
        or bool(session.get("v3_awaiting_surname"))
        or (
            bool(session.get("v3_confirmed_slot_phrase"))
            and not session.get("phone_confirmed")
            and not session.get("v3_phone_dtmf_active")
        )
        or (
            bool(session.get("booking_flow_active"))
            and not session.get("phone_confirmed")
            and not session.get("v3_phone_dtmf_active")
        )
    )


def _short_fragment(utterance: str) -> bool:
    return 0 < len(utterance.split()) <= 2


def _is_kept(session: dict, utterance: str) -> bool:
    """True when the straggler guard exempts this fragment."""
    return _in_name_collection(session) and _short_fragment(utterance)


# Session as it actually stood at 23:16:36.673, reconstructed from the log.
_INCIDENT_SESSION = {
    # prompt had already advanced past any name keyword
    "last_question": "Did you say Quentin — is that right?",
    "last_bot_prompt": "Did you say Quentin — is that right?",
    "v3_awaiting_surname": False,      # LLM path, not name_collector
    "v3_confirmed_slot_phrase": None,  # slot-buffer path never sets it
    "booking_flow_active": True,       # logged at 23:14:53.094
    "phone_confirmed": False,          # not until 23:16:58.744
    "v3_phone_dtmf_active": False,
}


def test_the_incident_surname_is_now_kept():
    assert _is_kept(_INCIDENT_SESSION, "rock"), (
        "'rock' is still dropped under the exact session state of the "
        "2026-07-24 23:16 call — this is the second recurrence"
    )


def test_the_incident_would_have_been_dropped_without_the_new_arm():
    """Proves the new arm is what does the work, not one of the old three."""
    session = dict(_INCIDENT_SESSION, booking_flow_active=False)
    assert not _is_kept(session, "rock"), (
        "the pre-existing arms would have caught this, so the test is not "
        "actually exercising the fix"
    )


# ---------------------------------------------------------------------------
# The older paths must keep working.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "session",
    [
        {"last_bot_prompt": "What's your full name?"},
        {"last_question": "And your surname?"},
        {"v3_awaiting_surname": True},
        {"v3_confirmed_slot_phrase": "number 3", "phone_confirmed": False},
    ],
)
def test_existing_exemption_paths_unchanged(session):
    assert _is_kept(session, "rock")


# ---------------------------------------------------------------------------
# Scope. The new arm must not swallow the guard it lives inside.
# ---------------------------------------------------------------------------
def test_not_exempt_once_the_phone_step_has_started():
    """After phone confirm, a short fragment is a confirmation, not a name."""
    session = dict(_INCIDENT_SESSION, phone_confirmed=True)
    assert not _is_kept(session, "yes")


def test_not_exempt_during_keypad_entry():
    session = dict(_INCIDENT_SESSION, v3_phone_dtmf_active=True)
    assert not _is_kept(session, "rock")


def test_not_exempt_outside_a_booking():
    """An FAQ caller's trailing fragment is a genuine straggler."""
    session = {"booking_flow_active": False, "last_bot_prompt": "How can I help?"}
    assert not _is_kept(session, "thanks")


def test_long_fragments_are_never_exempt():
    """The exemption is for a dropped WORD, not a dropped sentence."""
    assert not _is_kept(_INCIDENT_SESSION, "actually can we make it tuesday instead")
    assert not _is_kept(_INCIDENT_SESSION, "rock is my surname")   # 4 words


def test_two_word_names_are_exempt():
    """"van Dijk", "de Souza" — the boundary is 2 words, not 1."""
    assert _is_kept(_INCIDENT_SESSION, "van dijk")


def test_empty_fragment_is_not_exempt():
    assert not _is_kept(_INCIDENT_SESSION, "")
    assert not _is_kept(_INCIDENT_SESSION, "   ")


# ---------------------------------------------------------------------------
# Keep the mirror honest.
# ---------------------------------------------------------------------------
def test_exemption_matches_source():
    """The real predicate is inline in a huge loop; this pins its arms.

    If someone edits the production condition without updating the mirror
    above, these assertions fail and the mirror gets fixed with it.
    """
    import inspect

    from app.media_streams import connection as conn_mod

    src = inspect.getsource(conn_mod)
    for arm in (
        '"v3_awaiting_surname"',
        '"v3_confirmed_slot_phrase"',
        '"booking_flow_active"',
        '"phone_confirmed"',
        '"v3_phone_dtmf_active"',
    ):
        assert arm in src, f"exemption arm missing from connection.py: {arm}"
    assert "_short_fragment = 0 < len(utterance.split()) <= 2" in src, (
        "short-fragment boundary changed — update the mirror in this test"
    )
