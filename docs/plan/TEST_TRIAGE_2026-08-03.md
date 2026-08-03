# Test triage — the 95 red tests on `latency-eval`

**3 Aug 2026.** Done to answer one question: *can a cherry-pick into the clinic
branches be verified, when both sides start red?*

Scope per instruction: **`latency-eval` only.** The two `tests/auto/test_acuity_live.py`
failures are excluded — they are credential-gated (they SKIP without
`ACUITY_USER_ID`/`ACUITY_API_KEY`, and only run because an untracked `.env`
exists in the working tree). That leaves **93**.

---

## Verdict

**The red suite does not block the port, and the reason is stronger than
"they're only drift".**

The failing set is **deterministic**. Across five full-suite runs today, while
`turn_handler.py`, `llm_stream.py`, `connection.py` and `receptionist_tools.py`
were all substantially modified — six commits, four of them behavioural — the
failing node-ID set was **byte-identical every time**:

```
failed_b37     95 tests  md5=33bbe97e1b65
failed_b41     95 tests  md5=33bbe97e1b65
failed_b42     95 tests  md5=33bbe97e1b65
failed_b44     95 tests  md5=33bbe97e1b65
failed_b38b43  95 tests  md5=33bbe97e1b65
```

That is the property the port needs. A stable baseline means **diffing failing
node IDs detects an introduced regression regardless of how many tests were
already red** — which is exactly the method used to verify every fix today.

What it does *not* mean is that all 93 are harmless. Three are worth acting on,
and 36 sit on a live module. Both are recorded below rather than rounded off.

---

## Bucket 1 — pre-existing and engine-independent · **89 failures, 11 files**

| File | Failures |
|---|---|
| `test_name_collector.py` | 36 |
| `test_embedded_confirmation.py` | 20 |
| `test_mistake_recovery.py` | 8 |
| `test_silence_handler.py` | 5 |
| `test_greeting_builder.py` | 5 |
| `test_faq_continuation.py` | 5 |
| `test_filler_guard.py` | 4 |
| `test_service_fit_priority.py` | 2 |
| `test_policy_gate.py` | 2 |
| `test_service_fit_policy.py` | 1 |
| `test_returning_treatment_plan_exit.py` | 1 |

**Every one of these files was last modified 2026-06-07**, in `1e91f08`
(*"chore: add requirements.txt for Render deployment"*) — a commit that is not
about them. They have been untouched while the engine was rewritten repeatedly:
the JV name redesign (7 Jul), the v3 flow, the `template_v1` prompt engine, the
Gate 5 chain, phonetic TTS substitution.

**Determinism established, not assumed.** `name_collector.py` — the largest
cluster's subject — has **no imports at all** beyond `re`/`logging`/`typing`, and
is itself byte-frozen since the same commit. A self-contained frozen module
driven directly by a frozen test produces a fixed result. It cannot move, and it
cannot mask anything.

Sample failures, showing the shape:

```
assert 'Alcester' in 'What brings you in today at our Awlstuh clinic?'
   -> the engine now applies phonetic TTS substitution; the test pins spelling
assert 'accept' == 'ask'          -> name state-machine decision changed
assert 'fn_confirm' == 'sn_confirm' -> state names/transitions moved on
```

> ### ⚠️ The one thing in this bucket that is NOT settled
>
> `NameCollector` **is live** — `flow.py` imports it at six sites. So 36 failing
> tests describe a module that real calls still run. They have been red since at
> least June, they are not a regression, and they are not caused by anything done
> on this branch — but *"pre-existing"* is not the same as *"not a defect"*.
>
> **`B-33` sits in exactly this area** (a name invented from Susie's own
> utterance, with DTMF phone capture armed behind it). Whether any of these 36
> describe `B-33` or its neighbours is **unresolved and worth an hour** — but it
> is a defect-hunt, not a port blocker.

---

## Bucket 2 — a maintained test now asserting the bug · **2 failures**

`tests/test_dead_air_safety_net.py` — last modified **2026-07-19**, `ca4fb6f`
*"fix(turn-taking): garbage final must not cancel watchdog"*. Recent, and failing.

```
test_no_fire_while_tts_playing        assert fires == []
test_no_fire_while_phone_dtmf_active  assert fires == []
```

Both set a bare flag and expect suppression:

```python
stub._silence_handler._tts_playing = True     # and nothing else
```

**That is precisely the stale-flag state the code was changed to recover from.**
The "Bug A backstop" ([connection.py:13631](../../app/media_streams/connection.py))
exists because a chunk can start — setting `_tts_playing` — and have its finish
callback never fire, after which *both* silence nets stay inhibited and the call
runs to **dead air until hangup**. The fix distinguishes a genuinely-playing
chunk (`_tts_playout_end_mono` in the future) from a stuck flag.

The tests set `_tts_playing` without `_tts_playout_end_mono`, so they construct
the stuck-flag case and then assert the system must sit silent through it. **They
now pin the defect the code was fixed to avoid.**

**Action: update the tests, not the code.** The genuinely-playing case needs
`_tts_playout_end_mono` set into the future; the stuck-flag case deserves its own
test asserting the backstop *does* fire. Until then these two are worse than
noise — they would fail a correct implementation and pass a broken one.

---

## Bucket 3 — real, but on subsystems that are switched off · **2 failures**

| Test | What it says | Reachable today? |
|---|---|---|
| `test_sms_templates.py::test_slot_label_split_day_and_time` | SMS body renders `July 22, 2027` where the test wants `Tuesday 22 July`; location, maps and phone fields render empty | **No** — `SMS_ENABLED` defaults false |
| `test_alerts.py::test_dispatch_disabled_sends_nothing` | dispatch returns `{'sms': ['pipeline_error'], 'sentry': [...]}` when it should return `{}` | **No** — `OBS_ALERTS_ENABLED` defaults false |

Neither can affect a live call today. **Both become live the moment their flag is
flipped**, which is a scheduled activity, not a hypothetical — and the alerts one
means *"disabled"* does not fully disable. They belong to the `B-17`/`B-22` SMS
family already in the register.

---

## What this changes about the port

**It unblocks it.** The stated worry was that porting into a branch with its own
red baseline makes an inherited failure indistinguishable from an introduced one.
That worry is answered by determinism, not by the count: capture the clinic
branch's failing set **before** the cherry-pick, capture it after, and diff. Any
new node ID is yours. That is the same method that verified all six fixes today.

Two conditions on that method, both cheap:

1. **Capture the baseline on each clinic branch first**, before touching it.
   Do not reuse `latency-eval`'s set — the branches are 236–264 commits diverged
   and will have their own.
2. **Run with `-p no:randomly`.** Test order affects nothing here, but the diff
   method depends on a stable comparison.

---

## Honest residue

- **36 failures describe a live module** (`NameCollector`). Not a regression, not
  a port blocker, not yet proven harmless. `B-33` is the reason to look.
- **The two dead-air tests are actively misleading** and should be fixed before
  anyone trusts that file.
- **This triage classified by file, module history and failure shape** — not by
  reading all 93 assertions individually. The determinism evidence is empirical
  and strong; the per-test "drift vs real" call is inferred for Bucket 1 beyond
  the samples shown.
