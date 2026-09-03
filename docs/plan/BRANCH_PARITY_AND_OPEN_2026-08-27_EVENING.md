# Four-branch parity + open defects · 27 Aug 2026 (evening)

Follows `FOUR_BRANCH_PORT_AUDIT_2026-08-27.md` (13:52). All four branches have
moved since that audit; this covers the delta and re-checks its open items.
Everything read at `origin/`, never locally — the primary worktree was **147
commits stale**.

| Branch | Audited head (13:52) | Head now |
|---|---|---|
| `latency-eval` | `39152c2c` | `996f0e02` |
| `jv_v2` | `e449791c` | `3604550d` |
| `vitaledge-onboarding` | `1815ffcd` | `ca94c751` |
| `theorem-onboarding` | `c63fec61` | `65b829a0` |

## 1. Delta parity — clean

| Commit | LE | JV2 | TH | VE |
|---|---|---|---|---|
| `fix(slots)` "the second day" answered about the first (B-105) | yes | yes | yes | yes |
| `fix(availability)` a day nobody looked at called fully booked | yes | yes | yes | yes |
| `fix(barge-in)` "Yes, go on." to a caller who said nothing (B-107) | yes | yes | yes | yes |
| `config(overflow)` twenty seconds of ringing | yes | yes | no | no |

`config(overflow)` touches **only `app/clinics/jv_v1/clinic.json`**. VE and
Theorem do not have that clinic. **Correctly divergent — do not port.**

Guard family verified present on all four (`_supersede_slot_map`,
`reconcile_readback_time`, `_SPOKEN_KEY`, `slot_starts_spoken`,
`times_found_on_day`, `days_found_in_window`, `_nothing_was_said`).
`days_found_in_window` shows 2 sites on LE/TH vs 1 on JV2/VE — that difference
is **a comment line only**; the emitting statement is on all four.

Stale headings corrected: **B-80 and B-81 read "FIXED on `latency-eval` only"
but their own bodies record the 24 Aug port to all three.** Verified present on
all four. B-90's three regression tests
(`test_choosing_a_slot_is_not_a_time_preference`,
`test_a_band_named_in_a_question_is_not_a_preference`,
`test_time_preference_noted_is_not_speech`) are on all four — its P1 heading in
`OPEN_DEFECTS_2026-08-22.md` is also stale.

## 2. The one substantive discrepancy: B-86's bare-weekday widen

`_WIDEN_WINDOW_DAYS` site counts: **LE 10, JV2 10, TH 3, VE 10** — and the
count is misleading on two of them.

| Branch | Content | Reachable | Why |
|---|---|---|---|
| `latency-eval` | 10 sites | yes | Google body |
| `jv_v2` | 10 sites | yes | `google_calendar` falls through to the Google body |
| `theorem-onboarding` | **3 sites** | n/a | the bare-weekday widen block is **absent**; only the constant and the explicit-window widen exist |
| `vitaledge-onboarding` | 10 sites | **NO** | `_exec_check_availability` **early-returns at line 5159** to `_check_availability_diary`; the widen block is at 5473+ |

`_check_availability_diary` (VE) has **no widen of its own**:

    day_window = args.get("day_window")
    w_end = (w_start + timedelta(days=int(day_window))) if day_window else horizon

**New this evening — B-105 raises VE's exposure.** B-105 pushes a named day to
`day_window: 1`. On VE that makes `w_end = w_start + 1 day` with no widen behind
it, so a bare weekday falling outside the one-day window returns nothing and
reads as "not available" for a day that has slots. Not reproduced live; the
mechanism is read from the code.

This is the audit's §2 and it remains the top port item. It is **not a
cherry-pick** on either branch — VE needs it written against the diary reader,
Theorem against the Acuity path.

## 3. Finding 3 (day-pick under-offering) — LATENT, not live

Settled by replaying the obs corpus (733 calls) plus driving the real
`_cap_presented_slots`.

**The mechanism is real, and it is not prompt-adherence** as first diagnosed —
it is a deterministic code path:

    per_day = 1 if len(kept) > 1 else _MAX_PRESENTED_TIMES_SINGLE_DAY   # 3

`multi_day` trims **every day to one spoken time**; `single_day` shows up to 3.
Mode is chosen by `_is_specific_day`, computed **only** from the hint text —
`day_window` never enters the decision:

    _is_specific_day = (_week_range is not None and _week_range[0] == _week_range[1]) \
                       or (_has_weekday_name and not _has_week_anchor)

`_week_range` is itself only computed when `_has_week_anchor`.

**Empirically unreached.** Of **375 day-pick turns across 733 stored calls**,
7 drew a `multi_day` reply — and in **all 7** the named day was genuinely empty
("fully booked, I'm afraid"), i.e. correct fill-forward, not under-offering.
Every other day-pick got a `single_day` readout in full, with the "a few others
that day" tail where truncated.

**Severity capped:** `_cap_presented_slots` does set top-level
`more_times: true` on the multi_day path, so the caller is told more exist.
(Per-day `more_times` stays unset — the known open item.)

**Why it is quiet:** B-105's `day_window: 1` collapses a day-pick payload to one
day, so `len(kept) > 1` is false and `per_day` is 3. That guard is incidental,
not designed — a day-pick that ever yields 2+ days with the named day among them
still under-offers.

**Verdict: open-but-quiet, do not fix blind.** The fix would force `single_day`
whenever `search_narrowed_to` is a single date.

## 4. Incidental finding: weekday/date pairing — was live, now fixed

Scanned all 2,343 "Weekday + date + Month" phrases in stored assistant turns:
**57 mismatched (2.4%) across 17 calls, all `jv_v1`.**

Worst case `CAd045bbc3fa` (23 Aug): offered "Number 2, **Tuesday 25th August**",
caller said "the second one", Susie confirmed "**Tuesday the 26th** of August" —
the date drifted a day while the weekday name held, then "The slot I have is
**actually** Tuesday the 26th".

Most instances are Twilio magic test numbers (`+4477009xxxxx`) on build
`c4b5b0c5`. The last real-handset instance is **build `e449791c`, 13:53 today**
("Wednesday 22nd September is fully booked" — 22 Sep is a Tuesday). On
`7cfc8425` at 15:31 the **same caller, same phrasing** got "Tuesday 22nd
September" with three slots. **Fixed by `7cfc8425`, deployed on all four.**
Zero mismatches on any build after it.

## 5. Still open — carried from the register, NOT re-verified today

P1: B-82 (escape hatch covers 1 of 10 arm sites), B-85 (options numbered for a
keypad that is never armed), A (a price question becomes a booking instruction),
O2 (which path orphans the TTS bytes — unpinned), P ("I'll take that as a yes").
B-84 is open **deliberately**.

P2: `chunk_gate_ms` dominant latency term; first-turn latency is a cold prompt
cache; 6–9.8 s of speech per answer; the prompt asks for phrases the gates
delete; press-1 buried in the greeting (**Theorem only**); barge-in teardown
ordering.

P3: service visibility unverified; `GOOGLE_SERVICE_ACCOUNT_JSON` malformed on
the latency-eval service; `TRANSFER_FALLBACK_NUMBER` a hardcoded personal
number; `EVAL_STAFF_SMS_TO` warns but cannot refuse; **dependencies unpinned**;
startup banner hardcoded "Theorem Health".

Branch-specific, from the 13:52 audit and still standing:

- **JV** — `session["confirmation_sms_sent"] = True` set unconditionally at
  `receptionist_tools.py:7251`: no confirmation text, no follow-up, log says
  "sent". Canonical's `c4b5b0c5` does **not** transplant (both JV call sites
  discard the return).
- **Theorem** — duplicated `minimum_age_years: 7` (`clinic_config.py:463` and
  `:488`).
- **VE + Theorem** — nine screening commits unported; **dormant**, not
  defective, because neither has screening config. Only port alongside enabling
  screening.
- **Hold-speech family** — canonical only, coherent as a set. Port all five or
  none.

## Recommended order

1. **VE bare-weekday widen**, written against `_check_availability_diary` —
   most exposed live path, and B-105 has raised the exposure.
2. **Theorem bare-weekday widen** on the Acuity path.
3. **JV `confirmation_sms_sent`** — its own fix, not a port.
4. Theorem duplicate `minimum_age_years`.
5. Finding 3 hardening (force `single_day` when `search_narrowed_to` is one
   date) — only if a day-pick is ever seen drawing 2+ days.

---

# Outcome, same evening

## Deployed

| Fix | Branches | Rollback |
|---|---|---|
| B-107 wordless barge-in | all four | th `72c685f8`, jv2 `c64c7fc8`, VE `58a92bf6` |
| B-86 diary widen | `latency-eval` `784518cf`, VE `6c8eac49` | VE `ca94c751` |

## Built, verified, NOT pushed — awaiting green light

| Branch | Commits | Suite |
|---|---|---|
| `latency-eval` | `dc2478e7` latch, `ab0b3638` Acuity widen | 115/6697 vs 115/6690; 114/6705 vs 114/6698 |
| `theorem-onboarding` | `a0256351` age, `9a301e5e` latch, `1ec52e26` Acuity+helper | 102/6156 vs 102/6142 (+14) |
| `jv_v2` | `36ad95e7` latch + booking_sms return | 115/6495 vs 115/6488 |
| `vitaledge-onboarding` | `4fc23481` latch + booking_sms return | 115/6146 vs 115/6139 |

Every pair taken back to back on its own tree; every failing set diffed
IDENTICAL; every delta is the new regression files.

## Three things found while doing it

1. **The latch defect had TWO sites, not the one the audit named.**
   `_exec_cancel_appointment` carries the identical shape.

2. **A pre-existing false claim on the Acuity path.** `_days_found` counts every
   day in the result, not days matching the named weekday, and
   `_filter_tuples_by_preference` silently drops a day filter matching nothing —
   so the bare-weekday guidance told the model N further dates "matching the
   requested day" had times, about a weekday with none. Hard to reach before
   (a 1-day scan bailed out above it); the widen makes it easy. Now guarded on
   `_weekday_found`.

3. **A clean cherry-pick that would have NameError'd on Theorem.** It had
   neither `_named_weekdays` nor `_WEEKDAY_NAME_TO_INDEX` — still the
   pre-refactor inline `day_map`. Git reported no conflict and the file
   byte-compiled; the widen sits inside a `try` whose `except` returns
   "Could not fetch availability", so it would have degraded every named-day
   call on Mark's line into a soft apology with no traceback. Helper ported and
   the inline copy repointed at it.

## Deliberately NOT ported

The Acuity widen is **not** going to `jv_v2` or `vitaledge-onboarding`.
`_check_availability_acuity` is gated on
`_gate_cid in ("theorem", "theorem_v2", "theorem_v3")`, so it is dead code on
both. Porting it would also have required inventing a resolution for a
bare-weekday guidance block neither branch has ever carried. Correctly
divergent — do not "fix".

Finding 3 hardening is still deliberately not done; see §3. It is latent, and
the fix should wait until a day-pick is actually seen drawing 2+ days.

