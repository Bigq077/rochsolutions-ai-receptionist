"""Susie must answer the question, not deliver a lecture — template clinics.

This is the `clinic_template_prompt.py` counterpart to
`test_faq_answer_length.py`, which pins the same rules inside
`_build_theorem_v3`. Theorem got this fix on 2026-08-04 (e2a44f3); the clinics
that render through the template — vital_edge, jv_v1, demo — did not, and were
still running without it on 2026-08-09.

The evidence is Theorem's, because the template clinics have no equivalent
call corpus. That is a deliberate choice and worth stating plainly: the defect
is a property of how the model reads a brevity rule, not of one clinic's copy,
and the template's wording had the SAME two clauses that were present and
ignored on all seven reviewed calls.

What was already there, and is not the fix:

    "One to two sentences is right for almost every answer"
    "never volunteer information not asked about"

The template was in fact already stricter than Theorem's pre-fix wording — it
had the count cap, the HEADLINE rule and a no-long-lists rule. It is missing
the three things the sweep showed the count cap does not catch:

  1. sentence LENGTH, capped independently of count. One live answer was three
     sentences and still ran 20.1s, on a single 138-character clause. A count
     cap passes that turn.
  2. ONE OFFER, NEVER TWO. A turn ending with a transfer offer AND an
     alternative-site offer makes the caller choose between Susie's options
     instead of answering her question.
  3. the slot-list exemption. This is the dangerous one: the template's "do NOT
     enumerate long lists" is scoped to conditions/services/credentials today,
     but a future brevity edit that widens it would shorten a slot list and
     break booking outright.

Rendered, not read off the source — a rule in a branch that never renders for
these clinics is not in the prompt. See the clinic_template/theorem_v3 split.
"""

import pytest

from app.prompts.susie_system_prompt import build_system_prompt_parts

# Every clinic that renders through clinic_template_prompt.py — i.e. every
# clinic.json carrying "prompt_engine": "template_v1", which today is exactly
# these two.
#
# `demo` and `theorem` are deliberately NOT here. Writing this list as
# ["vital_edge", "jv_v1", "demo"] failed all 13 assertions on demo while the
# edit was perfectly correct: demo has no prompt_engine key and falls through
# to the legacy generic builder ("You are Susie, the AI receptionist for Roch
# Solutions"), so clinic_template_prompt.py never runs for it. Grep
# prompt_engine before adding a clinic here.
#
# That is also why they are the b55 containment canaries: an edit confined to
# clinic_template_prompt.py must leave demo and theorem byte-identical.
# theorem_v3 is absent for the opposite reason — it has its own copy of these
# rules, and its own test.
TEMPLATE_CLINICS = ["vital_edge", "jv_v1"]


def _render(clinic_id: str) -> str:
    static, dynamic = build_system_prompt_parts({
        "call_sid": "CAtest_faq_len",
        "clinic_id": clinic_id,
        "booking_flow_active": True,
        "collected": {},
    })
    return f"{static}\n\n{dynamic}"


@pytest.fixture(scope="module", params=TEMPLATE_CLINICS)
def prompt(request):
    return _render(request.param)


def test_prompt_actually_renders(prompt):
    assert len(prompt) > 10_000, f"template rendered only {len(prompt)} chars"


# ── the rules the count cap does not catch ──────────────────────────────────

def test_answer_only_what_was_asked_is_stated_as_a_rule(prompt):
    """"never volunteer information not asked about" was present and ignored.
    It is now also a named rule carrying the live examples that broke it,
    because a rule the model has already walked past needs evidence rather
    than repetition."""
    assert "ANSWER ONLY WHAT WAS ASKED" in prompt


def test_the_live_failures_are_quoted_as_examples(prompt):
    """Concrete beats abstract. Both are real turns."""
    low = prompt.lower()
    assert "how far the station is" in low, "the hours+parking example is missing"
    assert "how much is a single session" in low, "the price-aside example is missing"


def test_sentence_length_is_capped_independently(prompt):
    """Three sentences, 20.1s. Capping sentence COUNT alone passes that turn."""
    assert "KEEP THE SENTENCES SHORT TOO" in prompt
    assert "twenty words" in prompt


def test_only_one_offer_per_turn(prompt):
    assert "ONE OFFER, NEVER TWO" in prompt


def test_slot_lists_are_explicitly_exempt(prompt):
    """The most dangerous possible side effect: a shortened slot list leaves
    the caller unable to pick, which breaks booking outright."""
    assert "NONE OF THIS APPLIES TO READING OUT APPOINTMENT SLOTS" in prompt


# ── what must NOT be broken ─────────────────────────────────────────────────

def test_warmth_clause_survives(prompt):
    """The owner's brief: still answer like a human receptionist. A curt
    system is a different defect, not a fix for this one."""
    assert "Don't give clipped one-word answers" in prompt


def test_the_worked_example_shows_warmth_not_terseness(prompt):
    """The example is the thing the model actually copies, so it has to model
    the register we want, not just the length.

    Deliberately clinic-neutral. Theorem's version of this example names
    Redditch and its opening hours; porting that verbatim would have written
    one clinic's rota into the shared renderer every other clinic uses --
    which is the bug, not the fix.
    """
    assert "we're open Thursdays, nine till five" in prompt
    assert "not 'Thursdays'" in prompt


def test_the_existing_count_cap_survives(prompt):
    """The new sentence-LENGTH rule supplements the count cap, it does not
    replace it. Losing the count cap would trade one defect for another."""
    assert "One to two sentences is right for almost every answer" in prompt


def test_mid_booking_reask_is_untouched(prompt):
    """It stops the caller thinking the line dropped. Owner decision on the
    theorem side was to leave it alone; same here."""
    assert "MANDATORY WHEN A BOOKING IS ALREADY IN PROGRESS" in prompt
    assert "NEVER end the reply on a statement while a booking is in progress" in prompt


# ── the safety rules this sits next to must survive ─────────────────────────

def test_no_generic_signoff_rule_intact(prompt):
    """A brevity edit is exactly the kind of change that quietly trims a
    neighbouring instruction. This one is immediately below the edit."""
    assert "END YOUR REPLY WITH THE ANSWER AND NOTHING ELSE" in prompt


def test_staff_contact_rule_intact(prompt):
    assert "Never disclose a practitioner's direct" in prompt


def test_never_gatekeep_a_booking_intact(prompt):
    assert "NEEDS-PRACTITIONER FOLLOW-UP — NEVER GATEKEEP A BOOKING" in prompt
