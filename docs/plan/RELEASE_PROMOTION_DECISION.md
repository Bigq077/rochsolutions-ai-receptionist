# ADR-002 — Release promotion after the four-clinic fold

**Status:** Accepted (2026-09-01) — `latency-eval` = staging, `production` = the live line.
Branch created at `cda304a3`. **Inert until the Render services are repointed — see Action items.**
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

## Action items

**Owner (Render dashboard — Claude Code cannot see or do these; the branch is
inert until they are done):**

1. **Now, interim:** turn `autoDeploy` **off** on the three patient services.
   This alone closes the hole and needs no repo change.
2. Repoint the three patient services from `latency-eval` to **`production`**,
   `autoDeploy` on. Leave the demo service on `latency-eval`.
3. Confirm the demo service is the ONLY service on `latency-eval`.
4. Fill in `DEPLOYMENT_INVENTORY.md`, including line 53.

**Verification after step 2 — do not assume, prove it:** push a comment-only
commit to `latency-eval`, then check a demo call's cleanup log for
`[build_info] running build <sha>`. That log line is the only deploy proof —
`/health` returns a hardcoded `1.0.0`. The three patient services must NOT show
the new sha until `production` is pushed.

**Repo:**

5. Correct `CLAUDE.md` §2 and the branch/deploy block.
6. Consider a branch protection rule on `production` (fast-forward only).

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
| **Blocked on** | Owner action items 1–3. **Until then nothing has changed and every push still reaches patients.** |
