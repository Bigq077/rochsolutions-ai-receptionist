# SMS cost guard — BLOCKED, and four premises corrected

**Status: not started. `app/notifications/sms_guard.py` does not exist.**
The brief's own constraint — *"If `app/notifications/sms_guard.py` is missing,
stop and tell me rather than recreating it"* — is the binding instruction here,
so nothing in Tasks 1–5 was applied.

Everything below is groundwork that does **not** depend on the missing file, so
the work is ready to resume the moment it lands.

---

## 1. The blocker, with the evidence

| search | result |
|---|---|
| `find . -name "sms_guard*"` in the working tree | nothing |
| `git ls-tree -r` on `latency-eval`, `production`, `main`, `jv_v2`, `vitaledge-onboarding`, `theorem-onboarding`, `jv-v1-onboarding` | 0 on all seven |
| `git log --all --diff-filter=A` (every file ever ADDED on any ref) | no match |
| same three searches for `tests/regression/test_sms_guard.py` | no match |

It has never existed in this repository on any branch, and it was not deleted —
a deleted file would still appear in the `--diff-filter=A` history. The brief
describes it as *"already in this repo, fully documented — its module docstring
is the spec"*. **That docstring is the specification for Tasks 1–4, and it is
not recoverable from here.** Recreating it would mean inventing the spec and
then implementing against my own invention, which is the one outcome the
constraint exists to prevent.

---

## 2. Four premises in the brief that the code contradicts

Checked before stopping, because they are wrong independently of the missing
file and two of them would have caused damage.

### 2.1 🔴 `SMS_ENABLED` defaults **false**, not true

The brief says *"sms.py:172 defaults it true"* and *"That default is
deliberately ON"*, and instructs recording a correction on that basis.

[app/notifications/sms.py:36](app/notifications/sms.py:36):

```python
_SMS_ENABLED_DEFAULT = "false"
```

read at [:48](app/notifications/sms.py:48) by `sms_enabled()`. **The default is
OFF.** Recording the brief's correction would have written a false statement
into `docs/plan/README.md` — the opposite of what CLAUDE.md's "the code wins"
rule is for. The live log from CA51bb75fe confirms the runtime behaviour:
`[sms] SMS_ENABLED is off — outbound SMS suppressed (not sent)`.

### 2.2 The two defaults no longer disagree — there is ONE owner

The brief frames this as a split between `sms.py` and `booking_sms.py`.
[sms.py:18-20](app/notifications/sms.py:18) records that the split was closed:

> *"SMS_ENABLED gates two things that must never disagree … used to read
> `os.getenv("SMS_ENABLED", …)` with their own copy of the default"*

`booking_sms.py` now holds **no default of its own** — every hit in it is prose
about the suppressed-send case. So **CLAUDE.md §2 is stale on LOCATION but
correct on VALUE**: the default is false, and it lives in `sms.py`, not
`booking_sms.py`. That is the correction actually owed, and it is the reverse
of the one requested.

### 2.3 🔴 The line numbers are wrong by ~24 lines

The brief says insert after line 194, `to = redirect_staff_sms(to)`, and before
line 196, `# Truncate message if too long`.

Actual: `redirect_staff_sms(to)` is at
[sms.py:218](app/notifications/sms.py:218); `# Truncate message if too long` is
at [:220](app/notifications/sms.py:220). Line 194 sits inside the E.164
normalisation block, and a blind insertion there would have landed the guard
**before** normalisation — breaking the brief's own stated requirement that
`to` be E.164 when matched against the test-number list.

The brief's *reasoning* about placement is correct and should be kept; only the
line numbers are stale. The correct anchor is the code, not the number:

```
        _requested = to
        to = redirect_staff_sms(to)
        <-- HERE
        # Truncate message if too long
```

Also note [:216](app/notifications/sms.py:216): `_requested = to` is captured
just above, deliberately, because the redirect means the requested and dialled
numbers can differ. A guard inserted here sees the **dialled** number, which is
what the brief wants — worth stating explicitly, since `_requested` is sitting
right there and is the wrong one to match on.

### 2.4 "Confirm the suite is green before starting"

The suite is **deliberately red** and has been since 26 July. Current baseline
on `latency-eval` @ `12adacd7`: **98 failed / 8,043 passed / 20 skipped**. The
method is to diff failing SETS between two same-day runs, never to look for
green — see `TEST_BASELINE.md`. Treating "not green" as task one would have
started a multi-day project that is not this one.

---

## 3. What DOES check out — the choke point is real

The brief's core design premise is sound, and it is the one worth being sure
about, so it was verified rather than assumed.

`send_sms`'s own comment claims *"Every surface — smart follow-up, owner
alerts, booking SMS — funnels through this method."* Grepping every
`messages.create(` in `app/` returns 20 sites, but **19 are Anthropic** LLM
calls. The discriminator is `from_=`, which only Twilio takes:

```
app/notifications/sms.py:229:   from_=self.from_number,
```

**One Twilio outbound send in the entire codebase.** A guard at that point
covers all of it.

**The second billed path is already closed.** Twilio parses a messaging
webhook's HTTP body as TwiML and delivers any `<Message>` in it — that is how a
patient once received a bare *"ok"*. [routes/twilio.py:1744](app/routes/twilio.py:1744)
now returns `_EMPTY_TWIML` on every inbound-SMS path, with every reply sent
out-of-band through `send_sms`. So it does not bypass the guard, and it is not
a second source of billed segments.

---

## 4. What I deliberately did not do

* **Tasks 1–5.** All depend on the missing module or its docstring.
* **`.env` / `.env.example` (Task 2).** Documenting `SMS_TEST_NUMBERS` and
  `SMS_SEGMENT_LIMIT` while nothing reads them would ship config that silently
  does nothing — the failure the brief itself warns about, and the fourth
  instance of the pattern recorded in `[config-keys-that-never-reach-the-model]`.
  The warning wording the brief asks for is right and should be used verbatim
  when the module lands.
* **`GET /dev/sms` (Task 3).** Its body is `sms_guard.inbox()`.
* **The requested `docs/plan/README.md` correction.** It is factually wrong
  (§2.1). The correct one is §2.2 and is recorded here rather than written into
  the shared log unilaterally.

Constraints honoured throughout: no change to `flow.py`, `connection.py`,
`app/prompts/**` or any SMS template module; no dependencies; no reformatting;
no merge or rebase; nothing applied to `jv-v1-onboarding` or
`vitaledge-onboarding`.

---

## 5. What only you can do

1. **Supply `app/notifications/sms_guard.py`** — with its docstring, which is
   the spec for `to_gsm7`, `check_budget`, `is_test_number`, `record_fake` and
   `inbox`. Their signatures are implied by the brief but their semantics are
   not: what `check_budget` does when `SMS_SEGMENT_LIMIT` is exceeded (raise?
   truncate? log and pass?) changes whether a real patient text can be dropped,
   and that is not a guess worth making.
2. **Confirm the `+447502211207` scope.** That number is `TRANSFER_FALLBACK_NUMBER`'s
   hardcoded default at [app/config.py:66](app/config.py:66) and the handset
   placing the test calls. Silencing it stops **callback pings and owner
   alerts** as well as test noise — on the Theorem call today,
   `drop-off callback ping queued to ***6861` shows the owner-alert path is
   live and separately addressed, but a clinic service that inherits
   `SMS_TEST_NUMBERS` would silence a real destination.
3. **Decide where this lands.** `latency-eval` reaches only the demo line, so
   the £95.51 attributed to test-call alerts is largely demo-line traffic — but
   the segment-encoding half of the saving only reaches patients once it is
   promoted to `production`.

---

## 6. Environment as measured

Primary worktree `C:/Users/.../rochsolutions-ai-receptionist` is on
**`vitaledge-onboarding`**, not `latency-eval`, with 39 untracked plan
documents and modified `CLAUDE.md` / `.gitignore`. The brief says to stop if
HEAD is not `latency-eval` — flagging it rather than stopping, because the work
was done in a dedicated `latency-eval` worktree
(`AppData/Local/Temp/claude/b127-latency-eval`, clean, HEAD `12adacd7`) and the
primary tree was never touched.

⚠️ **Do not run `git clean -fd` in the primary tree** — those 39 documents are
committed only on `latency-eval`.
