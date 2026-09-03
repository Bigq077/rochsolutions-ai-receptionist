# Port handover — night fixes → Theorem + Vital Edge · 2026-08-26

Prepared overnight. **Everything is staged. In the morning you run push commands
and nothing else.**

Source of the fixes: eight commits landed on `latency-eval` on 25 Aug and were
deployed to `jv_v2` the same night. This document ports the applicable subset to
`theorem-onboarding` and `vitaledge-onboarding`.

---

## 1. The decision, in one table

| # | Fix (canonical SHA) | Theorem | Vital Edge | Why |
|---|---|---|---|---|
| 1 | `e9de5eef` — `that_is_the_only` deleted the answer | ✅ PORT | ✅ PORT | Gate 5 in `turn_handler.py`; reads `available_days`, which every reader builds identically |
| 2 | `d4430ecd` — scarcity keep-log fired on turns with nothing to keep | ✅ PORT | ✅ PORT | Same gate. Log-only. Must follow #1 |
| 3 | `ecd7d60d` — the named weekday was never searched (widen door 1) | ❌ **N/A** | ❌ **N/A** | Lives in the Google-Calendar body of `_exec_check_availability`. Both branches **early-return before reaching it** |
| 4 | `28245401` — one Tuesday reported as every Tuesday (widen door 2) | ❌ **N/A** | ❌ **N/A** | Same body, same early return |
| 5 | `6213f19a` — Susie named a day that does not exist | ✅ PORT | ✅ PORT | 2 of its 3 files are live here; the third hunk is inert (see §4) |
| 6 | `6861fd8b` — multi-day readout never updated the offer record | ✅ PORT | ✅ PORT | `slot_followup.py`; driven by payload shape, not by booking backend |
| 7 | `3c3fe45c` — Susie heard herself and answered | ✅ PORT | ✅ PORT | Pure media path in `connection.py`; no clinic coupling |
| 8 | `f4a09b83` — resolve each option's time inside the day it names | ✅ PORT | ✅ PORT | `slot_followup.py` |

**6 of 8 port. 2 are genuinely N/A.**

---

## 2. Why #3 and #4 are N/A — verified in code, not assumed

`_exec_check_availability` dispatches in this order:

```
1. clinic_id in ("theorem","theorem_v2","theorem_v3")  -> _check_availability_acuity   -> RETURN
2. booking_system == "google_calendar_provisional"      -> _check_availability_diary
                                                           / _published                -> RETURN
3. otherwise                                            -> falls through to the
                                                           Google-Calendar body
```

Confirmed config:

* Theorem line `+447380841468` resolves to `clinic_id = theorem_v3` -> **arm 1**.
* `vital_edge` is `booking_system: google_calendar_provisional`,
  `availability_mode: diary` -> **arm 2**.

Both return before the Google-Calendar body. `requested_day_empty` on both
branches sits *inside* that body (theorem line 4981, VE line 5213), so it is
unreachable too.

> ### CORRECTION — 26 Aug, after live call `CA7d38fb42`
>
> This section originally concluded "the weekday guard needs no equivalent
> stash on those readers." **That was wrong**, and a Theorem call proved it
> within four minutes:
>
> ```
> caller: "have you got anything on wednesday the 27th ... so tomorrow"
> tool:   week filter 2026-08-27 to 2026-08-27 — 0 days returned
>         {"error": "no_availability", "slots": []}
> Susie:  "Nothing on Wednesday the 27th I'm afraid — the next I have is
>          Friday the 28th of August at two in the afternoon"
> ```
>
> **27 August 2026 is a THURSDAY.** The caller said Wednesday; Susie repeated
> it. That is the B-87 shape the weekday guard exists to prevent.
>
> The guard did not fire because it had nothing to check against: the
> `no_availability` return carries `"slots": []` and **no `available_days`**,
> and `requested_day_iso` is only written in the Google-Calendar body, which
> Theorem never reaches. Deny-by-default worked as designed — it simply had an
> empty known-dates map.
>
> **Not a regression** — before the port Theorem had no guard at all and would
> have said the same thing. It is an INCOMPLETE fix, and the "inert hunk" in §4
> turns out to have a live equivalent that is missing here.
>
> **Fix needed:** stash the requested date into the session on the Acuity
> no-availability path (and the diary equivalent). The date is known to the tool
> — it is printed in `error_detail` as "on 27 August 2026" — it just never
> reaches the session where the guard can see it.

---

## 3. Why the other six DO apply — the payload shape is identical

This was the crux. All three readers build their result with the **same
`_build_days_data`** (line 221 on both branches), emitting:

```
date · day_label · slot_times · slot_times_spoken · slots
```

That is exactly what the six ported fixes consume:

* `_scarcity_claim_is_supported` reads `available_days[0].slot_times`
* `_correct_weekday_against_known_dates` reads `available_days[].date`
* `flatten_bookable_slots` reads `slot_times` / `slot_times_spoken` / `slots` / `date` / `day_label`

So they function on Acuity and diary output exactly as on Google Calendar.

---

## 4. One inert hunk, deliberately kept

`6213f19a` writes `session["requested_day_iso"]` inside the Google-Calendar
`requested_day_empty` branch. On these two branches that line is **dead code** —
harmless, never executed.

It was kept rather than excluded so `receptionist_tools.py` stays aligned across
branches. Divergence in that file is what makes every future port painful, and
excluding a hunk to avoid three dead lines is a bad trade.

---

## 5. What was NOT fixed and is NOT hidden

The two N/A fixes are unreachable code — but the **defect class they address is
mitigated, not proven absent**, on these branches.

`_filter_tuples_by_preference` silently discards a weekday filter that matches
nothing, and it is called from `_build_days_data` and `_select_presented_tuples`
— which **every** reader uses. What differs is the search horizon:

| Reader | Default window | Occurrences of each weekday | Risk |
|---|---|---|---|
| Acuity (Theorem) | **30 days** | ~4 | Low — filter rarely empties |
| diary (VE) | full booking horizon, capped | several | Low |
| Google Calendar (JV) | **7 days** | exactly 1 | This is why JV was hit |

Both narrow to a single day only when the model passes `day_window=1`, which is
the exact shape that produced the JV defect.

**Recommendation:** do not fix tonight. Log it as a follow-up to reproduce
against each reader before writing anything — the JV fix does not transplant,
each reader needs its own.

---

## 6. Verification performed

* Cherry-picks applied **clean, no conflicts**, with diffstats matching the
  canonical commits exactly on both branches.
* Verified by **content grep, not `git log`** — 15 markers per branch, all
  present (`git cherry` gives false answers on this repo).
* Theorem's two `put(_ack)`-counting tests (`test_location_ack_reaches_the_model`,
  `test_reschedule_flow_is_model_driven`) **pass** — the echo fix queues
  `put(_interrupted_now)`, which deliberately does not match that family.
* Ported regression files pass on both branches.
* Full suites measured against **each branch's own baseline, taken today**
  (a baseline is only valid for the date it was taken — see §8):

| Branch | Baseline (today) | With ports | Failing set |
|---|---|---|---|
| `theorem-onboarding` | 101 failed / 5818 passed | **101 failed / 5888 passed** | byte-identical, md5 `356af469` |
| `vitaledge-onboarding` | 103 failed / 5810 passed | **103 failed / 5880 passed** | byte-identical, md5 `40acb832` |

**Zero new failures, zero fixed-by-accident, +70 passing on each** — exactly the
tests the six commits carry. Every run bracketed by `md5sum` on the three most
edited files, identical either side.

Cross-check worth noting: `theorem-onboarding`'s failing set is byte-identical
to `latency-eval`'s, and `vitaledge-onboarding`'s to `jv_v2`'s. The four
branches fall into two consistent failing-set families, which is what you would
expect and is a good sign that no branch has drifted into its own private
breakage.

---

## 7. DEPLOY — the only thing left to do

Both branches serve live clinics and have `autoDeploy` on, so each push is a
deploy. Push them **one at a time** and confirm the build before the next.

### Theorem — Mark, `+447380841468`

```bash
git push origin port/night-fixes-theorem:theorem-onboarding
```

Head `f05c59f7` · 6 commits on top of `4c16e7dd`.

### Vital Edge — Jonathan, `+447426779875`

```bash
git push origin port/night-fixes-vitaledge:vitaledge-onboarding
```

Head `324174dc` · 6 commits on top of `1ae6e80c`.

Both commands run from the repo root and use branch refs, so they do NOT depend
on the overnight worktrees still existing.

**Deploy proof** is `[build_info] running build <sha>` in the Render log at call
cleanup. `/health` returns a hardcoded `1.0.0` and will lie to you.

### Rollback

Each branch gets six commits. To back a whole branch out:

```bash
# Theorem
git revert --no-commit f05c59f7 c6d0eddc f97a954f 9d68670b d000e486 29b2e14f

# Vital Edge
git revert --no-commit 324174dc 3d604d93 35cc7057 3513333a c59f1562 17a0ac9c
```

Or roll a single fix back by reverting just its SHA — they are independent
except that `d000e486` / `c59f1562` (the log fix) assumes `29b2e14f` /
`17a0ac9c` (the scarcity guard) is present.

---

## 8. Two traps that cost time last night

**Baselines expire at midnight.** The suite moved 100 -> 101 on `latency-eval`
and 102 -> 103 on `jv_v2` with *no code change* when the date rolled;
`test_absence_is_not_unavailability` builds `date.today() + 9 days`. Both
baselines here were taken **today**, against each branch's own parent. Never
reuse yesterday's number.

**Never edit a file while its suite is running.** A comment-only edit mid-run
reddened six `inspect.getsource` tests in files the change never touched, and it
reads exactly like a regression. All four runs here were bracketed by `md5sum`.
