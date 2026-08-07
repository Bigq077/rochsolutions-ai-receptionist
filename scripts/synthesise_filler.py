#!/usr/bin/env python3
"""
Pre-synthesis script — generates filler audio clips in Susie's voice.

Output:
  audio_clips/filler_checking.ulaw  — "Let me have a look at what we've got…"
  audio_clips/filler_moment.ulaw    — "Just one moment…"

Format: µ-law 8kHz (ulaw_8000) — Twilio-ready, no ffmpeg required.

Usage:
    python scripts/synthesise_filler.py            # skip if files already exist
    python scripts/synthesise_filler.py --force    # regenerate unconditionally

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
CLIPS = [
    ("Let me just check that for you…", _AUDIO_CLIPS_DIR / "filler_checking.ulaw"),
    ("Just one moment…",                _AUDIO_CLIPS_DIR / "filler_moment.ulaw"),
]


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
