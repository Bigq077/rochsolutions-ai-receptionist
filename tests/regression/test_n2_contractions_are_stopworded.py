# tests/regression/test_n2_contractions_are_stopworded.py
"""
N2 - "cancel it altogether" came back as "i can't", and the boost was ours.

Demo line, 9 Sep 2026 09:28:43, `CA7888178d5913d3a188a3f18c6a1efa59`, build
e340c35c0550. The caller answered the cancel retention question and STT
returned `"i can't"`. The FM-23 consent gate then refused `cancel_appointment`
- correctly, because that string is not consent to delete a real appointment -
Susie re-asked, and the judge scored the call 3 with tag `loop`.

This is B-66's mechanism, arriving through a different door. That fix boosted
the cancel family after measuring that "cancel" was the one control word STT
gets wrong; its own comment records the cause as *"every word it drifted toward
was boosted and 'cancel' was not"*. On northgate, `can't` was boosted at
keyterm index **2** and `cancel` at **84**.

WHY `can't` WAS THERE, and why it is a bug rather than a decision. It is not in
any hand-written list. `_KEYTERM_STOPWORDS` already holds `can`, `cant`,
`cannot` and `wont` - the authors plainly meant to drop this word - but
`_distinctive_tokens` strips only EDGE apostrophes, so the token `can't` never
matched the entry `cant` and survived a filter written to remove it.

Thirteen `trauma_fracture` and `serious_spinal` phrases reduce to it, which is
why it ranked so high:

    "can't put weight"       -> ["can't"]
    "can't walk on it"       -> ["can't"]
    "can't sleep for the pain"-> ["can't"]

NOTHING CLINICAL IS LOST, and that is the assertion this file exists to hold.
`weight`, `walk`, `stand`, `sleep` and `move` are ALREADY stopwords, deliberately
and with a measurement behind them - the file's rule is "a term earns a slot
only if STT is plausibly going to get it WRONG", and those transcribe cleanly.
The fracture screen's informative words - `swollen`, `swelling`, `deformed`,
`misshapen`, `snap`, `crack` - keep their slots. And the screen's TRIGGER
matching reads `clinic.json`, not the keyterm list, so it is untouched either
way.

Measured 2026-09-09, both clinics that sit at the 100-term cap gain back
exactly the anatomy the file says must never be starved:

    northgate  loses can't, won't -> gains "frozen shoulder", "tennis elbow"
    jv_v1      loses can't, won't -> gains "fasciitis", "tendon"
    vital_edge and theorem_v3     -> byte-identical (under the cap)
"""
from __future__ import annotations

import pytest

from app.clinic_config import get_clinic
from app.media_streams.stt_stream import (
    _KEYTERM_STOPWORDS,
    _distinctive_tokens,
    build_keyterms,
)

CLINICS = ["northgate", "jv_v1", "vital_edge", "theorem_v3"]


def _keyterms(clinic_id: str) -> list:
    return build_keyterms(get_clinic(clinic_id))


# ── the filter that was written to drop these now drops them ────────────────

@pytest.mark.parametrize(
    "phrase",
    [
        "can't put weight",
        "can't put any weight",
        "can't walk on it",
        "can't stand on it",
        "can't sleep for the pain",
        "won't move",
        "can't grip",
    ],
)
def test_a_contraction_is_dropped_like_its_apostrophe_free_twin(phrase):
    """`cant` and `wont` are in the stopword list; `can't` and `won't` are the
    same words with punctuation."""
    for token in _distinctive_tokens(phrase):
        bare = token.replace("'", "")
        assert bare not in _KEYTERM_STOPWORDS, (
            f"{token!r} survived a filter that already lists {bare!r}"
        )


def test_the_stopword_list_still_names_the_bare_forms():
    """If these ever leave the list this test is asserting nothing."""
    for word in ("can", "cant", "cannot", "wont"):
        assert word in _KEYTERM_STOPWORDS, word


def test_the_fix_is_precise_and_keeps_real_apostrophe_terms():
    """Only contractions the list already names are dropped. A possessive or a
    dialect form is ordinary vocabulary and must survive."""
    assert _distinctive_tokens("grip's gone") == ["grip's"]
    assert "golfer's" in _distinctive_tokens("golfer's elbow")


# ── the live consequence: the competitor is gone, the control words remain ──

@pytest.mark.parametrize("clinic_id", CLINICS)
def test_no_clinic_boosts_a_near_homophone_of_the_control_words(clinic_id):
    terms = _keyterms(clinic_id)
    assert "can't" not in terms, (
        f"{clinic_id} still boosts \"can't\" — it out-competed 'cancel' on "
        f"CA7888178d5913d3a188a3f18c6a1efa59"
    )
    assert "won't" not in terms


@pytest.mark.parametrize("clinic_id", CLINICS)
def test_the_cancel_family_is_still_boosted(clinic_id):
    """B-66 must survive this. Removing the competitor is worthless if the
    thing it was competing with falls off the cap."""
    terms = _keyterms(clinic_id)
    for word in ("cancel", "cancel it", "cancelled"):
        assert word in terms, (word, clinic_id)


# ── the safety assertion ────────────────────────────────────────────────────

@pytest.mark.parametrize("clinic_id", ["northgate", "jv_v1"])
def test_the_fracture_screen_keeps_every_word_it_relies_on(clinic_id):
    """The trade this fix must NOT make. `can't` carried thirteen
    trauma_fracture and serious_spinal phrases, so dropping it has to leave the
    screen's informative vocabulary intact."""
    terms = _keyterms(clinic_id)
    for word in ("swollen", "swelling", "deformed", "misshapen", "snap", "crack"):
        assert word in terms, (word, clinic_id)


@pytest.mark.parametrize("clinic_id", ["northgate", "jv_v1"])
def test_the_freed_slots_go_to_starved_anatomy(clinic_id):
    """The file's own rule: anatomical and pathological words keep their slots.
    Both capped clinics were losing some to a contraction."""
    terms = _keyterms(clinic_id)
    expected = {
        "northgate": ("frozen shoulder", "tennis elbow"),
        "jv_v1": ("fasciitis", "tendon"),
    }[clinic_id]
    for word in expected:
        assert word in terms, (word, clinic_id)


@pytest.mark.parametrize("clinic_id", CLINICS)
def test_the_cap_is_still_respected(clinic_id):
    assert len(_keyterms(clinic_id)) <= 100
