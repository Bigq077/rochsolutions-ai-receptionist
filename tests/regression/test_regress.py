"""Tests for app.obs.regress — the offline regression runner."""
from __future__ import annotations

from app.obs import regress


def _scenario(transcript, expected):
    return {"id": "regression_test", "transcript": transcript, "expected": expected}


def test_clean_transcript_passes():
    s = _scenario(
        [{"role": "assistant", "text": "Hi, you've reached the clinic. I'm Susie."},
         {"role": "user", "text": "book me in"}],
        {"no_technical_error": True, "greeting_contains": ["susie"]},
    )
    passed, failures, checked = regress.check_scenario(s)
    assert passed is True
    assert failures == []
    assert checked == 2


def test_technical_error_phrase_fails():
    s = _scenario(
        [{"role": "assistant", "text": "Sorry, I'm having a small technical issue."}],
        {"no_technical_error": True},
    )
    passed, failures, _ = regress.check_scenario(s)
    assert passed is False
    assert any("no_technical_error" in f for f in failures)


def test_not_said_banned_phrase_fails():
    s = _scenario(
        [{"role": "assistant", "text": "Please press one for Alcester."}],
        {"not_said": ["press one"]},
    )
    passed, failures, _ = regress.check_scenario(s)
    assert passed is False


def test_greeting_contains_missing_fails():
    s = _scenario(
        [{"role": "assistant", "text": "Hello there."}],
        {"greeting_contains": ["susie"]},
    )
    passed, failures, _ = regress.check_scenario(s)
    assert passed is False


def test_run_green_on_clean_dir(tmp_path):
    from app.obs import to_scenario
    call = {
        "call_sid": "CAcleanexample01", "clinic_id": "theorem", "quality_score": 2,
        "failure_tags": ["loop"], "rubric_version": "v1", "collected": {},
        "transcript": [
            {"role": "assistant", "text": "Hi, I'm Susie at the clinic."},
            {"role": "user", "text": "hello"},
        ],
    }
    to_scenario.write_scenario(to_scenario.build_scenario(call), tmp_path / "reg")
    assert regress.run(tmp_path / "reg") == 0


def test_run_red_on_regressed_scenario(tmp_path):
    # Hand-write a scenario whose transcript violates its own assertion.
    d = tmp_path / "reg"
    d.mkdir()
    (d / "__init__.py").touch()
    (d / "regression_bad.py").write_text(
        'SCENARIO = {"id": "regression_bad", '
        '"transcript": [{"role": "assistant", "text": "having a small technical difficulty"}], '
        '"expected": {"no_technical_error": True}}\n',
        encoding="utf-8",
    )
    assert regress.run(d) == 1


def test_run_empty_dir_is_green(tmp_path):
    assert regress.run(tmp_path / "does_not_exist") == 0
