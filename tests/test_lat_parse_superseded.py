"""Call-3 P3 (2026-07-19): superseded turn records must not inflate the
abandoned rate. A record closed because a newer dispatch replaced it (split
utterance / discarded fragment / deterministic branch) is emitted as
outcome=superseded; lat_parse buckets it separately from abandoned and
excludes both from TTFA pools."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _lat_line(seq: int, outcome: str, ttfa: int = 2000) -> str:
    return (
        f"[LAT] turn_seq={seq} path=llm outcome={outcome} ttfa_ms={ttfa} "
        f"content_ttfa_ms={ttfa} ep_dispatch_ms=0 llm_ttft_ms=1200 "
        f"chunk_gate_ms=600 tts_first_byte_ms=120 audio_wire_ms=0 flags=- "
        f"model=claude-sonnet-4-6 stt_model=universal-streaming-english "
        f"eot_confident=None capture_phase=conversation endpoint_wait_ms=500"
    )


def test_superseded_bucketed_separately(tmp_path):
    log = tmp_path / "synthetic.log"
    lines = (
        [_lat_line(i, "completed") for i in range(1, 6)]
        + [_lat_line(6, "superseded", ttfa=-1),
           _lat_line(7, "superseded", ttfa=-1),
           _lat_line(8, "abandoned", ttfa=-1)]
    )
    log.write_text("\n".join(lines), encoding="utf-8")
    out_json = tmp_path / "stats.json"

    r = subprocess.run(
        [sys.executable, str(_ROOT / "lat_parse.py"), str(log),
         "--json", str(out_json)],
        capture_output=True, text=True, cwd=str(_ROOT),
    )
    assert r.returncode == 0, r.stderr

    blob = json.loads(out_json.read_text(encoding="utf-8"))
    counts = blob["counts"]
    assert counts["completed"] == 5
    assert counts["superseded"] == 2, "superseded not bucketed separately"
    assert counts["abandoned"] == 1, (
        "superseded records leaked into the abandoned rate (Call-3 P3)"
    )
    # Both excluded from the TTFA pool: only the 5 completed feed percentiles.
    assert blob["stats"]["perceived_ttfa_ms"]["n"] == 5
    # And the report line itself names the bucket.
    assert "superseded" in r.stdout
