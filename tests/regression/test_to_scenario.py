"""Tests for app.obs.to_scenario — mining a PII-free scenario from a bad call."""
from __future__ import annotations

from app.obs import regress, to_scenario


def _bad_call() -> dict:
    return {
        "call_sid": "CA0123456789abcdef",
        "clinic_id": "theorem",
        "quality_score": 1,
        "failure_tags": ["dead_end", "wrong_info"],
        "rubric_version": "v1",
        "collected": {"name": "Jane Smith"},
        "transcript": [
            {"role": "assistant", "text": "Hello, this is Susie. What's your name?"},
            {"role": "user", "text": "Jane Smith, my number is 07870166861"},
            {"role": "assistant", "text": "Thanks. Email jane@example.com?"},
            {"role": "user", "text": "yes that's right"},
        ],
    }


def test_build_scenario_is_pii_free_and_shaped():
    s = to_scenario.build_scenario(_bad_call())
    assert s["id"] == "regression_89abcdef"
    # caller turns captured as responses, redacted
    assert len(s["responses"]) == 2
    joined = " ".join(t["text"] for t in s["transcript"])
    assert "07870" not in joined
    assert "@" not in joined
    assert "jane" not in joined.lower()
    assert s["expected"]["no_technical_error"] is True
    assert s["source"]["failure_tags"] == ["dead_end", "wrong_info"]
    # the raw call_sid must not leak — only a slug
    assert "0123456789" not in str(s)


def test_render_module_asserts_no_pii_and_is_loadable(tmp_path):
    s = to_scenario.build_scenario(_bad_call())
    path = to_scenario.write_scenario(s, tmp_path / "regressions")
    assert path.exists()
    # The generated module loads and yields exactly one SCENARIO for the runner.
    loaded = regress.load_scenarios(tmp_path / "regressions")
    assert len(loaded) == 1
    assert loaded[0]["id"] == "regression_89abcdef"


def test_render_module_raises_if_pii_would_leak():
    import pytest
    # A scenario whose caller content still holds a phone number must not render.
    dirty = {"id": "regression_x", "responses": ["call me on 07870166861"],
             "expected": {}, "transcript": [{"role": "user", "text": "07870166861"}],
             "source": {}}
    with pytest.raises(to_scenario.redact.PIILeakError):
        to_scenario.render_module(dirty)


def test_all_digit_slug_does_not_false_positive(tmp_path):
    # A call SID whose tail is all digits must still render (SID is not PII).
    call = {"call_sid": "CAdemo00000001", "clinic_id": "theorem", "quality_score": 1,
            "failure_tags": ["dead_end"], "rubric_version": "v1", "collected": {},
            "transcript": [{"role": "assistant", "text": "Hi, I'm Susie."},
                           {"role": "user", "text": "hello"}]}
    path = to_scenario.write_scenario(to_scenario.build_scenario(call), tmp_path / "reg")
    assert path.exists()
    assert regress.run(tmp_path / "reg") == 0
