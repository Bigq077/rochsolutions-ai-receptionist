# Triage report template

Copy this shape into `docs/triage/TRIAGE_<YYYY-MM-DD>.md`. Keep it short — the
value is the ranking, not the prose. A reader should be able to pick the top
cluster and hand it straight to `susie-debug` without asking a question.

---

```markdown
# Susie triage — <YYYY-MM-DD>

**Source:** tests/auto/results/results_<ts>.json
**Branch:** <branch> (<short sha>)
**Calls:** <n> | **Pass:** <n> | **Fail:** <n> | **Rate:** <n>%
**Clusters:** <n> root causes from <n> failing calls

## Ranked root causes

### 1. <one-line cause> — severity <1-5>

- **Hypothesis:** <subsystem/file>. <why the evidence points there, one sentence.>
- **Scenarios (<n>):** 4.1, 4.2, 4.3, 8.1, ...
- **Stall point:** flow_step=<n> (<state>), selected_slot=<value>, end=<end_reason>
- **Evidence:**
  > SUSIE: "<one representative line, verbatim>"
- **Discriminator:** <the one command or file that would confirm or kill this>
- **Next action:** hand to `susie-debug` as <scenario id>.

### 2. ...

## Cascades

- Cluster <n> never occurs without cluster <m>. Fix <m> first and re-run before
  treating <n> as real.

## Stale scenarios — not Susie bugs

| Scenario | Check | Why stale |
|---|---|---|
| 5.3 | duration_question_asked | removed from the standard flow |

## Not clustered

<Any failing call that did not fit a cluster, with one line on why. Never
silently drop a failure.>

## Evidence

```
$ python .claude/skills/susie-triage/scripts/collect_failures.py <path>
CALLS: 118 | PASS: 84 | FAIL: 34 | RATE: 71.2%
```
```

---

## Severity scale (from SKILL.md step 7)

| Severity | Meaning |
|---|---|
| 1 | A booking the caller believes exists but does not — silent failure |
| 2 | A booking that failed to complete at all |
| 3 | Caller abandoned, or reached a human unnecessarily |
| 4 | Wrong information given (price, hours, insurance, location) |
| 5 | Clumsy phrasing, banned words, minor ordering |

## Rules the template exists to enforce

- **Every failing call appears exactly once** — in a cluster, in the stale
  table, or under "Not clustered". A count that doesn't add up to the failure
  total means something was dropped.
- **One transcript excerpt per cluster**, not five. The excerpt is proof, not
  a log.
- **No fixes.** This report says what is broken and where to look. The diff
  belongs to `susie-debug`.

---

## Obs-mode variant

When the source is the live store rather than a suite run, the header and the
per-cluster evidence change. Everything else — ranking by patient impact, one
excerpt per cluster, every call accounted for — is identical.

```markdown
# Susie triage — <YYYY-MM-DD>

**Source:** obs store, last <n> days<, clinic X>
**Branch:** origin/latency-eval @ <sha>
**Calls:** <n> real (<n> operator test calls excluded)
**Fleet:** booking_rate <n>% | mean_score <n> | volume <n>
**Clusters:** <n> root causes from <n> calls with findings

## Invisible to the existing review

<The sev1-2 calls weekly.py's bottom decile cannot surface, because they score
well. If this section is empty, say so explicitly — it is a real result.>

## Ranked root causes

### 1. <cause> — severity <1-5>

- **Signals:** PHANTOM_BOOKING (3 calls)
- **Calls:** CA123..., CA456...
- **Build:** all on <sha> — regression with a known blast radius
  *(or: spread across N builds — not a single deploy)*
- **Clinic:** theorem only — check clinic.json before engine code
- **Evidence:**
  > SUSIE: "<one short line>"
- **Patients to contact:** <n>   ← severity 1 only
- **Next action:** `python -m app.obs.to_scenario CA123...` → `susie-debug`

## Already fixed

<Clusters confined to builds older than a known fix. List them, do not rank
them, and say which fix covers them.>
```

### Extra rules for obs reports

- **Severity 1 means people.** A `PHANTOM_BOOKING` cluster is not "3 findings",
  it is **3 patients who may turn up to nothing**. Give the count of patients to
  contact, and point at `docs/INCIDENT.md` §3c.
- **Always state the build spread.** One `build_sha` means a deploy regression
  with a known blast radius; many means something long-standing. It changes what
  the fix even is.
- **Never paste a raw transcript.** One short quoted line as evidence. These are
  special-category patient records — use `to_scenario`, which asserts redaction,
  for anything committed.
- **Say when a section is empty.** "No phantom bookings in 45 days" is one of the
  most valuable sentences this report can contain. Do not omit it for being
  uneventful.
