"""
Vital Edge live call (2026-08-20, CAa0f76e2c2851f9eb3f28eddc38b75e3b) — a caller
who had never booked anything was told his appointment was being pulled up.

Live-call trace:
    15:32:09,348  tool: name=request_callback args={"patient_name": "Ray Roger",
                  "phone": "07796479460", "notes": "Caller wants to speak to
                  Jonathan directly."}
    15:32:09,850  synthesise_chunk: text='Of course — just pulling your
                  appointment up…'
    15:32:09,637  tool result: {"success": true, ...}

Ray rang to ask for Jonathan. He had no appointment, never mentioned one, and
was never asked about one.

Root cause: `_FILLER_TOOLS` mapped both `request_callback` and `add_to_waitlist`
to `LOOKUP_FILLERS`. That was never a decision — 34becd6 added request_callback
(the Dylan Wilson fix) and copied `lookup_patient`'s mapping along with it; the
commit message does not mention fillers at all. Every phrase in LOOKUP_FILLERS
is about an appointment the caller already has:

    "Of course — just pulling your appointment up…"
    "No problem at all — let me find that for you…"

…and neither of these two tools starts from anything on file.

The tests below pin the two properties the replacement lists have to keep, both
learned the hard way by the sibling lists in the same module:

  * **Describe the action, never the outcome.** `with_filler` puts the primary
    phrase on the TTS queue BEFORE it awaits the executor, so a phrase claiming
    the clinic has been told is spoken even when the executor REFUSES — which
    `_exec_request_callback`'s phone gate now does. This is exactly what put
    "Getting that all booked in for you…" out of BOOKING_WRITE_FILLERS.
  * **Reference nothing the caller must already have.** No booking, no
    appointment, no diary.

Neither property is protected by `turn_handler._BANNED_SENTENCE_RE`: a filler is
queued straight to TTS and never passes through `sanitise_response`. That is why
they are asserted here instead.
"""

import inspect
import re

import app.filler_phrases as filler_phrases
from app.filler_phrases import LOOKUP_FILLERS
from app.media_streams.turn_handler import _BANNED_SENTENCE_RE


def _filler_tools_mapping() -> dict:
    """Tool name -> the filler list NAME it draws from, read out of the source.

    `_FILLER_TOOLS` is a local inside `_execute_tools`, so it cannot be
    imported. Parsing it is not a shortcut: resolving the list through the
    mapping rather than importing a constant by name is what makes these tests
    fail for the RIGHT reason on the code as it stood before the fix. Import
    `CALLBACK_FILLERS` directly and the pre-fix run dies with ImportError,
    which proves only that a constant is new — not that the phrases the caller
    actually heard were wrong.
    """
    from app.media_streams import llm_stream

    src = inspect.getsource(llm_stream).splitlines()
    try:
        start = next(
            i for i, ln in enumerate(src) if "_FILLER_TOOLS = {" in ln
        )
    except StopIteration:
        raise AssertionError(
            "_FILLER_TOOLS dict not found - was it renamed?"
        )
    end = next(
        i for i in range(start + 1, len(src)) if src[i].strip() == "}"
    )
    entries = {}
    for ln in src[start + 1:end]:
        entries.update(re.findall(r'"(\w+)":\s*(\w+)', ln))
    return entries


def _lists_under_test() -> dict:
    """The live phrase lists for the two tools, however they are named today."""
    mapping = _filler_tools_mapping()
    out = {}
    for tool in ("request_callback", "add_to_waitlist"):
        assert tool in mapping, f"{tool} has no _FILLER_TOOLS entry at all"
        list_name = mapping[tool]
        phrases = getattr(filler_phrases, list_name, None)
        assert phrases, (
            f"{tool} maps to {list_name}, which is not a list in "
            "app.filler_phrases"
        )
        out[f"{tool} -> {list_name}"] = phrases
    return out

# Words that presuppose something already on file for this caller.
_PRESUPPOSES_A_RECORD = (
    "appointment",
    "booking",
    "booked",
    "diary",
    "your slot",
    "reschedul",
)

# Phrasings that assert the owner has ALREADY been told. The filler is queued
# before the executor runs, so any of these can be spoken over a refusal.
_CLAIMS_THE_OUTCOME = (
    "i've let",
    "ive let",
    "i've told",
    "ive told",
    "i've passed",
    "ive passed",
    "has been told",
    "have been told",
    "is on the list",
    "you're on the list",
    "youre on the list",
    "all done",
    "sorted for you",
)


# -- the regression --------------------------------------------------------

def test_no_callback_filler_mentions_an_appointment():
    """The whole defect in one assertion. Ray had no appointment."""
    for name, phrases in _lists_under_test().items():
        for phrase in phrases:
            low = phrase.lower()
            for word in _PRESUPPOSES_A_RECORD:
                assert word not in low, (
                    f"{name} phrase {phrase!r} presupposes {word!r} — a caller "
                    "asking for a ring-back or a waitlist place has nothing on "
                    "file, which is how CAa0f76e2c heard 'just pulling your "
                    "appointment up'"
                )


def test_no_callback_filler_claims_the_clinic_has_been_told():
    """with_filler queues the primary phrase BEFORE awaiting the executor, so
    every phrase here can be spoken over a refusal — and _exec_request_callback
    now has a phone gate that refuses."""
    for name, phrases in _lists_under_test().items():
        for phrase in phrases:
            low = phrase.lower()
            for claim in _CLAIMS_THE_OUTCOME:
                assert claim not in low, (
                    f"{name} phrase {phrase!r} states the outcome as done. The "
                    "filler is queued before the executor runs, so on a refused "
                    "callback the caller is told the clinic knows when it does "
                    "not — the promise the whole request_callback contract exists "
                    "to prevent"
                )


def test_the_two_tools_no_longer_draw_from_lookup_fillers():
    """Read the mapping out of llm_stream rather than restating it: the defect
    was a dict entry, and a revert would put it straight back."""
    from app.media_streams import llm_stream

    src = inspect.getsource(llm_stream)
    mapping = re.search(r"_FILLER_TOOLS = \{(.*?)\n\s*\}", src, re.DOTALL)
    assert mapping, "_FILLER_TOOLS dict not found — was it renamed?"
    body = mapping.group(1)

    for tool, expected in (
        ("request_callback", "CALLBACK_FILLERS"),
        ("add_to_waitlist", "WAITLIST_FILLERS"),
    ):
        entry = re.search(rf'"{tool}":\s*(\w+)', body)
        assert entry, f"{tool} has no _FILLER_TOOLS entry at all"
        assert entry.group(1) == expected, (
            f"{tool} draws from {entry.group(1)}, not {expected} — "
            "LOOKUP_FILLERS talks about an appointment the caller has not got"
        )


def test_lookup_fillers_itself_is_untouched():
    """The other half of the contract: lookup_patient genuinely IS acting on an
    appointment the caller has, so its list must keep saying so. Narrowing it
    would fix this defect in the wrong place."""
    assert any("appointment" in p.lower() for p in LOOKUP_FILLERS), (
        "LOOKUP_FILLERS no longer references an appointment — lookup_patient "
        "runs when the caller HAS one, and that wording is correct there"
    )


def test_the_new_phrases_survive_gate_five():
    """A filler bypasses `sanitise_response`, so nothing strips a banned phrase
    out of one. Assert directly against the same rule table instead — a phrase
    the engine forbids the model to say must not reach the caller by this door.

    `banned_opener` and `markdown_emphasis` are excluded: the first only ever
    fires at the start of a MODEL chunk and would flag the deliberate
    "Of course — " openers the sibling lists already ship, and the second is an
    inline strip rather than a ban.
    """
    inline_rules = {"banned_opener", "markdown_emphasis"}
    for name, phrases in _lists_under_test().items():
        for phrase in phrases:
            for rule_name, pattern in _BANNED_SENTENCE_RE:
                if rule_name in inline_rules:
                    continue
                assert not pattern.search(phrase), (
                    f"{name} phrase {phrase!r} trips Gate 5's {rule_name!r} "
                    "rule. Fillers never pass through sanitise_response, so "
                    "this would be the one door by which the caller hears a "
                    "phrase the engine forbids everywhere else"
                )


def test_every_phrase_is_distinct_within_its_list():
    """with_filler tracks `used_fillers` to avoid repeating itself; duplicates
    inside one list defeat that silently."""
    for name, phrases in _lists_under_test().items():
        assert len(phrases) == len(set(phrases)), (
            f"{name} contains a duplicate, which collapses with_filler's "
            "used_fillers rotation"
        )
        assert len(phrases) >= 2, (
            f"{name} needs at least two phrases or every callback on a call "
            "sounds identical"
        )
