"""B-77 — Susie promised times that did not exist.

Live call CA98557584dcec6a2cc1e1c546e69e845d, 24 Aug 2026 10:22 GMT, Theorem
Alcester. Acuity returned exactly two slots for Tuesday 25 August (15:00 and
16:00). Both were read out. Susie then said:

    "The earliest I have is Tuesday 25th August — Number 1, three in the
     afternoon. Number 2, four in the afternoon. Any of those work?
     And I've a few others that day if neither suits."

There were no others that day. `_check_availability_acuity` only sets
more_times when a day has MORE than three times, so more_times was False and
the tail should never have been spoken — the Haiku slot formatter copied the
more_times=true example out of SLOT_FORMATTER_SYSTEM_PROMPT.

The claim is now owned by code: appended from more_times, stripped when the
model produces it anyway.
"""

import pytest

from app.tools.slot_followup import (
    MORE_TIMES_TAIL_MANY,
    MORE_TIMES_TAIL_ONE,
    reconcile_extra_slots_claim,
)


# The exact text spoken on the call.
LIVE_REPLY = (
    "The earliest I have is Tuesday 25th August — Number 1, three in the "
    "afternoon. Number 2, four in the afternoon. Any of those work? "
    "And I've a few others that day if neither suits."
)


def test_live_call_tail_is_stripped_when_no_further_times():
    """The defect itself: two slots offered, two slots exist, no tail."""
    out, action = reconcile_extra_slots_claim(LIVE_REPLY, more_times=False)

    assert action == "stripped"
    assert "few others" not in out.lower()
    assert "that day" not in out.lower()
    # The options the caller picks from must survive untouched — the
    # "Number N" wording is parsed for keypad selection.
    assert "Number 1, three in the afternoon." in out
    assert "Number 2, four in the afternoon." in out
    assert "Any of those work?" in out


@pytest.mark.parametrize(
    "paraphrase",
    [
        "And I've a few others that day if neither suits.",
        "I've got a couple more times that day.",
        "There are some other slots that day as well.",
        "I have several more openings that day.",
        "And there's more availability that day if neither works.",
    ],
)
def test_paraphrases_of_the_claim_are_stripped(paraphrase):
    """A prompt rule the model can paraphrase around is not a fix.

    The guard matches the CLAIM, not the one sentence the prompt used to
    teach — see write-gates that matched a single literal and were defeated
    the moment the wording moved.
    """
    body = (
        "The earliest I have is Tuesday 25th August — Number 1, three in the "
        "afternoon. Number 2, four in the afternoon. Any of those work? "
    )
    out, action = reconcile_extra_slots_claim(body + paraphrase, more_times=False)

    assert action == "stripped", f"not recognised as a claim: {paraphrase!r}"
    assert paraphrase not in out


def test_tail_is_appended_when_further_times_really_exist():
    """The other direction: more_times True and the model stayed silent."""
    reply = (
        "Tuesday 25th August — Number 1, three in the afternoon. "
        "Number 2, four in the afternoon. Any of those work?"
    )
    out, action = reconcile_extra_slots_claim(reply, more_times=True, n_offered=2)

    assert action == "appended"
    assert MORE_TIMES_TAIL_MANY in out
    # ...and BEFORE the closing question, so the caller's "yes" is unambiguous.
    assert out.rstrip().endswith("Any of those work?")


def test_single_time_gets_the_singular_tail():
    reply = "The available slot is three in the afternoon. Does that work?"
    out, action = reconcile_extra_slots_claim(
        "Tuesday — three in the afternoon. Does that work?",
        more_times=True,
        n_offered=1,
    )

    assert action == "appended"
    assert MORE_TIMES_TAIL_ONE in out
    assert out.rstrip().endswith("Does that work?")
    # ...and a completeness opener suppresses the append rather than
    # contradicting itself in one breath.
    _, action2 = reconcile_extra_slots_claim(reply, more_times=True, n_offered=1)
    assert action2 == "unchanged"


def test_no_double_tail_when_model_already_said_it_truthfully():
    reply = (
        "Tuesday 25th August — Number 1, three in the afternoon. "
        "Number 2, four in the afternoon. Any of those work? "
        + MORE_TIMES_TAIL_MANY
    )
    out, action = reconcile_extra_slots_claim(reply, more_times=True)

    assert action == "unchanged"
    assert out.lower().count("few others") == 1


@pytest.mark.parametrize(
    "legitimate",
    [
        "The available slots for Tuesday 25th August are — Number 1, three in "
        "the afternoon. Number 2, four in the afternoon. Any of those work?",
        "Here's what we've got coming up — Number 1, Monday the 20th — two in "
        "the afternoon. Or four in the afternoon. Number 2, Tuesday the 21st — "
        "nine in the morning. Any of those suit you?",
        "The available slot for Wednesday 26th August is ten in the morning. "
        "Does that work?",
        "Tuesday 4th August is fully booked, I'm afraid — the available slot "
        "for Wednesday 5th August is seven in the evening. Does that work?",
    ],
)
def test_legitimate_presentations_are_untouched(legitimate):
    """A strip that eats real speech is worse than the bug it fixes."""
    out, action = reconcile_extra_slots_claim(legitimate, more_times=False)

    assert action == "unchanged"
    assert out == legitimate


def test_never_blanks_a_reply():
    """A reply that is ENTIRELY a claim is left alone, not deleted.

    Silence on a slot turn is the C8-5 failure — the caller abandoned the
    call. An over-promise is recoverable; dead air is not.
    """
    out, action = reconcile_extra_slots_claim(
        "I've a few others that day.", more_times=False,
    )

    assert action == "unchanged"
    assert out.strip() != ""


def test_empty_text_is_safe():
    assert reconcile_extra_slots_claim("", more_times=True) == ("", "unchanged")
    assert reconcile_extra_slots_claim("   ", more_times=False)[1] == "unchanged"


def test_prompt_no_longer_teaches_the_tail():
    """The model copied this out of its own instructions.

    An example IS an instruction. Leaving the sentence in the prompt while a
    later line forbids it is the shape that produced the defect, so the
    example must not come back.
    """
    from app.prompts.susie_system_prompt import SLOT_FORMATTER_SYSTEM_PROMPT

    assert "few others" not in SLOT_FORMATTER_SYSTEM_PROMPT.lower()


# ───────────────────────────────────────────────────────────────────────────
# The wiring. The unit tests above prove the reconciler; these prove
# _flush_slot_buf actually calls it, which is where the live defect escaped.
# ───────────────────────────────────────────────────────────────────────────

# The two chunks the model produced on the live call, verbatim from the log:
#   slot buf chunk 0: 'The earliest I have is Tuesday 25th August — Number 1, three'
#   slot buf chunk 1: "Number 2, four in the afternoon. Any of those work? And I've"
LIVE_CHUNKS = [
    "The earliest I have is Tuesday 25th August — Number 1, three in the afternoon.",
    "Number 2, four in the afternoon. Any of those work? "
    "And I've a few others that day if neither suits.",
]


async def _run_flush(chunks, session):
    """Drive the real _flush_slot_buf and return the TTS chunks it emitted."""
    import asyncio

    from app.media_streams.llm_stream import LLMStream, PRE_SLOT_MARKER

    buf = asyncio.Queue()
    for c in chunks:
        await buf.put(PRE_SLOT_MARKER + c)
    tts = asyncio.Queue()

    await LLMStream._flush_slot_buf(buf, tts, session)

    out = []
    while not tts.empty():
        out.append(tts.get_nowait())
    return out


async def test_flush_strips_the_claim_on_the_live_call_text():
    """Replay of CA98557584dc through the real flush."""
    session = {"_slot_more_times": False, "_slot_n_offered": 2}

    spoken = " ".join(await _run_flush(LIVE_CHUNKS, session))

    assert "few others" not in spoken.lower()
    # Both options still reach TTS — a strip that loses an option is a worse bug.
    assert "three in the afternoon" in spoken
    assert "four in the afternoon" in spoken
    # And the keypad map is still armed off the corrected text.
    assert session.get("v3_dtmf_slot_map") == {
        "1": "three in the afternoon",
        "2": "four in the afternoon",
    }
    assert session.get("v3_awaiting_slot_selection") is True


async def test_flush_keeps_the_claim_when_more_times_is_true():
    session = {"_slot_more_times": True, "_slot_n_offered": 2}

    spoken = " ".join(await _run_flush(LIVE_CHUNKS, session))

    assert "few others" in spoken.lower()
    assert spoken.lower().count("few others") == 1


async def test_flush_defaults_to_stripping_when_ground_truth_is_absent():
    """No _slot_more_times in session → treat as "no further times".

    Deny by default: an absent flag must not license a claim about the
    calendar. A session missing the key is a code path that never saw a tool
    result, which is precisely when the model has nothing to go on.
    """
    spoken = " ".join(await _run_flush(LIVE_CHUNKS, {}))

    assert "few others" not in spoken.lower()


@pytest.mark.parametrize(
    "n_offered,expected",
    [
        (1, "if that doesn't suit."),
        (2, "if neither suits."),
        (3, "if none of those suit."),
    ],
)
def test_tail_agrees_with_the_number_of_options(n_offered, expected):
    """"neither" means two. A single day's spoken times cap at THREE."""
    reply = "Tuesday 25th August — Number 1, nine. Number 2, ten. Number 3, eleven. Any of those work?"
    out, action = reconcile_extra_slots_claim(reply, more_times=True, n_offered=n_offered)

    assert action == "appended"
    assert expected in out, out
    assert out.rstrip().endswith("Any of those work?"), out


def test_multi_day_never_gets_the_that_day_tail():
    """"that day" has no referent when two different days were just named.

    jv_v1 presents multi_day (2 days x 1 time) and sets a top-level
    more_times, so without this the append fires on every open availability
    request on that clinic and says "that day" after naming two.
    """
    multi = (
        "Here's what we've got coming up — Number 1, Monday the 24th — six in "
        "the evening. Number 2, Tuesday the 25th — seven in the evening. "
        "Either of those suit you?"
    )
    out, action = reconcile_extra_slots_claim(
        multi, more_times=True, allow_append=False,
    )

    assert action == "unchanged"
    assert "that day" not in out.lower()
    assert out == multi


def test_multi_day_still_strips_a_false_claim():
    """The append is gated by presentation mode; the strip never is."""
    multi = (
        "Number 1, Monday the 24th — six in the evening. Number 2, Tuesday "
        "the 25th — seven in the evening. Either of those suit you? "
        "And I've a few others that day if neither suits."
    )
    out, action = reconcile_extra_slots_claim(
        multi, more_times=False, allow_append=False,
    )

    assert action == "stripped"
    assert "few others" not in out.lower()


async def test_flush_does_not_append_on_multi_day():
    session = {
        "_slot_more_times": True,
        "_slot_n_offered": 2,
        "_slot_presentation_mode": "multi_day",
    }
    chunks = [
        "Here's what we've got coming up — Number 1, Monday the 24th — six in the evening.",
        "Number 2, Tuesday the 25th — seven in the evening. Either of those suit you?",
    ]
    spoken = " ".join(await _run_flush(chunks, session))

    assert "few others" not in spoken.lower()


async def test_flush_appends_on_single_day():
    session = {
        "_slot_more_times": True,
        "_slot_n_offered": 2,
        "_slot_presentation_mode": "single_day",
    }
    spoken = " ".join(await _run_flush(LIVE_CHUNKS[:1] + [
        "Number 2, four in the afternoon. Any of those work?"
    ], session))

    assert "few others" in spoken.lower()
