# tests/regression/test_d1_pronunciation_stays_in_the_tts_layer.py
"""
D1 - the phonetic spelling of Alcester leaked out of the TTS layer.

`_build_theorem_v3` taught the model to write "Awlstuh" so ElevenLabs would say
the town correctly. It worked, and it put the pronunciation hint into every
downstream consumer of Susie's words rather than only into the synthesiser.

Measured on the stored corpus, 9 Sep 2026, 134 Theorem calls:

  * "Awlstuh" appears in 105 transcripts; 62 of those have it in a line the
    MODEL wrote (the rest are engine constants, which is D1b).
  * Of the Theorem calls the judge scored 1-5 whose evidence mentions the
    clinic or location, 24 are the judge reading the spelling and reporting a
    "garbled clinic name" when nothing had gone wrong. Verbatim:
        "Susie replied 'Right - Is this for our Awlstuh or Redditch clinic?'
         (garbled clinic name)"
    That is ~19% of every low score Theorem has, and it is an artefact.
  * It cost a live defect: `f89a4c7e`, where the double-ask guard matched
    "alcester" - a spelling the prompt instructs the model never to produce.

The machinery to do this properly already exists and is already tested:
`_TTS_SUBSTITUTIONS_ELEVENLABS` and `_TTS_SUBSTITUTIONS_OPENAI` both rewrite
Alcester at synthesis time, and `test_pronunciation_has_one_owner.py` pins them.
So the prompt was duplicating a capability, not supplying a missing one.

WHAT HAD TO MOVE FIRST, and why this is not just a find-and-replace. Two engine
predicates were keyed on the phonetic spelling of SUSIE'S OWN speech, and both
would have failed silently the moment she started writing "Alcester":

  * the leading-affirmation stripper's "Awlstuh, perfect." rule;
  * `NAME_FALSE_POSITIVES`, which held "awlstuh" but not "alcester" - so
    "Alcester, perfect." would have matched the name-confirm pattern and stored
    the caller's name as "Alcester".

The caller-side alias lists are deliberately NOT touched: they enumerate what
STT hands back when the CALLER says the town, and "awlstuh" is a real thing to
hear. `_CLINIC_Q_SIGNALS` keeps both spellings for the same reason - removing a
guard entry is how `f89a4c7e` happened.
"""
from __future__ import annotations

import re

import pytest

from app.media_streams.connection import (
    _CLINIC_Q_SIGNALS,
    _strip_leading_affirmation,
)
from app.media_streams.tts_stream import (
    _apply_tts_substitutions_elevenlabs as _subs_11,
    _apply_tts_substitutions_openai as _subs_oai,
)
from app.name_capture import NAME_FALSE_POSITIVES
from app.prompts.susie_system_prompt import build_system_prompt_parts


# The one line that legitimately keeps the phonetic form: it lists spellings
# STT produces for the CALLER's speech.
_STT_VARIANTS_MARKER = "STT variants that mean Alcester"


def _theorem_prompt() -> str:
    """Rendered through the real entry point, never by calling the builder.

    `_build_theorem_v3` reads a dozen session keys and returns near-nothing
    when handed a bare dict — a fact about a prompt asserted against the module
    rather than against `build_system_prompt_parts` has been wrong here before.
    """
    static, dynamic = build_system_prompt_parts({
        "call_sid": "CAtest_d1",
        "clinic_id": "theorem_v3",
        "booking_flow_active": True,
        "collected": {},
    })
    return static + (chr(10) * 2) + dynamic


# ── the prompt no longer teaches the phonetic spelling ──────────────────────

def test_the_prompt_does_not_teach_susie_to_write_the_phonetic_form():
    prompt = _theorem_prompt()
    offenders = [
        line for line in prompt.splitlines()
        if "awlstuh" in line.lower() and _STT_VARIANTS_MARKER not in line
    ]
    assert not offenders, (
        "the phonetic spelling is back in the model-facing prompt:\n  "
        + "\n  ".join(o.strip()[:100] for o in offenders[:5])
    )


def test_the_prompt_still_names_the_town():
    """Removing the hint must not remove the clinic."""
    assert "Alcester" in _theorem_prompt()


def test_the_caller_side_variant_list_is_untouched():
    """What STT hands back for the CALLER is a different question, and
    'awlstuh' is genuinely one of the things it produces."""
    prompt = _theorem_prompt()
    assert _STT_VARIANTS_MARKER in prompt
    variants = next(
        l for l in prompt.splitlines() if _STT_VARIANTS_MARKER in l
    )
    assert "awlstuh" in variants.lower()


# ── the TTS layer still does the job the prompt was doing ───────────────────

def test_the_synthesiser_still_says_it_correctly():
    spoken = "Is this for our Alcester or Redditch clinic?"
    assert "Awlstuh" in _subs_11(spoken)
    assert "Awlstuh" in _subs_oai(spoken)
    assert "Alcester" not in _subs_11(spoken)


# ── the two predicates that had to be widened first ─────────────────────────

@pytest.mark.parametrize("town", ["Awlstuh", "Alcester"])
def test_the_affirmation_stripper_accepts_either_spelling(town):
    """`_V3_NAME_CONFIRM_PATTERNS`' own comment names 'Awlstuh, perfect.' as
    something that must be stripped rather than read as a name."""
    got = _strip_leading_affirmation(f"{town}, perfect. I've got you on 07502.")
    assert not got.lower().startswith(("awlstuh", "alcester")), got
    assert "I've got you on" in got


def test_alcester_cannot_be_captured_as_a_patients_name():
    """The bare name-confirm pattern matches "<Titlecase>, " — so the town has
    to be in the false-positive set under BOTH spellings or the caller ends up
    called Alcester."""
    for spelling in ("awlstuh", "alcester"):
        assert spelling in NAME_FALSE_POSITIVES, spelling


def test_the_clinic_question_guard_still_carries_both_spellings():
    """Deliberately unchanged for one release: old sessions and the whole
    stored corpus still contain the phonetic form, and removing a guard entry
    is the defect `f89a4c7e` was."""
    joined = " ".join(_CLINIC_Q_SIGNALS).lower()
    assert "awlstuh or redditch" in joined
    assert "alcester or redditch" in joined


# ── D1b: the engine's own phonetic constants never reach a reader ───────────
#
# The model writing "Alcester" is only half the job. `_LOC_RUNG1_OPEN`,
# `_LOC_RUNG2_CONFIRM` and `_LOC_RUNG3_DTMF` are written phonetically ON
# PURPOSE — their own comment says the DTMF handler and the clinic binding key
# off that wording — and they are queued to TTS already carrying it, so the obs
# transcript records it and the judge reads it. Rewriting those constants would
# mean changing strings several guards match on. Undoing the hint for the
# READER costs nothing on the call path and cannot change a spoken syllable.

def test_the_inverse_round_trips_the_forward_rule():
    from app.media_streams.tts_stream import undo_tts_substitutions

    written = "Is this for our Alcester or Redditch clinic?"
    assert undo_tts_substitutions(_subs_11(written)) == written


def test_every_pronunciation_rule_has_an_inverse():
    """A new rule with no inverse would leak silently — which is the whole
    failure mode this closes."""
    from app.media_streams import tts_stream as ts

    forward = {r for _, r in ts._TTS_SUBSTITUTIONS_ELEVENLABS}
    forward |= {r for _, r in ts._TTS_SUBSTITUTIONS_OPENAI}
    for spoken in forward:
        assert ts.undo_tts_substitutions(spoken) != spoken, (
            f"{spoken!r} is a pronunciation hint with no inverse — it will "
            f"reach the obs transcript and the judge verbatim"
        )


def test_the_inverse_never_raises():
    from app.media_streams.tts_stream import undo_tts_substitutions

    for bad in (None, "", "nothing to do here"):
        undo_tts_substitutions(bad)


def test_the_engine_ladder_is_recorded_canonically():
    """The three rung constants must read as English on the page."""
    from app.media_streams.connection import (
        _LOC_RUNG1_OPEN, _LOC_RUNG2_CONFIRM, _LOC_RUNG3_DTMF,
    )
    from app.media_streams.tts_stream import undo_tts_substitutions

    for rung in (_LOC_RUNG1_OPEN, _LOC_RUNG2_CONFIRM, _LOC_RUNG3_DTMF):
        # Still phonetic in the constant — the guards depend on that.
        assert "awlstuh" in rung.lower(), rung
        # But not once a reader sees it.
        assert "awlstuh" not in undo_tts_substitutions(rung).lower(), rung
        assert "alcester" in undo_tts_substitutions(rung).lower(), rung


def test_the_obs_record_site_applies_it():
    """Asserted on the source: reaching the record any other way means standing
    up the whole TTS loop, and the property is that ONE call sits between the
    display text and `record_assistant`."""
    import inspect
    import re as _re

    from app.media_streams import connection as _c

    src = inspect.getsource(_c)
    i = src.index("_obs_turns.record_assistant(self.session, _obs_display_text)")
    window = src[max(0, i - 900):i]
    assert "undo_tts_substitutions" in window, (
        "the obs transcript is no longer canonicalised before it is recorded"
    )
