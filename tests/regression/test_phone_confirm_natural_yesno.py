# tests/regression/test_phone_confirm_natural_yesno.py
"""
Phone read-back accepts a plain "yes" but not natural yes/no answers.

Reproduced 2026-07-27 verify call (CA77eebe…): Susie read the caller-ID back and
asked "is that the best number for the booking?"; the caller answered **"it is"**;
`_is_use_this_number("it is")` was False, so the confirm never fired, the turn
exited as conversational, phone_confirmed stayed unset and the booking derailed.

"it is" / "that's it" / "that is" are the natural affirmative answers to "is that
the best number?" — they must confirm. Negatives that merely contain "is" ("it
isn't the best", "it is not the right one") must still NOT confirm.
"""
from __future__ import annotations

import pytest

from app.media_streams.connection import _is_use_this_number


@pytest.mark.parametrize("answer", [
    "it is",
    "that's it",
    "thats it",
    "that is",
    "it is thanks",
    "yes it is",       # already worked (via "yes") — pin it stays working
])
def test_natural_affirmatives_confirm_the_number(answer):
    assert _is_use_this_number(answer) is True, f"{answer!r} confirms the read-back number"


@pytest.mark.parametrize("answer", [
    "it isn't the best",
    "it is not the right number",
    "no it isn't",
    "that's not it",
    "no",
    "use a different number",
])
def test_negatives_never_confirm(answer):
    assert _is_use_this_number(answer) is False, f"{answer!r} rejects the number — must not confirm"
