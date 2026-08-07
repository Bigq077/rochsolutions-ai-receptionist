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

# Must match the voice the live TTS stream uses, or the filler is a different
# person's voice cutting into Susie's turn. `app/media_streams/config.py:58`
# reads ELEVENLABS_VOICE_ID with this same default; the value hardcoded here
# until 2026-08-07 was 6fZce9LFNG3iEITDfqZZ, which is not it.
VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "kBag1HOZlaVBH7ICPE8x")
MODEL_ID = "eleven_flash_v2_5"

# Written to the repo root, which is where connection.py's _AUDIO_CLIPS_DIR
# reads them from. Both were CWD-relative and could disagree.
_AUDIO_CLIPS_DIR = Path(__file__).resolve().parents[1] / "audio_clips"

CLIPS = [
    ("Let me have a look at what we've got…", _AUDIO_CLIPS_DIR / "filler_checking.ulaw"),
    ("Just one moment…",                       _AUDIO_CLIPS_DIR / "filler_moment.ulaw"),
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

    print(f"[synthesise_filler] voice={VOICE_ID} model={MODEL_ID}")
    for text, output_path in CLIPS:
        if output_path.exists() and not force:
            print(f"[synthesise_filler] {output_path} exists — skipping (--force to regenerate)")
            continue
        print(f'[synthesise_filler] generating: "{text}"')
        generate(text, output_path, api_key)

    print("[synthesise_filler] done.")


if __name__ == "__main__":
    main()
