# Named-weekday readers — staged, not deployed · 2026-08-27

Closes the gap `FOUR_BRANCH_PORT_AUDIT_2026-08-27.md` §2 found: the named-weekday
guard existed on every branch and ran on only one of them.

**Nothing is pushed.** Three local branches are staged and suite-verified. JV goes
live tonight and the other two are gated deployment branches, so the deploy is
yours to time.

---

## 1. What was actually wrong

`_filter_tuples_by_preference` **discards a day filter that matches nothing**
rather than returning an empty list, so the caller always hears something. The
days in the payload may therefore be the days that were *left over*, and nothing
downstream can tell that apart from an answer about the day the caller asked for.

That helper is shared by all three readers. What differed was the horizon and the
guard on top:

| Reader | Clinic | Before | After |
|---|---|---|---|
| Google Calendar | JV | widen + disclosure, but **forbade the true answer on a closed day** and spent a round trip confirming it | closed day answered plainly, no wasted read |
| diary | Vital Edge | **no guard at all** — both the widen and the disclosure sat in bodies this reader returns before reaching | widen bounded by the booking horizon, plus disclosure |
| Acuity | Theorem | disclosure present and **firing on a false premise** | gated on the day actually being found |

---

## 2. Four commits on `latency-eval`

Branch `fix/named-weekday-widen-readers`, head **`101d3a93`**, base `39152c2c`.

| SHA | What |
|---|---|
| `58a360df` | diary reader: widen once, bounded by the booking horizon, plus the disclosure fields. One extra freebusy call, not two — `events` already spans `days_ahead`, so the all-day scan is re-clipped rather than re-read |
| `307de52e` | `_clinic_opens_on`: a day the clinic never opens is a different answer, not a harder search |
| `798cb103` | Acuity: stop claiming further matching dates for a day that was never found |
| `101d3a93` | the same closed-day hole in the Google-Calendar body — **this one is live on JV** |

### The two new defects found while doing it

**Theorem's payload was inventing dates.** `6230cd86` reads `_days_found` as a
count of dates matching the requested day. When the day filter has been
discarded it counts the leftovers. On a Thursday request with zero Thursday
slots the payload instructed the model, verbatim:

> "3 further date(s) matching the requested day also have times, and are NOT in
> this result."

There are no such dates. This is the dangerous kind: it does not merely permit a
wrong sentence, it asserts one in the model's own instructions. Reproduced in
`test_a_missing_weekday_is_not_reported_as_found.py`, red before `798cb103`.

**Susie could not tell a caller the clinic is shut that day.** The not-found
guidance forbids "the clinic does not open then" — right when the search just
did not reach far enough, wrong when the clinic genuinely never opens. **jv_v1 is
closed Sunday**, Alcester closes weekends, Redditch opens Monday and Thursday
only. On JV today a Sunday request also spends a Google round trip re-confirming
the working-hours envelope, which the caller hears as silence.

---

## 3. Verification

Every suite bracketed, every baseline taken **today** against each branch's own
parent (a baseline is only valid for the day it was taken —
`test_absence_is_not_unavailability` builds `today + 9 days`).

| Branch | Baseline | With the change | Failing set |
|---|---|---|---|
| `latency-eval` | 102 failed / 6666 passed | **102 / 6687** | byte-identical, md5 `72bf71fb` |
| `vitaledge-onboarding` | 104 failed / 6113 passed | **104 / 6128** | byte-identical, md5 `d8e62c95` |
| `theorem-onboarding` | 102 failed / 6112 passed | **102 / 6118** | byte-identical, md5 `72bf71fb` |

Zero new failures, zero fixed-by-accident. **+21 / +15 / +6 passing** — exactly
the new tests each branch carries.

Failing sets compared with digits included in the filter: a `[a-zA-Z_./-]` filter
hides the defect-numbered files and the diff still looks clean.

38 new regression tests across three files. Each was run against the code with
the fix backed out by hand (`git stash` does not revert in this tree): **7 of 13**
red on the diary file, **5 of 6** on the Acuity file, **2 of 2** on the
Google-body additions. The ones green either side are the leave-it-alone
invariants — round-trip count, no-weekday-named, horizon bound.

---

## 4. The ports

### Vital Edge — `port/weekday-readers-ve`, head `4faa2648` on `1815ffcd`

Three of the four cherry-picked clean. **`798cb103` was deliberately skipped**:
its prerequisite (`6230cd86`) is absent on this branch and the Acuity reader is
unreachable from Vital Edge anyway. Porting it would mean resolving a conflict in
dead code to satisfy a dead dependency.

### Theorem — `port/weekday-readers-th`, head `249c6d2a` on `c63fec61`

`58a360df` and `307de52e` do not apply — **there is no diary reader on this
branch**. `101d3a93` does not apply either; the Google-body widen was never
ported here.

> ### The trap worth knowing about
>
> `798cb103` cherry-picks **CLEANLY** onto stock `theorem-onboarding` and
> produces code that raises `NameError: name '_named_weekdays' is not defined`
> on the first availability call. `81da8da4` extracted that helper on the other
> three branches and never reached this one.
>
> `_check_availability_acuity` wraps its body in a broad `except Exception`, so
> it does not crash. It returns
> `{"error": "Availability check failed: name '_named_weekdays' is not defined"}`
> and Susie tells **every caller** the booking system is unavailable.
>
> A clean cherry-pick, a green-looking apply, and a totally dead availability
> path. Only running the tests caught it. `299f97ba` adds the missing helpers
> first and repoints the inline `day_map` at the single owner.

---

## 5. Deploy

Not tonight for JV. Suggested order once the go-live calls are clean:

```bash
# 1. canonical first — not a live line, push whenever
git push origin fix/named-weekday-widen-readers:latency-eval

# 2. Vital Edge  (Jonathan, +447426779875)
git push origin port/weekday-readers-ve:vitaledge-onboarding

# 3. Theorem  (Mark, +447380841468)
git push origin port/weekday-readers-th:theorem-onboarding
```

**Still to do, deliberately not in this batch:**

* **JV (`jv_v2`) gets nothing here.** It needs `101d3a93` — the closed-Sunday
  fix — which is a deploy of its own after tonight.
* **No widen on the Acuity path.** The scan is 30 days by default (~four
  occurrences of any weekday), so Theorem only narrows when the model passes
  `day_window`. Re-querying Acuity means re-running the lead-time,
  working-hours, bank-holiday and week-range filters over a second result set,
  inside the function CLAUDE.md names the danger zone. The disclosure routes the
  model to re-call with a later `after_date` instead, which is the recovery the
  Google path already prescribes.
* **The disclosure helper is a second implementation**, not a refactor of the
  inline Google-Calendar copy. That body serves a live line and this landed on
  go-live night. Dedup is a follow-up.
* **The diary reader does not exclude the appointment being moved** from its busy
  blocks (the B-77 shape), unlike the Google path. Pre-existing, out of scope,
  worth its own commit.
* **`lead_time_limited` is reported whenever any filter empties the set**, not
  just the lead-time one. Found while building the Acuity fixture: slots on a
  closed day were dropped by the working-hours filter and the payload blamed
  lead time. Misleading, not caller-visible, unfixed.

### Rollback

```bash
git revert --no-commit 101d3a93 798cb103 307de52e 58a360df   # latency-eval
git revert --no-commit 4faa2648 53799c36 adef78c2            # Vital Edge
git revert --no-commit 249c6d2a 299f97ba                     # Theorem
```

Pre-port tips for a hard reset: `latency-eval` `39152c2c`, Vital Edge `1815ffcd`,
Theorem `c63fec61`.
