# tests/regression/test_assemblyai_u35_lever.py
"""
Universal-3.5 Pro Realtime lever (ASSEMBLYAI_USE_U35), added 2026-07-31.

The point of these tests is NOT that U3.5 transcribes well — that needs real
audio. It is the two things that make the A/B safe to run at all:

  1. Default OFF is byte-identical to live. A latency-eval lever that quietly
     changes the STT model on every branch that imports config.py would put an
     unvalidated model in front of real callers.
  2. ASSEMBLYAI_USE_V2 still wins. It is the break-glass fallback; if someone
     sets it mid-incident while the U3.5 lever is still on, it must take effect.

config.py reads its env at import time, so each case reloads the module.
"""
import importlib
import os

import pytest


def _load_config(monkeypatch, **env):
    for key in ("ASSEMBLYAI_USE_U35", "ASSEMBLYAI_USE_V2",
                "U35_MIN_TURN_SILENCE", "U35_MAX_TURN_SILENCE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import app.media_streams.config as config
    return importlib.reload(config)


def test_default_off_is_unchanged_from_live(monkeypatch):
    """No env set => the old model, exactly as before the lever existed."""
    config = _load_config(monkeypatch)
    assert config.ASSEMBLYAI_USE_U35 is False
    url = config.assemblyai_ws_url()
    assert url == config.ASSEMBLYAI_WS_URL
    assert "speech_model=universal-streaming-english" in url
    assert "universal-3-5-pro" not in url
    # format_turns=false is load-bearing for the OLD model and must survive.
    assert "format_turns=false" in url


def test_lever_on_selects_u35(monkeypatch):
    config = _load_config(monkeypatch, ASSEMBLYAI_USE_U35="true")
    url = config.assemblyai_ws_url()
    assert "speech_model=universal-3-5-pro" in url
    # format_turns was REMOVED in U3.5 — sending it risks a rejected socket,
    # and formatting is unconditionally on regardless.
    assert "format_turns" not in url
    assert "sample_rate=16000" in url
    assert "encoding=pcm_s16le" in url


def test_v2_break_glass_beats_u35(monkeypatch):
    """Both levers on => V2 wins, because it is the incident fallback."""
    config = _load_config(
        monkeypatch, ASSEMBLYAI_USE_U35="true", ASSEMBLYAI_USE_V2="true"
    )
    url = config.assemblyai_ws_url()
    assert url == config.ASSEMBLYAI_WS_URL_V2
    assert "universal-3-5-pro" not in url


def test_u35_endpointing_starts_at_current_tuning(monkeypatch):
    """First A/B must vary the MODEL only, not the model and the endpointer.

    600/1280 are the values the old model runs today (min_turn_silence in
    ASSEMBLYAI_WS_URL, WS_C_CONV_* profiles). If someone 'helpfully' moves
    these to the U3.5 vendor defaults (100/1000), the comparison stops being
    attributable and this test should fail loudly.
    """
    config = _load_config(monkeypatch, ASSEMBLYAI_USE_U35="true")
    assert config.U35_MIN_TURN_SILENCE == 600
    assert config.U35_MAX_TURN_SILENCE == 1280
    url = config.assemblyai_ws_url()
    assert "min_turn_silence=600" in url
    assert "max_turn_silence=1280" in url


def test_u35_endpointing_is_env_sweepable(monkeypatch):
    """The knee is found by sweeping env on Render, not by redeploying code."""
    config = _load_config(
        monkeypatch,
        ASSEMBLYAI_USE_U35="true",
        U35_MIN_TURN_SILENCE="300",
        U35_MAX_TURN_SILENCE="900",
    )
    url = config.assemblyai_ws_url()
    assert "min_turn_silence=300" in url
    assert "max_turn_silence=900" in url


@pytest.mark.parametrize("raw", ["true", "TRUE", "1", "yes", "on", " true "])
def test_truthy_env_spellings_all_enable(monkeypatch, raw):
    config = _load_config(monkeypatch, ASSEMBLYAI_USE_U35=raw)
    assert config.ASSEMBLYAI_USE_U35 is True


@pytest.mark.parametrize("raw", ["false", "0", "no", "off", ""])
def test_falsy_env_spellings_all_stay_off(monkeypatch, raw):
    config = _load_config(monkeypatch, ASSEMBLYAI_USE_U35=raw)
    assert config.ASSEMBLYAI_USE_U35 is False


def test_punctuated_finals_do_not_break_the_safety_matcher(monkeypatch):
    """U3.5 always formats finals; the clinical truncation guard must not care.

    Under the old model no final was ever punctuated, and the comment at
    clinical_screening.py ~L616 says so. That premise dies with U3.5. The code
    survives because _norm() blanks punctuation — this test pins that, since a
    regression here would let a mid-clause safety answer read as complete.
    """
    from app.media_streams.clinical_screening import _looks_truncated

    # Mid-clause: 'where' cannot close an English clause.
    assert _looks_truncated("there's no marks where") is True
    # Same utterance as U3.5 would emit it — capitalised and punctuated.
    assert _looks_truncated("There's no marks where,") is True
    # A complete answer stays complete in both spellings.
    assert _looks_truncated("no I haven't") is False
    assert _looks_truncated("No, I haven't.") is False


def test_config_import_does_not_leak_env_between_tests(monkeypatch):
    """Reload back to the pristine state so later modules see the real config."""
    config = _load_config(monkeypatch)
    assert config.assemblyai_ws_url() == config.ASSEMBLYAI_WS_URL
    assert os.getenv("ASSEMBLYAI_USE_U35") is None
