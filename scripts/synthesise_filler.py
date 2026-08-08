#!/usr/bin/env python3
"""
Pre-synthesis script — generates filler audio clips in Susie's voice.

Output — two pools, one clip drawn from each per turn:
  audio_clips/filler_checking.ulaw, _2, _3, …   the hold phrase
  audio_clips/filler_moment.ulaw,   _2, _3, …   the "still going" phrase

Format: µ-law 8kHz (ulaw_8000) — Twilio-ready, no ffmpeg required.

Usage:
    ELEVENLABS_VOICE_ID=<live value> python scripts/synthesise_filler.py

The no-flag run skips clips that already exist, so adding a variant to a pool
below costs one API call for the new file and leaves the ones callers already
hear untouched — which is what you want, because regenerating is the only way
the live voice can silently change. Use --force only when you mean to recut
everything (a voice change, a model change).

Commit the output: Render deploys from git, so an untracked clip is a clip the
live service does not have.

Requirements:
    ELEVENLABS_API_KEY environment variable must be set.
    pip install httpx
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Must match the voice the LIVE TTS stream uses, or the filler is a different
# person's voice cutting into Susie's turn.
#
# ⚠️ There is deliberately NO default. One stood here until 2026-08-08 and it is
# how the wrong voice shipped. The history is worth keeping, because the second
# attempt failed the same way as the first for the opposite reason:
#
#   - Until 2026-08-07 this was hardcoded to 6fZce9LFNG3iEITDfqZZ. That was
#     called the bug, and it was "fixed" by reading ELEVENLABS_VOICE_ID with
#     config.py's default (kBag1HOZlaVBH7ICPE8x) as the fallback.
#   - Render actually sets ELEVENLABS_VOICE_ID=6fZce9LFNG3iEITDfqZZ, overriding
#     that default. The hardcoded value had been right all along.
#   - The generating shell did not have the variable set, so it silently took
#     the fallback and produced clips in a voice no caller ever hears. Confirmed
#     on CAa11b26a1 (2026-08-07): every /text-to-speech/ URL in the call log is
#     6fZce9LFNG3iEITDfqZZ, while the committed clips were kBag1HOZlaVBH7ICPE8x.
#
# A default cannot be correct here: this script runs on a laptop and the value
# that matters lives in Render's environment. Refuse to guess — being told to
# set a variable costs seconds, and a wrong voice is only ever found by ear, on
# a live patient call.
VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()
MODEL_ID = "eleven_flash_v2_5"

# Committed next to the clips so the voice they were cut in is auditable from
# the repo instead of from memory. Nothing reads it at runtime — it exists so
# the next person can answer "which voice is this?" without making a call.
_VOICE_RECORD = "VOICE_ID.txt"

# Written to the repo root, which is where connection.py's _AUDIO_CLIPS_DIR
# reads them from. Both were CWD-relative and could disagree.
_AUDIO_CLIPS_DIR = Path(__file__).resolve().parents[1] / "audio_clips"

# ⚠️ These must be intent-NEUTRAL. FillerGuard arms at the start of every
# booking-flow turn and fires 350ms in — before the model has decided whether it
# is calling check_availability, answering a question about Bupa, or taking a
# name. It cannot know why it is speaking, so its words have to be true either
# way.
#
# "Let me have a look at what we've got…" was not: it announces a diary lookup
# on a turn that may be nothing of the kind, and it was also verbatim
# THINKING_FILLERS_PRIMARY[0], so the caller could hear the recorded clip and
# then the TTS list say the same sentence again.
#
# Avoid anything `turn_handler._BANNED_SENTENCE_RE` strips from model speech —
# "bear with me", "just a moment", "one moment please" — or a deterministic clip
# becomes the one path by which the caller hears a phrase the engine forbids
# everywhere else. That has already happened twice, in two filler lists.
# ── Why these are POOLS and not two sentences ────────────────────────────────
# Owner report, 2026-08-08: latency is good but the hold phrase "sounds quite
# robotic". It is not the wording. Until this change each pool was one file, so
# every hold moment in every call on every clinic played a byte-identical
# recording — the same breath, the same stress, the same length. A person
# saying one sentence twice is never acoustically equal; a file is, and the
# second playing is what reads as a machine.
#
# So the variation has to be over RECORDINGS, not just words: five different
# sentences cut once each would still be five fixed waveforms, but the caller
# meets a different one each time and the metronome is gone. They are also
# varied in LENGTH and opening rhythm on purpose — a pool of five same-shaped
# sentences ("Let me X for you…" five times) still ticks.
#
# `FillerGuard.discover_clip_pool` reads these off disk by name: the first
# member keeps the historical filename and the rest are `_2`, `_3`, … Anything
# not yet generated is simply not in the pool, so a branch that has not run this
# script keeps working with the single clip it already has.
_PRIMARY_POOL = [
    # Variant 1 is the phrase already live on both clinics — kept first, and
    # kept at its original filename, so regenerating does not silently change
    # what a caller hears most often.
    "Let me just check that for you…",
    "Okay, let me pull the diary up…",
    "Right, let me see what we've got…",
    "I'll have a quick look for you now…",
    "Let me find out what's free…",
]

# Plays 2.5s after the primary when the LLM still has not answered. Shorter and
# lower-key than the primary pool by design: this is a second reassurance, not a
# second announcement, and re-announcing the lookup makes the wait feel longer.
_SECONDARY_POOL = [
    "Just one moment…",
    "Won't be a second…",
    "Just bringing that up now…",
    "Nearly with you…",
]


def _pool_paths(stem: str, count: int) -> list[Path]:
    """filler_checking.ulaw, filler_checking_2.ulaw, … — must match
    `app.media_streams.filler_guard.discover_clip_pool`, which stops at the
    first gap in the sequence."""
    return [
        _AUDIO_CLIPS_DIR / (f"{stem}.ulaw" if i == 0 else f"{stem}_{i + 1}.ulaw")
        for i in range(count)
    ]


# Flat (text, path) list — the shape the regression tests read.
CLIPS = list(zip(_PRIMARY_POOL, _pool_paths("filler_checking", len(_PRIMARY_POOL)))) + list(
    zip(_SECONDARY_POOL, _pool_paths("filler_moment", len(_SECONDARY_POOL)))
)


def generate(text: str, output_path: Path, api_key: str) -> None:
    try:
        import httpx
    except ImportError:
        print("ERROR: httpx not installed — run: pip install httpx", file=sys.stderr)
        sys.exit(1)

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}"
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            url,
            params={"output_format": "ulaw_8000"},
            headers={"xi-api-key": api_key, "Content-Type": "application/json"},
            json={
                "text": text,
                "model_id": MODEL_ID,
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            },
        )

    if resp.status_code != 200:
        print(f"ERROR: HTTP {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        sys.exit(1)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(resp.content)
    print(f"[synthesise_filler] {output_path}: {len(resp.content):,} bytes")


def main() -> None:
    force = "--force" in sys.argv

    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        print("ERROR: ELEVENLABS_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    if not VOICE_ID:
        print(
            "ERROR: ELEVENLABS_VOICE_ID not set.\n"
            "\n"
            "There is no default on purpose — the clips must be cut in the voice\n"
            "the LIVE service speaks in, and that value lives in Render's\n"
            "environment, not in this repo. Guessing it once already shipped\n"
            "hold clips in a voice no caller hears.\n"
            "\n"
            "Read it from the Render dashboard (Environment → ELEVENLABS_VOICE_ID)\n"
            "for the service you are generating for, then:\n"
            "\n"
            "    ELEVENLABS_VOICE_ID=<the live value> python scripts/synthesise_filler.py --force\n",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"[synthesise_filler] voice={VOICE_ID} model={MODEL_ID}")
    for text, output_path in CLIPS:
        if output_path.exists() and not force:
            print(f"[synthesise_filler] {output_path} exists — skipping (--force to regenerate)")
            continue
        print(f'[synthesise_filler] generating: "{text}"')
        generate(text, output_path, api_key)

    # Record the voice alongside the audio. Without this, "are these clips in
    # the right voice?" can only be answered by listening to a live call.
    _record = _AUDIO_CLIPS_DIR / _VOICE_RECORD
    _record.parent.mkdir(parents=True, exist_ok=True)
    _record.write_text(
        f"{VOICE_ID}\n"
        f"\n"
        f"ElevenLabs voice the clips in this directory were synthesised with,\n"
        f"model {MODEL_ID}, format ulaw_8000.\n"
        f"\n"
        f"This must equal ELEVENLABS_VOICE_ID on the Render service that serves\n"
        f"these clips. If it does not, the hold phrase is a different person\n"
        f"cutting into Susie's turn — regenerate, do not edit this file.\n",
        encoding="utf-8",
    )
    print(f"[synthesise_filler] recorded voice in {_record}")

    print("[synthesise_filler] done.")


if __name__ == "__main__":
    main()
