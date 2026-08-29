"""The recorded clip must say whether it ends open, and default to closed.

WHY THIS EXISTS
---------------
The clip is AUDIO. Nothing downstream can read its words, and two things need
them: `join_after_head`, to make the model's reply continue the clause rather
than restart after it, and the duplicate-opener strip, so the model does not say
the same sentence 1-2s later. The wording used to be a string literal in the
middle of `_fire()`, which meant recutting the audio and updating the text were
two separate acts of remembering -- and a wrong-voice clip has already shipped
once from exactly that kind of gap.

`open_clause` is the field that decides behaviour:

    CLOSED  "Let me just check that for you..."   ends in the ellipsis, which
                                                  ElevenLabs renders as a falling
                                                  contour and a pause -- the
                                                  canned-filler sound. It must be
                                                  the ONLY hold speech that turn.

    OPEN    "Let me just check -"                 an unfinished clause the
                                                  situational head completes:
                                                  "... what Saturday looks like -"
                                                  "half past nine is free."

Flipping the flag without recutting the audio makes the caller hear "Let me just
check that for you..." and then "Let me see what Saturday looks like -" -- the
exact double-phrase defect the arbiter exists to remove. So the flag is pinned
here, and it will fail the day someone flips it, which is the point: it should
fail until the clips are recut with the live voice.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.media_streams.filler_guard import clip_manifest

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "audio_clips" / "CLIPS.json"


def test_the_manifest_is_present_and_parses():
    assert MANIFEST.exists(), "audio_clips/CLIPS.json is missing"
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert "filler_checking" in (data.get("clips") or {})


def test_the_shipped_clip_is_still_closed():
    """Fails the day the flag is flipped, deliberately.

    Set it to true only in the same change that recuts the audio, and check the
    new recording actually ends on an open clause. Recutting needs the LIVE
    ElevenLabs voice -- it is a paid Voice Library voice, a free key returns 402,
    and synthesise_filler then falls back to a different voice.
    """
    manifest = clip_manifest()
    assert manifest["open_clause"] is False, (
        "the clip is declared OPEN. That is only correct if audio_clips/ has "
        "been recut so the recording ends on an unfinished clause; if it still "
        "says 'Let me just check that for you...' the caller now hears that AND "
        "the situational head, which is the double-phrase defect."
    )


def test_the_declared_text_matches_what_the_guard_reports():
    """The wording feeds join_after_head and the duplicate strip, so a manifest
    that drifts from the recording silently breaks both."""
    assert clip_manifest()["text"] == "Let me just check that for you…"


def test_an_unreadable_manifest_falls_back_to_closed():
    """Never raises, and fails to the safe side.

    A clip wrongly treated as closed costs one suppressed head. A clip wrongly
    treated as open makes the caller hear two ways of saying the same thing.
    """
    manifest = clip_manifest("no_such_clip")
    assert manifest["open_clause"] is False
    assert manifest["text"]
