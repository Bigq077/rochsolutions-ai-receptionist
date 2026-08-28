# Onboarding a clinic

**A clinic is a config file, not a branch.** Adding one touches no engine code
and creates no branch. If you find yourself editing anything under `app/`
except `clinic_config.py`'s number map, stop — that is the bug, not the fix.

This is verified, not aspirational: `tests/tenancy/` stands up a clinic that
does not exist from config alone and fails if `app/` ever learns its name.

---

## The three things

### 1. `app/clinics/<clinic_id>/clinic.json`

Copy the closest working tenant and edit it. `jv_v1` is the reference
`template_v1` clinic (physio, Google Calendar); `vital_edge` is the reference
provisional clinic (the practitioner confirms each booking).

Pick a `clinic_id` that is a slug: lowercase, no spaces.

**The keys that decide behaviour** — these are the gates the engine reads, so
they are how a clinic differs without a code change:

| key | why it matters |
|---|---|
| `prompt_engine: "template_v1"` | runs the free-form loop every live clinic uses. Omit it and the clinic falls into the FlowEngine path, which no live clinic runs. |
| `operational.booking_system` | `google_calendar` books directly; `google_calendar_provisional` writes a PENDING entry and notifies the owner; `manual_handoff` takes a message. |
| `operational.availability_mode` | only for provisional clinics: `published` (events *are* the offer), `diary` (events are the opposite of the offer), `handoff` (offer nothing). |
| `operational.calendar_id` | **the dangerous one — see below.** |
| `locations[]` | one entry for a single-site clinic; its `location_id` is auto-confirmed so the caller is never asked which site. Street address goes in `address_full`, not `address` — `address` is not read by anything. |
| `services[]` | each needs a `service_id`, a caller-facing `name`, and a duration. Nothing is bookable without this. |
| `operational.open_on_bank_holidays` | defaults to **false** — no slots on England/Wales bank holidays. Set it true only for a clinic that genuinely works them. Not to be confused with `opening_hours.bank_holidays`, which is free prose for the model and books nothing. |

### 2. A number in `app/clinic_config.py` → `TWILIO_TO_CLINIC`

One line, under the `ADD NEW CLIENT HERE` marker. This is the only edit outside
`app/clinics/`, and it is a routing table rather than behaviour.

### 3. Calendar credentials

Google tokens are stored per clinic under `google_tokens:<clinic_id>`. A clinic
is only isolated once it has authorised in its own right — the migration never
adopts the legacy key.

---

## Before you point the number at it

```bash
python -c "import app.clinic_config as c; print(c.validate_clinic_config('<clinic_id>') or 'OK')"
```

Empty list means safe to take calls. It is the checklist below, as code.

---

## The mistakes that do not make a sound

This system's worst failure class is *the call sounds perfect and the booking is
wrong*. Every item here produces a flawless-sounding call.

**The calendar id.** Onboarding is a copy, and the calendar id is an opaque
hash that find-and-replacing the clinic's *name* will not touch. Miss it and
every booking for the new clinic lands in the donor clinic's diary — the caller
hears the right thing, the confirmation SMS is right, and the damage is in
someone else's calendar. `validate_clinic_config` refuses two clinics sharing
one, and refuses `primary` (the service account's own calendar).

Both `operational.calendar_id` and a top-level `calendar_id` work. The
top-level key used to be silently discarded; it is honoured now, because the
legacy clinics use it and it is the obvious thing to reach for.

**A clinic.json that does not parse.** The loader swallows the error and
`get_clinic` falls back to the **demo** clinic: the caller hears the Roch
Solutions demo persona, and the booking goes to the demo calendar. Nothing
raises. A stray comma does this. So does a BOM from a Windows editor — that
one is handled, but the parse error is not, deliberately: dropping a live call
is worse than serving a degraded one. It is logged at ERROR, and
`validate_clinic_config` turns it into a pre-deploy failure. Run the validator.

**A fact in `clinic.json` that no renderer reads.** Adding a key does not put
it in front of the model. Check before concluding the model ignored it:

```bash
python -c "from app.prompts.susie_system_prompt import build_system_prompt_parts as b; \
print('\n'.join(map(str, b({'clinic_id':'<clinic_id>'}))))" | grep -i "<the fact>"
```

**Defaults that differ from canonical.** A clinic inherits SMS off and
reminders off. Both fail silently — nothing errors, the messages simply never
arrive.

---

## Renaming the tenant is the easy half

Onboarding is a copy, so the instinct is find-and-replace the clinic's name.
That handles names. It does not touch **facts**, and the donor's facts are
still in there, still stated as true. Building `northgate` from `jv_v1` on
2026-08-28, a clean name sweep still left:

- the donor's **prices and durations** (`£52`, "40 minutes") in `faq`
- an **evenings-only** book in the FAQ, under daytime `working_hours`
- **home visits** and **acupuncture** offered in `faq` and `treatment_guidance`
  by a clinic that sells neither
- a real **HCPC registration number** and a real **rugby club** in
  `team_and_availability`
- **`stt_variants`** — pure donor identity, and lowercase, so a case-sensitive
  rename misses it entirely. Left alone it teaches the recogniser the previous
  clinic's name as this clinic's name.

**Rewrite these blocks by hand, every time:** `faq`, `team_and_availability`,
`stt_variants`, `modality_labels`, `prompt_facts`, `pricing_and_policies`,
`call_handling`, and the `services` list in `treatment_guidance`.

`condition_knowledge` and `clinical_screening` — the ~49KB clinical layer —
are generic and *should* be inherited. That is the product, not the tenant.

### The two hours blocks

There are two, read by different things, and they must agree:

| block | read by | what it controls |
|---|---|---|
| `operational.working_hours` | the slot generator | the times Susie **offers** |
| `opening_hours.<location_id>` | `clinic_template_prompt.py:942` | the hours Susie **says** |

Update one and not the other and the caller is told you open at half four while
being offered nine in the morning — one call, one config file, nothing logged.
`validate_clinic_config` compares them day by day. Note the two blocks spell a
closed day differently: `null` in `working_hours`, the string `"Closed"` in
`opening_hours`. Both are accepted.

---

## What is *not* config yet

- **Theorem** (`theorem*`) is Acuity-backed and reads its prompt from a Python
  module, not `clinic.json`. It is not an onboarding template; do not copy it.
- The **number map** is a Python dict. Adding a clinic edits one line of code.
- The four live clinics are still separate branches. Collapsing those is the
  rest of Phase 3; a *new* clinic does not need it and must not wait for it.
