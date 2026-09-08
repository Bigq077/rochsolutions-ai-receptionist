"""A confirmation naming a weekday the offer it answers never mentioned.

Result 8 Sep 2026 over 921 calls: 3 hits, one of which is the caller's own
words ("let's go back to that first friday one"). The other two are jv_v1 calls
from the first week of August, fixed by 100b5614 / 7b698f63 on 2 August. Zero
after that. See docs/plan/P6B_REPLAY_2026-09-08.md before re-opening anything
this prints.

Stricter than the first attempt, which kept a stale `offer_day` across
unrelated lookups and reported a clean call (CA7454c983a1) as a defect.

Rule: take the numbered offer block, the caller turn that answers it, and the
VERY NEXT bot block. Only that adjacency. Compare the confirmation's weekdays
against the FULL set the offer named -- a multi-day offer names several, and
picking one of them is not a mismatch.
"""
import os, json, re, collections
from dotenv import load_dotenv
load_dotenv(".env")
from sqlalchemy import create_engine, text as sql

DAY = re.compile(r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", re.I)
NUMBERED = re.compile(r"\bnumber\s*(one|two|three|1|2|3)\b", re.I)
CONFIRM = re.compile(r"(so that's|that's .* at |the slot i have is|you're in for"
                     r"|shall i go ahead and book|all booked)", re.I)

e = create_engine(os.environ["OBS_DATABASE_URL"])
with e.connect() as c:
    rows = list(c.execute(sql(
        "select call_sid, clinic_id, transcript, build_sha from calls "
        "where transcript is not null order by call_sid")))

hits, calls = [], 0
for sid, clinic, t, sha in rows:
    if isinstance(t, str):
        try: t = json.loads(t)
        except Exception: continue
    calls += 1
    blocks, pending = [], []
    for turn in t or []:
        role = str(turn.get("role") or "").lower()
        body = str(turn.get("text") or "").strip()
        if not body: continue
        if role in ("assistant", "bot"):
            pending.append(body); continue
        if pending: blocks.append(("bot", " ".join(pending))); pending = []
        blocks.append(("user", body))
    if pending: blocks.append(("bot", " ".join(pending)))

    for i in range(len(blocks) - 2):
        (r0, b0), (r1, b1), (r2, b2) = blocks[i], blocks[i+1], blocks[i+2]
        if r0 != "bot" or r1 != "user" or r2 != "bot": continue
        if not NUMBERED.search(b0): continue
        offered = {d.lower() for d in DAY.findall(b0)}
        if not offered: continue
        if not CONFIRM.search(b2): continue
        if NUMBERED.search(b2): continue          # a new list, not a confirmation
        said = {d.lower() for d in DAY.findall(b2)}
        if said and not (said & offered):
            hits.append((sid, clinic, sha, sorted(offered), sorted(said),
                         b0[:100], b1[:60], b2[:130]))

print("calls %d   mismatched confirmations %d   in %d call(s)"
      % (calls, len(hits), len({h[0] for h in hits})))
for h in hits:
    print("\n%s  %s  build=%s" % (h[0][:14], h[1], (h[2] or "?")[:8]))
    print("   offered  [%s]: %s" % (",".join(h[3]), h[5]))
    print("   caller   : %s" % h[6])
    print("   confirmed[%s]: %s" % (",".join(h[4]), h[7]))
