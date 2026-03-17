from datetime import datetime

from .config import MIN_PASS_RATE


def build_report(all_results: list) -> str:
    total = len(all_results)
    passed = sum(
        1 for r in all_results
        if r.get("evaluation", {}).get("passed")
    )
    failed = total - passed
    pass_rate = passed / total if total > 0 else 0.0

    lines = []
    lines.append("=" * 60)
    lines.append("SUSIE AUTOMATED TEST REPORT")
    lines.append(f"Generated: {datetime.utcnow().isoformat()}")
    lines.append("=" * 60)
    lines.append(
        f"TOTAL: {total} | PASS: {passed} "
        f"| FAIL: {failed} | "
        f"RATE: {pass_rate * 100:.1f}%"
    )

    if pass_rate >= MIN_PASS_RATE:
        lines.append("STATUS: CLINIC READY")
    else:
        lines.append("STATUS: NOT READY")

    lines.append("")
    lines.append("RESULTS BY PHASE:")
    lines.append("-" * 40)

    # Group by phase
    phases: dict[str, list] = {}
    for r in all_results:
        phase = r.get("phase", "Unknown")
        phases.setdefault(phase, []).append(r)

    for phase, results in phases.items():
        phase_pass = sum(
            1 for r in results
            if r.get("evaluation", {}).get("passed")
        )
        lines.append(f"\n{phase}: {phase_pass}/{len(results)}")
        for r in results:
            ev = r.get("evaluation", {})
            status = "PASS" if ev.get("passed") else "FAIL"
            lines.append(f"  {status} {r['scenario_name']}")
            if not ev.get("passed"):
                lines.append(
                    f"     REASON: {ev.get('fail_reason', 'Unknown')}"
                )
                lines.append(
                    f"     DETAIL: {ev.get('detail', '')}"
                )

    lines.append("")
    lines.append("FAILURES REQUIRING FIXES:")
    lines.append("-" * 40)

    failures = [
        r for r in all_results
        if not r.get("evaluation", {}).get("passed")
    ]

    if not failures:
        lines.append("None — all tests passed!")
    else:
        for f in failures:
            ev = f.get("evaluation", {})
            lines.append(
                f"• {f['scenario_name']}: "
                f"{ev.get('fail_reason', 'No reason given')}"
            )

    lines.append("")
    lines.append("TIMING SUMMARY:")
    lines.append("-" * 40)
    durations = [r["duration_seconds"] for r in all_results if "duration_seconds" in r]
    if durations:
        lines.append(f"Average call duration: {sum(durations)/len(durations):.1f}s")
        lines.append(f"Shortest: {min(durations):.1f}s")
        lines.append(f"Longest:  {max(durations):.1f}s")

    return "\n".join(lines)


def save_report(all_results: list, output_dir) -> str:
    """Write the report to a timestamped file. Returns the file path."""
    from pathlib import Path

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"report_{ts}.txt"
    path.write_text(build_report(all_results), encoding="utf-8")
    return str(path)
