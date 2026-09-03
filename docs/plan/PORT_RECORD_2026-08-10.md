# Port Record — Theorem 2026-08-10 → latency-eval, vitaledge-onboarding, jv_v2

Eleven commits landed on `theorem-onboarding` between 17:53 and 21:45 on
2026-08-10 (ten fixes + one test-only). **All eleven are ported to all three
other branches.** Nothing was skipped and nothing was adapted away.

Committed, **not pushed** — no branch was deployed.

| Branch | Upstream at port | Port branch | Head | Ahead |
|---|---|---|---|---|
| `latency-eval` | `de8c3e0` | `port/le-theorem-0810` | `ccde6f5` | 12 |
| `vitaledge-onboarding` | `0609bee` | `port/ve-theorem-0810` | `e74e835` | 12 |
| `jv_v2` | `320a571` | `port/jv-theorem-0810` | `50e9553` | 12 |

12 = the 11 cherry-picks + 1 branch-adjustment commit.

Worktrees: `…/8bda1a40-…/scratchpad/p-{le,ve,jv}`.

---

## Why port branches and not the branches themselves

All three target branches are **checked out in other worktrees** — some by
parallel sessions:

| Branch | Held by | At |
|---|---|---|
| `vitaledge-onboarding` | the main working directory | `f46dd24` |
| `latency-eval` | `…/9e7cc717-…/scratchpad/le` | `82ac6e3` |
| `jv_v2` | `…/Temp/claude/jv2` | `320a571` |

Git refuses to move a checked-out branch, and forcing it would change another
session's working tree underneath it — the failure mode already recorded in
`parallel-sessions-share-one-worktree`. Note also that the main directory's
`vitaledge-onboarding` is **behind** origin (`f46dd24` vs `0609bee`), so moving
it would not have been a fast-forward.

Each port is a clean fast-forward of its upstream — **0 commits behind** — so
landing it is one command per branch, once the holding worktree is free:

```bash
git push origin ccde6f5274c4e492a09e253d16d565d6d92d13c5:latency-eval
```

```bash
git push origin e74e83566007adf4908691ccd312bad3d9b49d27:vitaledge-onboarding
```

```bash
git push origin 50e95536cae39b12877050ea48eff4aa18932554:jv_v2
```

> ⚠️ Every one of those pushes triggers a Render autodeploy of that service.
> `vitaledge-onboarding` is a **live clinic**; treat it as gated (out-of-hours,
> revert in hand). `latency-eval` is not a live line, but it is still a service
> that will rebuild.

---

## What was ported

| # | Commit | Fix | Effect off Theorem |
|---|---|---|---|
| 1 | `fbf68da` | "an earlier Wednesday" read as accepting the one on offer | **Live** — engine, `llm_stream.py` |
| 2 | `2c427c3` | hallucinated-slot backstop disarmed by a cleared cache | **Live** — engine |
| 3 | `117c56a` | diary stayed shut after the calendar refused the slot | **Live** — engine |
| 4 | `3a31154` | spelled surname correction dropped | **Live** — engine, `name_capture.py` |
| 5 | `0781138` | a guard refusal spoken as a fact about the diary | **Live** — engine |
| 6 | `a844e14` | four identical owner alerts for one failure | **Live** — engine |
| 7 | `3081b4e` | the dedupe test measured a branch's config | test-only |
| 8 | `6759ad5` | clinic question asked four times | inert (see below) |
| 9 | `2cc9cf1` | ten seconds of silence after the clinic ack | **partly live** (see below) |
| 10 | `1dc1037` | "August 19th" not read as a date, month replaced | inert (see below) |
| 11 | `4896fe2` | a payload gap spoken as "fully booked" | inert (see below) |

**Six of the ten are live engine fixes on every clinic** and were simply
missing. Fix 4's own source comment already said porting it was a straight file
copy.

### The four that are clinic-shaped

These were ported anyway, deliberately, so the four branches stay byte-identical
in `app/` — but their runtime effect off Theorem is limited:

- **8 — location ack (`6759ad5`).** The sites exist on every branch, but the
  intercept only fires for a clinic with two locations. Theorem is the only one.
  Inert elsewhere; ported so the next clinic that gains a second site inherits
  the fix rather than the bug.
- **9 — synthetic flag (`2cc9cf1`).** Three sites. Two are the location and DTMF
  ladders (Theorem-shaped). **The third — the lookup keypad read-back — is
  live on every clinic**, and it was a 2-tuple on all three branches. That one
  is a real fix everywhere.
- **10 — month-first date (`1dc1037`).** `_extract_week_range` is shared code
  but is called from exactly one place: `_check_availability_acuity`, which
  only Theorem reaches. Inert elsewhere today. See the gap below.
- **11 — payload honesty (`4896fe2`).** The new fields are emitted by the Acuity
  executor and the rule lives in `_build_theorem_v3`. Both exist on all four
  branches; neither is reached by a non-Theorem clinic.

---

## Adjustments the port needed

One extra commit per branch, `test(port): two pins and one site…`. **No runtime
change.**

1. **`_disp` is Theorem-only.** `test_requeue_after_ack_is_synthetic` required a
   `_disp` re-injection site on every branch. `_disp` is T-19's DTMF location
   re-queue and lives with the two-clinic ladder, so it does not exist off
   Theorem. The row is now skipped when absent rather than deleted — absent is
   tolerated, present-and-2-tuple still fails.

2. **`theorem_v3`'s prompt pin moved**, `d5d26ee076213608 → 31dcedf2fd28f98e`,
   identically on all three branches. Two tables carry it:
   `UNCHANGED_CLINIC_PROMPTS` (read by *three* tests — b55,
   `test_reason_question_once`, `test_under_age_booking_gate`) and the b57 table.
   Each of those three uses the pin to assert its **own** feature did not leak
   into `theorem_v3`; all three claims still hold, because what moved the prompt
   is a fourth, deliberate change. `jv_v1` and `vital_edge` are unchanged, which
   is what proves it.

   The hash differs from `theorem-onboarding`'s (`9f22c6b5168512a9`) because the
   prompt text either side of the addition differs between branches. The
   **addition is identical** — verified below.

---

## Verification

- **Every `app/` patch is byte-identical across all four branches.** Checked by
  hashing each commit's `-U0` diff restricted to `app/` and comparing to
  Theorem's. 11/11 match on all three branches — so no cherry-pick applied a
  subtly different hunk into different surrounding context.
- **Every suite is unchanged against its own pre-port baseline**, same failing
  set, not just the same count:

  | Branch | Before | After | Diff |
  |---|---|---|---|
  | `latency-eval` | 103 | 103 | identical |
  | `vitaledge-onboarding` | 101 | 101 | identical |
  | `jv_v2` | 103 | 103 | identical |

  Baselines were taken **with `.env` copied in** — without it a different set of
  tests runs and the comparison is meaningless.
- **All the new regression tests pass on all three branches.** They arrived with
  the cherry-picks and are absent from every failing set.
- Runtime state re-probed by AST afterwards: the three re-injection sites are
  3-tuples on all four branches (they were 2-tuples on all three before), and
  all ten fix markers are present on all four.

---

## Open, found during the port — not fixed

**VE and JV never narrow availability to a named date at all.** Their paths
(`_check_availability_published` for VE, the Google Calendar executor for JV)
filter only by **weekday name** and **time band**, via
`_filter_tuples_by_preference`. There is no calendar-date filter anywhere in
them. So a caller saying *"have you got August the nineteenth?"* on Vital Edge
gets the nearest slots, and the same "absence read as unavailability" the
Theorem fix addresses can occur through a different door.

This is **not** a missing port — it is an adjacent defect with no reproduced
call behind it, and closing it means giving those paths a date filter they have
never had. Flagged rather than built.

Deliberately **not** done with it: adding the `NEVER CALL A DAY FULL` rule to
the VE/JV prompts. That rule names payload fields their executors do not emit,
and a rule naming fields the tool never sends is worse than no rule.

**Minor convergence gap:** `theorem-onboarding`'s copy of
`test_requeue_after_ack_is_synthetic.py` still has the stricter `_disp`
assertion (it passes there, because the site exists). The other three now carry
the branch-aware version. One small commit on Theorem would make all four
identical.
