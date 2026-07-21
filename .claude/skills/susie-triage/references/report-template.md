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
