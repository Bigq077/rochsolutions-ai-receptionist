# Branch Decision — settle this before Phase 0

**Status: OPEN. Blocks everything.**

---

## The problem

The 10-day plan was written on the stated premise that `latency-eval` is the
canonical production base. `latency-eval`'s own documentation contradicts that.

`LATENCY.md` on that branch opens with a prime directive:

> *Total isolation from live. This branch is a lab, not a release candidate.
> Even a winning lever is promoted to live only as a separate, later PR — never
> by merging `latency-eval` as-is.*

A later note in the same file concedes that battle-hardening fixes now land on
`latency-eval` directly. So the branch's role has **drifted in practice** from
what it was chartered to be.

Drift is not a decision. Ten days of production hardening on a branch that was
explicitly designed never to ship is the most expensive mistake available in
this window, and it is only cheap to fix right now.

---

## What we know

| Branch | Deployed | Notes |
|---|---|---|
| `main` | Yes (Render service) | 142 commits ahead of `latency-eval`; has `CLAUDE.md` from an earlier "Phase 0: ground the repo" commit |
| `jv-v1-onboarding` | Yes | JV clinic |
| `vitaledge-onboarding` | Yes | Vital Edge clinic; newer than `latency-eval` |
| `latency-eval` | Yes | 189 ahead / 142 behind `main`. Has the latency instrumentation and locked baseline. Charter says lab-only. |

Also relevant: ~15 registered worktrees under
`C:/Users/quent/AppData/Local/Temp/claude/`, most marked prunable, including
`latency-eval-wt`. Clean these up before starting — stale worktrees are how a
session ends up measuring the wrong tree.

---

## The question to answer

**Which branch will be answering a real clinic's phone in August?**

That branch is the production base. Not the one with the best instrumentation,
not the one most recently worked on — the one that ships.

---

## How to decide (assign this to Claude Code before Phase 0)

Produce a comparison across `latency-eval`, `main` and `vitaledge-onboarding`:

1. **Deployment reality.** Which Render service runs each, which phone numbers
   route to it, which clinic. Cross-check against the Render dashboard.
2. **Divergence.** For each pair: commits ahead/behind, and *what the divergence
   actually consists of* — clinic config, latency tuning, or engine behaviour.
   Engine divergence is the only kind that matters here.
3. **What each would cost to harden.** If we pick branch X, what do we lose that
   exists elsewhere, and what would it take to bring across? Specifically: does
   the latency instrumentation (`app/media_streams/latency_timing.py`,
   `LATENCY.md` locked baseline) exist outside `latency-eval`? Does
   `clinical_screening.py`? Does the obs subsystem?
4. **Recommendation**, with the reasoning stated plainly.

---

## Likely outcomes, so you can think ahead

- **`latency-eval` is the base after all** — the charter is stale, the branch has
  become the real trunk. Then: update `LATENCY.md` to say so, because the next
  person to read it will be misled exactly as we were. Plan proceeds unchanged.
- **A live onboarding branch is the base** — then the latency instrumentation and
  any hardening from `latency-eval` needs porting, and Phase 0 gains a day.
- **`main` is the base** — the most divergence to reconcile, the most work, but
  the cleanest story if the four services are ever to collapse into one.

There is no wrong answer here that a day of thought won't reveal. There is a very
wrong answer that ten days of silence will.

---

## Decision

*Record it here when made, with the date and the reasoning. Every later document
assumes this answer.*

- **Decision:**
- **Date:**
- **Reasoning:**
- **Consequences for the plan:**
