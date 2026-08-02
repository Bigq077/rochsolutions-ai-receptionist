# tests/regression/test_reschedule_phone_confirm_verdict.py
"""U-06 (2 Aug 2026) — the reschedule/cancel lookup still used the broken predicate.

Background
----------
`_phone_confirm_verdict` replaced `_is_use_this_number` at the two BOOKING
phone-confirm sites in 8d152f0, because that predicate accepts a refusal:

    _is_use_this_number("don't use that one")   ->  True

The negation guard inside it tests for the substrings ``"not "`` and ``"no "``.
Neither occurs in ``"don't"``. Execution therefore reaches
``_USE_THIS_NUMBER_SIGNALS``, which contains ``"use that one"``, and the refusal
matches as consent.

8d152f0 deliberately left the third call site — the reschedule/cancel lookup at
``v3_awaiting_phone_confirm`` — on the old predicate, reasoning that it is "a
different question with its own keypad fallback". This test retires that
reasoning.

Why it is worth changing anyway
-------------------------------
The consequence at this site really is milder than at the booking sites: a wrong
`lookup_phone` produces a failed lookup, not a booking against an unreachable
number. But:

  * the else-branch keypad fallback only bounds the *unsettled* case. It does
    nothing for a refusal that is affirmatively misread as consent — that branch
    is never reached, because the predicate said yes;
  * a caller who says "don't use that one" is told their appointment cannot be
    found, on a number they explicitly rejected. That is a confusing dead end at
    the top of a reschedule, and it presents as "the lookup is broken";
  * it was the last live use of a predicate we have proven wrong in both
    directions, which means every future reader had to re-derive whether this
    site was safe.

The swap is behaviour-preserving on the unsettled path: `_phone_confirm_is_yes`
is False for "unsure" exactly as `_is_use_this_number` was, so the keypad
fallback keeps running for anything not clearly a yes.
"""

import inspect

import pytest

from app.media_streams import connection as conn
from app.media_streams.llm_stream import _phone_confirm_verdict


# ---------------------------------------------------------------------------
# The defect, at the predicate level. This is why the site had to move.
# ---------------------------------------------------------------------------
def test_the_old_predicate_reads_a_refusal_as_consent():
    """Pins the defect itself, so this test explains the change even after the
    call site has moved on.

    `_is_use_this_number` is retained in the module with no call sites (several
    regression tests assert its behaviour, and deleting it is a separate
    cleanup). This test is what makes that safe: if someone "fixes" the
    predicate and wires it back up, this fails and points at the reason it was
    retired instead."""
    assert conn._is_use_this_number("don't use that one") is True, (
        "the premise of this fix has changed — _is_use_this_number no longer "
        "accepts a refusal, so re-read whether U-06 is still needed"
    )


def test_the_verdict_settles_the_same_refusal_correctly():
    assert _phone_confirm_verdict("don't use that one") == "no"
    assert conn._phone_confirm_is_yes("don't use that one") is False


@pytest.mark.parametrize(
    "refusal",
    [
        "don't use that one",
        "don't use this number",
        "please don't use that number",
        "no need to use that one",
    ],
)
def test_refusals_never_select_the_caller_id(refusal):
    """Every one of these is accepted by the old predicate or reaches it; none
    may read as consent at the lookup site."""
    assert conn._phone_confirm_is_yes(refusal) is False, refusal


# ---------------------------------------------------------------------------
# The accept side must not regress. This site's whole purpose is to let a caller
# say "yes, look it up on this number" without touching the keypad.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "consent",
    [
        "yes",
        "yeah that's the one",
        "use this number",
        "that's the number",
        "yeah that's the best number",
    ],
)
def test_plain_consent_still_selects_the_caller_id(consent):
    assert conn._phone_confirm_is_yes(consent) is True, consent


def test_the_swap_widens_the_accept_set_rather_than_narrowing_it():
    """"yeah that's the one" was False under the old predicate — the caller was
    sent to the keypad for a clear yes. The new predicate accepts it. Recorded
    so a future reader can see the swap is not purely a safety change."""
    assert conn._is_use_this_number("yeah that's the one") is False
    assert conn._phone_confirm_is_yes("yeah that's the one") is True


# ---------------------------------------------------------------------------
# The site itself. The branch is inline in handle_transcript's loop and cannot
# be called in isolation, so the wiring is pinned against the source.
# ---------------------------------------------------------------------------
class TestTheLookupSiteIsWired:
    # `self.session.get("v3_awaiting_phone_confirm")` occurs three times — twice
    # as a NOT-guard on the booking sites, once as the reschedule block's own
    # `if`. Anchoring on it finds the booking site and silently tests the wrong
    # branch (it did, on the first draft of this file). The assignment below is
    # unique to the reschedule block.
    ANCHOR = 'self.session["v3_awaiting_phone_confirm"] = False'
    # The block's real end. A fixed character window was used first and broke as
    # soon as a comment was added to the block — a test that fails when someone
    # explains the code is a bad test. Slice between two unique markers instead.
    END = "continue  # Skip run_turn; wait for digits"

    @pytest.fixture
    def src(self):
        return inspect.getsource(conn.WebSocketCallHandler)

    @pytest.fixture
    def block(self, src):
        """Exactly the reschedule/cancel phone-confirm branch."""
        i = src.index(self.ANCHOR)
        return src[i:src.index(self.END, i) + len(self.END)]

    def test_the_block_markers_are_unique(self, src):
        """`self.session.get("v3_awaiting_phone_confirm")` occurs three times —
        twice as a NOT-guard on the booking sites. Anchoring on it silently
        tests the wrong branch (it did, on the first draft of this file)."""
        assert src.count(self.ANCHOR) == 1
        assert src.count(self.END) == 1

    def test_the_reschedule_site_uses_the_verdict(self, block):
        assert "_phone_confirm_is_yes(utterance)" in block, (
            "the reschedule/cancel lookup no longer routes through the verdict"
        )

    def test_the_old_predicate_has_no_call_sites_left(self, src):
        assert "_is_use_this_number(utterance)" not in src, (
            "a call site still passes a live utterance to the predicate that "
            "accepts \"don't use that one\""
        )

    def test_all_three_phone_confirm_sites_now_agree(self, src):
        """Two booking sites (8d152f0) plus this one. If a fourth appears it
        must be a deliberate decision, not an oversight."""
        assert src.count("_phone_confirm_is_yes(utterance)") == 3

    def test_the_keypad_fallback_is_still_the_else_branch(self, block):
        """The bound that makes an unsettled answer safe here. Without it a
        'no'/'unsure' verdict would fall through to the LLM with no number."""
        assert 'self.session["v3_phone_dtmf_active"] = True' in block
        assert "keypad" in block.lower()
