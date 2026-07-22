# ADR-001 — Production base branch for the cohort-1 hardening window

**Status:** Accepted (2026-07-21) — base = `release/cohort-1`, cut from `origin/latency-eval@022f816`.
Deployment-reality dashboard facts (§ Deployment reality) remain pending; they affect migration
sequencing, not the engine decision. Every later plan document assumes this answer.
**Date:** 2026-07-21
**Deciders:** Quentin (owner, sign-off) · Claude Code (analysis)
**Blocks:** Phase −1 gate → Phase 0. Do not start Phase 0 until this is Accepted.

---

## Context

The 10-day plan (`PRODUCTION_READINESS_PLAN.md`) was written on the premise that
`latency-eval` is the canonical production base. That branch's own `LATENCY.md`
opens with a prime directive calling it *"a lab, not a release candidate… never
promoted by merging as-is."* One of those is wrong. Ten days of hardening on a
branch designed never to ship is the most expensive error available in this
window, so this is settled first.

The system runs **four Render services, one clinic each, tenant chosen at deploy
time** (`CLINIC_NAME` + `ACUITY_CALENDAR_ID_*` in env; `render.yaml` has
`autoDeploy: true` and no branch pin). "Which branch is the base" therefore means
**which engine do we harden and then propagate to the per-clinic deployments.**

### Measurement provenance (read this — it changes the numbers)

There are **two clones on this machine**, with divergent branch pointers:

| branch | `…/GitHub/…` clone (**= origin, = what Render deploys**) | `…/Claude code free/…` clone (**where these docs live**) |
|---|---|---|
| `main` | `45ebabc` | `772e775` (6 days stale) |
| `latency-eval` | `022f816` | `22daa44` (1 day stale) |
| `jv-v1-onboarding` | `409d670` | `4630dda` = `409d670`-era + docs, **unpushed** |
| `vitaledge-onboarding` | `553d7e6` | `36ef5ed` (8 days stale) |

**All measurements in this ADR are taken against `origin/*` from the GitHub clone**
(verified equal to origin after `git fetch`). Measuring the "Claude code free"
clone's local branches is the wrong-tree trap `CLAUDE.md` warns about — it already
produced a wrong number this session (`app/obs/` on `jv-v1` measured **0** locally
vs the true **2** on origin). This is logged as a correction in `README.md`, and it
compounds **FM-20 (wrong branch deployed)**: the plan work exists only in an
unpushed clone.

---

## Decision (recommended)

**Adopt the `latency-eval` *engine* as the production trunk for cohort 1** — but
not under that name and not by shipping the branch wholesale:

1. **Cut a clean, honestly-named release branch** (e.g. `release/cohort-1`) from
   `origin/latency-eval` at a pinned commit. Harden *there*, not on `latency-eval`.
2. **Keep the latency levers OFF** (`WS_A_FAST_FIRST_CHUNK`,
   `WS_C_SEMANTIC_ENDPOINT`, `WS_B` unwired) — verified default-`false`, so the
   release boots byte-identical to today's live engine.
3. **Re-charter `LATENCY.md`**: scope its "lab, not a release candidate" language
   to the WS-A/B/C levers, not the branch, so the next reader is not misled exactly
   as this plan was.
4. **Deploy per-clinic from the release branch** (tenant by env/config), replacing
   the current per-clinic branches under change control (verify green + shadow-test
   before moving a live Twilio number).

**Not `main`** (a divergent, older product lineage), **not `vitaledge-onboarding`
or `jv-v1-onboarding` as-is** (they lack the safety and instrumentation the base
must carry).

This is `BRANCH_DECISION.md`'s "likely outcome 1" — *the charter is stale, the
branch has become the real trunk* — confirmed by the code. The plan proceeds
essentially unchanged.

---

## Options Considered

### Option A — `latency-eval` engine as trunk (re-chartered) · **RECOMMENDED**
| Dimension | Assessment |
|---|---|
| Porting cost | **~none** — it is the superset |
| Safety posture | **Best** — only branch with `clinical_screening.py` + full obs + latency instrumentation |
| Risk to `flow.py` | **None** — `handle_transcript` is byte-identical to the live branches |
| Doc churn | **Low** — plan + register already assume it |
| Baggage | Misleading name + charter; a live-number migration to sequence |

**Pros:** most-hardened engine; carries every live clinic's config; boots
byte-identical to live (levers off); zero cross-branch porting; keeps the 10-day
plan intact.
**Cons:** requires re-chartering `LATENCY.md` and (recommended) a rename/new
branch; the live JV and Vital Edge numbers must be migrated onto it under change
control; must inspect the 6 commits it is *behind* the live branches (cheap).

### Option B — `vitaledge-onboarding` (live) as base, port from `latency-eval`
| Dimension | Assessment |
|---|---|
| Porting cost | **Med–High** — see cost matrix |
| Safety posture | Good obs (18) but **must port `clinical_screening.py`** and wire it under time pressure |
| Risk | Manual, file-by-file integration of ~20 engine files — the exact exercise `CLAUDE.md` says to avoid on a deadline |

**Pros:** a live-chartered branch, no "lab" baggage; already has the full obs
subsystem; only 6 commits behind latency-eval.
**Cons:** must port latency instrumentation + the locked baseline +
`clinical_screening.py` (466 lines, **safety-critical**) + ~20 engine-hardening
files + 13 test files, each reviewed by hand. Strictly more work than A for a
less-instrumented result. Every plan doc must be re-pointed from latency-eval.

### Option C — `main` as base
| Dimension | Assessment |
|---|---|
| Porting cost | **Highest** |
| Divergence | 149 commits of main not in the cluster; cluster has 125–200 not in main |
| Clinic model | **Older/divergent** — `demo/jv/theorem`, no `vital_edge`, `jv` not `jv_v1` |

**Pros:** cleanest story *if* the four services are collapsed to one now.
**Cons:** throws away 125–200 commits of onboarding-cluster work; no latency
instrumentation, no `clinical_screening.py`; wrong clinic layout. Collapsing to one
service is a post-meeting project (Phase 6 roadmap), not a 10-day move.

### Option D — `jv-v1-onboarding` (live JV, the likely first cohort) as base
**Rejected.** It is the *least* complete engine: `app/obs/` = **2** files (vs 18),
no latency instrumentation, no `clinical_screening.py`, 81 commits behind
latency-eval. Choosing the clinic that ships first is not the same as choosing the
engine; the engine it currently runs is the one to *replace*, via A.

---

## Evidence — the four dimensions

### 1. Deployment reality — **partially blind; NEEDS DASHBOARD**
Derivable from the repo: four services (`main`, `jv-v1-onboarding`,
`vitaledge-onboarding`, `latency-eval`), `autoDeploy: true`, no branch pin;
Frankfurt region; tenant by env. Clinic *layout* per branch: `main` =
`demo/jv/theorem`; the other three = `demo/jv_v1/theorem/vital_edge`.

**Not determinable from the repo — please paste, per service (×4):**
- Connected **branch** (Settings → Build & Deploy) and **Auto-Deploy** on/off.
- **Current live commit SHA + deploy timestamp** (Events → latest "Deploy live").
- **Region** (confirm Frankfurt).
- Env values: **`CLINIC_NAME`**, `CLINIC_PHONE`, **`ACUITY_CALENDAR_ID_*`**,
  `OBS_*_ENABLED`, `SMS_ENABLED`, `WS_A_FAST_FIRST_CHUNK`, `WS_C_SEMANTIC_ENDPOINT`.
- From Twilio: **which phone number(s)** route to each service (the number's Voice
  webhook URL, matched to each service's `PUBLIC_URL`).

This decides *migration sequencing* (which live number moves first), **not the
engine choice** — A is a near-superset regardless of the dashboard.

### 2. Divergence — engine only (counts are not the point)
Ahead/behind on `origin/*` (left-only / right-only commits):

| pair | ahead/behind | reading |
|---|---|---|
| `latency-eval` vs `main` | **200 / 149** | deeply diverged lineages |
| `latency-eval` vs `vitaledge` | **53 / 6** | nearly the same branch |
| `latency-eval` vs `jv-v1` | **81 / 6** | latency-eval is a near-superset |
| `vitaledge` vs `jv-v1` | 40 / 12 | jv-v1 trails the cluster |
| `jv-v1` vs `main` | 125 / 149 | — |
| `vitaledge` vs `main` | 153 / 149 | — |

`latency-eval` → `vitaledge` divergence, classified: **engine 20 · tests 13 ·
latency 7 · obs 5 · docs 6 · clinic-config 2 · notif 2.** The 20 engine files are
core, not cosmetic: `connection.py`, `receptionist_tools.py`, `router.py`,
`stt_stream.py`, `chunker.py`, `turn_handler.py`, `clinical_screening.py`,
`llm_stream.py`, `intent_analyzer.py`, `name_capture.py`, `clinic_config.py`,
`config.py`, `main.py`, `call_logger.py`, prompts + summary/handoff tools. This is
where the battle-hardening landed. **`flow.py` is *identical* across all three**
(`latency-eval` = `vitaledge` = `jv-v1`), so the highest-risk artefact is unchanged
by the choice.

### 3. Cost to harden each (what is missing, what porting it costs)
| Base | Missing vs the ideal engine | Build cost |
|---|---|---|
| **A `latency-eval`** | nothing | **~0** (re-charter + migration verify) |
| B `vitaledge` | `latency_timing.py` + locked baseline, `clinical_screening.py` (wire, safety), ~20 engine files, 13 tests | Med–High |
| C `main` | all of B **plus** the 4-clinic config, `vital_edge`/`jv_v1`, 200 cluster commits; reconcile 149 main commits | Highest |
| D `jv-v1` | all of B **plus** the obs subsystem (16 more modules), 29 tests | High |

Present only on `latency-eval` (origin): `app/media_streams/latency_timing.py`,
`LATENCY.md`/`LATENCY_HARNESS.md`/`LATENCY_WS-C.md` + `lat_*` baseline data,
`app/media_streams/clinical_screening.py`. Obs is **not** latency-eval-unique — it
is full (18) on `vitaledge` and near-full (17) on `main`; only `jv-v1` is bare (2).

### 4. `LATENCY.md` prime directive — **stale charter for the branch; live constraint for the levers**
The directive (§0) governs the *latency experiment*: "total isolation from live…
lab, not a release candidate… every lever behind its own env flag, default OFF, so
the branch boots byte-identical to live." Two facts show it has been overtaken for
the *branch* but is still true for the *levers*:
- **Overtaken:** the same file's campaign note concedes *"fixes now land on
  latency-eval itself,"* and the code proves it — 20–22 engine-hardening files
  ahead of the live branches, and the only copy of `clinical_screening.py`.
- **Still true (verified):** `WS_A_FAST_FIRST_CHUNK` and `WS_C_SEMANTIC_ENDPOINT`
  both `os.getenv(…, "false")`, gated at their call sites — the levers are inert by
  default. `latency-eval` minus the flags *is* production-shaped code.

**Verdict:** keep the isolation rule for the levers (they stay OFF and are promoted
only by separate reviewed PRs); retire it as a description of the branch, which is
now the de-facto trunk. Rewrite `LATENCY.md` accordingly (Action 3).

---

## Trade-off analysis
The only serious contest is A vs B, and it reduces to one comparison:
**re-chartering a document (A) is cheaper and safer than porting ~20 engine files
plus a 466-line safety-critical module under a 10-day deadline (B).** A also wins on
the thing that matters most for a *clinical* product: `clinical_screening.py`
(deterministic emergency intercept + cauda-equina-class red-flag gating, **FM-04**)
exists and is wired only on latency-eval; on A it is present, on B/C/D it is a
time-pressured port. The residual cost unique to A — migrating the live JV and
Vital Edge numbers onto the trunk — is real but is change-control work, not
engineering, and the dashboard facts scope it.

---

## Consequences

**Easier:**
- Phase 0 latency mapping shrinks to a few hours (instrumentation already on the base) — as the plan anticipated.
- No cross-branch porting of obs / latency / clinical screening — saves ~1–2 days vs B and de-risks Phase 1–3.
- The plan and `FAILURE_MODE_REGISTER` need no re-pointing; A is what they assume.

**Harder / new work:**
- **Live-number migration.** JV (`jv-v1-onboarding`) and Vital Edge
  (`vitaledge-onboarding`) currently answer real calls on the *old* engine. Moving
  them onto the trunk is a verified cutover per clinic (green suite + shadow call +
  rollback ready). Sequence it with the dashboard facts. This addresses **FM-14
  (engine drift across branches)** by starting convergence.
- **Enabling `clinical_screening` changes call behaviour** on the live clinics
  (it will start intercepting/for-gating on red flags). That is a safety *win* but
  needs clinical sign-off and the FM-04 verification (does it cover *escalation*,
  and is the physiotherapy red-flag vocabulary — e.g. cauda equina — adequate?)
  before it fronts a real line.
- **Re-charter `LATENCY.md`** and rename/relocate the base so "latency-eval" stops
  meaning two things.
- **Push the plan docs / consolidate the clones** (FM-20). The unpushed
  `4630dda` and the stale second clone are a live "wrong branch deployed" hazard.

**To revisit post-meeting:** collapsing the four services to one via runtime
tenancy (Phase 6 roadmap) — the eventual home for the "one engine, many clinics"
model this ADR only starts.

---

## Action items
1. [ ] **Owner: confirm the base** (A, or override) and paste the § Deployment-reality dashboard facts.
2. [ ] Cut `release/cohort-1` from `origin/latency-eval@022f816`; harden there.
3. [ ] Verify all lever flags default OFF in the deployed env (dashboard) — release must boot byte-identical to live.
4. [ ] Inspect the 6 commits `latency-eval` is *behind* each live branch — confirm no live-only fix is lost.
5. [ ] Re-charter `LATENCY.md` (lever-scoped isolation) and record the base in every plan doc.
6. [ ] Push `4630dda` / reconcile the two clones; add a branch pin or deploy guard (FM-20).
7. [ ] Gate `clinical_screening` enablement on clinical sign-off + FM-04 verification before any live cutover.

---

## Decision record
- **Decision:** **Accepted — Option A.** The production base is `release/cohort-1`, cut from
  `origin/latency-eval@022f816` (pinned). Harden here; keep the latency levers OFF; deploy
  per-clinic from this branch under change control. Never merge or push to the live branches.
- **Date:** 2026-07-21
- **Reasoning:** `latency-eval` is the most-hardened engine and a near-superset of the live
  onboarding branches (53 ahead / 6 behind `vitaledge`, 81 / 6 behind `jv-v1`); it uniquely
  carries `clinical_screening.py`, the latency instrumentation, and the full obs subsystem,
  while `flow.py` is byte-identical to the live branches. Verified: WS-A/WS-C levers default
  OFF (`app/media_streams/config.py:170,194` — `.strip().lower() in ("true",…)`) and WS-B is
  unwired, so the branch boots byte-identical to live. Porting cost ≈ 0 vs days of hand-porting
  for any other base.
- **Consequences for the plan:** plan proceeds unchanged. Adds: (1) re-charter `LATENCY.md`
  (lever-scoped isolation); (2) migrate the live JV and Vital Edge numbers onto this engine
  under change control (dashboard-sequenced); (3) FM-01 closed here before any live cutover
  (done this session — see the FM-01 commit + `tests/regression/`).
