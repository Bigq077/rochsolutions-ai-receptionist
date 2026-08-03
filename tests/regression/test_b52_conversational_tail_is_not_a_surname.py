# tests/regression/test_b52_conversational_tail_is_not_a_surname.py
"""
B-52 — "by the way" was written to Google Calendar as the surname "Way".

CAb215dec57c7198603dace750d432d1f6, 3 Aug 2026, build 9032ee804f19:

    16:58:00  FINAL 'um my name is quentin by the way'
    16:58:02  [ms_conn v3] name persisted (normal path): 'Quentin Way'
    16:58:02  v3_phone_dtmf_active = True (name confirmed — phone collection phase)
    16:59:00  book_appointment patient_name: "Quentin Way"   -> event ut7p0a17j71f...

One fault, three symptoms, and the two downstream ones are why the whole call
felt muddled:

  * the surname step was SKIPPED — 'Quentin Way' is a complete first+surname, so
    nothing remained to ask for, and the caller was never asked their family name;
  * the phone phase armed at turn 2 ("name confirmed"), which is why the number
    was requested before the reason was finished and before any slot existed;
  * a real calendar event was created under a name the caller never gave.

NOT A REGRESSION. Reproduced identically at a1ef3dc (12 Jul), 7dfc0c2 (18 Jul),
aa0b3bd (31 Jul — last change to this file), 1fe8f7f (2 Aug, last commit before
the 3 Aug work) and 3af4bd8 (3 Aug 12:10, the last commit verified on a live
call). Three weeks old; it had simply never been said to Susie before.

TWO INDEPENDENT FAULTS. Fixing either alone leaves a live hole.

Fault 1 — the preposition class was half-present.
    SURNAME_STOPWORDS already held "on", "with", "for", "from", "to", "of" and
    "as", but not "by", "at" or "in". That is the accident this file already
    documents for "one" present / "six" absent. It is what produced 'By' on the
    long-tail branch ("my name is quentin by the way i've hurt my knee").

Fault 2 — _walk_particles_back dropped leading tokens without checking them.
    Dropping is legitimate only for a MIDDLE NAME: ["james","rock"] -> "rock".
    Given ["by","the","way"] it dropped "by the" and kept "way" — and "way"
    passes ok() on its own merits: not a stopword, not a contraction, not a
    false positive. NO WORD LIST REACHES IT. The signal is the company it
    keeps — "the" sits between the first name and the candidate, and a real
    surname group never contains an interior stopword.

That second point is why this is not fixed by adding "way" to a list. The
register's §A4 lesson is the whole argument: an open-ended set of English words
cannot be enumerated one live call at a time.
"""
from __future__ import annotations

import pytest

from app.name_capture import (
    SURNAME_PARTICLES,
    SURNAME_STOPWORDS,
    extract_surname,
)


# ── the live call, verbatim ────────────────────────────────────────────────

def test_the_live_call_utterance_yields_no_surname():
    assert extract_surname("um my name is quentin by the way", "quentin") == "", (
        "CAb215dec5: this booked a real appointment under 'Quentin Way'"
    )


# ── fault 2: a conversational tail is not a name sequence ──────────────────

@pytest.mark.parametrize("utterance,first", [
    ("um my name is quentin by the way",              "quentin"),
    ("my name is quentin by the way",                 "quentin"),
    ("sorry my name is quentin by the way",           "quentin"),
    ("i'm sarah by the way",                          "sarah"),
    ("it's tom by the way",                           "tom"),
    ("that's quentin by the way",                     "quentin"),
    ("my name is quentin at the moment",              "quentin"),
    ("i'm sarah in a rush",                           "sarah"),
    ("my name is quentin on the phone",               "quentin"),
    ("i'm tom for the record",                        "tom"),
])
def test_a_conversational_tail_is_never_a_surname(utterance, first):
    assert extract_surname(utterance, first) == "", (
        f"{utterance!r} is ordinary speech, not a name — capturing from it "
        "writes a stranger's name to a clinical record"
    )


# ── fault 1: the long-tail branch, which fault 2 alone does NOT close ──────

@pytest.mark.parametrize("utterance,first", [
    ("my name is quentin by the way i've hurt my knee",  "quentin"),
    ("i'm sarah by the way i need an appointment",       "sarah"),
    ("my name is quentin at the clinic right now",       "quentin"),
])
def test_the_long_tail_branch_does_not_take_a_preposition(utterance, first):
    """Tails longer than _MAX_SURNAME_TOKENS take the token straight after the
    first name via _walk_particles_forward, so the interior-stopword rule never
    sees them. Before the stopword class was completed this returned 'By'."""
    assert extract_surname(utterance, first) == ""


def test_the_preposition_class_stays_complete():
    """Source-pinned. Half of this class was present and half absent for no
    stated reason, and the absent half is what reached the calendar."""
    for word in ("on", "with", "for", "from", "to", "of", "as",
                 "by", "at", "in", "into", "onto", "via", "about"):
        assert word in SURNAME_STOPWORDS, (
            f"{word!r} left the stopword class — B-52 is reachable again"
        )


# ── controls: every surname shape that must SURVIVE ────────────────────────
# These matter more than the cases above. Over-rejecting a surname is not a
# safe failure: it sends the caller back round the surname loop, and B-15
# recorded that loop being asked twice on a live call.

@pytest.mark.parametrize("utterance,first,expected", [
    ("my name is quentin roch",          "quentin", "Roch"),
    ("my name is quentin james roch",    "quentin", "Roch"),      # middle name
    ("i'm maria de silva",               "maria",   "De Silva"),  # particle
    ("my name is piet van der berg",     "piet",    "Van Der Berg"),
    ("my name is john bin ahmed",        "john",    "Bin Ahmed"),
    ("it's sarah o'brien",               "sarah",   "O'Brien"),   # apostrophe
    ("i'm tom smith-jones",              "tom",     "Smith-Jones"),
    ("um yeah quentin rock",             "quentin", "Rock"),      # leading fillers
    ("my name is sarah jenkins please",  "sarah",   "Jenkins"),   # trailing filler
    ("that would be quentin rock",       "quentin", "Rock"),
    ("it's sarah jenkins thanks",        "sarah",   "Jenkins"),
])
def test_real_surnames_still_captured(utterance, first, expected):
    assert extract_surname(utterance, first) == expected, (
        "over-rejecting sends the caller back round the surname loop"
    )


def test_a_middle_name_is_still_dropped_not_rejected():
    """The dropped-token rule must distinguish a middle name from junk. This is
    the exact boundary: ['james','roch'] drops 'james' and keeps 'roch';
    ['by','the','way'] drops 'by the' and must keep nothing."""
    assert extract_surname("my name is quentin james roch", "quentin") == "Roch"
    assert extract_surname("um my name is quentin by the way", "quentin") == ""


def test_particles_are_exempt_from_the_dropped_token_rule():
    """Particles are dropped by the backward walk only when they bind forward
    onto the surname, so the rule must not count them as junk."""
    assert extract_surname("i'm maria de silva", "maria") == "De Silva"
    assert extract_surname("my name is piet van der berg", "piet") == "Van Der Berg"
    for p in ("de", "van", "der", "bin", "al"):
        assert p in SURNAME_PARTICLES


def test_no_surname_offered_is_still_no_surname():
    assert extract_surname("hi my name is quentin", "quentin") == ""
    assert extract_surname("", "quentin") == ""
    assert extract_surname("my name is quentin", "") == ""
