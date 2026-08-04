"""THEOREM_PORT_PLAN section 7 — the literal audit, re-run against the ported tree.

Every confirmation/CTA literal Theorem's prompt can emit must be visible to the
write-gate patterns Gate 5f uses. A taught closing with no matching gate pattern
is a B-36 waiting to happen: Susie announces a booking that never reached Acuity,
and the call sounds perfect.

This is the PATTERN side only. It does not replay recorded calls —
scripts/audit_gate5_blast_radius.py does, and its own header documents it
reporting "5 changed turns of 740, 0 emptied — clean" on a day Gate 5 was
rewriting callers' booking days. Theorem has no recorded calls on this engine.

THREE THINGS THIS SCRIPT GETS RIGHT THAT A NAIVE VERSION DOES NOT
1. Apostrophes. "That's you rescheduled" contains a single quote, so a naive
   '...' extractor splits mid-word and invents fragments like "s you rescheduled
   — you" that no gate will ever match. Every such "finding" is noise.
2. Gate 5b runs BEFORE 5f. Theorem's cancel closing ends "Is there anything else
   I can help with?" and _false_write_claim stands down on any "?". Dropping
   every quote containing "?" hides the cancel closing entirely; the correct
   model is to strip banned sentences the way sanitise_response does, then test
   what remains. Verified live: 5b removes that sentence.
3. Offers are not claims. "Shall I get you booked in" and "I can get you booked
   in with Mark" SHOULD be invisible to the gate — firing on them would strip
   ordinary speech, which is the Gate 5c over-fire failure mode. Only completed
   assertions are gaps.

Run:  python -m scripts.audit_theorem_literals
Exit: 1 if any taught write-closing is invisible to its gate.
"""
import re
import sys

from app.prompts.susie_system_prompt import _build_theorem_v3
from app.media_streams.turn_handler import (
    _false_write_claim,
    _BANNED_SENTENCE_RE,
    WRITE_FAMILY_BOOKING,
    WRITE_FAMILY_RESCHEDULE,
    WRITE_FAMILY_CANCEL,
)

SESSION = {
    "clinic_id": "theorem_v3",
    "collected": {},
    "selected_location": "alcester",
    "v3_location_confirmed": True,
}

FAMILIES = {
    "booking":    WRITE_FAMILY_BOOKING,
    "reschedule": WRITE_FAMILY_RESCHEDULE,
    "cancel":     WRITE_FAMILY_CANCEL,
}

CLAIM_WORDS = {
    "booking":    ["all booked", "booked in", "you're in for", "you are in for",
                   "that's you booked", "you're all set"],
    "reschedule": ["rescheduled", "moved you", "moved to", "changed to",
                   "switched", "now in for"],
    "cancel":     ["cancelled", "canceled", "taken that off", "taken it off"],
}

# Future/conditional markers. A string carrying one of these is an OFFER, and
# the gate is CORRECT to ignore it — see point 3 in the module docstring.
OFFER_MARKERS = [
    "shall i", "can i ", "could i", "would you like", "i can ", "let me",
    "we were just", "to get you", "i'll get you", "do you want",
    "happy to", "if you'd like", "a bare ",  # "a bare 'I've rescheduled'" is a WARNING
]

CONTRACT_LITERALS = ["use this number", "keypad"]


def quoted_strings(text):
    """Double-quoted spans, plus single-quoted spans that are not apostrophes.

    The lookarounds are the whole point: without them "That's you rescheduled"
    yields a fragment starting mid-word.
    """
    out = []
    for m in re.finditer(r'"([^"\n]{6,300})"', text):
        out.append((m.group(1), text[max(0, m.start() - 90):m.start()]))
    # An apostrophe INSIDE a word ("you're", "I've", "Mark's") is followed by a
    # letter; a real closing delimiter is not. Excluding ' from the character
    # class outright — the obvious first attempt — cannot match any closing at
    # all, because every one of them contains "you're".
    for m in re.finditer(
        r"(?<![A-Za-z])'((?:[^'\n]|'(?=[A-Za-z])){6,300}?)'(?![A-Za-z])", text
    ):
        out.append((m.group(1), text[max(0, m.start() - 90):m.start()]))
    return out


# A prompt quotes forbidden speech as often as it quotes taught speech. Lifting
# a counter-example out of its negation and calling it a taught closing is the
# classic way this audit produces a false category-3 blocker.
NEGATION_MARKERS = [
    "avoid", "do not say", "do NOT say", "don't say", "never say", "a bare",
    "wrong:", "not:", "instead of", "rather than", "forbidden", "banned",
    "do not add", "do NOT add", "warns against", "no close",
]


def is_counter_example(context):
    low = context.lower()
    return any(mk.lower() in low for mk in NEGATION_MARKERS)


def strip_banned(text):
    """Model Gate 5b, which runs before 5f and removes scripted closers."""
    for _name, pattern in _BANNED_SENTENCE_RE:
        text = pattern.sub("", text)
    return text.strip()


def is_offer(s):
    low = s.lower()
    return any(mk in low for mk in OFFER_MARKERS)


def main():
    static, _ = _build_theorem_v3(dict(SESSION))
    quotes = quoted_strings(static)
    print(f"built prompt: {len(static):,} chars, {len(quotes)} quoted strings\n")

    gaps = []
    for fam_name, fam in FAMILIES.items():
        words = CLAIM_WORDS[fam_name]
        cands = [(q, ctx) for q, ctx in quotes if any(w in q.lower() for w in words)]

        # Strip banned sentences FIRST. Theorem's cancel closing ends "Is there
        # anything else I can help with?", which contains "I can " and would
        # otherwise be misread as an offer and skipped entirely.
        claims, offers, counters, probes = [], [], [], {}
        for q, ctx in cands:
            probe = strip_banned(q)
            if not probe:
                continue  # entirely a scripted closer; 5b removes it before 5f
            probes[q] = probe
            if is_counter_example(ctx):
                counters.append(q)
            elif is_offer(probe):
                offers.append(q)
            else:
                claims.append(q)

        caught, missed = [], []
        for q in claims:
            (caught if _false_write_claim(probes[q], fam) else missed).append(q)

        status = "OK " if not missed else "GAP"
        print(f"[{status}] {fam_name:11} claims={len(claims):2} caught={len(caught):2} "
              f"missed={len(missed):2}   (ignored: {len(offers)} offer(s), {len(counters)} counter-example(s))")
        for q in caught:
            print(f"        caught : {q[:100]}")
        for q in missed:
            print(f"        MISSED : {q[:100]}")
            gaps.append((fam_name, q))
        print()

    print("── downstream-parsed contract literals ──")
    for lit in CONTRACT_LITERALS:
        ok = lit in static.lower()
        print(f"   {'OK ' if ok else 'MISSING'} {lit!r}")
        if not ok:
            gaps.append(("contract", lit))

    print()
    if gaps:
        print(f"RESULT: {len(gaps)} category-3 finding(s) — blocks cutover per section 9.")
        return 1
    print("RESULT: clean — every taught write-closing is visible to its gate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
