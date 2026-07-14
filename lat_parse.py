#!/usr/bin/env python3
"""
lat_parse.py — offline baseline parser for Susie latency-eval [LAT] lines.

Reads Render logs (or a pre-grepped [LAT] file, or stdin), extracts every
`susie.latency` [LAT] record, and prints the locked baseline table:
  - perceived TTFA + content TTFA  p50/p90/p95
  - llm_ttft / chunk_gate (WS-A) / tts_first_byte (WS-B) distributions
  - per-capture_phase breakdown
  - outcome mix + abandoned rate, filler-masked-turn count
  - tool/slot-buffer turns excluded from the split metrics

Everything is PII-free: it only ever touches the [LAT] fields.

Usage:
    python lat_parse.py call1.log call2.log call3.log
    grep LAT render.log | python lat_parse.py
    python lat_parse.py --json baseline.json  render.log      # also dump raw stats

Stats method: linear-interpolation percentiles (numpy 'linear' / type-7), so
numbers match np.percentile if you cross-check later.
"""
import sys
import re
import json
import math

# ---- LAT schema ------------------------------------------------------------
# A missing/not-applicable measurement is emitted as -1 and excluded per-metric.
INT_FIELDS = (
    "turn_seq", "ttfa_ms", "content_ttfa_ms", "ep_dispatch_ms", "llm_ttft_ms",
    "chunk_gate_ms", "tts_first_byte_ms", "audio_wire_ms", "endpoint_wait_ms",
)
STR_FIELDS = ("path", "outcome", "flags", "model", "stt_model", "eot_confident",
              "capture_phase")

LAT_RE = re.compile(r"\[LAT\]\s+(.*)")
LATEP_RE = re.compile(r"\[LAT-EP\]\s+(.*)")   # WS-C advisory cutoff lines
KV_RE = re.compile(r"(\w+)=(\S+)")


def parse_cutoff(line):
    """Parse a [LAT-EP] cutoff line -> {turn_seq, reason, capture_phase} or None."""
    m = LATEP_RE.search(line)
    if not m:
        return None
    rec = {}
    for k, v in KV_RE.findall(m.group(1)):
        rec[k] = int(v) if k == "turn_seq" else v
    return rec if "turn_seq" in rec else None


def parse_line(line):
    m = LAT_RE.search(line)
    if not m:
        return None
    rec = {}
    for k, v in KV_RE.findall(m.group(1)):
        if k in INT_FIELDS:
            try:
                rec[k] = int(v)
            except ValueError:
                rec[k] = None
        else:
            rec[k] = v
    return rec if "ttfa_ms" in rec or "turn_seq" in rec else None


def percentile(values, p):
    """Linear-interpolation percentile (numpy type-7). p in [0,100]."""
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    h = (len(xs) - 1) * (p / 100.0)
    lo = math.floor(h)
    frac = h - lo
    if lo + 1 < len(xs):
        return xs[lo] + frac * (xs[lo + 1] - xs[lo])
    return float(xs[lo])


def summarize(values):
    vals = [v for v in values if v is not None and v >= 0]
    if not vals:
        return None
    return {
        "n": len(vals),
        "min": min(vals),
        "p50": percentile(vals, 50),
        "p90": percentile(vals, 90),
        "p95": percentile(vals, 95),
        "max": max(vals),
        "mean": sum(vals) / len(vals),
    }


def fmt(s):
    if s is None:
        return "  n=0   (no data)"
    return (f"  n={s['n']:<3d} min={s['min']:>5.0f}  p50={s['p50']:>6.0f}  "
            f"p90={s['p90']:>6.0f}  p95={s['p95']:>6.0f}  max={s['max']:>5.0f}  "
            f"mean={s['mean']:>6.0f}")


def histogram(values, edges):
    """Simple bucket counts for a quick visual of chunk_gate spread."""
    counts = [0] * (len(edges) + 1)
    for v in values:
        placed = False
        for i, e in enumerate(edges):
            if v < e:
                counts[i] += 1
                placed = True
                break
        if not placed:
            counts[-1] += 1
    labels = []
    prev = 0
    for e in edges:
        labels.append(f"{prev}-{e}")
        prev = e
    labels.append(f"{prev}+")
    return list(zip(labels, counts))


def main():
    args = sys.argv[1:]
    json_out = None
    if "--json" in args:
        i = args.index("--json")
        json_out = args[i + 1]
        del args[i:i + 2]

    lines = []
    if args:
        for path in args:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines.extend(f.readlines())
    else:
        lines = sys.stdin.readlines()

    recs = [r for r in (parse_line(l) for l in lines) if r]
    cutoffs = [c for c in (parse_cutoff(l) for l in lines) if c]
    llm = [r for r in recs if r.get("path") == "llm"]
    if not llm:
        print("No [LAT] path=llm records found.", file=sys.stderr)
        sys.exit(1)

    completed = [r for r in llm if r.get("outcome") == "completed"]
    abandoned = [r for r in llm if r.get("outcome") == "abandoned"]
    other = [r for r in llm if r.get("outcome") not in ("completed", "abandoned")]

    # Filler-masked turns: content TTFA meaningfully exceeds perceived TTFA.
    masked = [r for r in completed
              if r.get("content_ttfa_ms", -1) >= 0 and r.get("ttfa_ms", -1) >= 0
              and r["content_ttfa_ms"] - r["ttfa_ms"] > 200]
    # Tool / slot-buffer turns: no first-chunk split emitted.
    tool = [r for r in completed if r.get("chunk_gate_ms", -1) < 0]

    # Metric pools — completed only; each metric drops its own -1s in summarize().
    pools = {
        "perceived_ttfa_ms": [r.get("ttfa_ms") for r in completed],
        "content_ttfa_ms":   [r.get("content_ttfa_ms") for r in completed],
        "llm_ttft_ms":       [r.get("llm_ttft_ms") for r in completed],
        "chunk_gate_ms":     [r.get("chunk_gate_ms") for r in completed],
        "tts_first_byte_ms": [r.get("tts_first_byte_ms") for r in completed],
        "ep_dispatch_ms":    [r.get("ep_dispatch_ms") for r in completed],
        # WS-C: endpoint silence before t0. Measured on ALL llm turns (incl.
        # abandoned) — it's upstream of the turn outcome.
        "endpoint_wait_ms":  [r.get("endpoint_wait_ms") for r in llm],
    }
    stats = {k: summarize(v) for k, v in pools.items()}

    total = len(llm)
    n_comp = len(completed)
    reliable = "OK (>=30)" if n_comp >= 30 else f"THIN (<30) — need {30 - n_comp} more"

    print("=" * 74)
    print("  SUSIE LATENCY-EVAL — BASELINE  (susie.latency [LAT])")
    print("=" * 74)
    print(f"  path=llm turns : {total}")
    print(f"  completed      : {n_comp}   ({reliable})")
    print(f"  abandoned      : {len(abandoned)}   (rate {len(abandoned)/total*100:.1f}% — excluded from TTFA)")
    if other:
        print(f"  other outcomes : {len(other)}  -> {sorted({r.get('outcome') for r in other})}")
    print(f"  filler-masked  : {len(masked)}  (content_ttfa >> perceived — WS-B filler working)")
    print(f"  tool/slot turns: {len(tool)}  (chunk_gate=-1 — no WS-A split on these)")
    print()
    print("  TIMING (ms)                    completed turns, per-metric -1s dropped")
    print("  " + "-" * 70)
    print(f"  perceived TTFA  (t4-t0)  {fmt(stats['perceived_ttfa_ms'])}")
    print(f"  content   TTFA  (unmasked){fmt(stats['content_ttfa_ms'])}")
    print(f"  llm_ttft                 {fmt(stats['llm_ttft_ms'])}")
    print(f"  chunk_gate  [WS-A]       {fmt(stats['chunk_gate_ms'])}")
    print(f"  tts_first_byte [WS-B]    {fmt(stats['tts_first_byte_ms'])}")
    print(f"  ep_dispatch              {fmt(stats['ep_dispatch_ms'])}")
    print(f"  endpoint_wait [WS-C]     {fmt(stats['endpoint_wait_ms'])}  (pre-t0 silence; all llm turns)")
    print()

    # WS-A histogram
    gates = [v for v in pools["chunk_gate_ms"] if v is not None and v >= 0]
    if gates:
        print("  chunk_gate distribution [WS-A proof]:")
        for label, c in histogram(gates, [200, 400, 600, 800, 1000]):
            bar = "#" * c
            print(f"    {label:>8} ms | {c:>2d} {bar}")
        share = stats["chunk_gate_ms"]["p50"] / stats["perceived_ttfa_ms"]["p50"] * 100
        print(f"    -> chunk_gate p50 is {share:.0f}% of perceived-TTFA p50 "
              f"({stats['chunk_gate_ms']['p50']:.0f}/{stats['perceived_ttfa_ms']['p50']:.0f} ms)")
        print()

    # Per-phase breakdown (perceived TTFA + chunk_gate p50)
    phases = sorted({r.get("capture_phase") for r in completed if r.get("capture_phase")})
    print("  BY capture_phase:")
    print(f"    {'phase':<14}{'n':>4}  {'ttfa p50':>9}  {'ttfa p90':>9}  {'gate p50':>9}")
    for ph in phases:
        grp = [r for r in completed if r.get("capture_phase") == ph]
        t = summarize([r.get("ttfa_ms") for r in grp])
        g = summarize([r.get("chunk_gate_ms") for r in grp])
        t50 = f"{t['p50']:.0f}" if t else "-"
        t90 = f"{t['p90']:.0f}" if t else "-"
        g50 = f"{g['p50']:.0f}" if g else "-"
        print(f"    {ph:<14}{len(grp):>4}  {t50:>9}  {t90:>9}  {g50:>9}")
    print()

    # WS-C: endpoint dead-time + cutoff rate, per phase (the two Phase-1 numbers)
    ep_phases = sorted({r.get("capture_phase") for r in llm if r.get("capture_phase")})
    cut_by_phase = {}
    for c in cutoffs:
        cut_by_phase[c.get("capture_phase", "?")] = cut_by_phase.get(c.get("capture_phase", "?"), 0) + 1
    ep_any = any(r.get("endpoint_wait_ms", -1) >= 0 for r in llm)
    print("  WS-C ENDPOINT (pre-t0 dead-time + cutoff rate), by capture_phase:")
    if not ep_any and not cutoffs:
        print("    (no endpoint_wait_ms / [LAT-EP] data — Phase-1 instrumentation not deployed yet)")
    else:
        print(f"    {'phase':<14}{'n':>4}  {'ep_wait p50':>11}  {'ep_wait p90':>11}  {'cutoffs':>8}")
        for ph in ep_phases:
            grp = [r for r in llm if r.get("capture_phase") == ph]
            e = summarize([r.get("endpoint_wait_ms") for r in grp])
            e50 = f"{e['p50']:.0f}" if e else "-"
            e90 = f"{e['p90']:.0f}" if e else "-"
            nc = cut_by_phase.get(ph, 0)
            rate = f"{nc}/{len(grp)} ({nc/len(grp)*100:.0f}%)" if grp else f"{nc}"
            print(f"    {ph:<14}{len(grp):>4}  {e50:>11}  {e90:>11}  {rate:>8}")
        print(f"    -> total [LAT-EP] cutoffs: {len(cutoffs)}  (advisory; confirm by listen-back)")
    print("=" * 74)
    print("  NOTE: barge-in rate is NOT a [LAT] field yet (barged_in outcome")
    print("  deferred). Count it from full logs via 'barge-in #N confirmed' if needed.")
    print("=" * 74)

    if json_out:
        blob = {
            "counts": {"llm": total, "completed": n_comp,
                       "abandoned": len(abandoned), "masked": len(masked),
                       "tool": len(tool)},
            "stats": stats,
        }
        with open(json_out, "w", encoding="utf-8") as f:
            json.dump(blob, f, indent=2)
        print(f"  wrote {json_out}")


if __name__ == "__main__":
    main()
