# Google Sheets + booking-id audit — 2026-08-17

Done offline (phone unavailable). Sources: `app/tools/handoff.py`,
`app/tools/actionable_summary.py`, the live Theorem Sheet (read-only), and the
obs store (280 calls).

**Headline: Sheets is working on Theorem, and the booking-id instrumentation is
sound. The one open risk is a config flag I cannot see from here.**

---

## 1. Two Sheets modules — only one is live

| Module | Status |
|---|---|
| `app/integrations/sheets.py` (`append_row`, `safe_append_row`) | **dead — zero callers** |
| `app/tools/handoff.py` (`fire_and_forget_append_summary_row`) | **live** — used by `connection.py` at call cleanup |

Same trap as the two SMS template modules: editing `integrations/sheets.py`
changes nothing on a call. `safe_append_row` there also swallows every exception
into a returned string that nobody reads — harmless only because it is dead.

## 2. The real risk — `SHEETS_ENABLED` defaults to **false** on every live branch

`app/tools/handoff.py:27`

```python
SHEETS_ENABLED = os.getenv("SHEETS_ENABLED", "false").strip().lower() == "true"
```

Its own comment says: *"Do NOT port this default to main/theorem/jv live
branches."* **It was ported anyway.** Confirmed identical on `latency-eval`,
`jv_v2`, `theorem-onboarding` and `vitaledge-onboarding`. Only `main` has no
gate.

So a live clinic writes **no call record at all** unless `SHEETS_ENABLED=true`
is set in that service's Render env. It fails silently by design — the only
signal is a `WARNING` line per call.

| Clinic | Writing to its Sheet? | How I know |
|---|---|---|
| Theorem | ✅ **yes** | rows landing, most recent dated **17 Aug** |
| JV | ❓ **unverified** | its `GOOGLE_SHEETS_ID` is not in the local `.env` |
| Vital Edge | ❓ **unverified** | same |

**Action:** confirm `SHEETS_ENABLED=true` in the Render env for the JV and Vital
Edge services. That is a dashboard check, not a code change.

## 3. Blank rows in the Sheet — already fixed, no action

Rows 73–76 of `CallSummaries` have 14 of 15 columns empty, only the JSON blob
populated. All four are `outcome: "no_audio"`, dated **7 Aug**.

Rebuilding a row from that same stored blob with today's
`build_actionable_summary_row` produces a **complete** row —
`Summary='Anonymous caller – no_audio'`, `Outcome='no_audio'`, `Phone`, `Date`,
`Duration`, `SID` all present. So the builder that produced the blanks has
already been fixed. Historical, not live.

> Two wrong theories I had and discarded — recorded so nobody re-derives them:
> **"Sheets is dead"** (the last *rows* are 7 Aug, but the last *populated* row
> is a Google serial date, `46251.29792` = **17 Aug** — the sheet is not in
> chronological order); and **"the row builder drops known fields"** (it does
> not; proved by rebuilding).

## 4. The `calendar_event_id` → `acuity_booking_id` "switch" is not a switch

It is per-clinic by provider, and looked like a date change only because of who
called on which day.

| Clinic | Calls | `calendar_event_id` | `acuity_booking_id` | `booking_confirmed` |
|---|---|---|---|---|
| `jv_v1` | 214 | 65 | 0 | 65 |
| `theorem_v3` | 39 | 0 | 10 | 10 |
| `vital_edge` | 27 | 3 | 0 | 0 |

**Zero calls have `booking_confirmed=true` with no id recorded.** The
instrumentation is internally consistent: every confirmed booking carries an id,
so "did it land?" *is* answerable from the data for confirmed bookings.

One oddity, low priority: Vital Edge has 3 event ids but `booking_confirmed=0`.
VE books **provisionally**, so this is probably correct by design — but it means
a VE booking cannot be counted by that column.

## 5. What this does not tell you

`booking_confirmed=false` with no id does **not** distinguish *"no booking was
attempted"* from *"the booking was lost"*. Roughly two-thirds of calls are in
that state, which is consistent with an ordinary booking rate. Only the diary
separates them — which is why the call sheet says check the diary, not the
read-back.
