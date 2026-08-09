"""Susie must never tell a caller she is a real person — template clinics.

The `clinic_template_prompt.py` counterpart to `test_ai_disclosure_theorem.py`.
Theorem got this on 2026-08-04 (8d3a22f) after a live call:

    Caller: "um are you a real person"
    Susie:  "Yes, I'm an AI receptionist — what can I help you with?"

The trailing clause being true is what makes it easy to miss on a listen-
through: the turn sounds like a disclosure while its first word is the opposite
of one. A caller who hears "yes" and stops listening has been told there is a
human on the line.

vital_edge and jv_v1 were still in that state on 2026-08-09, and measurably
so. Probing the RENDERED prompt before this fix:

    "9b. AI disclosure"     0 occurrences
    "Do NOT deny being AI"  0
    "are you a robot"       0
    "computer"              0
    "real person"           1   <- and it is a style rule pushing the OTHER way

A complete "## 9b. AI disclosure" block, with the mandated answer, does exist
in susie_system_prompt.py. It renders for no clinic on this branch — it is
dead text behind the prompt_engine split, which is exactly how it sat in the
repo while the live call went wrong. Hence every assertion here runs against
the rendered prompt; asserting on the source is what let this ship on Theorem.

The rule mandates the OPENING WORD, not the sentiment, because the defect was
the first word. "Yes, I'm an AI receptionist" is honest, discloses, and is
still wrong — a rule that only says "be honest" leaves it available.
"""

import pytest

from app.prompts.susie_system_prompt import build_system_prompt_parts

# clinic_id -> (clinic name as spoken, practitioner the caller is offered)
TEMPLATE_CLINICS = {
    "vital_edge": ("Vital Edge Therapy", "Jonathan"),
    "jv_v1": ("Joint Venture Physiotherapy", "Marcus"),
}

# Words a caller actually uses. Theorem's version of this rule was written from
# the same list; a disclosure rule that only anticipates "are you an AI" misses
# the phrasing that actually broke it ("are you a real person").
CALLER_WORDINGS = ["real person", "a human", "a robot", "a machine",
                   "a computer", "an AI"]


def _render(clinic_id: str) -> str:
    static, dynamic = build_system_prompt_parts({
        "call_sid": "CAtest_disclosure",
        "clinic_id": clinic_id,
        "booking_flow_active": True,
        "collected": {},
    })
    return f"{static}\n\n{dynamic}"


@pytest.fixture(scope="module", params=sorted(TEMPLATE_CLINICS))
def clinic(request):
    return request.param, _render(request.param)


# ── the rule itself ─────────────────────────────────────────────────────────

def test_the_rule_renders_at_all(clinic):
    """The whole defect was a rule that existed in the repo and rendered for
    nobody."""
    _, prompt = clinic
    assert "AI DISCLOSURE — NON-NEGOTIABLE" in prompt


def test_the_opening_word_is_mandated(clinic):
    """Not the sentiment — the first word. 'Yes, I'm an AI receptionist' is
    honest and still wrong."""
    _, prompt = clinic
    assert 'your answer OPENS WITH THE WORD "No"' in prompt


def test_answering_yes_is_explicitly_forbidden(clinic):
    _, prompt = clinic
    assert 'Never answer "yes" to "are you a real person"' in prompt
    assert "Never claim to be human" in prompt


@pytest.mark.parametrize("wording", CALLER_WORDINGS)
def test_caller_wordings_are_covered(clinic, wording):
    _, prompt = clinic
    assert wording in prompt, f"the rule does not anticipate {wording!r}"


def test_dodging_is_forbidden(clinic):
    """Answering a different question is the other way to avoid disclosing."""
    _, prompt = clinic
    assert "never dodge the question by answering a different one" in prompt


def test_the_answer_is_capped_at_one_sentence(clinic):
    """So this fix does not become the FAQ-length defect. See
    tests/regression/test_faq_answer_length_template.py."""
    _, prompt = clinic
    assert "That sentence is the whole answer" in prompt
    assert "do not over-explain" in prompt
    assert "do not apologise for being an AI" in prompt


# ── it must be the CLINIC's own sentence, not a ported one ──────────────────

def test_the_mandated_sentence_names_this_clinic(clinic):
    """Rendered per clinic, not hardcoded. Theorem's version names Theorem
    Health and offers Mark; porting that verbatim would have put one clinic's
    identity into the renderer every other clinic shares."""
    clinic_id, prompt = clinic
    name, practitioner = TEMPLATE_CLINICS[clinic_id]
    assert f"No — I'm Susie, {name}'s AI receptionist" in prompt


def test_the_caller_is_offered_this_clinics_practitioner(clinic):
    """A sole-practitioner clinic names them one sentence earlier, so a vague
    'put you through to the clinic' would read as a different, lesser offer."""
    clinic_id, prompt = clinic
    _, practitioner = TEMPLATE_CLINICS[clinic_id]
    assert f"put you through to {practitioner} if you'd rather speak to a person" in prompt


@pytest.mark.parametrize("leaked", ["Theorem Health", "Awlstuh", "Redditch",
                                    "put you through to Mark"])
def test_no_theorem_identity_leaked_in(clinic, leaked):
    """The guard against porting by copy-paste. Clinic facts in engine code is
    the bug, not the fix."""
    _, prompt = clinic
    assert leaked not in prompt, f"{leaked!r} leaked into a template clinic"


# ── the counter-pressure that produced "Yes" ────────────────────────────────

def test_the_voice_rule_disambiguates_manner_from_claim(clinic):
    """'Sound like a real person' is correct guidance and stays, but it was
    the text most likely to be read as permission to answer yes. On Theorem
    it was one of three style rules asking the model to seem human, with
    nothing asking it to disclose."""
    _, prompt = clinic
    assert "That is about MANNER, never a claim" in prompt
    assert "see AI DISCLOSURE above" in prompt


def test_the_voice_rule_itself_survives(clinic):
    """The fix is to disambiguate it, not to delete it — a cold, robotic
    Susie is a different defect."""
    _, prompt = clinic
    assert "Sound like a real person speaking on the phone" in prompt


def test_disclosure_is_stated_before_the_voice_rule(clinic):
    """Ordering is load-bearing: the voice rule points back at the disclosure
    rule, so the disclosure has to have been read first."""
    _, prompt = clinic
    assert prompt.index("AI DISCLOSURE — NON-NEGOTIABLE") < prompt.index(
        "Sound like a real person speaking on the phone"
    )


# ── the block it was inserted into must survive ─────────────────────────────

def test_identity_block_intact(clinic):
    """A brevity or identity edit is exactly the kind of change that trims a
    neighbouring instruction."""
    _, prompt = clinic
    assert "You are Susie, the AI receptionist for" in prompt
    assert "You are not a clinician" in prompt


def test_sole_practitioner_line_intact(clinic):
    clinic_id, prompt = clinic
    _, practitioner = TEMPLATE_CLINICS[clinic_id]
    assert f"All appointments are with {practitioner}, the clinic's sole practitioner" in prompt
