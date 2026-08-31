"""Record the Theorem reminders/SMS decision in the runbook and at the prompt."""

# ── 1. the fold runbook ────────────────────────────────────────────────────
p = "docs/FOLD_THE_CLINIC_BRANCHES.md"
s = open(p, encoding="utf-8").read()
anchor = "**Per service, out of hours:**"
assert s.count(anchor) == 1
NOTE = """### ⚠️ THEOREM's two switches are NOT both `true` — and one of them is a trap

**`APPOINTMENT_REMINDERS_ENABLED=false` on Theorem. Owner decision, 2026-08-31:
Mark already has his own reminder system**, and two sets of reminders to the
same patient is the failure being avoided. Set it EXPLICITLY false rather than
leaving it unset: canonical's default is already `false` so unset behaves
correctly, but the banner then reads `OFF (DEFAULT)`, which is
indistinguishable from someone having forgotten. Checked, not assumed: there is
no reminder promise anywhere in `theorem_v3`'s rendered prompt.

**`SMS_ENABLED=true` on Theorem is NOT optional.** Its closing lines promise a
confirmation text three times — on booking, reschedule and cancel — and that
promise is **hardcoded into `_build_theorem_v3`, not gated on `sms_enabled()`**.
Measured: the line renders identically with the variable unset, `false` and
`true`. Unlike the template clinics, where prompt and sender share one owner.

And **the default flips on the fold**: `theorem-onboarding` has
`_SMS_ENABLED_DEFAULT = "true"`, canonical has `"false"`. So Theorem's SMS works
today whether or not anyone set it, and stops the moment its service is
repointed — Susie then tells every caller a text is on its way while none is
sent. Mark's `owner_alerts` (`+447870166861`) ride on the same switch.

    SMS_ENABLED=true                      # required
    APPOINTMENT_REMINDERS_ENABLED=false   # explicit, owner decision

    [deploy] SMS_ENABLED=ON (explicit) | APPOINTMENT_REMINDERS_ENABLED=OFF (explicit)

If SMS is ever genuinely wanted off here, **the closing lines must change
first** — the switch alone turns a true statement into a lie told to every
caller.

"""
s = s.replace(anchor, NOTE + anchor, 1)
open(p, "w", encoding="utf-8", newline="\n").write(s)
print("  runbook updated")

# ── 2. a warning where the promise is actually written ─────────────────────
q = "app/prompts/susie_system_prompt.py"
t = open(q, encoding="utf-8").read()
target = "On success: say exactly this closing message"
assert t.count(target) == 1, "closing-message anchor: %d" % t.count(target)
i = t.rindex("\n", 0, t.index(target)) + 1
indent = t[i:t.index(target)]
WARN = (
    indent + "# ⚠️ THIS PROMISES A CONFIRMATION TEXT, AND THE PROMISE IS UNGATED.\n"
    + indent + "# The same claim appears in the reschedule and cancel closings below.\n"
    + indent + "# None of the three reads `sms_enabled()` — they render identically\n"
    + indent + "# whether SMS_ENABLED is unset, false or true. So on this clinic\n"
    + indent + "# SMS_ENABLED=true is REQUIRED, not optional: turning it off makes\n"
    + indent + "# Susie tell every caller a text is on its way while none is sent.\n"
    + indent + "# Note the default FLIPS on a fold — theorem-onboarding defaults\n"
    + indent + "# true, canonical defaults false — so a repoint alone can cause it.\n"
    + indent + "# If SMS is ever wanted off here, CHANGE THESE LINES FIRST.\n"
    + indent + "# (APPOINTMENT_REMINDERS_ENABLED is a separate switch and is\n"
    + indent + "# deliberately false for Theorem — Mark has his own reminders — and\n"
    + indent + "# that is safe because nothing here promises a reminder.)\n"
)
t = t[:i] + WARN + t[i:]
open(q, "w", encoding="utf-8", newline="\n").write(t)
print("  warning added at the closing lines")

import ast
ast.parse(open(q, encoding="utf-8").read())
print("  parses OK")
