r"""B-142 / B-125d: "not soon enough" was answered with times that moved LATER.

`CA1c6c8360d218c4b5c715d1a48409690e` (4 Sep 2026 17:54, northgate demo line,
build 3868895f — the call placed to verify B-125c).

    17:55:00  caller: "um yeah i need an appointment as soon as possible"
              day_preference captured: as soon as possible
    17:55:37  offer 1   Sat 09:00, 12:20 | Mon 08:00, 17:10 | Tue 08:50, 17:10
    17:55:50  caller: "no that's not soon enough i need an appointment sooner
                       what's the soonest you have i need it today if possible"
    17:55:55  offer 2   Sat 09:50, 11:30 | Mon 08:50, 16:20 | Tue 09:40, 16:20
    17:56:05  caller: "no i need it sooner than that what's the soonest slot
                       you have"
    17:56:14  Susie:  "those are the soonest available"
    17:56:19  hang-up.  outcome=abandoned, 90s

**Every single time in the second offer is later than the first.** 09:00 was
bookable on Saturday for the whole call and was never offered again.

THIS IS B-137, ONE LEVEL DOWN. B-137 (`7eb61dd2`) taught `choose_presented_days`
to lead with the EARLIEST days when `day_preference` says the caller wants the
soonest. `caller_wants_soonest` was read nowhere else, so the DAY order was
corrected while the TIME order inside each day stayed inverted: B-116's
unheard-first rule drops the earliest time PRECISELY BECAUSE it has been
spoken, and the earliest time is the only one that can answer "anything
sooner".

"Sooner" and "what else" are opposite questions — B-137's own words, and the
reason its fix has to exist at both levels.

Two claims then went out on top of the bad offer:

  * `[ms_gate5] removed an unsupported EARLIEST claim` — B-125c firing
    CORRECTLY on "the soonest I have is Saturday 5th September — ten to ten in
    the morning". 09:50 is not Saturday's earliest. The guard was right; the
    claim was only false because the offer was wrong.
  * "those are the soonest available" went straight through. That is B-125d,
    the copula-first frame, pinned as a strict xfail on the evening of 4 Sep
    and left open on the reasoning that widening a strip guard wanted a call
    first. It reached a live caller four hours later.
"""
from __future__ import annotations

import pytest

from app.media_streams.turn_handler import (
    _claim_strip_would_fragment,
    _earliest_claim_is_supported,
    _names_an_earliest_claim,
    _strip_earliest_claim,
    sanitise_response,
)
from app.tools.slot_followup import (
    caller_wants_soonest,
    choose_presented_indices,
    record_spoken_slots,
)

# ── The diary from the call, verbatim from the check_availability payload ──

#: The spoken labels the readout actually uses, for the day the claims below
#: are about. `_earliest_claim_is_supported` compares against these strings, so
#: a placeholder here would test the placeholder rather than the guard.
SATURDAY_SPOKEN = [
    "nine in the morning",
    "ten to ten in the morning",
    "twenty to eleven in the morning",
    "half past eleven in the morning",
    "twenty past twelve in the afternoon",
]


def _day(date, times, label=None, spoken=None):
    return {
        "date": date,
        "day_label": label or date,
        "slot_times": list(times),
        "slot_times_spoken": list(spoken) if spoken else [f"spoken {t}" for t in times],
        "slots": [{"start": f"{date}T{t}:00+01:00"} for t in times],
        "times_found_on_day": len(times),
        "times_not_shown": 0,
    }


SATURDAY = _day(
    "2026-09-05", ["09:00", "09:50", "10:40", "11:30", "12:20"],
    label="Saturday 5th September", spoken=SATURDAY_SPOKEN,
)
MONDAY = _day("2026-09-07", [
    "08:00", "08:50", "09:40", "10:30", "11:20", "12:10",
    "13:00", "13:50", "14:40", "15:30", "16:20", "17:10",
])
TUESDAY = _day("2026-09-08", [
    "08:50", "09:40", "10:30", "11:20", "12:10", "13:00",
    "13:50", "14:40", "15:30", "16:20", "17:10",
])
DAYS = [SATURDAY, MONDAY, TUESDAY]

# What the caller actually heard, from the two recorded offers.
LIVE_OFFER_1 = ["09:00", "12:20", "08:00", "17:10", "08:50", "17:10"]
LIVE_OFFER_2 = ["09:50", "11:30", "08:50", "16:20", "09:40", "16:20"]


def _two_offers(day_preference: str):
    """Read every day twice, recording the first read, exactly as the engine
    does when the caller comes back with "that's not soon enough"."""
    session = {
        "clinic_id": "northgate",
        "day_preference": day_preference,
        "available_days": DAYS,
    }
    offers = []
    for _ in range(2):
        picked = []
        for day in DAYS:
            picked += [day["slots"][i] for i in choose_presented_indices(session, day, 2)]
        offers.append([s["start"][11:16] for s in picked])
        record_spoken_slots(session, picked)
    return offers


# ── The defect ─────────────────────────────────────────────────────────────

def test_the_live_first_offer_is_reproduced():
    """Anchors the fixture against the call. If this drifts, the numbers below
    stop describing what a caller actually heard."""
    assert _two_offers("as soon as possible")[0] == LIVE_OFFER_1


def test_asking_for_sooner_does_not_move_every_time_later():
    """The whole defect, in one assertion."""
    first, second = _two_offers("as soon as possible")
    assert second != LIVE_OFFER_2, (
        "the second offer is the one the caller hung up on"
    )
    for a, b in zip(first, second):
        assert b <= a, (
            f"caller asked for something SOONER and {a} became {b}: "
            f"{first} -> {second}"
        )


def test_the_earliest_time_survives_into_the_second_offer():
    """09:00 was bookable all call and was dropped precisely because he had
    heard it. Repeating it is not circular — it is the true answer."""
    _, second = _two_offers("as soon as possible")
    assert "09:00" in second, second


@pytest.mark.parametrize("preference", [
    "as soon as possible", "today", "tomorrow", "tonight", "this week",
])
def test_every_soonest_preference_reaches_the_time_level(preference):
    """`caller_wants_soonest` is the ONE owner of this question. A preference
    the day level honours and the time level ignores is the whole bug."""
    assert caller_wants_soonest({"day_preference": preference})
    _, second = _two_offers(preference)
    assert "09:00" in second, second


# ── "What else have you got" is the OPPOSITE question, and is unchanged ────

def test_what_else_still_leads_with_the_unheard():
    """B-116/B-119 must survive intact. Without a soonest preference the second
    offer is still the times the caller has not heard — this is the behaviour
    that answers "anything else that day", and it is byte-identical to the
    live second offer, which is what makes it the right rule for that question
    and the wrong one for "sooner"."""
    first, second = _two_offers("")
    assert first == LIVE_OFFER_1
    assert second == LIVE_OFFER_2


def test_an_unrelated_preference_does_not_trip_the_soonest_arm():
    """"next week" and "whenever" scope the caller AWAY from today."""
    assert not caller_wants_soonest({"day_preference": "next week"})
    assert not caller_wants_soonest({"day_preference": "whenever"})
    assert not caller_wants_soonest({})


# ── B-125d — the sentence that went out on top of the bad offer ────────────

LIVE_D_CLAIM = "those are the soonest available."


def _session(**over):
    s = {"clinic_id": "northgate", "available_days": [SATURDAY]}
    s.update(over)
    return s


def test_the_copula_first_frame_is_recognised():
    """The frame both original patterns miss: no leading superlative, no
    pronoun subject the trailing pattern knows. A SHAPE IS NOT A FAMILY."""
    assert _names_an_earliest_claim(LIVE_D_CLAIM)


@pytest.mark.parametrize("claim", [
    "Those are the soonest available.",
    "Monday the 7th is the soonest we have.",
    "Ten to ten is the earliest I've got.",
    "Half eleven was the first available.",
])
def test_the_copula_first_frame_is_stripped_when_false(claim):
    """Spoken with a sentence beside it, as it was on the call.

    A claim that is the WHOLE turn is a different case: `sanitise_response`
    deliberately restores the original rather than fall silent, so the trade
    it makes is pinned separately below.
    """
    turn = claim + " Would either of those work for you?"
    out = sanitise_response(turn, _session())
    assert "soonest" not in out.lower(), out
    assert "earliest" not in out.lower(), out
    assert "first available" not in out.lower(), out
    assert "Would either of those work for you?" in out, out


def test_a_claim_that_is_true_of_the_day_is_left_alone():
    """Saturday IS the earliest day here and the sentence names no time, so
    there is nothing for it to be wrong about. Conditional in the new frame
    too, not a ban."""
    claim = "Saturday the 5th is the very soonest we can do."
    assert _earliest_claim_is_supported(claim, _session())
    assert sanitise_response(claim, _session()) == claim


@pytest.mark.parametrize("claim", [
    "those are the soonest available.",
    "they are the soonest we have.",
    "these are the earliest I've got.",
])
def test_a_bare_pronoun_subject_leaves_no_fragment(claim):
    r"""A subject with no content of its own. Removing the predicate leaves
    "Those." — a fragment by any reading.

    The seam rule reaches it only because its tail is `\s*$` and not `\s+$`:
    the trailing patterns open with `\s*`, so the match swallows the space and
    the dangling word ends the prefix with nothing after it.
    """
    assert _claim_strip_would_fragment(claim), claim
    assert _strip_earliest_claim(claim) == "", claim
    out = sanitise_response(claim, _session()).lower()
    for fragment in ("those.", "they.", "these."):
        assert fragment not in out, out


def test_the_sentence_beside_it_survives():
    """A turn is not one sentence. The bad claim goes; the rest stands."""
    text = "Those are the soonest available. Would either of those work?"
    out = _strip_earliest_claim(text)
    assert "Would either of those work?" in out, out
    assert "soonest" not in out.lower(), out


def test_a_true_copula_first_claim_survives():
    """Conditional in all three frames, not just the two that had it."""
    claim = "Nine in the morning on Saturday 5th September is the soonest we have."
    assert _earliest_claim_is_supported(claim, _session())
    assert sanitise_response(claim, _session()) == claim


@pytest.mark.parametrize("innocent", [
    "That's the earliest appointment you've had with us.",
    "Is that the earliest you can manage?",
    "Your appointment is at eight in the morning.",
    "Saturday 5th September — nine in the morning.",
])
def test_the_new_frame_does_not_eat_ordinary_speech(innocent):
    """Over-firing here deletes a whole sentence, so the frame has to want a
    superlative that CLOSES its clause."""
    assert sanitise_response(innocent, _session()) == innocent


def test_the_pronoun_frame_still_owns_its_own_sentences():
    """ORDER IS LOAD-BEARING. Both trailing patterns match "that's the earliest
    I've got"; the pronoun one runs first and consumes "that", while the bare
    copula frame alone would cut at the apostrophe and strand it.

    Asking every pattern about the ORIGINAL sentence — rather than about the
    text as the strip will see it — reported a dangling "that" and dropped four
    correct sentences. Pinned so the walk cannot go back to a flat loop.
    """
    claim = "Five past nine on Tuesday 1st September — that's the earliest I've got."
    assert not _claim_strip_would_fragment(claim)
    assert _strip_earliest_claim(claim) == "Five past nine on Tuesday 1st September."


# -- B-142b: an adverb between the copula and the article -------------------

@pytest.mark.parametrize("claim", [
    "Monday the 7th is actually the soonest available.",
    "Monday the 7th is really the soonest we have.",
    "Monday the 7th is definitely the earliest I've got.",
    "Monday the 7th - that's actually the earliest I've got.",
])
def test_an_adverb_after_the_copula_does_not_hide_the_claim(claim):
    r"""B-142b, CA06a26c636b3df78 18:17:41 — "Saturday is ACTUALLY the soonest
    available". That sentence was TRUE, so no caller was misled, but the guard
    never judged it: one word moved and the claim stopped being checked.

    Matched as a SHAPE (`\w+ly`) rather than a word list. Guessing the
    vocabulary produced "very", then "actually", and would produce the next one.
    """
    assert _names_an_earliest_claim(claim), claim
    out = sanitise_response(claim + " Would that work?", _session())
    assert "soonest" not in out.lower(), out
    assert "earliest" not in out.lower(), out
    assert "Would that work?" in out, out


@pytest.mark.parametrize("innocent", [
    "Priya is usually the one who does the assessment.",
    "Your appointment is at eight in the morning.",
    "Is that the earliest you can manage?",
])
def test_the_adverb_slot_does_not_open_the_pattern_up(innocent):
    r"""`\w+ly` is broad on purpose, so what keeps it safe is everything after
    it: a superlative from the list, closing its clause."""
    assert not _names_an_earliest_claim(innocent), innocent
    assert sanitise_response(innocent, _session()) == innocent


def test_a_true_claim_with_an_adverb_still_survives():
    """Conditional here too. Saturday IS the earliest day on this payload."""
    claim = "Saturday the 5th is actually the soonest we have."
    assert _earliest_claim_is_supported(claim, _session())
    assert sanitise_response(claim, _session()) == claim


def test_a_strip_that_leaves_only_punctuation_reports_empty():
    """When the claim was the WHOLE sentence the trailing frames leave their
    own full stop behind. "." is truthy, so `sanitise_response`'s `or result`
    fallback read it as a successful strip and would have spoken it.

    Pre-existing, and reachable the moment a bare claim is its own chunk.
    """
    claim = "That's the earliest I've got."
    assert _strip_earliest_claim(claim) == ""
    assert sanitise_response(claim, _session()) == claim
