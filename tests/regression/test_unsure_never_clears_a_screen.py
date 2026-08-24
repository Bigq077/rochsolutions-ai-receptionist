""""I don't know" is not "no".

Found 2026-08-24 while fixing the denied-keyword bug, and live on committed HEAD
before then. Two independent routes cleared a clinical red-flag screen for a
caller who had just said they could not answer it:

    "i dont know maybe"  -> clear    _NEGATIVE_PATTERNS contains "i dont",
                                     which substring-matches "i dont know"
    "no idea mate"       -> clear    _NEGATIVE_WORDS contains "no", and
                                     "no idea" leads with it

That is the same defect the module already records — "no" matching inside
"know" — reappearing one word later, and it contradicts this module's own stated
doctrine that unsure is not a no. It is a false CLEAR: the dangerous direction,
because the screen is marked answered, the booking is not frozen, and no
escalation is ever spoken.

The hedge vocabulary already carried "not sure", so ORDER was the whole bug: the
negative branches ran first and never gave it a turn. An unsure answer is now
`hedged`, which the HEDGE PROBE handles properly — one narrowing question naming
the symptom, escalate if the answer to THAT is not a clean no.
"""
import json
from pathlib import Path

from app.media_streams import clinical_screening as cs

_CLINIC = json.loads(
    (Path(__file__).resolve().parents[2] / "app" / "clinics" / "jv_v1"
     / "clinic.json").read_text(encoding="utf-8")
)


def _screens():
    found = []

    def walk(o):
        if isinstance(o, dict):
            if "red_flag_answer_keywords" in o:
                found.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(_CLINIC)
    return found


TRAUMA = next(s for s in _screens() if s["id"] == "trauma_fracture")

UNSURE = (
    "i dont know", "i dont know maybe", "i dont know really",
    "no idea mate", "no idea", "not a clue",
    "cant remember", "cant remember really", "couldnt say",
    "hard to tell", "dunno", "im not sure", "not really sure",
)


def test_no_unsure_answer_ever_clears_a_screen():
    for t in UNSURE:
        for scr in _screens():
            v = cs.classify_screen_answer(t, scr)
            assert v != "clear", f"{scr['id']}: {t!r} graded {v}"


def test_unsure_is_hedged_so_the_probe_runs():
    """`hedged` is the verdict the HEDGE PROBE acts on. `unclear` hands the
    decision to the model instead, which is what this module exists to avoid."""
    for t in UNSURE:
        assert cs.classify_screen_answer(t, TRAUMA) == "hedged", t


def test_a_real_denial_still_clears():
    for t in ("no", "nope", "no its not deformed", "definitely not",
              "not a very deformed case sis mate nothing serious"):
        assert cs.classify_screen_answer(t, TRAUMA) == "clear", t


def test_a_real_red_flag_still_escalates():
    for t in ("yeah i cant put weight on it", "its swollen up massively",
              "it does look out of shape", "yeah i do"):
        assert cs.classify_screen_answer(t, TRAUMA) == "red_flag", t


def test_unsure_outranks_a_denied_keyword():
    """"i dont know if its deformed" names a keyword and negates nothing —
    it must hedge, not ride the denied-keyword clear signal."""
    assert cs.classify_screen_answer(
        "i dont know if its deformed", TRAUMA
    ) == "hedged"
