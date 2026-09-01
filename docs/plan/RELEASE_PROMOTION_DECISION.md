# ADR-002 — Release promotion after the four-clinic fold

**Status:** Accepted (2026-09-01) — `latency-eval` = staging, `production` = the live line.
**LIVE since 2026-09-01** — services repointed, all action items closed.
First promotion `cda304a3` → `1d85d13e`, verified on the demo line before promoting.
**Date:** 2026-09-01
**Deciders:** Quentin (owner, sign-off) · Claude Code (analysis)
**Supersedes:** nothing. **Amends:** ADR-001's deployment model, which assumed one branch per clinic.

---

## Context

By 2026-09-01 all four Render services track `latency-eval`. Tenancy is resolved
at runtime from the Twilio `to=` number (`clinic_config.TWILIO_TO_CLINIC`), so
one codebase serves five numbers across four clinics. That fold was correct and
is not in question here — it closed **FM-14** ("engine drift across the four
deployed branches", likelihood **5**, the highest in the register), whose stated
resolution was exactly this: *"resolved by runtime tenancy"*.

What the fold also did, without anyone deciding it, was **delete the staging
gate**. Branch-per-clinic had been providing one as a side effect: work landed
on the demo branch, was called, and only then was ported. With one branch and
`autoDeploy: true`, a push reaches four clinics — three of them patient lines —
at once.

**This is not theoretical. It happened during the session that produced this
document.** Commit `c0b37d87` was pushed, verified by a demo call, and the call
log revealed a defect in it (`e94d3686` fixed it). For roughly twenty minutes
all four clinics ran the broken build. The defect was benign — a reason was not
written to the call record — but nothing about the process made it benign. A
caller-audible regression would have reached patients on three lines with no
gate in front of it.

Two orthogonal concerns had been conflated:

| Concern | Question it answers | Status |
|---|---|---|
| **Tenancy** | *which clinic* is being served | Solved — runtime config. Leave alone. |
| **Promotion** | *which code version* reaches whom | **Never solved.** Branches were hiding it. |

---

## Decision

**Two branches, one lineage.**

- **`latency-eval`** — staging. The demo service tracks it. Push freely; the
  only phone it can reach is **+447366263180** (`northgate`).
- **`production`** — the live line. The three patient services track it. It
  moves ONLY by fast-forward from `latency-eval`, after a demo call.

```bash
git push origin latency-eval                # deploys the demo line only
#   ... call +447366263180, read the log ...
git push origin latency-eval:production     # deploys the three patient lines
```

**`production` is always an ancestor of `latency-eval`.** That single property is
what makes this cheap: promotion is a fast-forward, never a cherry-pick, so
there is no port, no divergence, no per-branch prompt-hash re-pinning, and the
commit a clinic runs is bit-identical to the one that was called.

**Rollback** is moving the pointer back, not reverting code:

```bash
git push --force-with-lease origin <last-good-sha>:production
```

---

## Options considered

### Option A — one demo branch + three live branches, port in one go · **REJECTED**

The owner's initial proposal, and the intuitive one. Rejected because it buys the
gate by reopening **FM-14 at L5**. Evidence from this repo's own history of that
model: `git cherry` mis-reporting port status (patch-ids differ after a
cherry-pick, so it calls ported work unported and hides real gaps); B-44 fixed on
one branch and half-ported; seven wrong-surname fixes stranded on JV for two
weeks; every prompt edit needing a per-branch hash re-pin because the hash
differs per branch.

Concretely: the change that prompted this ADR was one commit plus one test file.
Under Option A it would have been four cherry-picks, four suite runs and three
hash re-pins — and each of those is a place a safety fix can be silently dropped.

**The load-bearing insight:** the fear of "porting to all the live branches" only
applies when branches *diverge*. A promotion branch never diverges. Option B
delivers Option A's gate without Option A's cost.

### Option B — `latency-eval` → `production` · **ACCEPTED**

See Decision. Cost: one extra branch and one extra push per release.
Risk: someone pushes straight to `production`, bypassing the gate — mitigate with
a GitHub branch protection rule, or with discipline plus the fact that
`production`'s reflog makes the bypass visible after the fact.

### Option C — one branch, `autoDeploy` off on the three live services · **ADOPTED AS INTERIM**

Zero repo change, closes the hole in five minutes, and is worth doing
immediately. Rejected as the permanent answer because Render's "deploy latest
commit" ships the branch **tip at click time**: push two commits, test only the
first, and the click ships both. It is also not recorded in git, so nothing
afterwards can answer "what is actually live?" — which is the question that cost
this session an hour of wrong assumptions about `jv_v2`.

### Option D — per-clinic feature flags · **REJECTED as a general gate**

Right tool for an individual risky behaviour change, wrong tool for release
promotion. This codebase already carries substantial flag debt (`OBS_*`,
`SMS_ENABLED`, the U3.5 STT lever, call overflow — all built and off), and a flag
cannot gate an engine change in `llm_stream.py` or `connection.py` anyway.

### Option E — a dedicated staging service · **DEFERRED, still wanted**

A fifth service on a test number with test calendars. `DEPLOYMENT_INVENTORY.md`
line 53 has asked *"Is there a staging service?"* since Phase 0 and it has never
been answered. Deferred because Option B closes the urgent hole; kept open
because of the coverage gap below.

### Option F — automated call suite before promotion · **COMPLEMENTARY**

`tests/auto/` and the `factory/call-suite-*` work already exist. Synthetic calls
against staging are what would make this gate cheap enough to run every time
rather than only when someone remembers.

---

## Consequences

### What this does NOT fix — the coverage gap

**A green demo call validates the engine, not the tenants.**

`northgate` is a genuine proxy for `jv_v1`: same `template_v1` engine, same
39-condition library, its own calendar (pinned by `tests/tenancy/`). A demo call
exercises the JV code path faithfully.

It says **nothing** about the other two:

| clinic | why the demo call misses it |
|---|---|
| `theorem_v3` | renders a hardcoded Python prompt that `clinic.json` never reaches, and short-circuits to the Acuity executor |
| `vital_edge` | no condition library, massage services, diary reader rather than the Google-Calendar path |

So promotion gates *engine regressions*. Tenant-specific regressions on Theorem
and VE remain uncovered, and the fix for that is Option E plus staging tenants
mirroring them the way `northgate` mirrors JV. **Do not read a green demo call
as clearance for all four clinics.**

### Other consequences

- `CLAUDE.md` §2 and the `render.yaml` note are stale: they describe four
  branches on four services with a cherry-pick port workflow. Correcting them is
  an action item below, not a side effect of this ADR.
- `FM-20` ("wrong branch deployed", I5) changes shape rather than disappearing:
  there are now two branches with real meaning, and pointing a patient service at
  `latency-eval` silently removes its gate.
- The canonical-first rule is retired. There is nothing left to port to.

---

## Action items — status at close of 2026-09-01

All six are done. Kept rather than deleted because the ORDER mattered: item 1 is
what made it safe to push item 5 at all.

**Owner (Render dashboard — Claude Code cannot see or do these):**

1. ✅ **`autoDeploy` OFF on the three patient services.** Done first, as the
   interim. This is what let the rest of the day proceed: with the hole closed,
   a push to `latency-eval` reached only the demo line even before the repoint.
2. ✅ **Repointed** the three patient services `latency-eval` → `production`,
   autoDeploy back on.
3. ✅ Demo service confirmed as the only service on `latency-eval`.
4. ✅ `DEPLOYMENT_INVENTORY.md` filled to the limit of what is knowable from the
   repo; the dashboard-only cells are marked and still need the owner.

**Repo:**

5. ✅ `CLAUDE.md` §2 rewritten (`1d85d13e`), §3's "tenant selection happens at
   deploy time" corrected against the code, `render.yaml` given a header saying
   it does not decide what is live. The STATE CHECK block was flipped in
   `6fbf1679` once the repoint landed — leaving it saying "inert" would have cost
   the next session the same over-caution the 2026-08-02 posture once cost.
6. ✅ Ruleset on `production`: restrict deletions, block force pushes, require
   linear history — with the owner on the **bypass list**, deliberately. Linear
   history is the rule that actually encodes this ADR: divergence is the failure
   mode, and a merge commit is how it would start.
   **Deliberately NOT enabled:** *require a pull request* would reject the
   promotion push outright (it is not a merge and has no PR), and *require status
   checks* would block every promotion forever, because this suite's baseline is
   RED by design (117 failing; you verify by diffing the failing set).
   Caveat: blocking force pushes also blocks the documented rollback, which is
   why the bypass list exists. It is a guardrail against accident, not a lock to
   dismantle mid-incident.

## First promotion through the gate

`cda304a3` → `1d85d13e`, fast-forward.

Verified on the demo line BEFORE promoting — call
`CAc119b8838f556ac20f9552dee2e4021f`, `[build_info] running build 1d85d13e`,
booked end-to-end (event `vjqu5nlcsk5mp7iogh6fh21u14`), every continuation chunk
lowercase, i.e. no severed sentence.

**Why "production was never called" is not an objection.** The promotion is a
pointer move to the same commit object and the same tree; Render deploys a
commit, not a branch name. `requirements.txt` is fully `==` pinned (since the
2026-08-21 `anthropic>=0.40.0` → 1.0.0 outage), so the patient rebuild resolves
the same dependency set. The moment promotion becomes a cherry-pick, that
argument fails — which is exactly what *require linear history* protects.

**What the demo call still did NOT prove, same commit or not:** the demo service
runs `SMS_ENABLED=off` and `APPOINTMENT_REMINDERS_ENABLED=off`, confirmed in that
call's log. The confirmation-SMS and reminder paths executed **zero lines**. That
is fine for an engine change and useless as a gate for anything near
notifications — add that to the tenant coverage gap below, not instead of it.

**Confirmed on the patient services**, 2026-09-01 15:05: the Render dashboard
shows `vitaledge` (`srv-d8va6cbtqb8s73fbpvag`) on branch **`production`**, commit
**`1d85d13`**, **Live** — with the preceding event still reading `cda304a`, so
the transition is in the platform's own record. Owner reports all three patient
services live on it.

That is deploy evidence, not runtime evidence. The remaining check is cheap and
worth doing on the next real patient call: `[build_info] running build 1d85d13e`
in the cleanup log proves the *serving process* is that build, which is a
slightly stronger claim than "the deploy went Live".

---

## Decision record

| | |
|---|---|
| **Decision** | `latency-eval` = staging (demo line), `production` = the three patient lines; promotion is a fast-forward after a demo call |
| **Created** | `production` at `cda304a3`, identical to `latency-eval` — i.e. exactly what all four services were already running |
| **Rejected** | A (branch per clinic — reopens FM-14 at L5), D (flags as a general gate) |
| **Interim** | C (`autoDeploy` off on patient services) — do this first, it is five minutes |
| **Deferred** | E (staging service), F (automated call suite) — both still wanted |
| **Known gap** | A demo call does not validate `theorem_v3` or `vital_edge` |
| **Status** | Live. Action items 1–6 all closed 2026-09-01. `production` carries a ruleset: no deletions, no force pushes, linear history required, owner on the bypass list. |
| **Proven** | `vitaledge` shown Live on `production` @ `1d85d13` in the Render dashboard, 15:05; all three patient services reported live. Runtime confirmation via `[build_info]` on a real call is still worth one look. |
