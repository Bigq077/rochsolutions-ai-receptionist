"""
recorder.py — Persists call results, transcripts, and recordings to disk.

After each call:
  1. Saves the raw result dict as JSON
  2. Saves the human-readable transcript as .txt
  3. Optionally downloads the Twilio call recording as .mp3
"""

import json
import logging
from datetime import datetime
from pathlib import Path

import httpx

from .config import RESULTS_DIR, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN
from .transcript import build_transcript

logger = logging.getLogger(__name__)


class Recorder:
    def __init__(self, results_dir: Path = RESULTS_DIR):
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def save_result(self, result: dict) -> Path:
        """Persist the raw result dict as JSON. Returns the file path."""
        scenario_id = result["scenario_id"].replace(".", "_")
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = self.results_dir / f"result_{scenario_id}_{ts}.json"

        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        logger.info(f"Result saved → {path}")
        return path

    def save_transcript(self, result: dict) -> Path:
        """Write a human-readable transcript. Returns the file path."""
        scenario_id = result["scenario_id"].replace(".", "_")
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = self.results_dir / f"transcript_{scenario_id}_{ts}.txt"

        path.write_text(build_transcript(result), encoding="utf-8")
        logger.info(f"Transcript saved → {path}")
        return path

    async def download_recording(self, result: dict) -> Path | None:
        """
        Download the Twilio call recording (if available) and save as .mp3.
        Returns the file path, or None if no recording URL is present.
        """
        recording_url = result.get("recording_url")
        if not recording_url:
            logger.debug(
                f"[{result['scenario_id']}] No recording URL — skipping download"
            )
            return None

        # Twilio recordings require Basic auth
        scenario_id = result["scenario_id"].replace(".", "_")
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = self.results_dir / f"recording_{scenario_id}_{ts}.mp3"

        # Append .mp3 to the URL if not already present
        url = recording_url
        if not url.endswith(".mp3"):
            url = url + ".mp3"

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.get(
                    url,
                    auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
                )
                response.raise_for_status()

            path.write_bytes(response.content)
            logger.info(f"Recording saved → {path}")
            return path

        except Exception as e:
            logger.warning(
                f"[{result['scenario_id']}] Failed to download recording: {e}"
            )
            return None

    async def record_all(self, result: dict) -> dict:
        """
        Convenience: save result JSON, transcript, and download recording.
        Returns a dict of saved file paths.
        """
        paths = {
            "result": str(self.save_result(result)),
            "transcript": str(self.save_transcript(result)),
            "recording": None,
        }

        recording_path = await self.download_recording(result)
        if recording_path:
            paths["recording"] = str(recording_path)

        return paths


def load_results(results_dir: Path = RESULTS_DIR) -> list[dict]:
    """Load all saved result JSON files from the results directory."""
    results_dir = Path(results_dir)
    results = []
    for path in sorted(results_dir.glob("result_*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                results.append(json.load(f))
        except Exception as e:
            logger.warning(f"Failed to load {path}: {e}")
    return results
