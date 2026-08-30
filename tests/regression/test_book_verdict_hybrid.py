"""FM-01 affirmation verdict — L1 deterministic, L2 classifier, and the D2 fix.

CA7e389a47 (1 Aug 2026, build 4c21557). The caller said "go for it", "i said
yes", "go for it", "i said yes" — five affirmatives across 172 seconds — and
was never booked. Two defects, and it took both:

D1  "go for it" matched no yes-pattern, so FM-01 blocked the write.
D2  The model then claimed "All booked"; Gate 5f correctly caught the phantom
    and re-steered to the confirmation question. But sanitise_response runs
    TWICE per turn — per-chunk on the way to TTS, then again over the whole raw
    full_reply to derive last_bot_prompt. Gate 5f is stateful: the second pass
    took its already-fired branch and returned "" for the entire reply. So
    last_bot_prompt became "" at the exact moment the caller had been asked the
    confirmation question, and the gate that reads last_bot_prompt blocked
    every subsequent book as "question not asked". The two gates deadlocked.

    Proof in the log — same text, 10ms apart:
        18:34:24.013  re-steering to the confirmation question: "All booked..."
        18:34:24.023  additional false-confirmation chunk dropped: "All booked..."

Measured against the live pattern sets before the fix:

    "go for it"                         BOOK=False  <- the lost booking
    "crack on" / "go on then"           BOOK=False
    "don't do it"                       BOOK=True   <- WRONG booking
    "don't book it"                     BOOK=True   <- WRONG booking
    "yes but can we do friday instead"  BOOK=True   <- books the WRONG SLOT

The wrong-booking half is the more serious one and it is fixed WITHOUT the
classifier — L1's negation and correction cues are deterministic. L2 only ever
sees what L1 could not settle, and fails closed to a re-ask.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import app.media_streams.llm_stream as ls


# ── L1: pure, no network, no classifier ──────────────────────────────────────

@pytest.mark.parametrize("text", [
    "don't do it", "dont do it", "do not book it", "don't book it",
    "don't go for it", "please don't", "not yet", "hold off",
])
def test_negation_never_books(text):
    """These BOOK=True today. A caller refusing must never be read as consent —
    this is the wrong-booking class, and it is fixed deterministically."""
    assert ls._book_verdict_deterministic(text) == "no", text


@pytest.mark.parametrize("text", [
    "yes but can we do friday instead",
    "yeah actually hang on",
    "yes, actually no, make it Tuesday",
    "yep but could we change the time",
    "yes although can i ask something first",
])
def test_affirmative_with_a_correction_never_books(text):
    """FM-01's docstring always required this; only the literal "actually no"
    was ever caught. 'yes but can we do friday instead' booked the slot the
    caller was trying to change. Deliberately 'no' rather than 'unsure' — a hard
    requirement must not be delegated to a classifier that could answer yes."""
    assert ls._book_verdict_deterministic(text) == "no", text


@pytest.mark.parametrize("text", ["yes", "yes please", "yeah", "go ahead", "do it", "yep"])
def test_clear_yes_settles_without_the_classifier(text):
    assert ls._book_verdict_deterministic(text) == "yes", text


@pytest.mark.parametrize("text", ["no", "nope", "no thanks", "um, i'm not really sure", ""])
def test_clear_no_settles_without_the_classifier(text):
    assert ls._book_verdict_deterministic(text) == "no", text


def test_an_absent_reply_is_not_consent():
    assert ls._book_verdict_deterministic("") == "no"
    assert ls._book_verdict_deterministic("   ") == "no"


@pytest.mark.parametrize("text", [
    "go for it", "crack on", "go on then", "if you would", "i suppose so",
])
def test_the_unsettled_middle_is_handed_on_not_guessed(text):
    """L1 does not guess. These are the utterances L2 exists for — including
    the one that lost CA7e389a47."""
    assert ls._book_verdict_deterministic(text) == "unsure", text


# ── Routing: the classifier is reached only for 'unsure' ─────────────────────

def _msgs(text):
    return [{"role": "user", "content": text}]


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["yes", "no", "don't book it", "yes but make it friday"])
async def test_settled_replies_never_touch_the_network(text):
    """~90% of turns must pay zero latency and zero network. If this breaks,
    every booking turn starts making an API call."""
    with patch.object(ls, "_classify_book_reply", new=AsyncMock()) as spy:
        await ls._book_reply_verdict(_msgs(text), {})
    spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_unsure_middle_reaches_the_classifier():
    with patch.object(ls, "_classify_book_reply", new=AsyncMock(return_value="yes")) as spy:
        got = await ls._book_reply_verdict(_msgs("go for it"), {})
    spy.assert_awaited_once()
    assert got is True


@pytest.mark.asyncio
async def test_the_call_that_caused_this_now_books():
    """CA7e389a47's exact utterance."""
    with patch.object(ls, "_classify_book_reply", new=AsyncMock(return_value="yes")):
        assert await ls._book_reply_verdict(_msgs("um go for it"), {}) is True


# ── Failing closed ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_classifier_error_blocks_rather_than_books():
    """Fail CLOSED. The fallback is a re-ask, never a booking on an answer we
    could not read."""
    with patch.object(ls, "_classify_book_reply", new=AsyncMock(side_effect=RuntimeError("boom"))):
        assert await ls._book_reply_verdict(_msgs("go for it"), {}) is False


@pytest.mark.asyncio
async def test_a_classifier_timeout_blocks_rather_than_books():
    with patch.object(ls, "_classify_book_reply", new=AsyncMock(side_effect=asyncio.TimeoutError)):
        assert await ls._book_reply_verdict(_msgs("go for it"), {}) is False


@pytest.mark.asyncio
async def test_no_api_key_blocks_rather_than_books():
    with patch.object(ls, "_classify_book_reply",
                      new=AsyncMock(side_effect=Exception("Could not resolve authentication method"))):
        assert await ls._book_reply_verdict(_msgs("go for it"), {}) is False


@pytest.mark.asyncio
async def test_the_flag_off_falls_back_to_L1_only():
    """The live off-switch. With L2 disabled an unsettled reply blocks and
    re-asks — the pre-fix behaviour — while L1 still blocks 'don't book it'."""
    with patch.object(ls, "BOOK_CLASSIFIER_ENABLED", False):
        with patch.object(ls, "_classify_book_reply", new=AsyncMock()) as spy:
            assert await ls._book_reply_verdict(_msgs("go for it"), {}) is False
        spy.assert_not_awaited()
        assert await ls._book_reply_verdict(_msgs("yes"), {}) is True
        assert await ls._book_reply_verdict(_msgs("don't book it"), {}) is False


def test_the_flag_defaults_on():
    from app.media_streams import config
    assert config.BOOK_CLASSIFIER_ENABLED is True
    assert config.BOOK_CLASSIFIER_TIMEOUT_S <= 1.5, (
        "the write-ack filler is ~1s of cover; a longer wait is dead air on the "
        "booking turn"
    )


# ── Memoisation: the tool loop retries ───────────────────────────────────────

@pytest.mark.asyncio
async def test_the_verdict_is_memoised_across_tool_retries():
    """CA7e389a47 called book_appointment three times in ONE turn. Without a
    cache that is three serialised Haiku calls, and the filler only covers the
    first."""
    session = {}
    with patch.object(ls, "_classify_book_reply", new=AsyncMock(return_value="yes")) as spy:
        for _ in range(3):
            assert await ls._book_reply_verdict(_msgs("go for it"), session) is True
    assert spy.await_count == 1


@pytest.mark.asyncio
async def test_a_new_utterance_is_not_served_from_the_cache():
    session = {}
    with patch.object(ls, "_classify_book_reply", new=AsyncMock(return_value="yes")) as spy:
        await ls._book_reply_verdict(_msgs("go for it"), session)
        await ls._book_reply_verdict(_msgs("crack on"), session)
    assert spy.await_count == 2


def test_the_cache_is_cleared_each_turn():
    """Alongside the other per-turn resets, or a stale verdict outlives the
    utterance that produced it."""
    import inspect
    src = inspect.getsource(ls.LLMStream)
    assert 'session.pop("_book_verdict_cache", None)' in src


# ── L0 must remain untouched ─────────────────────────────────────────────────

def test_the_filler_matcher_is_still_sync_and_unchanged():
    """_book_reply_is_affirmative feeds the FM-25 write-ack filler and is
    imported and called synchronously by test_write_ack_filler_gate. Making it
    async to add a classifier would break both — and would delay the very filler
    whose audio covers the classifier's latency."""
    import inspect
    assert not inspect.iscoroutinefunction(ls._book_reply_is_affirmative)
    assert ls._book_reply_is_affirmative(_msgs("yes please")) is True
    assert ls._book_reply_is_affirmative(_msgs("no")) is False


def test_the_shared_yes_patterns_were_not_edited():
    """fast_path's tuple has other consumers. The fix is a new function beside
    it, never an edit to it."""
    from app.media_streams.fast_path import _YES_PATTERNS
    assert "go for it" not in _YES_PATTERNS, (
        "editing the shared tuple would also change fast_path's is_yes, and "
        "would make \"don't go for it\" book"
    )


# ── State comes from what was SPOKEN, not what was generated ─────────────────
#
# The root cause under D1/D2. sanitise_response IS the gate chain and several
# gates are stateful, so running it a second time at turn end over the raw
# full_reply fired them twice: Gate 5f, having already re-steered per-chunk,
# returned "" for the whole reply. And even when it did not blank, it described
# the GENERATION, not the delivery — so the model's own history said "All
# booked" when the caller had heard "shall I go ahead?". The model then believed
# it had confirmed, said it again, and was rewritten again.

def test_the_second_gate_pass_is_what_blanked_the_reply():
    """The mechanism, pinned. This is why deriving state from a second pass over
    full_reply could never be correct."""
    from app.media_streams import turn_handler as th

    session = {"booking_flow_active": True}
    first = th.sanitise_response("All booked — see you Thursday.", session)
    second = th.sanitise_response("All booked — see you Thursday.", session)
    assert "shall i go ahead" in first.lower(), "first pass re-steers"
    assert second.strip() == "", "second pass drops it — the source of the bug"


def test_spoken_chunks_are_accumulated():
    s = {}
    ls._record_spoken(s, "So that's Quentin, Thursday the 6th.")
    ls._record_spoken(s, "Shall I go ahead and book that in?")
    assert s["_spoken_this_turn"] == (
        "So that's Quentin, Thursday the 6th. Shall I go ahead and book that in?"
    )


def test_chunks_are_joined_with_a_space():
    """ResponseChunker._emit() strips each chunk. Joining without a separator
    produces "available.Friday 7th August" — the C5 artifact in
    docs/plan/AUDIT_2026-07-29.md."""
    s = {}
    ls._record_spoken(s, "There's nothing else available.")
    ls._record_spoken(s, "Friday 7th August has two slots.")
    assert "available.Friday" not in s["_spoken_this_turn"]
    assert "available. Friday" in s["_spoken_this_turn"]


def test_empty_chunks_are_ignored():
    s = {}
    for junk in ("", "   ", None):
        ls._record_spoken(s, junk)
    assert not s.get("_spoken_this_turn")


def test_the_accumulator_is_cleared_each_turn():
    """Or a turn inherits the previous turn's speech as its own."""
    import inspect
    src = inspect.getsource(ls.LLMStream)
    assert 'session.pop("_spoken_this_turn", None)' in src


def test_the_turn_end_prefers_spoken_and_falls_back():
    """The fallback is load-bearing: SAFE_FALLBACK_PHRASE, the guaranteed
    fallback, the Gate-5 fallback and the ack filler all speak WITHOUT passing
    through the chunk seam. A turn carried entirely by one of those must not
    blank last_bot_prompt."""
    import inspect
    src = inspect.getsource(ls.LLMStream)
    assert '_spoken_turn = (session.pop("_spoken_this_turn", "") or "").strip()' in src
    assert "_display_reply = sanitise_response(full_reply, session)" in src, (
        "the no-chunks fallback was removed — a fallback-only turn would blank "
        "the state"
    )


def test_both_records_hold_what_was_heard_and_the_raw_is_kept_beside_it():
    """conversation_history feeds the model its own prior turns; session["turns"]
    feeds live clinics' owner summaries. BOTH must hold what was actually said.

    This was `test_history_records_what_was_heard_and_turns_stays_raw` and
    asserted the opposite of its second half — "turns ... is deliberately left
    on the raw shape". True when written (2026-08-02 scoped the owner path out),
    changed on 2026-08-30: leaving it raw meant the owner was told Susie said
    "All booked — you're in for Thursday" on a call where Gate 5f had stopped
    the caller ever hearing it. The sibling test below gives the reason for the
    model; it applies to the owner unchanged.

    The raw generation is NOT discarded — it moves to a `raw` key, because
    connection.py's Gate 5g name recovery reads it and losing it costs the
    caller their name four times over (CA041352eb). See
    tests/regression/test_the_owner_summary_says_what_was_spoken.py.
    """
    s = {}
    ls._append_history(
        s, "um go for it",
        "Sorry — before I confirm anything, shall I go ahead and book that in for you?",
        raw_text="All booked — you're in for Thursday.",
    )
    assert "shall i go ahead" in s["conversation_history"][-1]["content"].lower()
    assert "shall i go ahead" in s["turns"][-1]["text"].lower()
    assert "all booked" not in s["turns"][-1]["text"].lower()
    assert s["turns"][-1]["raw"] == "All booked — you're in for Thursday."


def test_the_model_is_never_told_it_said_something_it_did_not():
    """The loop's engine. With the raw claim in history the model reads back its
    own "All booked", believes the booking happened, and re-claims it — which
    Gate 5f rewrites again, forever."""
    s = {}
    ls._append_history(
        s, "um go for it",
        "Sorry — before I confirm anything, shall I go ahead and book that in for you?",
        raw_text="All booked — you're in for Thursday.",
    )
    assert "all booked" not in s["conversation_history"][-1]["content"].lower()


def test_what_the_caller_heard_satisfies_the_confirmation_gate():
    """The re-steer must contain the phrase the book gate looks for, or the
    re-ask can never complete however good the affirmation matcher is."""
    from app.media_streams import turn_handler as th

    lbp = th._FALSE_CONFIRM_RESTEER.lower()
    assert "shall i go ahead" in lbp or "book that in" in lbp


def test_the_old_stash_patch_is_gone():
    """It papered over the second stateful pass; the accumulator removes the
    need for it. Leaving both would mean two mechanisms for one fact."""
    import inspect
    from app.media_streams import turn_handler as th
    assert "_false_confirm_spoken" not in inspect.getsource(th)
    assert "_false_confirm_spoken" not in inspect.getsource(ls)


# ── Client construction (found by reading CA7d46c2bc back) ───────────────────

def test_the_classifier_passes_its_api_key_explicitly():
    """Every other Anthropic client in this module passes api_key explicitly.
    A classifier that cannot authenticate fails closed — indistinguishable, in
    the transcript, from the caller having said no."""
    import inspect
    src = inspect.getsource(ls._classifier_client)
    assert "api_key=ANTHROPIC_API_KEY" in src


def test_the_classifier_client_is_reused():
    """A per-call client pays connection setup inside the timeout budget. The
    first such call measured 1.8s locally before it even reached auth — a
    fail-closed timeout on the caller's first 'go for it'."""
    ls._classifier_client_cached = None
    a = ls._classifier_client()
    b = ls._classifier_client()
    assert a is b


def test_the_timeout_leaves_room_for_a_cold_call():
    from app.media_streams import config
    assert 1.2 <= config.BOOK_CLASSIFIER_TIMEOUT_S <= 2.0, (
        "too tight and a cold connection fails closed; too loose and it "
        "outlasts the filler that is covering it"
    )
