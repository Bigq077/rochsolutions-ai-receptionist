# Triaging real calls (`--obs`)

Suite results and production calls share almost no vocabulary. This is what
changes when the input is the `calls` table (`app/obs/models.py`) rather than
`tests/auto/results/*.json`.

Requires `origin/latency-eval` (22 `app/obs/` modules). `jv-v1-onboarding` has 2.

---

## Access

```bash
python .claude/skills/susie-triage/scripts/collect_failures.py --obs --days 7
#   --clinic <id>        one clinic
#   --since YYYY-MM-DD   explicit window
```

Needs `OBS_DATABASE_URL` (or `DATABASE_URL`). **Not** `OBS_CAPTURE_ENABLED` —
that flag gates *writing* new calls, so anything already captured is readable
with it off.

## Relationship to `app/obs/weekly.py`

`weekly.py` is the existing Monday ritual. Run it first:

```bash
python -m app.obs.weekly --days 7
```

This skill **wraps it, never replaces it**. The headline figures come from the
same `app/obs/reports.summarise()`, so the two cannot disagree about volume,
booking rate or mean score. What the skill adds:

| `weekly.py` | `--obs` triage |
|---|---|
| volume, booking rate, mean score | same numbers, same function |
| `failure_tags` counted | tags **clustered to root cause** |
| bottom decile **by score** | ranked by **patient impact** |
| — | derived signals the score cannot see |
| — | rollup by `build_sha` and `final_state` |

### The gap that matters

**`weekly.py` ranks by `quality_score`, and the worst defects do not score
badly.** A phantom booking reads as a clean, successful call: the caller was
greeted well, a slot was agreed, Susie confirmed. What failed is the *write*,
which no transcript can show. Those calls sit near the top of the score
distribution and never enter the bottom decile.

The collector prints them under **"INVISIBLE TO `app/obs/weekly.py`"**. Read
that section first — it is the reason the mode exists.

## Derived signals

Computed in `scripts/obs_source.py`; nothing else in the pipeline reports them.

### `PHANTOM_BOOKING` — severity 1

`booking_confirmed` is set, but neither `acuity_booking_id` nor
`calendar_event_id` came back. A caller believes they have an appointment that
does not exist — `INCIDENT.md` severity 1 exactly.

Such a call typically stores `success=true`, `outcome="booked"`,
`quality_score=5`, `failure_tags=[]`. Every existing signal calls it perfect.
**Always rank it first**, and treat each one as a patient to phone.

### `DORMANT_SCREENING` — severity 2

`screening.arm_paths` maps `{screen_id: "trigger"|"orphan"}`. An `orphan` with no
`trigger` anywhere means a red-flag clinical screen armed and never fired.
`models.py` records that this signature previously took a human reading a full
log to spot, in the 2026-07-25 sweep.

### Outcome- and score-derived

`ABANDONED`, `NO_BOOKING`, `MISROUTED` from `outcome`; `LOW_QUALITY` from
`quality_score <= 2`. These are **terminal** — like `booking_confirmed` in suite
mode, they say a call went wrong without saying where. Split them by
`final_state` before reporting.

## Severity ranking

| Sev | Signals |
|---|---|
| 1 | `PHANTOM_BOOKING`, `booking_error`, `hallucination`, `wrong_info` |
| 2 | `DORMANT_SCREENING`, `missed_escalation`, `wrong_service_fit` |
| 3 | `dead_end`, `loop`, `ABANDONED`, `caller_frustration` |
| 4 | `LOW_QUALITY`, `NO_BOOKING`, `MISROUTED` |

Rank by this, never by count. Two phantom bookings outrank forty clumsy phrasings.

## Clustering dimensions

- **`final_state`** — the obs equivalent of a stall state.
- **`build_sha`** — indexed precisely because "grouping defects by build is the
  main question asked of this table". A cluster confined to one `build_sha` is a
  regression with a known blast radius and a known culprit deploy.
- **`clinic_id`** — a cluster in one clinic is usually config
  (`clinic.json`), not engine.

## Handing a cluster on

Every row prints `python -m app.obs.show <call_sid>`. From there:

```bash
python -m app.obs.to_scenario <call_sid>   # PII-redacted regression scenario
```

That is the handoff to `susie-debug` — the real call, not a description of it.
See `susie-debug/references/mining-scenarios.md`.

## Caveats

- **`slot_offers` is NULL before 2026-09-03** by absence, not by measurement, and
  no back-fill exists.
- **`build_sha` is NULL before 2026-07-31.**
- **`latency` is NULL whenever `LATENCY_TIMING` is off**, which is the default —
  a clinic that never enabled it stores nothing rather than zeros. Absent latency
  data is not fast latency.
- **Unjudged calls have no `quality_score` or `failure_tags`.** They can still
  carry derived signals, which is why `PHANTOM_BOOKING` detection does not
  require the judge.
- Transcripts are **special-category PII**. Quote one short line as evidence;
  never paste raw transcripts into a report, and use `to_scenario` (which asserts
  redaction) for anything committed.
