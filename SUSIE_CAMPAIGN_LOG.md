# Susie Battle-Hardening — Campaign Log

Running log for the ~60-call sweep. **Sweep first, batch findings, fix by priority after.**
Companion: `SUSIE_BATTLE_HARDENING_PLAYBOOK.md` (scenarios) + `SUSIE_HANDOFF_JULES.md` (§8 regression IDs).

---

## Results log (one row per call)

| # | Date | Call SID | Scenario | PASS/FAIL | Landed surname | Findings | Latency note |
|---|---|---|---|---|---|---|---|
| 1 | 2026-07-16 | CA9d23…18e3 | BK-1 | PASS (caveats) | John Smith (not booked — hung up at confirm) | F-002 (price not spoken) | flags=**A** ⚠, perceived ttfa ≈2.0–2.4s, ~baseline |

---

## Findings tracker (batch — triage after the sweep)

| ID | Sev (draft) | Type | Scenario | Symptom | Root-cause guess | Status |
|---|---|---|---|---|---|---|
| F-001 | — (harness) | Setup / data hygiene | ALL calls | `[LAT]` shows `flags=A` → `WS_A_FAST_FIRST_CHUNK` is ON on the eval; every turn is contaminated, no clean `flags=-` baseline is being collected | Env var left ON from the earlier A/B; needs Render toggle + Manual Deploy | ⬜ open |
| F-002 | P3? (P2?) | Behaviour | BK-1 | Price/service/duration (**MSK initial, £52, 40 min**) never spoken to caller — not on routing, not at final "shall I book?" | Prompt doesn't require price/duration disclosure on booking; unclear if by design (cf. FQ-1) — needs Quentin ruling | ⬜ open |
| F-003 | P3 | Template smell | BK-1 (all calls) | STT `keyterms_prompt` still boosts `["Alcester","Redditch"]` (Theorem leftover) — internal, not spoken | Word-boost list hardcoded Theorem, not read from clinic.json | ⬜ open |
| F-004 | — (known) | Response length | BK-1 | Slot list ran ~18s continuous TTS | Known "biggest real lever" — out of scope until prompt change signed off | ⬜ noted |

**Severity key:** P1 = wrong booking / template leakage / safety miss · P2 = core flow breaks/no recovery · P3 = cosmetic/tone/minor. "—" = not a Susie code defect (harness/known).
