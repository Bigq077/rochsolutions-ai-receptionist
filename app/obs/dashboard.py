"""
app/obs/dashboard.py
--------------------
Internal read-only trend dashboard over the `calls` table (spec §5.5).

    python -m app.obs.dashboard [--weeks 8] [--clinic theorem] [--html out.html]

Prints volume, booking rate, mean quality score, and top failure tags, sliced by
clinic and ISO week. Deliberately a CLI (+ optional static HTML export) rather than
a web route — it adds nothing to the live FastAPI app. Internal-only; no client view.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import config
from app.obs import reports, store


def _fmt_score(v: Optional[float]) -> str:
    return f"{v:.2f}" if isinstance(v, (int, float)) else "—"


def _top_tags(tags: Dict[str, int], n: int = 3) -> str:
    if not tags:
        return ""
    return ", ".join(f"{k}×{v}" for k, v in list(tags.items())[:n])


def render_text(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "(no calls in range)"
    header = f"{'clinic':<14} {'week':<9} {'vol':>4} {'book%':>6} {'mean':>5}  top failure tags"
    lines = [header, "-" * max(len(header), 60)]
    for r in rows:
        lines.append(
            f"{(r['clinic_id'] or '')[:14]:<14} {r['week']:<9} {r['volume']:>4} "
            f"{r['booking_rate'] * 100:>5.0f}% {_fmt_score(r['mean_quality_score']):>5}  "
            f"{_top_tags(r['failure_tags'])}"
        )
    return "\n".join(lines)


def render_html(rows: List[Dict[str, Any]], generated: str) -> str:
    cells = []
    for r in rows:
        cells.append(
            "<tr>"
            f"<td>{escape(str(r['clinic_id'] or ''))}</td>"
            f"<td>{escape(r['week'])}</td>"
            f"<td class='n'>{r['volume']}</td>"
            f"<td class='n'>{r['booking_rate'] * 100:.0f}%</td>"
            f"<td class='n'>{_fmt_score(r['mean_quality_score'])}</td>"
            f"<td>{escape(_top_tags(r['failure_tags'], 5))}</td>"
            "</tr>"
        )
    body = "\n".join(cells) or "<tr><td colspan='6'>(no calls in range)</td></tr>"
    return f"""<!doctype html>
<meta charset="utf-8"><title>Susie — call quality</title>
<style>
 body{{font:14px system-ui,sans-serif;margin:2rem;color:#111;background:#fff}}
 @media (prefers-color-scheme:dark){{body{{color:#eee;background:#111}}}}
 h1{{font-size:1.1rem}} table{{border-collapse:collapse;width:100%;max-width:820px}}
 th,td{{padding:.4rem .6rem;border-bottom:1px solid #8884;text-align:left}}
 td.n,th.n{{text-align:right;font-variant-numeric:tabular-nums}}
 caption{{caption-side:bottom;color:#8888;font-size:.8rem;margin-top:.5rem}}
</style>
<h1>Susie — call quality by clinic &amp; week</h1>
<table>
<thead><tr><th>clinic</th><th>week</th><th class="n">vol</th><th class="n">book%</th>
<th class="n">mean</th><th>top failure tags</th></tr></thead>
<tbody>
{body}
</tbody>
<caption>Internal — generated {escape(generated)}</caption>
</table>
"""


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.obs.dashboard")
    parser.add_argument("--weeks", type=int, default=8, help="lookback window in weeks")
    parser.add_argument("--clinic", default=None, help="filter to one clinic_id")
    parser.add_argument("--html", default=None, help="also write a static HTML file")
    args = parser.parse_args(argv)

    if not config.DATABASE_URL:
        print("ERROR: DATABASE_URL not set — no store to report on.", file=sys.stderr)
        return 2

    since = datetime.now(timezone.utc) - timedelta(weeks=args.weeks)
    calls = store.list_calls(since=since, clinic_id=args.clinic)
    rows = reports.by_clinic_week(calls)

    print(f"Susie call quality — last {args.weeks} weeks"
          + (f" (clinic={args.clinic})" if args.clinic else "") + "\n")
    print(render_text(rows))

    if args.html:
        generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        Path(args.html).write_text(render_html(rows, generated), encoding="utf-8")
        print(f"\nWrote {args.html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
