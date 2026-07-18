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
| 2 | 2026-07-17 | CA4bdd7ffd57587098f58958d3764d7453 | BK-2 (booking, returning patient) | PASS (caveats) | 9 (7 completed, 2 abandoned) | [2026-07-17_BK-2_CA4bdd7f.redacted.txt](2026-07-17_BK-2_CA4bdd7f.redacted.txt) |
| 3 | 2026-07-17 | CA173cd301882309b1c1df2fbdbbc4887b | BK-3 (booking, over video) | PASS routing | 5 | [2026-07-17_BK-3_CA173cd3.redacted.txt](2026-07-17_BK-3_CA173cd3.redacted.txt) |
| 4 | 2026-07-17 | CAf25ef9afa3615603f76b572b9f5e57fb | BK-4 (acupuncture) | PASS routing | 6 | [2026-07-17_BK-4_CAf25ef9.redacted.txt](2026-07-17_BK-4_CAf25ef9.redacted.txt) |
| 5 | 2026-07-17 | CAb9a82abef0e8aa21476e68483a26a243 | BK-5 (sports massage, "the hour one") | PARTIAL (F-008) | 9 | [2026-07-17_BK-5_CAb9a82a.redacted.txt](2026-07-17_BK-5_CAb9a82a.redacted.txt) |
| 6–10 | 2026-07-17 | *(pending batch)* | BK-6…BK-10 | see `SUSIE_CAMPAIGN_LOG.md` | — | ⏳ redaction pending — raw in chat transcript |

> **Archive completeness:** redacted logs committed for **BK-1…BK-5**. BK-6…BK-10 are fully
> scored in `SUSIE_CAMPAIGN_LOG.md`; their redacted logs are queued for the next batch pass
> (raw text preserved in the working session). Note: BK-3 & BK-4 captures were `cleanlog`-v1
> filtered (Susie's spoken TTS wording not retained); BK-5+ retain wording.

## Accuracy notes (per call)

- **#1 BK-1** — Routing correct (new → `msk_initial_assessment`, bolton). Slot select, name
  capture (first+surname, first-name-only readback), phone, confirmation all correct. Hung up
  before final "yes" (verify-then-stop → no booking side-effect). Caveats logged in
  `SUSIE_CAMPAIGN_LOG.md`: F-001 (`flags=A` — WS-A lever contaminated the latency data),
  F-002 (price/service/duration never spoken). Perceived TTFA ≈ 2.0–2.4s (≈ locked baseline).
- **#2 BK-2** — Returning patient correctly routed to `msk_treatment_session` (follow-up, NOT
  the £52 initial). Split name "john"/"smith" back-filled to John Smith; barge-in re-present
  clean; "anytime" accepted (T8). Hung up at confirm → outcome=abandoned, no side-effect.
  Caveats: F-002 (reconfirmed 2/2), F-005 (phone-confirm fragmentation, 2 abandoned turns),
  F-006 (endpoint_wait=-1 on single-word name/phone finals). flags=A still.
- **#3 BK-3** — "over video" → `virtual_appointment`/`remote` routing correct. Clean name/phone/
  verify-then-stop. Wording not captured (F-007, filter v1). `turn_seq` is per-worker, not per-call.
- **#4 BK-4** — "book acupuncture" → `service=acupuncture` correct. "last slot" resolved (SL-5);
  watchdog re-ask on slow reply; **DTMF 11-digit phone capture ✓ (PH-2)**. Wording not captured (F-007).
- **#5 BK-5** — "the hour one" sports massage → routing `sports_massage` ✓ **but 60min/£55 length
  not reflected in tool args or wording (F-008 — needs verify)**. 3 abandoned turns (F-012 slot
  friction). "first slot" resolved; "That's already noted" recovery ✓.
- **#6–10** — scored in `SUSIE_CAMPAIGN_LOG.md`. Highlights: **BK-9 corticosteroid E4 safety PASS**
  (launching-soon, never booked, waitlisted for Marcus, T7 safety net verified); **F-009 surname-drop
  reproduced** (BK-6, BK-10); **F-010 home-visit booked with no address** (BK-7); same-day ✓ (BK-10).

---

*Add one row + one note per reviewed call. Drop the raw log in `logs/raw/`, regenerate the
redacted copy, link it here.*
