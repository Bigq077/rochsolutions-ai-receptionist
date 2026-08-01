"""A keypad-entered number never set `phone_confirmed`, so booking looped.

`CAb4e2cf4b05765c46b5c2433d20ef2090` (31 Jul 2026, build 6b706bb, clinic jv_v1).
The caller declined the caller-ID number, was correctly routed to the keypad,
typed eleven digits — and the booking still looped:

    23:38:23  caller  "um no it's a different number"
    23:38:24  Susie   "No problem — go ahead and type the number on your keypad."
    23:38:35  DTMF 11-digit complete -> immediate finalize '07502211207'
              [ms_conn v3] DTMF phone collection complete
    23:38:37  Susie   "So that's Quentin, Friday 7th August… shall I book that in?"
    23:38:46  caller  "um yes go ahead"
    23:38:49  [book] BLOCKED — phone not confirmed (A1) phone_confirmed=None
    23:38:51  Susie   "I've got you on oh seven five oh two… best number?"   <- re-ask
    23:39:01  caller  "um yes that's the best number"
    23:39:04  book_appointment BLOCKED — booking confirmation question not asked
    23:39:15  caller  "um yes go ahead"                                     <- booked

Four wasted turns, ~40 s. None of the three DTMF finalize paths set
`phone_confirmed`, and `book_appointment`'s A1 gate reads exactly that flag. Its
refusal text then instructs the model to *"read the number you already have back
to them"* — i.e. the caller-ID readback the caller had just declined. So the only
way out of the loop was to confirm the number they had explicitly rejected.

This is NOT A4 (`_is_use_this_number`, fixed in 6b706bb): that matcher was never
consulted, because the caller went down the keypad path. Same visible symptom,
different mechanism — which is why the A4 fix did not prevent this call.

Second defect, latent and worse, guarded here too: the loop's escape hatch stores
`_confirm_caller_number(session)` — the CALLER ID — into `collected["phone"]`. In
this call the typed number happened to equal the caller ID so nothing was lost.
Had the caller typed a genuinely different number, saying "yes that's the best
number" would have silently replaced it with the one they declined, and no
read-back exists that would catch it.
"""
from __future__ import annotations

import pytest

import app.media_streams.connection as conn


class _Sess(dict):
    """Session stand-in — the helper only ever does dict access."""


class _Harness:
    """Minimal object exposing the two attributes the helper touches."""

    def __init__(self, session):
        self.session = session

    _commit_dtmf_phone_for_booking = conn.WebSocketCallHandler._commit_dtmf_phone_for_booking


def _booking_session(**over):
    s = _Sess({"v3_caller_intent": "booking", "twilio_from_local": "07502211207"})
    s.update(over)
    return s


# ── The defect ──────────────────────────────────────────────────────────────

def test_keypad_number_sets_phone_confirmed():
    """The A1 gate reads phone_confirmed and nothing else."""
    s = _booking_session()
    _Harness(s)._commit_dtmf_phone_for_booking("07700900456")
    assert s["phone_confirmed"] is True, (
        "book_appointment's A1 gate will refuse the write and the model will "
        "re-ask the phone question — the CAb4e2cf4b loop"
    )


def test_keypad_number_is_the_number_stored():
    s = _booking_session()
    _Harness(s)._commit_dtmf_phone_for_booking("07700900456")
    assert s["collected"]["phone"] == "07700900456"
    assert s["phone_number"] == "07700900456"


def test_typed_number_is_not_the_caller_id():
    """The whole point of the keypad path: the caller wants a DIFFERENT number."""
    s = _booking_session(twilio_from_local="07502211207")
    _Harness(s)._commit_dtmf_phone_for_booking("07700900456")
    assert s["collected"]["phone"] != s["twilio_from_local"]


# ── The latent overwrite ────────────────────────────────────────────────────

def test_keypad_entry_is_flagged_so_the_verbal_path_cannot_clobber_it():
    """`phone_entered_by_keypad` is what both verbal-confirm branches check."""
    s = _booking_session()
    _Harness(s)._commit_dtmf_phone_for_booking("07700900456")
    assert s.get("phone_entered_by_keypad") is True


def test_verbal_confirm_branches_are_guarded_in_source():
    """Both branches store the CALLER ID. Assert neither can run once a typed
    number is on record — a source check, because the branches are buried in
    the transcript handler and cannot be called in isolation."""
    import inspect

    src = inspect.getsource(conn)
    assert src.count("phone_entered_by_keypad") >= 3, (
        "a verbal-confirm branch lost its keypad guard — a typed number can be "
        "overwritten by the caller ID the caller declined"
    )


# ── Scope: lookup flows must NOT be affected ────────────────────────────────

@pytest.mark.parametrize("intent", ["cancel", "reschedule"])
def test_lookup_flows_do_not_confirm_a_booking_phone(intent):
    """There the typed number is a lookup KEY for an existing booking, not the
    contact number for a new one. Confirming it would let a booking inherit a
    number the caller never offered for that purpose."""
    s = _booking_session(v3_caller_intent=intent)
    _Harness(s)._commit_dtmf_phone_for_booking("07700900456")
    assert s.get("phone_confirmed") is not True
    assert "phone" not in s.get("collected", {})


# ── Refusals ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("partial", ["", "0770", "077009", "0770090045"])
def test_a_partial_number_is_never_confirmed(partial):
    """Below the finalize thresholds, refuse rather than trust a short buffer."""
    s = _booking_session()
    _Harness(s)._commit_dtmf_phone_for_booking(partial)
    if len(partial.replace(" ", "")) < 10:
        assert s.get("phone_confirmed") is not True, partial


def test_a_ten_digit_number_is_accepted():
    """The idle-finalize path pads to 11, but near-complete finalizes at 9+;
    ten digits is a real UK number missing its leading zero."""
    s = _booking_session()
    _Harness(s)._commit_dtmf_phone_for_booking("7700900456")
    assert s["phone_confirmed"] is True


# ── All three finalize paths must commit ────────────────────────────────────

def test_every_dtmf_finalize_path_commits_the_number():
    """There are three finalize paths (11-digit immediate, idle, near-complete).
    The defect was that none of them committed; a new one that forgets would
    reopen the loop silently."""
    import inspect

    src = inspect.getsource(conn.WebSocketCallHandler)
    commits = src.count("_commit_dtmf_phone_for_booking(complete)")
    injects = src.count("_inject_phone_context_for_lookup(complete)")
    assert commits == injects == 3, (
        f"finalize paths out of step: {commits} commit(s) vs {injects} "
        "inject(s) — every path that queues a DTMF number as a transcript must "
        "also commit it"
    )
