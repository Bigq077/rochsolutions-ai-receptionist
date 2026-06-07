#!/usr/bin/env python3
"""
Pre-synthesis script — generates audio_clips/filler_checking.ulaw.

Calls the ElevenLabs TTS API, converts the PCM16 16kHz response to
µ-law 8kHz, and writes the result to audio_clips/filler_checking.ulaw.
The file is loaded at startup by FillerGuard in connection.py.

Usage:
    python scripts/synthesise_filler.py            # skip if file already exists
    python scripts/synthesise_filler.py --force    # regenerate unconditionally

Requirements:
    ELEVENLABS_API_KEY environment variable must be set.
    pip install httpx
"""
from __future__ import annotations

import audioop
import os
import sys
from pathlib import Path

VOICE_ID    = "kBag1HOZlaVBH7ICPE8x"
MODEL_ID    = "eleven_flash_v2_5"
TEXT        = "Let me have a look—"
OUTPUT_PATH = Path("audio_clips/filler_checking.ulaw")


def main() -> None:
    force = "--force" in sys.argv

    if OUTPUT_PATH.exists() and not force:
        print(
            f"[synthesise_filler] {OUTPUT_PATH} already exists — "
            "skipping (pass --force to regenerate)"
        )
        return

    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        print(
            "[synthesise_filler] ERROR: ELEVENLABS_API_KEY not set",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        import httpx  # type: ignore[import]
    except ImportError:
        print(
            "[synthesise_filler] ERROR: httpx not installed — run: pip install httpx",
            file=sys.stderr,
        )
        sys.exit(1)

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}"
    headers = {
        "xi-api-key":   api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "text":     TEXT,
        "model_id": MODEL_ID,
        "voice_settings": {
            "stability":       0.5,
            "similarity_boost": 0.75,
        },
        "output_format": "pcm_16000",
    }

    print(f"[synthesise_filler] Calling ElevenLabs (voice={VOICE_ID}, model={MODEL_ID})…")
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, headers=headers, json=payload)

    if resp.status_code != 200:
        print(
            f"[synthesise_filler] ERROR: HTTP {resp.status_code}: {resp.text[:200]}",
            file=sys.stderr,
        )
        sys.exit(1)

    pcm16 = resp.content  # raw PCM16 16kHz mono bytes

    # Downsample 16kHz → 8kHz, then encode PCM16 → µ-law
    pcm8k, _ = audioop.ratecv(pcm16, 2, 1, 16000, 8000, None)
    ulaw      = audioop.lin2ulaw(pcm8k, 2)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_bytes(ulaw)
    print(f"[synthesise_filler] Written {len(ulaw)} bytes → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
