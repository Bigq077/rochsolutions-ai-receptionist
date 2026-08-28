# tests/regression/test_b117_the_spent_band_is_acknowledged.py
"""
B-117 - the wording half of B-116, same call (CA13b8dc5cb8, 28 Aug 2026).

B-98 opens a time band once the caller has heard every slot it kept, and B-116
then leads the readout with times they have NOT heard. For a caller who asked
for mornings and used them up, those times are all afternoons. Correct, and
silent about why:

    caller:  "and what about tuesday morning again"
    Susie:   "On Tuesday the 8th of September - one in the afternoon, two in
              the afternoon..."

Read as not listening. That caller asked a third time and hung up
(outcome=abandoned, judge score 2). So Susie now says why first:

    "I've given you all the mornings I have that day, I'm afraid.
     On Tuesday the 8th of September - one in the afternoon, ..."

SENTENCE ONLY. This must never change which times are offered; the test below
pins that. The claim is decided where B-98 decides it and carried on the
payload as band_spent_label - never re-derived from the text, because "you have
heard all the mornings" is a fact about this caller's history and the retrieval
path is the only thing that knows it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.tools.receptionist_tools import _filter_tuples_by_preference
from app.tools.slot_followup import acknowledge_spent_band

READOUT = "The available slots for Tuesday 8th September are - Number 1, midday."
_TZ = timezone(timedelta(hours=1))


def _tuples(times):
    base = (datetime.now(_TZ) + timedelta(days=11)).date()
    out = []
    for hh in times:
        st = datetime(base.year, base.month, base.day, hh, 0, tzinfo=_TZ)
        out.append((st, st + timedelta(minutes=60)))
    return out


# ---------------------------------------------------------------------------
# The sentence
# ---------------------------------------------------------------------------
def test_the_spent_band_is_named_before_times_outside_it():
    text, action = acknowledge_spent_band(READOUT, "mornings")
    assert action == "prepended"
    assert text.startswith("I've given you all the mornings I have that day, I'm afraid.")
    assert READOUT in text


def test_nothing_is_said_when_the_band_was_not_spent():
    assert acknowledge_spent_band(READOUT, "") == (READOUT, "unchanged")


def test_the_apology_does_not_stack_on_a_reflush():
    once, _ = acknowledge_spent_band(READOUT, "mornings")
    twice, action = acknowledge_spent_band(once, "mornings")
    assert action == "unchanged"
    assert twice.count("I'm afraid") == 1


def test_the_wording_survives_the_banned_phrase_table():
    """It becomes the FIRST sentence of the chunk, where the opener rules bite.
    A banned opener would be stripped silently and the caller would hear the
    unexplained readout B-117 exists to prevent."""
    from app.media_streams.turn_handler import _BANNED_SENTENCE_RE

    for label in ("mornings", "afternoons", "evenings"):
        text, _ = acknowledge_spent_band(READOUT, label)
        first = text.split(".")[0] + "."
        assert not [n for n, rx in _BANNED_SENTENCE_RE if rx.search(first)]


def test_it_says_nothing_about_which_times_were_chosen():
    """The whole contract. B-116 picks the times; this only explains them."""
    text, _ = acknowledge_spent_band(READOUT, "mornings")
    assert text.endswith(READOUT)


# ---------------------------------------------------------------------------
# The claim reaches the sentence from where B-98 decides it
# ---------------------------------------------------------------------------
def test_the_retrieval_path_reports_the_band_it_spent():
    """One owner. B-98 already knows the band is used up on this day; without
    this the readout would need a second copy of that rule, which is how this
    family keeps regrowing."""
    tuples = _tuples([9, 10, 13, 14, 15])
    spoken = {t[0].isoformat()[:19] for t in tuples if t[0].hour < 12}
    out: dict = {}
    _filter_tuples_by_preference(tuples, "morning", spoken, out=out)
    assert out.get("band_label") == "mornings"
    assert out.get("band_spent_days") == {str(tuples[0][0].date())}


def test_an_unspent_band_reports_nothing():
    tuples = _tuples([9, 10, 13, 14, 15])
    out: dict = {}
    _filter_tuples_by_preference(tuples, "morning", set(), out=out)
    assert not out.get("band_spent_days")


def test_the_out_dict_is_optional():
    """Six other callers pass no out-dict and must keep working."""
    tuples = _tuples([9, 10, 13])
    assert _filter_tuples_by_preference(tuples, "morning", set())
