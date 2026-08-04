"""Keyterm boosting must reflect the clinic, not the clinic the engine grew up in.

`_GENERIC_KEYTERMS` was written for JV: a Yorkshire PHYSIOTHERAPY clinic. It is
applied to every clinic that does not override it, and it boosts:

  - "physiotherapy", "physiotherapist", "osteopath", "acupuncture", "shockwave",
    "psychotherapy", "laser", "prescribing", "Pilates", "musculoskeletal"
  - Yorkshire dialect: "nowt", "owt", "summat", "reight", "gradely", "nesh",
    "mardy", "gi'o'er", "champion", "mint"

On the first live Universal-3.5 call for Vital Edge (2026-08-04) that list took
roughly 30 of the 100 available slots — boosting vocabulary for treatments
Jonathan declines, and a dialect from 200 miles away, while the massage words
that decide whether a booking is heard correctly competed for what was left.

`clinic.json` may now carry:

    "stt_keyterms": {"use_generic": false, "terms": [...]}

Absent the key, the composition is unchanged, which is what the JV and Theorem
tests below exist to hold.
"""
from app.clinic_config import get_clinic
from app.media_streams.stt_stream import (
    _GENERIC_KEYTERMS,
    _KEYTERMS_MAX,
    build_keyterms,
)

PHYSIO_ONLY = {
    "osteopath", "shockwave", "psychotherapy", "laser", "prescribing",
    "pilates", "musculoskeletal", "rehabilitation", "physiotherapist",
}
YORKSHIRE = {
    "nowt", "owt", "summat", "reight", "gradely", "nesh", "mardy",
    "gi'o'er", "champion", "mint",
}


# ---------------------------------------------------------------------------
# Vital Edge — the clinic that exposed this
# ---------------------------------------------------------------------------
def test_vital_edge_boosts_no_physio_or_yorkshire_vocabulary():
    terms = {t.lower() for t in build_keyterms(get_clinic("vital_edge"))}
    leaked = terms & (PHYSIO_ONLY | YORKSHIRE)
    assert not leaked, f"Vital Edge is boosting irrelevant vocabulary: {sorted(leaked)}"


def test_vital_edge_boosts_what_it_actually_sells():
    terms = {t.lower() for t in build_keyterms(get_clinic("vital_edge"))}
    for must in (
        "deep tissue", "sports massage", "massage",
        "neck back and shoulders", "amino neural therapy",
        "knots", "tension", "pressure",
    ):
        assert must in terms, f"Vital Edge is not boosting {must!r}"


def test_vital_edge_boosts_its_own_geography():
    """A Kingston clinic hearing Midlands town names is worse than hearing none."""
    terms = {t.lower() for t in build_keyterms(get_clinic("vital_edge"))}
    assert "kingston upon thames" in terms
    assert {"surbiton", "norbiton", "new malden"} & terms


def test_declined_treatments_are_still_audible():
    """She has to HEAR "acupuncture" to decline it correctly."""
    terms = {t.lower() for t in build_keyterms(get_clinic("vital_edge"))}
    assert {"acupuncture", "reiki", "physio"} <= terms


# ---------------------------------------------------------------------------
# Everyone else is untouched
# ---------------------------------------------------------------------------
def test_a_clinic_without_the_key_is_unchanged():
    """The override is opt-in; absent it, the generic tier still applies."""
    theorem = {t.lower() for t in build_keyterms(get_clinic("theorem"))}
    # Theorem does not fill the cap, so the generic tier is reachable and its
    # regional vocabulary still present — i.e. nothing changed for it.
    assert theorem & YORKSHIRE, "the generic tier stopped applying to Theorem"


def test_no_clinic_at_all_yields_the_generic_list():
    assert build_keyterms(None) == list(_GENERIC_KEYTERMS)[:_KEYTERMS_MAX]


def test_an_empty_override_does_not_silently_drop_the_generic_list():
    """use_generic defaults True — a partial override must not blank the list."""
    got = build_keyterms({"stt_keyterms": {"terms": ["widget"]}})
    assert "widget" in got
    assert set(_GENERIC_KEYTERMS[:5]) <= set(got)


# ---------------------------------------------------------------------------
# The budget
# ---------------------------------------------------------------------------
def test_every_clinic_stays_within_the_vendor_cap():
    for cid in ("vital_edge", "jv_v1", "theorem"):
        assert len(build_keyterms(get_clinic(cid))) <= _KEYTERMS_MAX, cid
