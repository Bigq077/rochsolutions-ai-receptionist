# Four-branch port audit · 2026-08-27

Audits `latency-eval` (canonical) against the three live deployment branches.

> **REVISED after owner review, same day.** Two findings in the first draft were
> wrong in opposite directions. §1 was **downgraded** — VE and Theorem have no
> screening config, so the classifier gap is dormant, not a live P1. §2 was
> **upgraded and re-aimed** — the named-weekday defect is not Theorem-specific,
> and **Vital Edge is the most exposed of the three**, not the least. The method
> note in §0 explains how the first draft got §2 wrong.

**All four branches audited at `origin/`, not locally.** Local refs were 145–248
commits stale in this worktree.

| Branch | Audited head |
|---|---|
| `latency-eval` | `39152c2c` |
| `jv_v2` | `e449791c` |
| `vitaledge-onboarding` | `1815ffcd` |
| `theorem-onboarding` | `c63fec61` |

**53 of 62 canonical `app/` commits are at 84–100% on all four branches.** The
convergence is real. What follows is the residue.

---

## 0. Method, and its one blind spot

Every canonical commit since 18 Aug touching `app/` was fingerprinted by its own
added lines (comments and short lines stripped), then matched against the *file
content* of all four branches. Subject-line and `git cherry` comparisons were
both run first and both proved unusable — subjects over-report, and `git cherry`
gives false answers on this repo.

**The blind spot: content presence is not reachability.** A cherry-pick can land
a fix in a function the branch never calls. `PORT_HANDOVER_2026-08-26.md` §4
flagged this deliberately ("one inert hunk, deliberately kept"), and the first
draft of this audit walked straight into it — scoring `vitaledge-onboarding`
98–100% on the weekday chain when the code sits in a body VE early-returns
before reaching. **Every finding below has had its enclosing function traced and
its dispatch path checked.**

The three availability readers and what dispatches to them, in
`_exec_check_availability`:

| Branch | Clinic config | Dispatches to | Falls through to Google body? |
|---|---|---|---|
| `jv_v2` | `google_calendar` | — | **yes** |
| `vitaledge-onboarding` | `google_calendar_provisional`, `availability_mode: diary` | `_check_availability_diary` (line 5159) | no |
| `theorem-onboarding` | `clinic_id` in `theorem*` | `_check_availability_acuity` | no |

---

## 1. Screening — dormant on VE and Theorem, not defective  (DOWNGRADED)

**`app/media_streams/clinical_screening.py` is 1426 lines on `latency-eval` and
`jv_v2`, 1117 on VE and Theorem.** Nine screening fixes that reached `jv_v2` as
`3fe8d16` were never ported onward, and the classifier there returns **three
verdicts, not four** — there is no `hedged` outcome.

**This is not a live defect.** Neither branch has a `clinical_screening` config
block:

* `app/clinics/vital_edge/clinic.json` — no `clinical_screening` key.
* Theorem's live config is `clinic_config.py` — no `clinical_screening` anywhere.
* The only file carrying the block on either branch is `app/clinics/jv_v1/clinic.json`,
  which is not the clinic those services serve.

`update_screening_state()` opens with `if not screening_enabled(clinic): return
{"action": "none", "speak": None}`, and `screening_enabled` requires the block
**and** `enabled: true`. The deterministic layer never runs on either line.

**What it does mean:** the port is a **prerequisite**, not a cleanup. If
screening is ever switched on for Vital Edge or Theorem, the classifier that
would run is the old one. Measured behaviour of that classifier, both versions
loaded side by side and run against a cauda-equina-shaped screen:

| answer | LE / jv_v2 | VE / Theorem, *if enabled* |
|---|---|---|
| "I don't know" | `hedged` | **`clear`** |
| "er yeah I do" | `red_flag` | `unclear` |
| "maybe" / "a bit" | `hedged` | `unclear` |
| "yes" / "no" | `red_flag` / `clear` | same |

The `clear` row is the one that matters: `_NEGATIVE_PATTERNS` contains `"i don't"`,
correct for *"I don't have any numbness"* and fatal for *"I don't know"*.
Canonical tests `_UNSURE_PHRASES` **before** the negative patterns.

**Action: none now.** Port the nine commits *as part of* enabling screening on
either clinic, never after.

---

## 2. The named-weekday defect is NOT Theorem-specific  (RE-AIMED)

The defect: a caller names a weekday, the search window contains only occurrences
of it that are full, `_filter_tuples_by_preference` **silently discards a day
filter that matches nothing** rather than returning empty, other days are
presented in its place, and the model reads the gap as clinic state —
*"Tuesday isn't available at the moment, I'm afraid."* Found on `jv_v1`,
24 Aug 2026, where Tuesday 1 September had four free slots one day past the
window.

`_filter_tuples_by_preference` is **shared by every reader** (called from
`_build_days_data` and `_select_presented_tuples`). The mechanism therefore
exists on all three branches. What differs is the horizon, and what guard sits
on top.

### The corrected picture

| | `jv_v2` | `vitaledge-onboarding` | `theorem-onboarding` |
|---|---|---|---|
| Reader | Google Calendar | diary | Acuity |
| Horizon | `day_window` default 7 | `days_ahead` **17**, narrowed by `day_window` | progressive, `explicit_window` bypass |
| Occurrences of each weekday | exactly 1 | 2–3, **fewer when narrowed** | ~4 |
| `81da8da4` widen ("go and look further") | **present and reached** | present at line 5506 — **unreachable**, returns at 5159 | **absent** |
| `6230cd86` weekday honesty ("say the rule") | n/a — Acuity body | present in `_check_availability_acuity` — **unreachable** | **present and reached** |
| Guard in the reader that actually runs | widen | **none** | honesty field |

Grepping VE's diary reader (lines 5745–5960) for
`_has_week_anchor|_has_weekday_name|days_not_shown|_named_weekdays` returns
**zero matches**. It returns a bare `{"error": "no_availability"}` with no
`available_days`, which is the same empty-known-dates shape that defeated the
weekday guard on Theorem call `CA7d38fb42` (`PORT_HANDOVER_2026-08-26.md` §2).

### So

* **JV is fixed** — the widen runs. This is where the defect was found and where
  it was closed.
* **Vital Edge is the most exposed branch.** Both mechanisms are in the file and
  neither is in the execution path. Its live reader has no weekday guard of any
  kind, and a 17-day horizon narrows further whenever the model passes
  `day_window`.
* **Theorem is partly covered** — it lacks the widen but *does* have the honesty
  field, which forbids saying or implying the clinic is closed on that weekday.
  It will still fail to *find* the later occurrence; it should no longer
  misdescribe it.

`PORT_HANDOVER_2026-08-26.md` §5 rated VE's risk "Low — full booking horizon,
capped". That was wrong on both counts: the horizon is 17 days, not full, and
the ported fix does not run.

**Recommendation:** VE needs its own widen inside `_check_availability_diary` —
the canonical patch does not transplant, exactly as §5 predicted for each reader.
Theorem needs the widen on the Acuity path. Neither is a cherry-pick. Not
tonight; neither branch is JV.

---

## 3. Correctly divergent — verified N/A, do not "fix"

Each traced to its enclosing function before being dismissed.

| Commits | Absent from | Why that is right |
|---|---|---|
| `c208f55f` `e4d07a3b` `4a337b0f` `2048582d` | JV, VE | Every hunk inside `_book/_cancel/_reschedule_appointment_acuity` or `AcuityAdapter` |
| `6230cd86` | JV, VE | `_check_availability_acuity` only — and see §2 for why VE's copy of that body is moot |
| `79a2ea06` | JV, Theorem | `_book_appointment_provisional` — Vital Edge only |
| `f51d21e3` | JV, Theorem | The VE diary reader repoint |
| `f660483b` `0e19e836` `07227720` `cbdf37e2` `4b0ac75a` | JV, VE | Theorem age policy and prices |
| `bd21f325` `5cba8b95` | Theorem | VE age gate and VE emergency bypass |
| `562715d3` | Theorem | Theorem never asks the reason for visit — porting this reintroduces the question |
| `9dbc463a` `a16688d6` | Theorem | Duration re-ask. Mark sells no service with a choice of lengths |
| `59ea85bb` `a292c3ff` | Theorem | `clinic_template_prompt.py`. Theorem's live prompt is the hardcoded `theorem_v3` builder |

---

## 4. The hold-speech family — canonical only, and coherent as a set

```
63778dfc  one arbiter that decides what the caller hears while waiting
cbde450e  every producer now asks the arbiter before speaking
d6b739c6  the reply continues the hold phrase instead of restarting after it
1eafa81f  stop talking over a normal turn, and stop leaving half a sentence
fc583462  the prompt no longer asks for the phrases the gates delete
```

`fc583462` reads like an ordinary prompt fix and looks portable. **It is not.**
It tells the model *"The system speaks the holding phrase for you, BEFORE your
reply"* — true only once the arbiter (`63778dfc`) is present. Ported alone to JV
or VE it would suppress every holding phrase and replace it with nothing, leaving
dead air on exactly the turns that need cover.

**Port the five together or none of them.** None tonight.

---

## 5. Genuine residual on JV — needs its own fix, not a port

`c4b5b0c5` fixed `send_reschedule_confirmation()` returning a flat `True` for a
suppressed or failed send, so the end-of-call follow-up router stood down over a
text that never went out.

On `jv_v2` the function still returns `True` unconditionally
(`app/notifications/booking_sms.py:359`). Both JV call sites
(`receptionist_tools.py:4595`, `:7239`) **discard the return** — so the canonical
fix would change nothing there. Instead, JV's Google-Calendar reschedule path
sets `session["confirmation_sms_sent"] = True` unconditionally at
`receptionist_tools.py:7251`, reaching the same failure by a different route:
**no confirmation text and no follow-up, with a log line saying "sent".**

The defect class is live on JV; the canonical patch does not transplant.
Detection is on tonight's sheet (Call 6, handset only).

---

## 6. What this audit does not cover

- **Test-suite parity.** No suite was run. Baselines expire at midnight
  (`test_absence_is_not_unavailability` builds `today + 9 days`), so
  `PORT_HANDOVER_2026-08-26.md` §6's numbers are not reusable today.
- **Config drift** beyond the JV keys read for the call sheet. Theorem's
  duplicated `minimum_age_years: 7` (`clinic_config.py:463` and `:488`) is still
  open from the 26 Aug sheet.
- **Commits before 18 Aug** — see `four-branch-port-closed-2026-08-25`.
- **Reachability of the 53 converged commits.** Enclosing functions were traced
  for the residue, not for the commits that scored high everywhere. Given §2,
  a high content score on a branch with its own reader is weaker evidence than
  it looks.

---

## Recommended order

1. **Tonight:** JV go-live only. Nothing here touches `jv_v2` except §5, which is
   detection-only on the sheet.
2. **Next:** Vital Edge's weekday widen, written against `_check_availability_diary`
   (§2). It is the most exposed live path and it is not a cherry-pick.
3. **Then:** Theorem's widen on the Acuity path (§2).
4. **Then:** JV's own `confirmation_sms_sent` fix (§5).
5. **Only alongside enabling screening** on VE or Theorem: the nine screening
   commits (§1).
6. **When the hold rework is ready:** all five together, to all three (§4).
