"""Theorem's reschedule closing sat outside Gate 5f — a category-3 port blocker.

Found by the THEOREM_PORT_PLAN section 7 literal audit, 2026-08-04.

`_FALSE_RESCHEDULE_CLAIM_RE` REQUIRES an object after the verb — `moved
you/that/it to ...`, never a bare `moved to ...`. That is deliberate: it is what
keeps "we've moved to a new building" from being stripped as a false
confirmation.

`clinic_template_prompt.py` was shaped to fit that gate. It mandates "That's you
rescheduled — you're now in for ..." and at :2321 explicitly warns against "A
bare 'I've rescheduled to [date]'".

`_build_theorem_v3` taught exactly the form the template forbids:

    "I've rescheduled to [date/time]. Confirmation text on its way."

Objectless, so the guard could not see it. A refused reschedule narrated in
Theorem's own mandated wording passed silently — the failure the port plan calls
category 3, and the one class a listening test can never find.

Fixed by aligning Theorem's closing to the template's shape. NOT by widening the
regex, which would re-open the new-building false positive.
"""
import re

import pytest

from app.prompts.susie_system_prompt import _build_theorem_v3
from app.media_streams.turn_handler import (
    _false_write_claim,
    WRITE_FAMILY_RESCHEDULE,
)


def _prompt() -> str:
    built = _build_theorem_v3({
        "clinic_id": "theorem_v3",
        "collected": {},
        "selected_location": "alcester",
        "v3_location_confirmed": True,
    })
    if isinstance(built, tuple):
        built = "\n".join(str(p) for p in built)
    return built


@pytest.fixture(scope="module")
def prompt():
    return _prompt()


# The closing as the prompt now teaches it, with the placeholders filled.
THEOREM_CLOSING = (
    "That's you rescheduled - you're now in for Monday the 10th at three "
    "in the afternoon. Confirmation text on its way."
)
OLD_CLOSING = (
    "I've rescheduled to Monday 10th August at three in the afternoon. "
    "Confirmation text on its way."
)


def test_the_closing_theorem_now_teaches_is_seen_by_gate_5f():
    """The whole point. On a REFUSED reschedule this must be recognised as a
    claim so the guard can re-steer instead of letting a phantom through."""
    assert _false_write_claim(THEOREM_CLOSING, WRITE_FAMILY_RESCHEDULE)


def test_the_old_closing_really_was_invisible():
    """Pins the defect itself. If this ever starts passing, someone widened
    _FALSE_RESCHEDULE_CLAIM_RE — check the new-building case below before
    accepting it."""
    assert not _false_write_claim(OLD_CLOSING, WRITE_FAMILY_RESCHEDULE)


def test_the_prompt_no_longer_teaches_the_bare_form():
    p = _prompt()
    assert "That's you rescheduled" in p
    assert "you're now in for" in p


def test_the_prompt_explicitly_forbids_the_bare_form(prompt):
    """Steering only works if the model is told WHY. The template prompt does
    the same at clinic_template_prompt.py:2321."""
    assert "bare 'I've rescheduled to [date]'" in prompt


# ── the false positive the object requirement exists to prevent ───────────


def test_a_clinic_that_has_physically_moved_is_still_not_a_claim():
    """The reason _FALSE_RESCHEDULE_CLAIM_RE demands an object. If a fix ever
    'solves' this by loosening the regex, this is what breaks — an ordinary FAQ
    answer gets stripped and replaced with a re-steer."""
    for line in (
        "We've moved to a new building just up the road.",
        "The clinic moved to Redditch last year.",
    ):
        assert not _false_write_claim(line, WRITE_FAMILY_RESCHEDULE)


@pytest.mark.parametrize("line", [
    "Would you like me to move it for you?",
    "I can move that for you if you'd like.",
    "Let me get that moved for you.",
])
def test_offers_and_in_progress_lines_stay_quiet(line):
    """An over-fire here strips real speech — the Gate 5c failure mode."""
    assert not _false_write_claim(line, WRITE_FAMILY_RESCHEDULE)


# ── the two prompts must not drift apart again ────────────────────────────


def test_theorem_and_template_teach_the_same_shape():
    """Two clinics, two prompt files, one gate. The gate is tuned to a wording;
    if either prompt drifts off it, that clinic loses the guard silently — which
    is exactly how this defect existed at all."""
    from app.prompts import clinic_template_prompt as ctp

    tpl = re.sub(r"\s+", " ", open(ctp.__file__, encoding="utf-8").read())
    assert "That's you rescheduled" in tpl, (
        "the template's mandated closing changed — re-check Theorem's against it"
    )
    assert "That's you rescheduled" in re.sub(r"\s+", " ", _prompt())
