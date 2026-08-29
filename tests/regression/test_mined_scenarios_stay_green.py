"""The scenarios mined out of the obs corpus, checked in the ordinary suite.

WHY THIS EXISTS
---------------
`app/obs/to_scenario.py` and `app/obs/regress.py` both worked, were both
unit-tested, and had never once been run against real data:
`tests/auto/scenarios/regressions/` did not exist. Free coverage sitting on the
floor. `scripts/harvest_regressions.py` fills it; this runs it.

WHAT A GREEN RUN HERE DOES AND DOES NOT MEAN
--------------------------------------------
`regress.py` re-checks a STORED transcript. It does not re-drive the engine. So
this is a never-regress-this-transcript net -- it catches a banned phrase coming
back into a fixture -- and it is NOT a before/after instrument: it cannot tell
you a fix worked, because the transcript it reads was recorded before the fix
existed.

That also bounds what the assertions can ever be. A scenario embeds a
HISTORICAL call, defects included, so an assertion can only pin something that
was already true of it. Sharpen `expected` by hand as fixes land -- the emitted
baseline is `no_technical_error` and nothing more, deliberately, because
to_scenario captures the input and the recorded failure and does not invent the
correct behaviour.

The instruments that DO measure a change are `tests/harness/` (re-drives the
live free-form turn loop) and `scripts/replay_situational_heads.py` (measures
one change across all 733 stored calls).

PII
---
Every scenario passed `redact.assert_transcript_clean` before it was written,
and that is a hard failure rather than a warning. The names that remain are
STAFF names in Susie's own speech -- Marcus, Jonathan, Leanne -- which are
public clinic identity, not caller data. `test_no_scenario_carries_caller_pii`
below re-checks the committed files rather than trusting the generator.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.obs.regress import check_scenario, load_scenarios

SCENARIO_DIR = Path(__file__).resolve().parents[1] / "auto" / "scenarios" / "regressions"

_SCENARIOS = load_scenarios(SCENARIO_DIR)


def test_the_mined_scenarios_are_actually_there():
    """Guards the failure mode this whole thing was in for months: the runner
    works, the directory is empty, and every run reports success."""
    assert _SCENARIOS, (
        f"no mined scenarios in {SCENARIO_DIR} — run "
        f"python -m scripts.harvest_regressions"
    )


@pytest.mark.parametrize(
    "scenario", _SCENARIOS, ids=[s.get("id", "?") for s in _SCENARIOS]
)
def test_a_mined_scenario_still_passes_its_assertions(scenario):
    passed, failures, checked = check_scenario(scenario)
    assert checked, f"{scenario.get('id')} asserts nothing at all"
    assert passed, f"{scenario.get('id')}: {failures}"


# A caller's number or email must never reach a committed fixture. Staff names
# are fine and expected -- they are Susie's own speech about the clinic.
_PHONE = re.compile(r"\b0\d{9,10}\b|\+44\d{9,}")
_EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


@pytest.mark.parametrize(
    "path", sorted(SCENARIO_DIR.glob("*.py")), ids=lambda p: p.stem
)
def test_no_scenario_carries_caller_pii(path):
    """Re-checked on the committed file, not trusted from the generator.

    The generator asserts this too, but the file is what ships, and a fixture is
    exactly the kind of thing that gets hand-edited later.
    """
    if path.name == "__init__.py":
        pytest.skip("package marker")
    text = path.read_text(encoding="utf-8")
    assert not _PHONE.search(text), f"{path.name} carries a phone number"
    assert not _EMAIL.search(text), f"{path.name} carries an email address"
