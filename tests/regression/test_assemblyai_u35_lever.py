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
    for key in ("ASSEMBLYAI_USE_U35", "ASSEMBLYAI_USE_V2", "U35_DEFORMAT",
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
    # CORRECTED 2026-08-04 against the live WebSocket API reference: format_turns
    # is NOT removed in U3.5 — it is accepted and defaults false. This assertion
    # previously demanded its ABSENCE on the theory that sending it would get the
    # socket rejected; the reference says unrecognised params are ignored, not
    # rejected, so that fear was doubly unfounded. Ask for the unformatted
    # contract at the socket rather than undoing formatting downstream.
    assert "format_turns=false" in url
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


# ---------------------------------------------------------------------------
# De-formatting: the three consumers that read the transcript RAW
# ---------------------------------------------------------------------------
# The first audit of this lever checked only the two consumers that normalise
# (clinical_screening, fast_path) and concluded punctuation was harmless. It is
# not. These three sites break, and each one is a silent caller-facing failure,
# so they are pinned here as the acceptance criteria for _deformat_transcript().


def test_deformat_restores_the_unformatted_contract():
    from app.media_streams.stt_stream import _deformat_transcript as d

    assert d("My name is Sarah.") == "my name is sarah"
    assert d("Yes, that's right!") == "yes that's right"
    # Grouped digits are rejoined, not merely de-punctuated — see
    # test_deformat_rejoins_grouped_phone_digits for why the old expectation
    # ("07502 211207") was itself the bug.
    assert d("07502 211207.") == "07502211207"
    # Idempotent, and a no-op on text that was never formatted — so it stays
    # safe if the model, or the flag, changes underneath it.
    assert d("my name is sarah") == "my name is sarah"
    assert d(d("My name is Sarah.")) == "my name is sarah"
    assert d("") == ""


def test_deformat_keeps_apostrophes_and_hyphens():
    """Load-bearing in the name and yes/no matchers — stripping them is a bug."""
    from app.media_streams.stt_stream import _deformat_transcript as d

    assert d("It's O'Brien.") == "it's o'brien"
    assert d("Smith-Jones.") == "smith-jones"


def test_deformat_saves_the_phone_number_from_being_discarded():
    """connection.py:411 ^\\d{5,}$ — a trailing stop loses the caller's number.

    Without de-formatting, "07502211207." fails the phone match and falls
    through to _is_short_meaningless_fragment(), which returns True and the
    number is dropped. Silent, and the caller never gets called back.
    """
    from app.media_streams.connection import _is_short_meaningless_fragment
    from app.media_streams.stt_stream import _deformat_transcript as d

    assert _is_short_meaningless_fragment("07502211207") is False
    # The regression this guards against:
    assert _is_short_meaningless_fragment("07502211207.") is True
    # ...and the fix:
    assert _is_short_meaningless_fragment(d("07502211207.")) is False


def test_deformat_rejoins_grouped_phone_digits():
    """Stripping punctuation alone still loses a GROUPED number.

    U3.5's formatter groups long digit runs. connection.py's phone path is
    ^\\d{5,}$ guarded by len(words) == 1, so "07502 211207" is two words, fails
    the match, and is discarded by _is_short_meaningless_fragment exactly as the
    punctuated form was. Measured 2026-08-04 with the shim on and no rejoin:
    both "07502 211207." and "0750 221 1207." were DISCARDED.
    """
    from app.media_streams.connection import _is_short_meaningless_fragment
    from app.media_streams.stt_stream import _deformat_transcript as d

    # The regression this guards against — grouped, de-punctuated, still lost:
    assert _is_short_meaningless_fragment("07502 211207") is True

    for grouped in ("07502 211207.", "0750 221 1207.", "07502 211 207"):
        assert d(grouped) == "07502211207"
        assert _is_short_meaningless_fragment(d(grouped)) is False


def test_deformat_does_not_weld_ordinary_numbers_together():
    """The rejoin must not invent a phone number out of a time, year or age."""
    from app.media_streams.stt_stream import _deformat_transcript as d

    # Not a whole-utterance digit run — untouched.
    assert d("I'm 34 years old.") == "i'm 34 years old"
    assert d("Half 9 on the 12th.") == "half 9 on the 12th"
    # Whole-utterance digits, but under the 7-digit floor — untouched.
    assert d("9 30.") == "9 30"          # a spoken time
    assert d("20 25.") == "20 25"        # a spoken year
    assert d("12 08.") == "12 08"        # a spoken date
    # Idempotent on an already-joined number.
    assert d(d("07502 211207.")) == "07502211207"


def test_deformat_keeps_the_name_wrapper_patterns_firing():
    """flow.py:1450-1461 — else the LABEL gets stored as the caller's name."""
    from app.media_streams.flow import _NAME_WRAPPER_PATTERNS
    from app.media_streams.stt_stream import _deformat_transcript as d

    def _is_wrapper(text: str) -> bool:
        t = text.strip().lower()
        return any(p.match(t) for p in _NAME_WRAPPER_PATTERNS)

    assert _is_wrapper("my name is") is True
    # The regression this guards against:
    assert _is_wrapper("My name is.") is False
    # ...and the fix:
    assert _is_wrapper(d("My name is.")) is True


def test_deformat_keeps_name_after_is_extractor_firing():
    """name_collector.py:406 is anchored \\s*$ — punctuation silences it."""
    from app.media_streams.name_collector import _extract_name_after_is
    from app.media_streams.stt_stream import _deformat_transcript as d

    assert _extract_name_after_is("my first theme is quentin") == "Quentin"
    # The regression this guards against:
    assert _extract_name_after_is("My first theme is Quentin.") is None
    # ...and the fix:
    assert _extract_name_after_is(d("My first theme is Quentin.")) == "Quentin"


def test_deformat_defaults_on_but_only_bites_with_u35(monkeypatch):
    config = _load_config(monkeypatch)
    assert config.U35_DEFORMAT is True          # on by default
    assert config.ASSEMBLYAI_USE_U35 is False   # ...and inert, because U35 is off

    config = _load_config(monkeypatch, U35_DEFORMAT="false")
    assert config.U35_DEFORMAT is False


# ---------------------------------------------------------------------------
# [LAT] attribution: the log line must name the model that actually ran
# ---------------------------------------------------------------------------
# _STT_MODEL was hardcoded to universal-streaming-english via STT_MODEL_TAG, an
# env var nothing sets. Every [LAT] line therefore reported the old model even
# when U3.5 served the call, so the one field the A/B needs to tell its two arms
# apart could not tell them apart. A whole eval call was read as a control run
# on the strength of that field before anyone checked [ms_stt] init.


def _load_latency_timing(monkeypatch, **env):
    for key in ("ASSEMBLYAI_USE_U35", "ASSEMBLYAI_USE_V2", "STT_MODEL_TAG"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import app.media_streams.latency_timing as lt
    return importlib.reload(lt)


@pytest.mark.parametrize("env,expected", [
    ({},                                  "universal-streaming-english"),
    ({"ASSEMBLYAI_USE_U35": "true"},      "u3.5-pro"),
    ({"ASSEMBLYAI_USE_V2":  "true"},      "v2"),
    # V2 > U3.5, same precedence as assemblyai_ws_url().
    ({"ASSEMBLYAI_USE_U35": "true",
      "ASSEMBLYAI_USE_V2":  "true"},      "v2"),
])
def test_lat_stt_model_follows_the_lever(monkeypatch, env, expected):
    assert _load_latency_timing(monkeypatch, **env)._STT_MODEL == expected


def test_lat_stt_model_matches_the_stt_variant_tag(monkeypatch):
    """[LAT] and [ms_stt] init must agree, or offline joins are wrong.

    Both lines are grepped when attributing a run; if they disagree on the
    spelling of the model, the two halves of the same call stop joining.
    """
    lt = _load_latency_timing(monkeypatch, ASSEMBLYAI_USE_U35="true")
    config = _load_config(monkeypatch, ASSEMBLYAI_USE_U35="true")
    stt_variant = (
        "v2" if config.ASSEMBLYAI_USE_V2
        else "u3.5-pro" if config.ASSEMBLYAI_USE_U35
        else "universal-streaming-english"
    )
    assert lt._STT_MODEL == stt_variant


def test_lat_stt_model_tag_still_overrides(monkeypatch):
    """Offline replays label themselves; an explicit tag beats the live flags."""
    lt = _load_latency_timing(
        monkeypatch, ASSEMBLYAI_USE_U35="true", STT_MODEL_TAG="replay-2026-07-31"
    )
    assert lt._STT_MODEL == "replay-2026-07-31"


@pytest.mark.parametrize("raw", ["TRUE", "1", "yes", "on", " true "])
def test_lat_stt_model_accepts_the_same_spellings_as_config(monkeypatch, raw):
    """Divergent parsing would make [LAT] disagree with the socket it describes."""
    assert _load_latency_timing(
        monkeypatch, ASSEMBLYAI_USE_U35=raw
    )._STT_MODEL == "u3.5-pro"


def test_latency_timing_stays_free_of_hot_path_imports():
    """The module's stated guarantee — derive from env, never import config."""
    import pathlib
    src = pathlib.Path(
        "app/media_streams/latency_timing.py"
    ).read_text(encoding="utf-8")
    for forbidden in ("from app.media_streams.config", "import config",
                      "from .config", "from app.media_streams import config"):
        assert forbidden not in src


def test_config_import_does_not_leak_env_between_tests(monkeypatch):
    """Reload back to the pristine state so later modules see the real config."""
    config = _load_config(monkeypatch)
    assert config.assemblyai_ws_url() == config.ASSEMBLYAI_WS_URL
    assert os.getenv("ASSEMBLYAI_USE_U35") is None
