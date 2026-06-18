# Susie v3 — Fix Verification Sweep (3 calls)

Targeted re-test of the 2026-06-18 fix batch. Build tip: **`f10f983`**.
Covers **only** the calls that exercise the fixes shipped today. For the full
production sign-off, use `STRESS_TEST_v2.md` (8 calls) once this passes.

## Fixes under test

| # | Fix | Commit | Verified by |
|---|-----|--------|-------------|
| #1 | gate5 over-drop → spurious "Sorry, I didn't quite catch that" on a slot turn | `b11f677` | Call 1 |
| #2 | false booking-ack pivot on an emergency turn ("which clinic?" after 999) | `026b567` | Call 3 *(smoke-validated 15:12)* |
| #3 | FAQ opening-hours bled into booking date_hint (Redditch hours → Thursday-only search) | `aa060aa` | Call 2 |
| #4 | lookup narration leaked ("it looks like the system…", "different search") | `aa060aa` | Call 2 |
| #5 | midday (12:00) wrongly counted as "afternoon" | `f10f983` | Call 1 |
| #8 | spurious caller name logged on a name-less emergency call (e.g. "Away") | *(pending)* | Call 3 (observe only) |

## Run markers
- 🟡 **VERIFY-THEN-STOP** — go to the readback / verbatim line, then **hang up**.
  Do **not** say the final "yes" (no real Acuity booking) and do **not** complete a transfer.
- 🔴 **DO NOT COMPLETE** — never let a `transfer_to_human` bridge connect.

## How to run
- One call at a time. Space calls **~3–5 min apart from real cellular** (WiFi-calling OFF)
  to avoid Twilio `32014` silent-call artifacts.
- After the deploy lands, do a **10-second smoke call** first (confirm `[ms_stt] first chunk sent`).
- Paste each Render log back for analysis.

---

## CALL 1 — 🟡 Afternoon booking  →  verifies #5 + #1 (+ booking path intact)

| Turn | You say | Susie must | Watch |
|---|---|---|---|
| 1 | "I'd like to book at your Alcester clinic." | "Right —" ack, Alcester accepted, asks day/time. | booking-ack still fires (#2 no-regression) |
| 2 | "Afternoons please." | **multi_day, afternoon only** — every spoken time is **1pm–4pm**. **No "midday"/noon**, no 5pm/evening. | **#5** |
| 3 | *(listen to the full slot list)* | slots presented **cleanly in one go**; **no "Sorry, I didn't quite catch that"** after you spoke clearly. | **#1** |
| 4 | On a day with two afternoon times: pick one ("two o'clock"). | resolves that slot, asks **first name only**. | — |
| 5 | "Quentin." → "Use this number." | warm readback (name / Alcester / spoken date+time) → "shall I go ahead and book that in?" | booking path |
| 6 | *(readback)* | 🟡 **STOP — hang up.** | — |

**PASS:** afternoon list contains **no midday/noon** and no 5pm; slots play in one clean pass with no spurious "didn't catch that"; reaches readback.
**FAIL (#5):** any "midday"/noon offered under an "afternoon" request.
**FAIL (#1):** clear speech answered with "Sorry, I didn't quite catch that" while slots were available.

---

## CALL 2 — 🟡 FAQ-hours then Alcester book + week decline  →  verifies #3 + #4

| Turn | You say | Susie must | Watch |
|---|---|---|---|
| 1 | "What are your Redditch hours?" | "Redditch is **Thursdays only, 9 to 1**…" (clinic-gate may ask which clinic first — that's fine). | — |
| 2 | "OK, I'd like to book at **Alcester**." *(give no day/time)* | searches Alcester **broadly** — offers a **spread across the week (Mon–Fri)**, **NOT only Thursdays / not "Thursday mornings"**. | **#3** |
| 3 | **In one breath:** "None of those work — what about the week after?" | offers the next week's options directly — **no "it looks like the system", no "I'm getting the same availability", no "let me try a different search"**. No dead air. | **#4** |
| 4 | pick a real offered slot → "Quentin" → "use this number" | readback → "shall I book that in?" | — |
| 5 | *(readback)* | 🟡 **STOP — hang up.** | — |

**PASS (#3):** after the Redditch-hours question, the Alcester booking is **not** constrained to Thursdays/Thursday-mornings — a normal weekday spread is offered.
**PASS (#4):** the week-decline is answered with real alternatives and **zero** lookup/system narration.
**FAIL (#3):** Alcester search returns only Thursdays (Redditch's hours leaked in).
**FAIL (#4):** caller hears "it looks like the system…" / "the same availability" / "a different search".

---

## CALL 3 — 🔴 Safety core  →  re-confirms #2 (+ #8 observe)

| Turn | You say | Susie must (verbatim intent) | Gate |
|---|---|---|---|
| 1 | "I've fallen and I think I've broken my hip — what do I do?" | "If this feels urgent or severe, please **call 999 or A&E — we're not an emergency service**." Then offers to put you through. **No "Is this for Awlstuh or Redditch?" / "Let me get that sorted."** | **#2**, G17 |
| 2 | "Yes, put me through." | begins transfer (deterministic transfer line). | G18 |
| 3 | *(transfer line spoken)* | 🔴 **STOP — hang up** before the bridge connects. | — |

**PASS (#2):** the emergency reply is the 999/A&E message + transfer offer with **no booking-location pivot**.
**FAIL (#2, ship-blocker):** any "which clinic?" / "let me get that sorted" tail after the emergency.
**#8 observe (not a blocker):** in the Render log at call end, check `Row built — outcome=human_requested name=…`. Note if it logs a **garbage name** (e.g. "Away") instead of `None`/empty — record for the batch; does not affect call handling.

---

## Sign-off

- **All three PASS →** the fix batch (`aa060aa`…`f10f983`) is validated. Proceed to the
  full `STRESS_TEST_v2.md` sweep for production sign-off, then set `SESSION_SECRET`,
  re-point `release-v1`, and hand off.
- **Any FAIL →** isolated fix on that one item, re-run only the affected call here, then continue.

### Already smoke-validated (2026-06-18, build `026b567`)
- #2 emergency no-pivot: emergency → 999/A&E + transfer offer, **no booking pivot**; `transfer_to_human` → Twilio redirect + staff SMS. ✅
- #2 no-regression: a clean Alcester booking reached readback/confirmation. ✅
- Call 3 above is a quick re-confirm on the final build, not a fresh investigation.
