"""B-14 — pronunciation is owned by the local substitution table, not a remote dictionary.

The ElevenLabs pronunciation-dictionary loader was removed on 2026-08-02. It had
never executed: config/pronunciation_dict.json held {"Alcester": "Awlstuh"}, a
word->alias map, while the loader wanted a {pronunciation_dictionary_id,
version_id} locator pair. The lookup failed on every startup, the locator stayed
None, and the request body was never touched.

Removing dead code needs no test. What DOES need one is the assumption the
removal rests on: that the one word the dictionary demonstrably mattered for is
already covered locally. If _TTS_SUBSTITUTIONS_ELEVENLABS ever loses its Alcester
rule, the dictionary is no longer there to be the second line of defence, and
the failure is silent — a mispronounced clinic name on a live call, audible to
the caller and to nobody reading logs.

The other half is a guard against the thing that made this worth removing rather
than repairing: two mechanisms defining pronunciation, free to disagree. See
DEFECT_REGISTER.md §A4 for what that costs when it is allowed to happen.
"""
import inspect

from app.media_streams import tts_stream
from app.media_streams.tts_stream import (
    _apply_tts_substitutions_elevenlabs as _subs,
)


# ── The assumption the removal rests on ─────────────────────────────────────

def test_alcester_is_still_spoken_phonetically():
    assert "Awlstuh" in _subs("Our Alcester clinic has a slot on Thursday.")


def test_alcester_substitution_is_case_insensitive():
    """The model's casing is not a contract; the caller hears the same word."""
    for raw in ("Alcester", "alcester", "ALCESTER"):
        assert "Awlstuh" in _subs(f"We're in {raw}."), raw


def test_the_substitution_survives_normal_sentence_punctuation():
    for sentence in (
        "That's Alcester, Thursday at half past six.",
        "Alcester?",
        "…at Alcester — is that alright?",
    ):
        assert "Alcester" not in _subs(sentence), sentence


# ── The dead path stays dead ────────────────────────────────────────────────

def test_the_dictionary_loader_is_gone():
    assert not hasattr(tts_stream, "_get_pron_dict_locator"), (
        "the pronunciation-dictionary loader is back. Before restoring it, "
        "decide which mechanism OWNS pronunciation — two that can disagree is "
        "the failure pattern this removal exists to avoid"
    )
    for name in ("_PRON_DICT_LOCATOR", "_PRON_DICT_LOADED"):
        assert not hasattr(tts_stream, name), name


def test_no_locator_field_is_sent_to_elevenlabs():
    """Asserted on the source, because the body is built inside a live request.

    Reaching this any other way means standing up a streaming HTTP mock for a
    field whose whole point is that it is absent — the assertion would be a
    fixture testing itself.
    """
    src = inspect.getsource(tts_stream.TTSStream.synthesise_chunk)
    assert "pronunciation_dictionary_locators" not in src
