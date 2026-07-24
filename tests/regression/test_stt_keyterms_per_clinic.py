# tests/regression/test_stt_keyterms_per_clinic.py
"""
P1 (2026-07-24) — the STT keyterm boost carried two words, and neither helped.

Incident
--------
Two consecutive JV test calls said "the back of my calf is swollen and warm".
AssemblyAI returned "car" on the first and "coffee" on the second.

`clinical_screening.match_screen_trigger()` is exact-substring on the raw
transcript, and the DVT screen triggers on the literal token "calf". Neither
"car" nor "coffee" matched, so `pending_screen` never armed, `screen_red_flag`
was never set, and the `book_appointment` tool-boundary block stayed dormant.
The life-safety path — the one the architecture promises "never depends on the
model" (connection.py, Clinical screening Layer 1) — ran entirely on the model.

On the second call that cost the right answer: the caller replied "no I'm just
kind of tired lately", which `classify_screen_answer()` scores as **clear**
(no red-flag keyword; first word "no"). Layer 1 would have cleared the screen
and moved to booking. Layer 2 escalated to NHS 111 instead. The two layers
disagreed, and which one decided the caller's outcome came down to whether
AssemblyAI heard one word.

Root cause
----------
`_WORD_BOOST_TERMS` (109 terms, including "calf") was DEAD. Its only sender,
`_send_word_boost()`, was disconnected when v3 began rejecting the post-Begin
JSON message with close code 3006, and the terms were never migrated to the
`?keyterms_prompt=` URL parameter that replaced it. The live boost was a
hardcoded `["Alcester", "Redditch"]` — two Theorem clinic names, boosting
nothing useful on any other clinic, and hardcoded clinic identity in engine
code besides (CLAUDE.md: clinic-specific behaviour belongs in clinic.json).

Fix
---
`build_keyterms(clinic)` composes the list per clinic, screening vocabulary
first, and `start()` sends it at connection time.
"""

import json

import pytest

from app.media_streams.stt_stream import (
    _GENERIC_KEYTERMS,
    _KEYTERM_MAX_CHARS,
    _KEYTERM_MAX_WORDS,
    _KEYTERMS_MAX,
    build_keyterms,
)


def _lower(terms):
    return {t.lower() for t in terms}


# ---------------------------------------------------------------------------
# The regression itself.
# ---------------------------------------------------------------------------
def test_jv_clinic_boosts_calf():
    """The exact word whose mis-transcription defeated the DVT screen."""
    from app.clinic_config import get_clinic

    terms = build_keyterms(get_clinic("jv_v1"))
    assert "calf" in _lower(terms), (
        "'calf' is not boosted for jv_v1 — this is the word that came back as "
        "'car' and 'coffee' and took the deterministic red-flag layer out of "
        "the call"
    )


@pytest.mark.parametrize("clinic_id", ["jv_v1", "theorem", "vital_edge"])
def test_every_screening_trigger_word_is_boosted(clinic_id):
    """Every distinctive word that can arm a red-flag screen must be boosted.

    This is the general form of the bug: a trigger word the STT is not primed
    for is a screen that silently never arms, and nothing logs it.

    Asserted per WORD, not per phrase. JV has 167 screening keywords; boosting
    them whole overruns the 100-term API cap and truncates two entire screens.
    Boosting "calf" is what makes "the back of my calf" transcribe correctly,
    so the contract is that no screen loses its distinctive vocabulary.
    """
    from app.clinic_config import get_clinic
    from app.media_streams.stt_stream import _distinctive_tokens

    clinic = get_clinic(clinic_id)
    boosted = _lower(build_keyterms(clinic))

    missing = []
    for screen in (clinic.get("clinical_screening") or {}).get("screens") or []:
        for kw in screen.get("trigger_keywords") or []:
            for token in _distinctive_tokens(kw):
                if token not in boosted:
                    missing.append((screen.get("id"), kw, token))

    assert not missing, (
        f"{clinic_id}: screening trigger words not boosted: {missing} — each "
        "is a red-flag screen that can fail to arm on a mis-transcription"
    )


def test_screening_vocabulary_outranks_generic():
    """Priority order survives the 100-term cap.

    The cap truncates from the end, so if generic vocabulary sorted first the
    safety vocabulary would be the part that gets dropped.
    """
    clinic = {
        "clinical_screening": {"screens": [{"id": "dvt", "trigger_keywords": ["calf"]}]}
    }
    terms = build_keyterms(clinic)
    assert terms[0] == "calf"


# ---------------------------------------------------------------------------
# No clinic identity in engine code.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name", ["Alcester", "Redditch", "Theorem", "Kinwarton", "Greig", "Leanne"]
)
def test_no_clinic_names_hardcoded_in_engine_vocabulary(name):
    """CLAUDE.md: clinic-specific behaviour belongs in clinic.json.

    These were hardcoded in the engine list. They now arrive from each clinic's
    own config, which is also what makes them correct on the other clinics.
    """
    assert name.lower() not in _lower(_GENERIC_KEYTERMS), (
        f"{name!r} is clinic identity hardcoded in engine code"
    )


def test_theorem_still_gets_its_own_names():
    """Removing the hardcode must not cost Theorem its boost."""
    from app.clinic_config import get_clinic

    boosted = _lower(build_keyterms(get_clinic("theorem")))
    assert {"alcester", "redditch"} & boosted, (
        "Theorem lost the clinic names that used to be hardcoded"
    )


# ---------------------------------------------------------------------------
# Slot budget. The cap is the scarce resource, and every wasted slot is taken
# from the tier below -- which is how the whole generic clinical vocabulary
# nearly vanished for the one clinic that overflows.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("clinic_id", ["jv_v1", "theorem", "vital_edge"])
def test_no_structural_config_keys_are_boosted(clinic_id):
    """`stt_variants` category keys are schema, not speech.

    `clinic_name` and `services` are the shape of the config; nobody utters
    them. They were being sent to AssemblyAI as literal keyterms, costing two
    slots per clinic off a hard cap of 100.
    """
    from app.clinic_config import get_clinic

    boosted = _lower(build_keyterms(get_clinic(clinic_id)))
    leaked = {"clinic_name", "services", "brand_names", "locations"} & boosted
    assert not leaked, f"{clinic_id}: config schema keys boosted as vocabulary: {leaked}"


def test_service_slugs_are_normalised_to_spoken_form():
    """Nested `stt_variants` keys are slugs; underscores are not pronounced."""
    from app.clinic_config import get_clinic

    boosted = _lower(build_keyterms(get_clinic("vital_edge")))
    assert "deep tissue massage" in boosted
    assert not any("_" in t for t in boosted), "un-normalised slug reached keyterms"


def test_anatomy_survives_the_cap_on_the_overflowing_clinic():
    """jv_v1 is at the 100-term cap, so tier order decides what it loses.

    Regression: the generic list led with Northern dialect ("nesh", "gradely",
    "mardy"), so jv_v1 spent all of its surviving generic slots on idiom and
    dropped the body parts callers actually name. Dialect is also regionally
    wrong for Kingston. Clinical vocabulary must outrank it.
    """
    from app.clinic_config import get_clinic

    boosted = _lower(build_keyterms(get_clinic("jv_v1")))
    missing = {
        "knee", "hip", "shoulder", "ankle", "spine", "neck",
        "hamstring", "achilles", "calf",
    } - boosted
    assert not missing, f"jv_v1 lost core anatomy to the cap: {sorted(missing)}"


# ---------------------------------------------------------------------------
# API limits and defensive behaviour. A bad clinic config must degrade, not
# break the call: with no STT there is no conversation at all.
# ---------------------------------------------------------------------------
def test_respects_api_limits():
    from app.clinic_config import get_clinic

    for clinic_id in ("jv_v1", "theorem", "vital_edge"):
        terms = build_keyterms(get_clinic(clinic_id))
        assert len(terms) <= _KEYTERMS_MAX, f"{clinic_id}: too many keyterms"
        for t in terms:
            assert len(t) <= _KEYTERM_MAX_CHARS, f"{clinic_id}: {t!r} too long"
            assert len(t.split()) <= _KEYTERM_MAX_WORDS, f"{clinic_id}: {t!r} too many words"


def test_terms_are_unique():
    from app.clinic_config import get_clinic

    terms = build_keyterms(get_clinic("jv_v1"))
    lowered = [t.lower() for t in terms]
    assert len(lowered) == len(set(lowered)), "duplicate keyterms waste capped slots"


def test_none_clinic_degrades_to_generic():
    """Clinic unresolved (env misconfiguration) must still boost the basics."""
    terms = build_keyterms(None)
    assert "calf" in _lower(terms)
    assert terms  # never empty


@pytest.mark.parametrize(
    "clinic",
    [
        {},
        {"clinical_screening": None},
        {"clinical_screening": {"screens": None}},
        {"clinical_screening": {"screens": ["not-a-dict"]}},
        {"locations": [None, "not-a-dict"]},
        {"stt_variants": {"a": {"b": ["c"]}}},   # nested dict/list mix
    ],
)
def test_malformed_config_does_not_raise(clinic):
    """A malformed clinic.json must not be able to break STT startup."""
    terms = build_keyterms(clinic)
    assert isinstance(terms, list)
    assert all(isinstance(t, str) for t in terms)


def test_output_is_json_serialisable():
    """The list goes straight into the WebSocket URL as JSON."""
    from app.clinic_config import get_clinic

    encoded = json.dumps(build_keyterms(get_clinic("jv_v1")))
    assert encoded.startswith("[")
    # Sanity bound on URL growth -- 100 terms should not approach header limits.
    assert len(encoded) < 4000, f"keyterms JSON is {len(encoded)} chars"
