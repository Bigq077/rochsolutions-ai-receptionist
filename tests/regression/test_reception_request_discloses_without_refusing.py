"""
A routing request and an identity question need opposite openings.

Two live calls, one gap:

  theorem CA82ec06 (21 Aug) — "uh can i speak to somebody in the reception
  please" got "No — I'm Susie, Theorem Health's AI receptionist…". Honest, and
  wrong: to a caller who has just asked to be put through, a sentence opening
  "No" reads as a refusal of what they asked for. It cost 21 seconds and a whole
  extra turn to reach the transfer they requested in their first sentence.

  jv CA9ca88398 (22 Aug) — the same wording got "No problem at all — I'm the
  receptionist here." Role-true (she IS the reception) but the word "AI" fell
  off, so the caller came back with "can I speak to a REAL receptionist please"
  before he was told what he was talking to. Judge scored that call 2.

One rule with a binary trigger produces both failures, because the model has no
third thing to say: fire the disclosure rule at a routing request and you get
theorem, withhold it and you get jv. So this adds a SECOND rule rather than
widening the first — widening it would make the theorem over-match more likely,
and the "No" opener is exactly wrong here.

The identity question's behaviour is deliberately untouched: "are you a real
person" must still open with the word "No". That rule exists because
"Yes, I'm an AI receptionist" (theorem, 2026-08-04) is honest, disclosing and
still wrong — a caller who hears "yes" and stops listening has been told there
is a human on the line.
"""

import re

import pytest

from app.clinic_config import get_clinic
from app.prompts import clinic_template_prompt as tpl

CLINICS = ("jv_v1", "vital_edge")

# Openers gate5 strips from the front of a chunk. A mandated sentence that
# begins with one is deleted in transit and the caller hears nothing.
_BANNED_OPENERS = (
    "Absolutely", "Certainly", "Of course", "Sure thing", "Sure", "Wonderful",
    "Fantastic", "Exactly", "Indeed", "Definitely", "Totally", "Obviously",
    "Clearly", "Lovely", "Right so", "Perfect", "Great",
)


def _identity(clinic_id):
    c = get_clinic(clinic_id) or {}
    assert c, f"{clinic_id} not on this branch"
    return tpl._render_identity(c, tpl._tokens(c))


def _rule(clinic_id, heading):
    for line in _identity(clinic_id).split("\n"):
        if line.startswith(heading):
            return line
    raise AssertionError(f"{heading!r} missing from {clinic_id}'s identity block")


def _mandated_sentence(clinic_id):
    m = re.search(r'Say: "(.*?)"', _rule(clinic_id, "ASKED FOR RECEPTION"))
    assert m, "the routing rule quotes no sentence for the model to say"
    return m.group(1)


@pytest.mark.parametrize("clinic_id", CLINICS)
def test_the_routing_request_has_its_own_rule(clinic_id):
    """THE regression — the gap both calls fell through."""
    rule = _rule(clinic_id, "ASKED FOR RECEPTION")
    for word in ("reception", "someone", "somebody", "a person"):
        assert word in rule, f"{word!r} is not a recognised routing request"


@pytest.mark.parametrize("clinic_id", CLINICS)
def test_it_does_not_open_with_no(clinic_id):
    """theorem CA82ec06. A bare "No" to a transfer request sounds like refusal."""
    said = _mandated_sentence(clinic_id)
    assert not said.lstrip().startswith("No"), said
    assert 'does NOT open with' in _rule(clinic_id, "ASKED FOR RECEPTION")


@pytest.mark.parametrize("clinic_id", CLINICS)
def test_it_discloses(clinic_id):
    """jv CA9ca88398. Owning the role is fine; dropping the AI word is not."""
    said = _mandated_sentence(clinic_id)
    assert "AI receptionist" in said, said


@pytest.mark.parametrize("clinic_id", CLINICS)
def test_it_still_offers_the_human(clinic_id):
    """The jv caller did eventually want a person — the route stays on offer."""
    said = _mandated_sentence(clinic_id)
    assert "put you through" in said, said


@pytest.mark.parametrize("clinic_id", CLINICS)
def test_it_names_this_clinic_s_own_human(clinic_id):
    """Rendered per clinic, not a hardcoded name."""
    c = get_clinic(clinic_id) or {}
    prac = c.get("practitioner") or (c.get("prompt_facts") or {}).get("practitioner")
    if not prac:
        pytest.skip(f"{clinic_id} has no named practitioner")
    assert str(prac).split()[0] in _mandated_sentence(clinic_id)


@pytest.mark.parametrize("clinic_id", CLINICS)
def test_the_sentence_survives_the_gates(clinic_id):
    """
    Two ways a mandated sentence dies silently between here and the caller:
    gate5 strips a banned opener off the front of a chunk, and last_bot_prompt
    is capped at 200 chars — a reply that loses its "?" to the cap switches
    clinical screening off for that turn (B-31).
    """
    said = _mandated_sentence(clinic_id)
    assert len(said) < 200, f"{len(said)} chars — the 200-char cap will eat the '?'"
    for opener in _BANNED_OPENERS:
        assert not said.startswith(opener), f"gate5 strips {opener!r}: {said}"


@pytest.mark.parametrize("clinic_id", CLINICS)
def test_naming_the_role_always_carries_the_ai_word(clinic_id):
    """The invariant the jv call actually broke."""
    rule = _rule(clinic_id, "NAMING YOUR OWN ROLE")
    assert "unprompted" in rule
    assert "receptionist here" in rule, (
        "the rule should name the exact sentence that failed, so it cannot be "
        "softened into a vague instruction"
    )


@pytest.mark.parametrize("clinic_id", CLINICS)
def test_the_identity_question_still_opens_with_no(clinic_id):
    """
    Must not regress while fixing the other direction. "Yes, I'm an AI
    receptionist" is honest, disclosing and still wrong — the rule mandates the
    OPENING WORD, not the sentiment.
    """
    rule = _rule(clinic_id, "AI DISCLOSURE")
    assert 'OPENS WITH THE WORD' in rule
    assert '"No"' in rule
    assert "a real person" in rule and "a robot" in rule
