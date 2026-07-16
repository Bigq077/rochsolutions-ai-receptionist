# Susie Call Archive — accuracy stats

Archive of every call log we've reviewed during the battle-hardening campaign, kept for
accuracy tracking. **Committed copies are REDACTED** (caller MSISDN + captured names
pseudonymized) per the repo's GDPR rule (harness §9 / handoff §9). Unredacted source-of-truth
logs are retained locally under `logs/raw/` (gitignored — never committed/pushed).

**PII policy:** the `[LAT]`/`[LAT-EP]` timing lines are PII-free by design. Everything shared
upward should come from the redacted `.txt` files, not `logs/raw/`. (Redacted archives use a
`.txt` extension on purpose — the repo's `.gitignore` blocks `*.log`, so raw logs can never be
committed by accident.)

---

## Index

| # | Date | Call SID | Scenario | Verdict | Turns (path=llm) | Redacted log |
|---|---|---|---|---|---|---|
| 1 | 2026-07-16 | CA9d2343714dee431f87b7b871356218e3 | BK-1 (booking, new patient, in-clinic) | PASS (caveats) | 5 | [2026-07-16_BK-1_CA9d2343.redacted.txt](2026-07-16_BK-1_CA9d2343.redacted.txt) |

## Accuracy notes (per call)

- **#1 BK-1** — Routing correct (new → `msk_initial_assessment`, bolton). Slot select, name
  capture (first+surname, first-name-only readback), phone, confirmation all correct. Hung up
  before final "yes" (verify-then-stop → no booking side-effect). Caveats logged in
  `SUSIE_CAMPAIGN_LOG.md`: F-001 (`flags=A` — WS-A lever contaminated the latency data),
  F-002 (price/service/duration never spoken). Perceived TTFA ≈ 2.0–2.4s (≈ locked baseline).

---

*Add one row + one note per reviewed call. Drop the raw log in `logs/raw/`, regenerate the
redacted copy, link it here.*
