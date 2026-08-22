"""
Theorem: asking for reception is a routing request, not "are you real?".

CA82ec06 (21 Aug, +447380841468 — Mark's live line). The caller opened with
"uh can i speak to somebody in the reception please" and got the AI DISCLOSURE
sentence verbatim, opening "No —". Honest, and wrong: to someone who has just
asked to be put through, a reply opening "No" reads as a refusal of the thing
they requested. It cost 21 seconds and a whole extra turn to reach the transfer
he had asked for in his first sentence.

jv CA9ca88398 (22 Aug) is the other half of the same gap. With no rule covering
the routing request, Susie said "I'm the receptionist here" — role-true, AI word
gone — and the caller had to come back asking for a "REAL receptionist".

One binary trigger produces both failures because the model has no third thing
to say. So this adds a SECOND rule rather than widening the first: widening it
would make the over-match here MORE likely, and "No" is exactly the wrong
opening for a request to be put through.

Theorem needs its own copy because theorem_v3 is a hardcoded prompt in
susie_system_prompt.py, not the shared template — the template fix (59ea85b,
a292c3f) renders byte-identical for every Theorem clinic and never reaches this
line. Note also that latency-eval's theorem_v3 carries NO disclosure rule at
all; the prompts diverge by branch, so this is deliberately theorem-onboarding
only.
"""

import re

import pytest

from app.prompts.susie_system_prompt import build_system_prompt_parts

# Openers gate5 strips off the front of a chunk — a mandated sentence starting
# with one is deleted in transit and the caller hears nothing.
_BANNED_OPENERS = (
    "Absolutely", "Certainly", "Of course", "Sure thing", "Sure", "Wonderful",
    "Fantastic", "Exactly", "Indeed", "Definitely", "Totally", "Obviously",
    "Clearly", "Lovely", "Right so", "Perfect", "Great",
)


def _prompt(clinic_id="theorem_v3"):
    static, dynamic = build_system_prompt_parts({"clinic_id": clinic_id})
    return (static or "") + "\n" + (dynamic or "")


def _said():
    m = re.search(r'Say: "(You\'re through to reception.*?)"', _prompt(), re.S)
    assert m, "the routing rule quotes no sentence for the model to say"
    return m.group(1)


def test_the_routing_request_has_its_own_rule():
    """THE regression — CA82ec06's exact opening."""
    p = _prompt()
    assert "ASKED FOR RECEPTION" in p
    for word in ("reception", "someone", "somebody", "a person"):
        assert word in p


def test_it_does_not_open_with_no():
    """A bare "No" at a caller asking to be put through sounds like refusal."""
    assert not _said().lstrip().startswith("No"), _said()
    assert "does NOT open with" in _prompt()


def test_it_discloses_and_offers_mark():
    said = _said()
    assert "AI receptionist" in said, said
    assert "Mark" in said, "Theorem's human route is Mark: " + said


def test_it_survives_the_gates():
    """
    Two silent deaths between the prompt and the caller: gate5 strips a banned
    opener off the front of a chunk, and last_bot_prompt is capped at 200 chars
    — a reply that loses a trailing "?" stops counting as a question and
    disarms clinical screening (B-31). This sentence ends in a full stop, so
    the cap is not load-bearing today; it becomes so the moment a "?" is added.
    """
    said = _said()
    assert len(said) < 200, f"{len(said)} chars"
    for opener in _BANNED_OPENERS:
        assert not said.startswith(opener), f"gate5 strips {opener!r}"
    assert "bear with me" not in said.lower(), "gate5 deletes this sentence whole"


def test_naming_the_role_always_carries_the_ai_word():
    p = _prompt()
    assert "NAMING YOUR OWN ROLE" in p
    assert "receptionist here" in p, (
        "the rule should name the exact sentence that failed, so it cannot be "
        "softened into a vague instruction"
    )


def test_the_identity_question_still_opens_with_no():
    """
    Untouched on purpose. "Yes, I'm an AI receptionist" (2026-08-04, this line)
    is honest, disclosing and still wrong — the rule mandates the OPENING WORD,
    not the sentiment.
    """
    p = _prompt()
    assert 'OPENS WITH THE WORD' in p and '"No"' in p
    assert "a real person" in p and "a robot" in p


@pytest.mark.parametrize("clinic_id", ["jv_v1", "vital_edge"])
def test_the_template_clinics_are_untouched(clinic_id):
    """
    Containment. This edit is in Theorem's hardcoded prompt; the template
    clinics have their own copy of both rules and must not gain a second one
    from here.
    """
    p = _prompt(clinic_id)
    assert p.count("ASKED FOR RECEPTION") <= 1, (
        f"{clinic_id} now renders the rule twice — the Theorem edit has leaked "
        "into the shared template"
    )
