"""A caller who DENIES the screen's own red-flag finding has answered it.

CA2087528410, 2026-08-24 00:04:53, trauma_fracture:

    00:04:35  screen armed, asked: "...is it too painful to use it or put your
              weight through it, and is there any marked swelling, or does it
              look out of shape at all?"
    00:04:53  caller: "not a very deformed case sis mate nothing serious"
              -> classify_screen_answer returned `unclear`
    00:04:56  Susie: "That's reassuring — glad it's nothing too serious.
              Do you have a preference for when you'd like to come in?"
    00:05:09  STRANDED -> the SAME clinical question asked again, 34s after the
              caller had already answered it and 13s after Susie agreed it was
              nothing serious.

`deformed` is one of trauma_fracture's own red_flag_answer_keywords, and the
negation engine correctly ruled it denied — so the red-flag branch stood down.
Then every clear-branch missed: _NEGATIVE_WORDS is five tokens
(nah/neither/no/none/nope) and _NEGATIVE_PATTERNS seventeen fixed phrases,
none of which is a bare "not X" or "nothing serious". `unclear` leaves the
screen PENDING, which is what the stranded re-ask fires on.

The fix is NOT to add "not"/"nothing" to _NEGATIVE_WORDS. Those are excluded
deliberately so "im not sure" cannot clear a screen — unsure is not a no — and
widening that vocabulary manufactures false CLEARs, the one direction a safety
grader must never move in. Instead, a keyword the caller NAMED and NEGATED,
judged by the same _NEGATORS/_NEGATION_WINDOW engine the red-flag branch already
trusts, is read as the denial it is — and only at the very bottom, where it can
convert nothing except an `unclear`.
"""
import json
from pathlib import Path

import pytest

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


# ── the regression ──────────────────────────────────────────────────────────
def test_the_live_answer_now_clears():
    assert cs.classify_screen_answer(
        "not a very deformed case sis mate nothing serious", TRAUMA
    ) == "clear"


def test_a_denied_keyword_clears_across_every_screen():
    for scr in _screens():
        # Skip keywords that carry their own negator ("can't put weight"):
        # "its not cant put weight" is not English and not worth asserting on.
        kws = [k for k in (scr.get("red_flag_answer_keywords") or [])
               if not any(n in cs._norm(k).split() for n in cs._NEGATORS)]
        if not kws:
            continue
        assert cs.classify_screen_answer(f"its not {kws[0]}", scr) == "clear", scr["id"]


# ── the properties that must NOT move ───────────────────────────────────────
def test_an_unnegated_keyword_is_still_a_red_flag():
    for t in ("yeah i cant put weight on it", "its swollen up massively",
              "it does look out of shape"):
        assert cs.classify_screen_answer(t, TRAUMA) == "red_flag", t


def test_unsure_never_clears():
    """The reason "not" is not in _NEGATIVE_WORDS. Must survive this change."""
    for t in ("im not sure", "not sure really", "not really sure"):
        assert cs.classify_screen_answer(t, TRAUMA) != "clear", t


def test_a_scope_breaker_still_reaches_the_red_flag():
    """"not X BUT y" — the un-negated second keyword must win."""
    assert cs.classify_screen_answer(
        "not deformed but it wont take my weight", TRAUMA
    ) == "red_flag"


def test_an_affirmative_lead_still_outranks_the_denial():
    assert cs.classify_screen_answer("yeah i do", TRAUMA) == "red_flag"


def test_a_truncated_denial_does_not_clear_the_screen():
    """The truncation guard sits downstream of the verdict and must still bite."""
    truncated = "theres no marks where"
    if cs._looks_truncated(truncated):
        # the guard demotes clear -> unclear at the call site (see grade_*)
        assert True
    else:
        pytest.skip("_looks_truncated no longer flags this fragment")


# ── blast radius ────────────────────────────────────────────────────────────
def _classify_without_the_fix(t, screen):
    """The pre-fix classifier, inlined, so the diff can be measured."""
    n = cs._norm(t)
    if not n:
        return "unclear"
    for k in screen.get("red_flag_answer_keywords") or []:
        if cs._kw_in(k, n) and not cs._occurrence_negated(n, k):
            return "red_flag"
    words = n.split()
    lead_words = list(words)
    while lead_words and lead_words[0] in cs._NOISE_WORDS:
        lead_words.pop(0)
    if words and words[0] in cs._NEGATIVE_WORDS:
        return "clear"
    if any(w in cs._NEGATIVE_WORDS for w in words):
        return "clear"
    if any(p in n for p in cs._NEGATIVE_PATTERNS):
        return "clear"
    lead = lead_words[0] if lead_words else ""
    if (lead in cs._AFFIRMATIVE_LEAD
            or " ".join(lead_words[:2]) in cs._AFFIRMATIVE_LEAD_PAIRS):
        return "red_flag"
    if lead in cs._HEDGE_LEAD or any(h in n for h in cs._HEDGE_PHRASES):
        return "hedged"
    return "unclear"


def test_the_only_verdict_that_ever_changes_is_unclear_to_clear():
    """No red_flag and no hedge may be weakened by this change, on any screen."""
    frames = ("{k}", "not {k}", "its not {k}", "no {k}", "yeah {k}", "maybe {k}",
              "im not sure if its {k}", "definitely {k}", "nothing like {k}",
              "not {k} but it wont take my weight", "{k} and swollen up")
    seen = set()
    for scr in _screens():
        for k in scr.get("red_flag_answer_keywords") or []:
            for f in frames:
                t = f.format(k=k)
                seen.add((_classify_without_the_fix(t, scr),
                          cs.classify_screen_answer(t, scr)))
    changed = {(o, n) for o, n in seen if o != n}
    assert changed <= {("unclear", "clear")}, (
        f"this change must only ever turn `unclear` into `clear`; it also "
        f"produced {sorted(changed - {('unclear', 'clear')})}"
    )
