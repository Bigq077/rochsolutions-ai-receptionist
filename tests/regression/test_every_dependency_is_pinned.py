"""A floating dependency took all four clinics down without a code change.

2026-08-21: `anthropic>=0.40.0` resolved to 1.0.0 on a routine rebuild. 1.0.0
moved `temperature` out of `AsyncMessages.stream()`, so every turn raised
TypeError and every reply degraded to the safe fallback phrase. Four services,
mid-morning, no deploy of ours involved — and a revert did not fix it, because
the range simply re-resolved on the next build.

This test cannot catch that failure as it happens: CI installs from the same
floating range the deploy does, so a bad release breaks CI and production
together, with CI offering no warning first. What it CAN do is stop the
precondition ever existing again — a requirement without an exact pin.

Kept deliberately narrow. It asserts the SHAPE of requirements.txt, never a
particular version, so bumping a dependency is a one-line edit and this test
stays quiet. Bump versions freely; just never widen one back to a range.
"""

import pathlib

import pytest

try:                                          # packaging is not a direct dep
    from packaging.requirements import Requirement
except ImportError:                           # pragma: no cover
    from pip._vendor.packaging.requirements import Requirement


REQUIREMENTS = pathlib.Path(__file__).resolve().parents[2] / "requirements.txt"


def _declared():
    """(lineno, text) for every real requirement line — comments/blanks out."""
    out = []
    for i, line in enumerate(
        REQUIREMENTS.read_text(encoding="utf-8").splitlines(), 1
    ):
        s = line.strip()
        if s and not s.startswith("#"):
            out.append((i, s))
    return out


def test_requirements_txt_exists_and_is_not_empty():
    assert REQUIREMENTS.is_file(), f"{REQUIREMENTS} is missing"
    assert _declared(), "requirements.txt declares nothing — did a path change?"


@pytest.mark.parametrize("lineno,text", _declared())
def test_the_dependency_is_pinned_to_an_exact_version(lineno, text):
    """`==` and nothing else.

    `>=` is the shape that broke production. `~=` and `<` are barely better:
    both still let an untested release install itself on a rebuild nobody
    triggered.
    """
    req = Requirement(text)
    spec = str(req.specifier)

    assert spec, (
        f"requirements.txt:{lineno} `{text}` has NO version constraint, so a "
        f"rebuild installs whatever is newest that day. That is how "
        f"anthropic 1.0.0 reached four live clinics."
    )
    assert spec.startswith("=="), (
        f"requirements.txt:{lineno} `{text}` is not pinned to an exact "
        f"version (constraint: {spec}). Use `==`. A range means a rebuild can "
        f"change what runs without any commit — the anthropic 1.0.0 outage, "
        f"which a revert could not fix because the range re-resolved."
    )


def test_no_requirement_uses_a_range_operator():
    """The same property stated over the whole file, so the failure message
    names every offender at once rather than one parametrised case."""
    offenders = [
        f"{lineno}: {text}"
        for lineno, text in _declared()
        if not str(Requirement(text).specifier).startswith("==")
    ]
    assert not offenders, (
        "unpinned requirements found:\n  " + "\n  ".join(offenders)
    )
