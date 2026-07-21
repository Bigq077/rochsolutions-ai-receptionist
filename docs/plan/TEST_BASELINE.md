# Test Baseline

**Template — fill during Phase 0 item 1. Do not proceed to Phase 1 until complete.**

Branch: _______________  Commit: _______________  Date: _______________

---

## Summary

| Metric | Value |
|---|---|
| Test files | |
| Tests collected | |
| Passed | |
| Failed | |
| Skipped | |
| Errors (collection) | |
| Wall-clock runtime | |

Command used: `pytest -q` (config: `pytest.ini`, `asyncio_mode = auto`,
`testpaths = tests`). Note `app/booking/tests/` is outside `testpaths` — run it
separately and record it here too.

---

## Failures

One row per failing test. Every failure must end in **Fixed** or **Quarantined**
with a reason. A test left failing with no verdict is how a week goes wrong.

| Test | Failure | Root cause | Verdict | Notes |
|---|---|---|---|---|
| | | | Fixed / Quarantined | |

**Quarantine rule:** a test may only be quarantined if it is failing for a reason
unrelated to correctness of live behaviour (e.g. it asserts against a clinic
config that no longer applies to this branch). If a test fails because the system
does the wrong thing, that is a defect, not a quarantine candidate — log it in
the failure-mode register instead.

---

## Skipped tests

Skips hide as effectively as failures. List every skip and why.

| Test | Skip reason | Legitimate? |
|---|---|---|

---

## Flakiness check

Run the suite three times. Any test that does not produce the same result all
three times is flaky and must be recorded here — flaky tests are worse than
absent ones, because they train you to ignore red.

| Test | Runs passed / 3 | Suspected cause |
|---|---|---|

---

## Verdict

- [ ] Suite is green (or every failure has a written verdict)
- [ ] No unexplained skips
- [ ] No flaky tests, or all flakes documented
- [ ] Runtime is short enough to run on every change (target: under 2 min)

**Gate 0 item 1 passes when all four boxes are ticked.**
