"""
A withheld caller's number must never become their phone number.

Twilio does not always put a number in `From`. On a withheld call it sends a
word — "anonymous", "unavailable", "restricted" — or +266696687, which is
ANONYMOUS dialled on a keypad. All are truthy, so the old `if twilio_from:`
accepted them, wrote the word into collected["phone"], and set
phone_from_twilio=True, which skips the phone step entirely.

Observed live on 2026-08-06 (theorem_v3), two calls:

    17:13:43  [ms_conn] caller number from Twilio: anonymous
    17:14:32  Row built — outcome=abandoned name=None phone=yes dur=44s
    17:14:28  Invalid phone number — SMS aborted: 'anonymous'

Two distinct harms:
  * a completed booking writes "anonymous" to Acuity as the patient's phone,
    and lookup_patient keys on phone — so every withheld caller collides on a
    single record;
  * the CallSummaries row says phone=yes for a caller who cannot be contacted.

The SMS layer rejects it, which is the only reason no real appointment has
been corrupted yet. Acuity does not check. Fix the source, not the symptom.

Ordering note: this must land BEFORE the transcript-filter fixes. Those make
withheld callers able to complete a booking, and without this that booking
lands in Acuity under the phone number "anonymous" — trading a loud failure
for a silent one.
"""

import inspect

import pytest

from app.media_streams import connection
from app.media_streams.connection import _is_usable_caller_id


# ── real numbers must survive ──────────────────────────────────────────────

@pytest.mark.parametrize("number", [
    "+447870166861",   # Mark's mobile — the forwarded-call CLI seen all day
    "+447760512084",   # the one genuine outside caller, 13:28 on 2026-08-06
    "07870 166861",    # local format with a space
    "+15551234567",    # international — must not be turned away
])
def test_a_real_number_is_usable(number):
    assert _is_usable_caller_id(number)


# ── the sentinels must not ─────────────────────────────────────────────────

@pytest.mark.parametrize("sentinel", [
    "anonymous", "Anonymous", "ANONYMOUS",
    "unavailable", "UNAVAILABLE", "restricted", "unknown",
    "private", "withheld", "blocked",
    "+266696687",   # ANONYMOUS on a keypad — numeric, still not diallable
    "266696687",
])
def test_withheld_sentinels_are_not_phone_numbers(sentinel):
    assert not _is_usable_caller_id(sentinel)


@pytest.mark.parametrize("junk", ["", "   ", None, "+44123", "abc", "-"])
def test_junk_is_not_a_phone_number(junk):
    assert not _is_usable_caller_id(junk)


# ── the guard is wired in, ahead of everything that reads twilio_from ──────

def _handler_source():
    return inspect.getsource(connection)


def test_guard_runs_before_the_forwarded_call_guard():
    """
    Ordering is load-bearing: both guards blank twilio_from, and everything
    downstream (initial["twilio_from"], collected["phone"]) reads it after.
    """
    src = _handler_source()
    ours = src.index("NON-NUMERIC caller-ID")
    forwarded = src.index("FORWARDED-CALL caller-ID detected")
    assert ours < forwarded


def test_guard_runs_before_collected_phone_is_populated():
    src = _handler_source()
    assert src.index("NON-NUMERIC caller-ID") < src.index(
        "caller number from Twilio"
    )


def test_the_guard_blanks_rather_than_passing_through():
    """
    Blanking routes the caller down the existing "will collect manually" path.
    Anything else (defaulting, or keeping the string) reintroduces the defect.
    """
    src = _handler_source()
    start = src.index("if twilio_from and not _is_usable_caller_id(twilio_from):")
    arm = src[start:start + 600]
    assert 'twilio_from = ""' in arm


def test_phone_step_is_not_skipped_for_a_withheld_caller():
    """
    The end state that matters: phone_from_twilio must only be set when a
    usable number arrived, because it is what suppresses the phone question.
    """
    src = _handler_source()
    populate = src.index("caller number from Twilio")
    block = src[populate:populate + 700]
    assert 'phone_from_twilio"] = True' in block
    # ...and it sits under `if twilio_from:`, which the guard has now emptied.
    assert src[:populate].rindex("if twilio_from:") < populate
