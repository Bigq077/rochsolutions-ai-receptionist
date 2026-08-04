# tests/regression/test_a3_theorem_surname_is_read_back.py
"""
A3, carried by hand to Theorem — the surname must be spoken back in Step 9.

`914cda3` fixed this for the `template_v1` clinics and said so in its own scope
note: *"Does not reach theorem/demo — no prompt_engine key, so they run the
legacy susie_system_prompt.py. The Theorem port must carry this by hand."*
This is that hand-carry, and this file is its regression net.

The original incidents were on jv_v1, but nothing about them is clinic-specific:

    2 Aug  parser  "by the way"  -> surname `Way`   event ut7p0a17j71fabqpm9jlpspo24
    3 Aug  STT     "Roch"        -> surname `Rook`  event 94q9h39eo4n9qdm2o0eer81890

Different subsystems, identical outcome, because the surname was never said
aloud. The causes are unbounded — a word list fixed `Way`, and the next one
arrived from somewhere else eight hours later — so the read-back is the only
control that covers the whole class. Theorem collects a surname the same way
(Step 7, `collect_and_store(full_name=...)`) and passes it to the same
`book_appointment`, so it inherited the same exposure.

**What this pins, and why each half matters:**

  1. Step 9 must require the FULL name, and its worked example must show one.
  2. The old blanket prohibitions — "never read back, repeat, spell, or confirm
     the surname" and "the surname is never spoken back" — must be gone.
  3. **The loop guard must survive.** The prohibition was not arbitrary: B-15
     recorded a caller sent round the surname loop twice on a live call, and
     over-rejecting a surname costs more than mis-hearing one — it can lose the
     booking outright. So the surname is still never plausibility-checked, never
     spelled, and never a confirmation question of its own.
  4. The read-back must still clear the 200-char `last_bot_prompt` cap (B-31 /
     B-38). Theorem's read-back names the clinic — it is multi-site — so it runs
     longer than the template's and has less headroom.
  5. Scope: the reschedule and cancel read-backs are DIFFERENT write families
     and must stay untouched (`REGISTER_B_U` §B-36 — never share a re-steer
     across families). Their name comes from `lookup_patient`, not from STT on
     this call, so it is not the same exposure.

Assertions are semantic, not literal. Rewording Step 9 is fine; dropping the
surname from it, or reintroducing a surname confirmation question, is not.
"""
from __future__ import annotations

import re

import pytest

from app.prompts.susie_system_prompt import build_system_prompt_parts

# theorem_v3 is matched as a literal string in build_system_prompt_parts and has
# no clinic.json — get_clinic("theorem_v3") falls back to `demo`. Going through
# the real entry point is deliberate: it is the one path llm_stream.run_turn
# takes, and it is the only way to be sure the edited branch is the live one.
CLINIC_ID = "theorem_v3"


def _prompt() -> str:
    """The built prompt, both halves joined. The booking steps live in the
    static (cacheable) half; the dynamic half is per-turn CALL STATE."""
    static, dynamic = build_system_prompt_parts({"clinic_id": CLINIC_ID})
    return static + "\n" + dynamic


def _slice(p: str, start_marker: str, end_marker: str, what: str) -> str:
    """Slice a named block out of the prompt, failing with a readable message
    rather than a bare ValueError when the marker has gone."""
    if start_marker not in p:
        pytest.fail(
            f"{what} is missing from the Theorem prompt — {start_marker!r} not "
            "found. If A3 has been reverted, that is the defect this file "
            "exists to catch; if the block was merely renamed, update the "
            "marker."
        )
    start = p.index(start_marker)
    return p[start:p.index(end_marker, start)]


def _step9() -> str:
    """Just the Step 9 read-back block, so a rule that happens to appear
    elsewhere in a 94K-char prompt cannot satisfy a Step 9 assertion."""
    return _slice(
        _prompt(), "9. Warm readback summary", "10. Call book_appointment",
        "The Step 9 read-back",
    )


def _step9a() -> str:
    """Step 9 plus the 9a surname clause — the whole read-back contract."""
    return _slice(
        _prompt(), "9a. THE SURNAME", "10. Call book_appointment",
        "The 9a surname clause",
    )


# ---------------------------------------------------------------------------
# 1. The surname reaches the caller's ear
# ---------------------------------------------------------------------------

def test_step9_requires_the_full_name():
    """Step 9 must ask for both names, not just 'caller name'."""
    step9 = _step9()
    assert re.search(r"FULL name", step9), (
        "Step 9 no longer requires the caller's FULL name. This is A3: the "
        "surname goes to a real calendar having never been spoken aloud, so "
        "the caller never gets a chance to correct it."
    )
    assert re.search(r"first name AND surname", step9), (
        "Step 9 must name both parts explicitly — 'full name' alone was the "
        "wording in place when two wrong surnames were written to calendars."
    )


def test_step9_worked_example_shows_a_surname():
    """The worked example is what the model actually imitates. A 'Correct:'
    example carrying a bare first name teaches the defect back in."""
    step9 = _step9()
    correct = step9[step9.index("Correct:"):step9.index("Wrong:")]
    assert re.search(r"So that's [A-Z][a-z]+ [A-Z][a-z]+,", correct), (
        "Step 9's 'Correct:' example no longer reads back two names. The "
        "example outweighs the rule for an LLM — this must show a surname."
    )


def test_the_surname_is_named_as_said_exactly_once():
    """9a is the clause that stops the surname leaking into other turns."""
    p = _prompt()
    assert "SAID EXACTLY ONCE" in p, (
        "The 9a 'said exactly once' clause is gone. Without it the surname "
        "either disappears again or starts being echoed at every step."
    )


def test_no_blanket_ban_on_reading_the_surname_back():
    """Both pre-fix prohibitions must be gone. These are the exact strings the
    prompt carried when it shipped the defect."""
    p = _prompt()
    for banned in (
        "read back, repeat, spell, or confirm the surname",
        "the surname is never spoken back",
    ):
        assert banned not in p, (
            f"The pre-A3 prohibition {banned!r} is back in the Theorem prompt. "
            "It forbids exactly the read-back that A3 exists to add."
        )


# ---------------------------------------------------------------------------
# 2. B-15 — the loop guard the prohibition was actually there for
# ---------------------------------------------------------------------------

def test_surname_is_still_never_a_confirmation_question():
    """Saying the surname inside a summary is not the same as asking about it.
    Asking re-opens B-15: the caller goes round the surname loop and can drop
    the booking. Over-rejecting costs more than mis-hearing."""
    p = _prompt()
    assert "no plausibility check" in p, (
        "The surname is being plausibility-checked again. B-15: a caller was "
        "sent round the surname loop twice on a live call."
    )
    step9 = _step9() + _step9a()
    assert "do NOT ask them to spell it" in step9, (
        "The 'never spell the surname' guard is gone from the read-back."
    )
    assert re.search(r"NOT a confirmation question about the name", step9), (
        "Step 9 no longer states that the read-back is not a name-confirmation "
        "question — that distinction is the whole of the B-15 guard."
    )


def test_correction_is_accepted_without_a_further_question():
    """A corrected surname must be taken silently and the summary re-stated —
    not turned into a second question about the name."""
    block = _step9a()
    assert "take the correction" in block and "re-state the whole summary" in block, (
        "The correction path is gone from 9a. Without it a corrected surname "
        "produces another question instead of a re-stated summary."
    )


# ---------------------------------------------------------------------------
# 3. B-31 / B-38 — the 200-char last_bot_prompt cap
# ---------------------------------------------------------------------------
#
# last_bot_prompt is capped at 200 chars. B-31: a 205-char paraphrase lost its
# trailing "?" to the cap. Theorem's read-back names the clinic (Alcester /
# Awlstuh — it is multi-site), so adding a surname eats headroom the template
# clinics did not have to worry about.

_LAST_BOT_PROMPT_CAP = 200

# Deliberately unkind: a double-barrelled surname, the longest month, a spelled
# half-hour, and the clinic name.
_WORST_CASE_READBACK = (
    "So that's Christopher Fotheringay-Wallace, Wednesday the 24th of "
    "September at half past eleven in the morning at Awlstuh — shall I go "
    "ahead and book that in?"
)


def test_readback_with_a_surname_still_fits_under_the_prompt_cap():
    assert len(_WORST_CASE_READBACK) < _LAST_BOT_PROMPT_CAP, (
        f"The worst-case Theorem read-back is {len(_WORST_CASE_READBACK)} chars, "
        f"at or over the {_LAST_BOT_PROMPT_CAP}-char last_bot_prompt cap. B-31: "
        "a read-back long enough to be truncated loses its trailing '?'."
    )


def test_step9_still_forbids_the_clauses_that_would_overrun_the_cap():
    """The bans on duration and appointment type are what buy the headroom the
    surname now spends. Theorem keeps the clinic name — it is multi-site — so
    these are the only slack left."""
    step9 = _step9()
    assert "session duration" in step9, (
        "Step 9 no longer excludes the session duration from the read-back. "
        "With the surname included, that is what pushes the CTA past the cap."
    )
    assert "appointment type" in step9, (
        "Step 9 no longer excludes the appointment type from the read-back — "
        "same cap risk."
    )


# ---------------------------------------------------------------------------
# 4. Scope — the other write families are untouched
# ---------------------------------------------------------------------------

def test_reschedule_and_cancel_readbacks_are_not_given_a_surname_rule():
    """B-36: never share a re-steer across write families. The reschedule and
    cancel read-backs get their name from lookup_patient, not from STT on this
    call, so they carry a different exposure and must not be swept in."""
    p = _prompt()
    resched = p[p.index("RESCHEDULE READBACK RULES"):]
    resched = resched[:resched.index("→")] if "→" in resched else resched[:1500]
    assert "surname" not in resched.lower(), (
        "A surname rule has leaked into the RESCHEDULE read-back. That is a "
        "different write family (B-36) and its name comes from a lookup."
    )

    cancel = p[p.index("CANCEL READBACK RULES"):]
    cancel = cancel[:cancel.index("→")] if "→" in cancel else cancel[:1500]
    assert "surname" not in cancel.lower(), (
        "A surname rule has leaked into the CANCEL read-back — same reasoning."
    )


# ---------------------------------------------------------------------------
# 5. The port contract itself
# ---------------------------------------------------------------------------

def test_theorem_runs_the_prompt_this_file_asserts_against():
    """If theorem_v3 ever stops routing to _build_theorem_v3, every assertion
    above becomes vacuous while still passing. Pin the routing."""
    static, _ = build_system_prompt_parts({"clinic_id": CLINIC_ID})
    assert len(static) > 50_000, (
        "theorem_v3 no longer builds the large legacy prompt — it may have "
        "been converted to template_v1. If so, this file's assertions are now "
        "vacuous and A3 is covered by test_a3_surname_is_read_back.py instead."
    )
    assert "9. Warm readback summary" in static, (
        "Step 9 has been renamed or removed; these assertions no longer bind."
    )
