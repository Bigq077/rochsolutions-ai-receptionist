"""
Regression (O-4): the filler clips must exist where the handler looks for them.

`WebSocketCallHandler.__init__` builds a FillerGuard over
`audio_clips/filler_checking.ulaw` and `audio_clips/filler_moment.ulaw`. Neither
file was in the repo, and `audio_clips/` did not exist at all. FillerGuard's
response to a missing clip is `self._clip = b""` plus a log warning, so every
availability lookup played nothing and the caller heard 3–6 s of dead air. It
had been that way since the guard was written; nothing in the call surfaced it.

A log warning is what let this run in production, so the check belongs somewhere
that fails loudly. If this file is red, run:

    ELEVENLABS_API_KEY=... python scripts/synthesise_filler.py

and commit the two files — Render deploys from git, so an untracked clip is a
clip the live service does not have.

The paths are asserted against the constant the handler actually uses, not a
literal, so moving the directory cannot leave this test passing against a stale
location.
"""
from __future__ import annotations

import audioop

import pytest

from app.media_streams.connection import _AUDIO_CLIPS_DIR
from app.media_streams.filler_guard import discover_clip_pool

# The first member of each pool. These two must always exist — without them the
# guard has nothing to play at all. The numbered variants are checked separately
# below: they are what stops the clip sounding like a recording, but a branch
# that has not regenerated yet is degraded, not broken.
CLIPS = ["filler_checking.ulaw", "filler_moment.ulaw"]

# Everything actually on disk, variants included, so a malformed variant is
# caught by the format checks the same way the first member is.
POOLED = [
    p.name
    for stem in ("filler_checking", "filler_moment")
    for p in discover_clip_pool(_AUDIO_CLIPS_DIR / f"{stem}.ulaw")
]


def test_the_clips_directory_exists():
    assert _AUDIO_CLIPS_DIR.is_dir(), (
        f"{_AUDIO_CLIPS_DIR} does not exist — every filler is silence. "
        f"Run scripts/synthesise_filler.py and commit the output."
    )


@pytest.mark.parametrize("clip", CLIPS)
def test_each_clip_is_present_and_non_empty(clip):
    path = _AUDIO_CLIPS_DIR / clip
    assert path.is_file(), f"{path} missing — FillerGuard will load b'' and play nothing"
    assert path.stat().st_size > 0, f"{path} is empty"


@pytest.mark.parametrize("stem", ["filler_checking", "filler_moment"])
def test_each_pool_has_more_than_one_variant(stem):
    """
    A pool of one is a single recording replayed for the life of the service —
    same breath, same stress, to the byte. Owner report 2026-08-08: "latency is
    great but it sounds quite robotic". The rotation code is in place; this is
    red until the audio exists, and it can only be cut with the paid key and the
    LIVE voice id, neither of which belongs in this repo.
    """
    pool = discover_clip_pool(_AUDIO_CLIPS_DIR / f"{stem}.ulaw")
    assert len(pool) > 1, (
        f"{stem} is a pool of {len(pool)} — every call plays the identical "
        f"recording. Read ELEVENLABS_VOICE_ID off the Render service, then:\n"
        f"    ELEVENLABS_VOICE_ID=<live value> python scripts/synthesise_filler.py\n"
        f"(no --force: it generates only the missing variants and leaves the "
        f"clips callers already hear alone). Then commit audio_clips/."
    )


@pytest.mark.parametrize("clip", POOLED or CLIPS)
def test_each_clip_is_plausible_8k_mulaw(clip):
    """
    Twilio needs raw µ-law 8 kHz with no container. An MP3 or a WAV written to a
    .ulaw name would load, send, and play as noise — worse than the silence it
    replaces — so check the bytes rather than the filename.
    """
    path = _AUDIO_CLIPS_DIR / clip
    if not path.is_file():
        pytest.skip("covered by test_each_clip_is_present_and_non_empty")

    raw = path.read_bytes()

    for magic, fmt in ((b"RIFF", "WAV"), (b"ID3", "MP3"), (b"OggS", "Ogg"), (b"fLaC", "FLAC")):
        assert not raw.startswith(magic), (
            f"{path} is a {fmt} container, not raw µ-law — check "
            f"output_format=ulaw_8000 in scripts/synthesise_filler.py"
        )

    # 8 kHz µ-law is 8000 bytes/sec. Both clips are short spoken phrases.
    seconds = len(raw) / 8000.0
    assert 0.4 <= seconds <= 6.0, (
        f"{path} decodes to {seconds:.2f}s at 8kHz µ-law — wrong sample rate or "
        f"wrong format; the filler is meant to be a short phrase"
    )

    # Decodes as µ-law and carries actual signal, not a block of silence.
    pcm = audioop.ulaw2lin(raw, 2)
    assert audioop.rms(pcm, 2) > 100, f"{path} decodes to near-silence"


def test_the_handler_resolves_the_directory_independently_of_cwd():
    """
    The paths were CWD-relative until 2026-08-07. A worker started from anywhere
    but the repo root silently loaded nothing.
    """
    assert _AUDIO_CLIPS_DIR.is_absolute(), (
        "_AUDIO_CLIPS_DIR is relative — it resolves against the process CWD, "
        "which is not guaranteed to be the repo root under the Render worker"
    )
