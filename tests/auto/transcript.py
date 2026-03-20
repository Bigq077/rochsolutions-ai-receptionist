import time
from datetime import datetime


def build_transcript(result: dict) -> str:
    """
    Build a clean, chronologically-ordered transcript from a raw call result.
    Suitable for human reading and for passing to the Claude evaluator.
    """
    lines = []
    lines.append(f"SCENARIO: [{result['scenario_id']}] {result['scenario_name']}")
    lines.append(f"PHASE:     {result.get('phase', 'Unknown')}")
    lines.append(f"CALL SID:  {result.get('call_sid', 'N/A')}")
    lines.append(f"DURATION:  {result.get('duration_seconds', 0.0):.1f}s")
    lines.append(f"TURNS:     {result['turns']}")
    lines.append(f"END:       {result['end_reason']}")
    lines.append(f"TIMESTAMP: {result.get('timestamp', 'unknown')}")
    lines.append("=" * 60)

    # Interleave Susie and patient turns chronologically
    all_turns = []

    for t in result.get("susie_said", []):
        all_turns.append(
            {
                "speaker": "SUSIE",
                "text": t["text"],
                "timestamp": t["timestamp"],
                "turn": t["turn"],
                "confidence": t.get("confidence"),
            }
        )

    for t in result.get("test_said", []):
        all_turns.append(
            {
                "speaker": "PATIENT",
                "text": t["text"],
                "timestamp": t["timestamp"],
                "turn": t["turn"],
                "confidence": None,
            }
        )

    all_turns.sort(key=lambda x: x["timestamp"])

    if not all_turns:
        lines.append("[No speech captured]")
    else:
        for turn in all_turns:
            conf_str = ""
            if turn["confidence"] is not None:
                conf_str = f" (conf={turn['confidence']:.2f})"
            lines.append(f"{turn['speaker']}: {turn['text']}{conf_str}")

    lines.append("=" * 60)
    return "\n".join(lines)


def save_transcript(result: dict, output_dir) -> str:
    """
    Write the transcript to a file. Returns the file path.
    """
    from pathlib import Path

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    scenario_id = result["scenario_id"].replace(".", "_")
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"transcript_{scenario_id}_{ts}.txt"
    path = output_dir / filename

    transcript_text = build_transcript(result)
    path.write_text(transcript_text, encoding="utf-8")
    return str(path)
