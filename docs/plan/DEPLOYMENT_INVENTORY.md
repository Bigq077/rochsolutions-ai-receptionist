# Deployment Inventory

**Template — fill during Phase 0 item 4. Requires the Render dashboard; Claude
Code cannot see it.**

You are about to promise reliability for a system whose deployments have not been
enumerated. This table is the cheapest hour in the plan.

---

## Services

| Render service | Branch | Clinic | Twilio number(s) | Acuity calendar(s) | Current commit | Last deploy | Region |
|---|---|---|---|---|---|---|---|
| | `main` | | | | | | |
| | `jv-v1-onboarding` | | | | | | |
| | `vitaledge-onboarding` | | | | | | |
| | `latency-eval` | | | | | | |

---

## Engine drift

The four services run four different versions of the engine. Quantify it — this
is FM-14, and it is the reason a fix verified on one clinic may not exist on
another.

| Pair | Commits ahead/behind | Divergence is mostly… | Engine behaviour differs? |
|---|---|---|---|
| `main` ↔ `latency-eval` | 142 / 189 | | |
| `main` ↔ `jv-v1-onboarding` | | | |
| `main` ↔ `vitaledge-onboarding` | | | |
| `latency-eval` ↔ `vitaledge-onboarding` | | | |

"Divergence is mostly…" should be one of: **clinic config** (fine),
**latency tuning** (fine), **engine behaviour** (a problem — it means a fix
exists for one clinic and not another).

---

## Deploy safety

`render.yaml` declares a single service with `autoDeploy: true` and **no branch
pin** — the branch is set per-service in the dashboard. This is FM-20: a push to
the wrong branch changes what answers a real clinic's phone, with no review step.

| Check | Status | Notes |
|---|---|---|
| Is `autoDeploy` on for every service? | | |
| Is the branch pinned per service in the dashboard? | | |
| Does any service auto-deploy from a branch used for experiments? | | **If yes, this is urgent.** |
| Who can push to the deploy branches? | | |
| Is there a staging service? | | |

---

## Secrets

37 env vars per `.env.example`. Per service, confirm presence — not values.

| Service | All 37 set? | Missing | Notes |
|---|---|---|---|

Note the tenant-identity vars — `CLINIC_NAME`, `CLINIC_ADDRESS`, `CLINIC_PHONE`,
`ACUITY_CALENDAR_ID_ALCESTER` / `_REDDITCH` / `_MARK` / `_LEANNE` — and the obs
vars `OBS_DATABASE_URL`, `OBS_ALERT_SMS_TO`, plus `SMS_ENABLED`. These are the
ones that will move into `clinic.json` under runtime tenancy (Phase 6).

---

## Worktree hygiene

`git worktree list` shows ~15 registered worktrees under
`C:/Users/quent/AppData/Local/Temp/claude/`, most marked *prunable*. Stale
worktrees are how a session ends up confidently measuring the wrong tree — which
has already happened once on this project.

```
git worktree prune
git worktree list      # confirm what remains is intentional
```

- [ ] Pruned
- [ ] Remaining worktrees are intentional and documented

---

## What could not be determined from the repo

List anything requiring dashboard access, and get it from Ismael.
