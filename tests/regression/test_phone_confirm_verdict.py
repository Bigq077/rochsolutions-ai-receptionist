# tests/regression/test_phone_confirm_verdict.py
"""
The caller-ID confirmation must be judged, not pattern-matched.

CAcb4a11b90 (2 Aug 2026). Susie asked "I've got you on … — is that the best
number for the booking?" and the caller said "yeah that's the one".
`_is_use_this_number` returned False, so phone_confirmed was never set,
book_appointment's A1 gate refused the write, the model re-asked the same
question, the caller repeated the same words, and the call was abandoned after
two full cycles with name, slot and number all collected. Nobody was told
(B-02). The A1 gate behaved correctly throughout — there is simply no ladder
bounding the verbal phone confirm the way there is for DTMF.

Found while fixing it, and worse: `_is_use_this_number("don't use that one")`
returns True. A caller explicitly REFUSING the caller ID would have it stored
as confirmed and booked on — a wrong booking rather than a missed one.

Both are the identical defect `_book_verdict_deterministic` was written to
close on the booking path ("don't book it" -> BOOK=True), and its comment says
why a bigger list cannot fix it:

    "Substring matching cannot be repaired by adding more substrings: adding
     'go for it' to the yes list also makes 'don't go for it' book."

So the same shape is applied here: negation and correction are evaluated BEFORE
the affirmative, and 'unsure' is a real third state rather than a guess.

Two things this deliberately does NOT reuse from the booking verdict:

1. fast_path._YES_PATTERNS. It is tuned for "shall I book that in?", where
   "please" means "yes please". Replaying both predicates over the 950 stored
   caller turns in the obs corpus, reusing it accepted 209 extra utterances
   including '11 in the morning please' and '28 please at 5'.
2. Bare substring matching. Those patterns are unanchored, so 'ok' matches
   inside 'looking'. The affirmatives here are word-boundary matched.

Corpus-verified: against those 950 real caller turns, every utterance the old
predicate accepted and this one does not is a '…please' fragment that is not a
phone confirmation at all ('john smith please', 'next tuesday please', 'please
hold') or an explicit refusal.
"""
from __future__ import annotations

import inspect

import pytest

import app.media_streams.connection as conn
from app.media_streams.llm_stream import _phone_confirm_verdict as verdict


class TestTheCallThatWasLost:
    def test_the_exact_utterance_from_CAcb4a11b90(self):
        assert verdict("yeah that's the one") == "yes"

    @pytest.mark.parametrize("said", [
        "that's the one", "thats the one", "yep that's the one",
        "that's the number", "that'll do", "that's it",
        "it is", "that's the best number", "that is the best number",
    ])
    def test_natural_acceptances_are_accepted(self, said):
        assert verdict(said) == "yes", said


class TestARefusalCanNeverBeReadAsConsent:
    """The wrong-booking half. Ordering is what closes it — these all contain
    an affirmative or an accept-phrase and must still resolve to 'no'."""

    @pytest.mark.parametrize("said", [
        "don't use that one",
        "don't use this number",
        "do not use that number",
        "please don't use this one",
        "rather not use that one",
    ])
    def test_negation_beats_the_affirmative(self, said):
        assert verdict(said) == "no", said

    @pytest.mark.parametrize("said", [
        "yeah but can i use a different number",
        "yes but actually i'll give you another one",
        "yeah hang on, that's the old number",
        "yes, change it",
    ])
    def test_a_correction_in_progress_is_not_consent(self, said):
        assert verdict(said) == "no", said

    @pytest.mark.parametrize("said", ["no", "nope", "no thanks", "not really", ""])
    def test_plain_refusals(self, said):
        assert verdict(said) == "no", said


class TestTheWordingTheePromptAsksFor:
    """Step 8 tells callers to say "use this number". _is_use_this_number also
    deliberately caught the STT truncations. Dropping either would regress the
    one phrase the system asks for by name."""

    @pytest.mark.parametrize("said", [
        "use this number", "use that number", "use this one",
        "this number", "that number", "keep this number",
    ])
    def test_accepted(self, said):
        assert verdict(said) == "yes", said


class TestTheFalsePositivesThatMadeUsAbandonYES_PATTERNS:
    """Corpus-derived. Reusing fast_path._YES_PATTERNS accepted all of these
    because "please" is an affirmative for the BOOKING question only."""

    @pytest.mark.parametrize("said", [
        "john smith please", "next tuesday please", "60 minute please",
        "anytime please", "please hold", "number please", "water 6 please",
    ])
    def test_please_alone_is_not_a_phone_confirmation(self, said):
        assert verdict(said) != "yes", said

    def test_short_affirmatives_are_word_bounded(self):
        """'ok' must not match inside 'looking' — the reason these are regex
        word-boundary matched rather than substrings."""
        assert verdict("i'm looking at my phone") != "yes"
        assert verdict("okay") == "yes"

    def test_bare_acknowledgements_still_work(self):
        for said in ("okay", "ok", "sure", "yes", "yeah", "correct"):
            assert verdict(said) == "yes", said


class TestTheThirdState:
    def test_unsettled_replies_are_unsure_not_guessed(self):
        """'unsure' must exist as a distinct outcome. Collapsing it into 'no'
        is what produced the re-ask loop; collapsing it into 'yes' would book
        on an unconfirmed number."""
        assert verdict("hmm") == "unsure"
        assert verdict("what was the question") == "unsure"

    def test_unsure_is_not_treated_as_consent_by_the_adapter(self):
        assert conn._phone_confirm_is_yes("hmm") is False


class TestTheCallSitesActuallyUseIt:
    def test_both_booking_sites_are_wired(self):
        src = inspect.getsource(conn.WebSocketCallHandler)
        assert src.count("_phone_confirm_is_yes(utterance)") == 2

    def test_the_reschedule_lookup_site_is_deliberately_untouched(self):
        """A different question ("was it booked under this number?") with its
        own keypad fallback. Out of scope for this fix."""
        src = inspect.getsource(conn.WebSocketCallHandler)
        assert "_is_use_this_number(utterance)" in src

    def test_the_keypad_guard_is_still_in_front_of_the_store(self):
        """A typed number outranks the caller ID and must never be overwritten
        by it — the guard that held on three calls in the 2 Aug sweep."""
        src = inspect.getsource(conn.WebSocketCallHandler)
        i = src.index("_phone_confirm_is_yes(utterance)\n                                and not")
        assert 'phone_entered_by_keypad' in src[i:i + 200]
