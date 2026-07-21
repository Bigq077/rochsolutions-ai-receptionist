# Deleted Test Triage

**Template — fill during Phase 0 item 2.**

~30 test files differ between `main` and the production base branch. Some were
removed correctly (Theorem-specific, and this branch serves a different clinic).
Some may have been removed because they were failing during latency work. The
second category is a live defect wearing a disguise.

Generate the list with:

```
git diff --stat main <base-branch> -- tests/
```

---

## Classification

For each deleted file:

- **(a) Clinic-specific, correctly removed** — the test asserts behaviour only
  meaningful for a clinic this branch does not serve.
- **(b) Generic guard, wrongly removed** — the test protects engine behaviour
  that applies to every clinic. **Restore it.** If it fails on restoration, that
  failure is a defect and goes in the failure-mode register.
- **(c) Superseded** — the coverage exists elsewhere now. Name the replacement
  test. "Probably covered somewhere" is not an answer.

| Test file | Lines | Class | Reasoning | Action | Restored? |
|---|---|---|---|---|---|
| `test_greeting_disclosure_guard.py` | 196 | | | | |
| `test_transfer_disabled_gate.py` | 108 | | | | |
| `test_transfer_line_spoken.py` | 70 | | | | |
| `test_theorem_canonical.py` | 267 | | | | |
| `test_caller_concerns.py` | 191 | | | | |
| `test_location_deictic_clinic.py` | 74 | | | | |
| `test_location_gate_sticky_reask.py` | 84 | | | | |
| `test_location_indifference.py` | 94 | | | | |
| `test_location_ladder_escape.py` | 40 | | | | |
| `test_book_affirmative_gate.py` | 88 | | | | |
| `test_faq_clinic_gate.py` | 55 | | | | |
| `test_emergency_reask_suppression.py` | 52 | | | | |
| `test_clinic_config_mapping.py` | 66 | | | | |
| `test_greeting_disclosure_guard.py` | | | | | |
| `test_llm_stream_turns.py` | 37 | | | | |
| `test_logistics_faqs.py` | 50 | | | | |
| `regression/test_booking_phone_step.py` | 56 | | | | |
| `regression/test_surname_straggler_suppression.py` | 89 | | | | |
| *(complete from the git diff — this list is not exhaustive)* | | | | | |

---

## Ones to look at hardest

Judgement, not mechanics — these are the deletions whose *names* suggest they
were guarding against exactly the failure modes we care about:

- **`test_emergency_reask_suppression`** — anything touching emergency handling
  is FM-04 territory. Removing an emergency-path guard during latency work would
  be a serious mistake. Check this one first.
- **`test_transfer_disabled_gate` / `test_transfer_line_spoken`** — transfer to a
  human is the degradation path in FM-03 and FM-05. If these are gone, the
  escape hatch is untested.
- **`test_book_affirmative_gate`** — sounds like it guards *when the system
  treats a caller utterance as booking consent*. That is the FM-01 invariant.
  Read the deleted test before deciding anything.
- **`regression/test_booking_phone_step`** — a deleted file from
  `tests/regression/` is a stronger signal than a deleted unit test. Regression
  tests exist because something already broke once.

---

## Output

- Count classified (a) / (b) / (c):
- Files restored:
- New defects found by restoration (assign FM numbers):
- **Verdict:** did any deletion hide a live defect? Yes / No

If the answer is Yes, that finding outranks everything else in Phase 0 — bring it
to Ismael before continuing.
