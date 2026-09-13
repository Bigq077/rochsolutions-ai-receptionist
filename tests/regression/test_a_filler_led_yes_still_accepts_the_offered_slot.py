"""A yes with a filler in front of it is still a yes to the slot on the table.

`CAafb7f031`, northgate demo line, 13 Sep 2026 13:15, build 86b77625 -- an
ORDINARY booking, not the hard script:

    Susie : "Number 3, Wednesday the 16th — eight in the morning, or ten
             past five in the evening. Any of those work?"
    caller: "yeah the wednesday at 8 works"
            -> caller ACCEPTED 2026-09-16T08:00 (correct)
    Susie : "Yes — eight in the morning on Wednesday 16th September is
             free. Shall I book that in for you?"
    caller: "uh yeah go for it"
    model : check_availability(date_hint="8am", day_window=1)   (spurious)
    engine: BLOCKED — slots already retrieved ... the refused lookup named
            ONE day -- reading that day (N4)
    Susie : "Wednesday 16th September — Number 1, twenty to ten in the
             morning. Number 2, half past ten ..."      (eight left out: heard)
    caller: "i was booking an 8 o'clock slot ... why'd you take it away"

The re-query guard that exists for exactly this ("caller is accepting an
already-offered slot; do not re-list", `slot_offer_still_live`) asks
`utterance_accepts_offered_slot` first. "uh yeah go for it" failed it twice
over: "uh yeah" is not the exact phrase "yeah", and "go for it" lived only in
the exact-match set. So the refusal fell through to the dedup branch, whose
N4 answer is a readout of the day. The same words on 10 Sep (CA2ac47ad588)
were fine only because the model did not re-query that time.

Not a regression from the 12-13 Sep chain: nothing on this path changed.
A gap in the guard's own vocabulary, fixed in the guard's own vocabulary --
leading fillers stripped, "go for it" in the containment set.
"""
from __future__ import annotations

import pytest

from app.tools.slot_followup import utterance_accepts_offered_slot


@pytest.mark.parametrize("utterance", [
    "uh yeah go for it",
    "uh yeah",
    "um yes please",
    "ok go for it",
    "erm, yeah that works",
    "oh go ahead",
    "yeah go for it",
])
def test_a_filler_led_yes_accepts(utterance):
    assert utterance_accepts_offered_slot(utterance), utterance


@pytest.mark.parametrize("utterance", [
    "uh no",
    "uh what else have you got",
    "um yeah but not tuesday",
    "so",
    "um",
    "uh could you do a different day",
])
def test_a_filler_led_non_acceptance_still_declines(utterance):
    assert not utterance_accepts_offered_slot(utterance), utterance
