"""A caller who spelled their surname had the SPELLING written to the record.

CA8d5b2e3e, northgate, 7 Sep 2026, 10:43:12 — a verification call on the
demo line. Asked for their name, the caller said:

    "um yes that'll be quentin and then my surname is r-o-c-h roch"

and the record was built as:

    [ms_conn v3] name persisted (normal path): 'Quentin R-O-C-H'
    Row built — outcome=reached_confirmation name=Quentin R-O-C-H

The surname sanitiser keeps hyphens, and it has to — Smith-Jones, Al-Sayed,
Lloyd-Webber. So `r-o-c-h` arrived as one ordinary-looking token, cleared every
name stoplist, and was title-cased straight into what reaches the calendar and
the confirmation SMS.

WHY IT IS INVISIBLE, and why it belongs with the wrong-surname family rather
than with parsing tidiness: the spoken read-back that same turn said "So that's
Quentin Roch", because the model reads its own sentence rather than the stored
field, and the surname is never read back for confirmation. The caller hears
their name pronounced correctly and rings off. Nobody can hear the defect; it
surfaces when the clinic looks at the diary.

`backfill_surname` already understood spelling, and understood it better than
the first version of this fix assumed: rule 2's own sanitiser turns hyphens into
spaces, so it has always read "r-o-c-h" correctly. What had never seen a
spelling was the MARKER path in `extract_surname`, which takes the first
plausible token after "surname is" and was handed the hyphenated one.

So the collapse lives in `extract_surname` alone, and the last two tests here
are why. An earlier version normalised in both, which destroyed the very run
rule 2 scans for and silently lost the surname on seven stored turns that
resolve correctly today.

The second half of this file is the load-bearing half. A run of single letters
joined by hyphens is a spelling; a real double-barrelled name has WORDS on both
sides of the hyphen. If that distinction ever collapses, this fix starts
mangling Smith-Jones — a worse defect than the one it repairs, and on a commoner
name.
"""

import pytest

from app.name_capture import (
    backfill_surname,
    collapse_spelled_runs,
    extract_surname,
)

_FIRST = "Quentin"


# -- the live call ---------------------------------------------------------

def test_the_live_utterance_stores_the_word_not_the_spelling():
    said = "um yes that'll be quentin and then my surname is r-o-c-h roch"
    assert extract_surname(said, _FIRST) == "Roch"
    assert backfill_surname(said, _FIRST) == "Roch"


@pytest.mark.parametrize("said,expected", [
    # spelling first, then the word -- the live shape
    ("my surname is r-o-c-h roch", "Roch"),
    # the word first, then the spelling
    ("my surname is roch r-o-c-h", "Roch"),
    # the spelling alone
    ("my surname is r-o-c-h", "Roch"),
    ("my last name is s-m-i-t-h smith", "Smith"),
    # short surnames people really do spell
    ("my surname is l-i li", "Li"),
    ("my surname is n-g", "Ng"),
])
def test_a_spelled_surname_resolves_to_the_word(said, expected):
    assert extract_surname(said, _FIRST) == expected, said


def test_the_separated_spelling_still_works():
    """The shape `backfill_surname` already handled must not regress."""
    assert backfill_surname("surname is r o c h roch", _FIRST) == "Roch"


# -- what must NOT change: genuine hyphenated surnames ---------------------

@pytest.mark.parametrize("said,expected", [
    ("my surname is smith-jones", "Smith-Jones"),
    ("my surname is al-sayed", "Al-Sayed"),
    ("my surname is lloyd-webber", "Lloyd-Webber"),
    ("my surname is jean-baptiste", "Jean-Baptiste"),
    ("my surname is o-brien", "O-Brien"),
])
def test_a_real_double_barrelled_surname_keeps_its_hyphen(said, expected):
    """The hyphen the sanitiser keeps is kept FOR these. Collapsing them would
    be a worse defect than the one this fix repairs, on a commoner name."""
    assert extract_surname(said, _FIRST) == expected, said


# -- and the normaliser must not touch ordinary speech --------------------

@pytest.mark.parametrize("text", [
    "i've got a bad knee",
    "it's my lower back that's the problem",
    "e-mail me the details",
    "my postcode is b97 5ab",
    "a follow-up appointment please",
    "it's a long-standing problem",
])
def test_the_normaliser_leaves_ordinary_speech_alone(text):
    assert collapse_spelled_runs(text) == text, text


def test_the_normaliser_needs_a_run_of_single_letters():
    """Every segment must be ONE letter. That is the whole discriminator
    between a spelling and a hyphenated word."""
    assert collapse_spelled_runs("r-o-c-h") == "roch"
    assert collapse_spelled_runs("smith-jones") == "smith-jones"
    assert collapse_spelled_runs("ro-ch") == "ro-ch"
    assert collapse_spelled_runs("r-och") == "r-och"

# -- what the corpus taught, and the reason the collapse is NOT in backfill --

@pytest.mark.parametrize("said,expected", [
    # No explicit marker, so rule 1 never fires and rule 2 -- the spelled-run
    # scan -- is the only thing that can resolve these. Its own sanitiser
    # already turns the hyphens into spaces, so it has ALWAYS read these
    # correctly. Collapsing the run before it destroys exactly what it looks
    # for: measured over the stored corpus, an earlier version of this fix
    # silently lost the surname on seven turns that resolve today.
    ("um yeah that'll be roch r-o-c-h", "Roch"),
    ("uh that'd be roch r-o-c-h", "Roch"),
    ("uh rock r-o-c-h", "Roch"),
    ("green g-r-e-e-n like the color", "Green"),
])
def test_a_spelled_surname_with_no_marker_still_resolves(said, expected):
    assert backfill_surname(said, _FIRST) == expected, said


def test_backfill_does_not_collapse_before_its_own_run_scan():
    """Structural, because the behavioural tests above pass either way on some
    inputs and the ordering is the whole point.

    `backfill_surname` must read the RAW utterance. Rule 1 delegates to
    `extract_surname`, which collapses for itself; rule 2's sanitiser turns
    hyphens into spaces and needs the run intact.
    """
    import inspect

    from app import name_capture

    src = inspect.getsource(name_capture.backfill_surname)
    assert "collapse_spelled_runs" not in src, (
        "backfill_surname must not collapse spelled runs -- rule 2 scans for "
        "the single-letter run that collapsing removes"
    )
