"""Pair up the Pile B divergences by section, canonical vs theorem-onboarding."""
import difflib
import os
import re
import sys

S = sys.argv[1]
can = open(os.path.join(S, "pb_can.txt"), encoding="utf-8").read().split("\n")
th = open(os.path.join(S, "pb_th.txt"), encoding="utf-8").read().split("\n")

HEAD = re.compile(r"^[A-Z][A-Z0-9 ,'&/()—–-]{8,}$")


def sectionise(lines):
    out, sec = {}, "(top)"
    for l in lines:
        if HEAD.match(l.strip()):
            sec = l.strip()
        out.setdefault(sec, []).append(l)
    return out


cs, ts = sectionise(can), sectionise(th)
rows = []
for sec in ts:
    a, b = cs.get(sec, []), ts[sec]
    if a == b:
        continue
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        rows.append({
            "section": sec,
            "tag": tag,
            "canonical": [x for x in a[i1:i2] if x.strip()],
            "theorem": [x for x in b[j1:j2] if x.strip()],
        })

rows = [r for r in rows if r["canonical"] or r["theorem"]]
print(f"  {len(rows)} divergences across {len({r['section'] for r in rows})} sections\n")
for n, r in enumerate(rows, 1):
    kind = {"replace": "REWORDED", "delete": "CANONICAL-ONLY",
            "insert": "THEOREM-ONLY"}[r["tag"]]
    print(f"  [{n:02d}] {r['section'][:44]:44} {kind:15} "
          f"can={len(r['canonical'])} th={len(r['theorem'])}")

import json
json.dump(rows, open(os.path.join(S, "pileb_rows.json"), "w", encoding="utf-8"),
          indent=1, ensure_ascii=False)
print(f"\n  written: pileb_rows.json")
