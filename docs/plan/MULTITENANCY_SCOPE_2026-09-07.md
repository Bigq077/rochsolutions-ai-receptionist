# Multi-tenancy — what actually blocks the cohort

7 September 2026. Measured against the tree at `08e99fab`, not read from a plan.

---

## 0. The headline, and it is not what CLAUDE.md says

CLAUDE.md §3 states:

> **But tenant selection happens at deploy time, not runtime.** … one service
> still serves one clinic. That is the structural blocker to onboarding at
> cohort scale.

**That is stale.** Tenant selection is already per-call, by dialled number:

```
[ms_conn] clinic_id resolved: northgate (to=+447366263180)
```

and one service already holds five tenants — from this afternoon's boot log:

```
[deploy] +447426779875 -> vital_edge  | google_calendar_provisional
[deploy] +447380841468 -> theorem_v3  | acuity
[deploy] +447367002651 -> jv_v1       | google_calendar
[deploy] +447366530580 -> theorem_v2  | acuity
[deploy] +447366263180 -> northgate   | google_calendar
```

`TWILIO_TO_CLINIC` says so in its own comment, and names the proof:

> Repointed 2026-08-28 from jv_v1 to `northgate`, the fourth clinic, which
> exists only as `app/clinics/northgate/clinic.json`. **This line IS the Phase 3
> gate: a clinic added with no engine change and no branch of its own,
> answering a real call.**

So the question is not "can the engine be multi-tenant". It is: **which shapes
of clinic can be added without engine work, and which cannot.**

---

## 1. The real blocker: one tenant is hardcoded, and so is its shape

Not multi-tenancy. **Theorem.**

### 1a. Acuity is single-tenant by construction

```python
ACUITY_CONFIG = {
    "theorem": {
        "user_id":  os.getenv("ACUITY_USER_ID"),
        "api_key":  os.getenv("ACUITY_API_KEY"),
        "calendar_ids": {
            "alcester": os.getenv("ACUITY_CALENDAR_ID_ALCESTER"),
            "redditch": os.getenv("ACUITY_CALENDAR_ID_REDDITCH"),
            …
        },
    },
}
ACUITY_CONFIG["theorem_v2"] = ACUITY_CONFIG["theorem"]
ACUITY_CONFIG["theorem_v3"] = ACUITY_CONFIG["theorem"]

def get_acuity_config(clinic_id: str = "theorem") -> dict:
    return ACUITY_CONFIG.get(clinic_id, ACUITY_CONFIG.get("theorem", {}))
```

One entry, un-prefixed env vars, and a fallback that hands **Theorem's
credentials to any clinic_id it does not recognise**. A second Acuity clinic
would silently book into Theorem's diary.

### 1b. Provider routing switches on the clinic NAME, not the provider

Six sites in `receptionist_tools.py`:

```python
if _resolve_clinic_id(session) in ("theorem", "theorem_v2", "theorem_v3"):
    return await _book_appointment_acuity(args, session)
```

— and the same for lookup, cancel, reschedule, patient lookup, availability.

**The information they need is already in config.** `get_clinic("theorem_v3")`
returns `booking_system='acuity'` today. The engine has the answer and asks the
name instead. This is exactly the rule CLAUDE.md sets and this code breaks:

> If you find yourself writing `if clinic == "..."` in `app/`, stop — that is
> the bug, not the fix.

### 1c. Theorem's SHAPE is hardcoded too

`THEOREM_LOCATIONS`, `THEOREM_PRACTITIONERS`, `THEOREM_APPOINTMENT_TYPES` are
module-level dicts describing one tenant's two sites and two practitioners. And
`theorem_v3` renders from its own prompt builder, `_build_theorem_v3`, not the
template — which is why today's read-back steer needed two call sites.

So a clinic that is *Theorem-shaped* — two sites, practitioner routing — cannot
use the template path even if it books through Google.

---

## 2. What onboarding costs today, by shape

| clinic shape | cost | evidence |
|---|---|---|
| single site, Google Calendar, template prompt | **`clinic.json` + `knowledge.md` + one line in `TWILIO_TO_CLINIC` + Google OAuth + deploy** | northgate, added 28 Aug with no engine change |
| provisional-booking variant | same | vital_edge, already runs on `booking_system: google_calendar_provisional` |
| **Acuity** | **engine work — impossible today** | §1a + §1b |
| **two sites / practitioner routing** | **engine work** | §1c |

The first two are the Hands On Money majority case. **That is the good news
CLAUDE.md is hiding.**

---

## 3. What breaks between 4 clinics and 40

Nothing in the routing. Three things in the process:

1. **`TWILIO_TO_CLINIC` is a Python dict in source.** Every onboarding is a code
   edit and a deploy. Fine at five; at 250 it is 250 deploys and a merge queue.
2. **Every tenant shares one Render service and one process.** One bad
   `clinic.json` is caught by `validate_clinic_config()` pre-deploy, and
   `_load_clinic_json` fails to the DEMO clinic with a logged error rather than
   dropping the call — that trade is already made deliberately and documented.
   But a slow tenant's Acuity call still occupies a worker shared with everyone.
3. **Secrets are per-service env vars**, so tenant N's Google/Acuity credentials
   live in the same environment as tenant 1's. Google tokens are already
   per-clinic in Redis (`google_tokens:<clinic_id>`); Acuity is not.

---

## 4. Staged path

Ordered by value per unit of risk. **Only Phase A is a pre-webinar candidate.**

### Phase A — route on the PROVIDER, not the name  ·  ~half a day  · shippable

Replace the six `clinic_id in ("theorem", …)` provider routes with
`booking_system == "acuity"`.

**Behaviour-preserving today, and that is checkable rather than hoped:** the
only clinics reporting `acuity` are `theorem`, `theorem_v2`, `theorem_v3`, and
no other clinic does. Verified this afternoon:

```
jv_v1       google_calendar
northgate   google_calendar
vital_edge  google_calendar_provisional
theorem*    acuity
```

**One caution, and it is recorded from a previous mistake:** safety nets gated
on `booking_system ==` have silently excluded clinics before. This phase must
change **provider routing only** — the branches that choose an executor — and
must not touch any branch that gates a guard, a screen or a write rule. Those
stay on the clinic id until each is considered on its own.

Unblocks: routing for a second Acuity clinic. Does **not** finish it — §1a still
hands over Theorem's credentials.

### Phase B — per-clinic Acuity credentials  ·  ~a day

`ACUITY_CONFIG` becomes per-clinic with prefixed env vars, and
`get_acuity_config` **stops falling back to Theorem** — an unknown clinic must
fail loudly, not book into someone else's diary. Needs Render env work.

Completes "a second Acuity clinic is possible".

### Phase C — onboarding without a deploy  ·  ~a day

Move `TWILIO_TO_CLINIC` out of source. Turns each onboarding from a code change
into a data change.

### Phase D — generalise the Theorem shape  ·  the big one, post-webinar

Multi-site and practitioner routing into `clinic.json`; retire
`THEOREM_LOCATIONS`/`PRACTITIONERS`/`APPOINTMENT_TYPES` and `_build_theorem_v3`.
This is the one that makes every clinic shape template-able, and it is a
refactor of the same order as Phase 2 of the slot work.

---

## 5. Two things found in passing

* **`app/clinics/demo/clinic.json` is not JSON.** It contains Python source — a
  fragment of `clinic_config.py`. Harmless today, because `demo` and `theorem*`
  resolve from the legacy `CLINICS` dict before the loader is reached, so the
  file is never read. It becomes a live fault the moment anyone migrates demo to
  the data-driven path, which is exactly what Phase D would do. Delete it.
* **CLAUDE.md names the wrong files.** It says clinic names are hardcoded in
  `fast_path.py`, `flows/brain.py` and `providers/acuity.py`. All three are now
  **clean — zero occurrences**. The concentration is `receptionist_tools.py`
  (24) and `clinic_config.py` (20), neither of which it mentions.

---

## 6. What I would tell the webinar audience

The honest position, if asked "can you onboard us":

* **single-site, Google Calendar** — yes, today, and it has been done once with
  no engine change;
* **Acuity** — not yet, and it is Phase A+B, roughly two days;
* **multi-site with practitioner routing** — not yet, and it is a real project.

That is a much better answer than CLAUDE.md's, and it is the true one.
