"""A caller who names only a body part gets a REASON, not a warm noise.

Measured over 400 stored calls on 2026-09-07: 171 complaint openings named a
body part, and **110 of them named nothing else** — "book me in for my left
ankle, it's nothing serious". `condition_knowledge` needs a description to
match on, so those turns had no anchor at all and the wording was free
generation:

    "that left ankle's been giving you trouble —"
    "a bit of a dodgy ankle —"
    "got it —"

while the 61 that DID describe something got real content off the library:

    "that first-few-steps stiffness that warms up as you get going is the
     classic Achilles pattern"

That split was the deliberate outcome of dropping `condition_knowledge.
mandatory` on 2026-09-05, which stopped the model INTERROGATING to manufacture
specificity it had no entry for (CA3c3ca344, 8.7s to first content, then it
talked over itself). The compulsion had to go. What was missing was something
honest to say instead.

THE SHAPE OF THE FIX, and the two things it is NOT:

  * NOT a phrase pool. Fixed wording would say the same words every call, which
    is the canned sound this exists to avoid. Content is anchored, wording is
    the model's — the condition library already proves that renders naturally.
  * NOT a description of the anatomy. "Ankles are usually the joint or the
    tendons around it" is a leaflet. A receptionist who knows physiotherapy
    tells you WHY it is worth looking at. That test — would this line fit any
    body part equally? — is written into the block, because it is the thing
    that keeps it short, warm and non-diagnostic.

northgate only. JV and Theorem carry real patients and this is clinical copy;
it does not reach them until their practitioner has read it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.clinic_config import get_clinic
from app.prompts.clinic_template_prompt import build_clinic_prompt

HEADING = "BODY PART NAMED, NOTHING DESCRIBED"
POINTER = "use the REGION LIBRARY"


def _prompt(clinic_id: str) -> str:
    a, b = build_clinic_prompt({"clinic_id": clinic_id}, get_clinic(clinic_id))
    return (a or "") + (b or "")


def _regions(clinic_id: str = "northgate"):
    return ((get_clinic(clinic_id).get("region_knowledge") or {})
            .get("regions") or [])


# ── It exists, and it reaches the model ─────────────────────────────────────

def test_the_block_renders_for_northgate():
    text = _prompt("northgate")
    assert HEADING in text
    assert "REGION LIBRARY" in text


def test_every_body_part_the_engine_recognises_has_an_entry():
    """The owner's bar: no body part falls back to a generic response.

    Measured against `hold_speech._BODY` — the list the engine already uses to
    decide that a caller has named a body part at all — so the two cannot drift
    apart without this failing.
    """
    import app.hold_speech as hs

    keywords = {k.lower() for r in _regions() for k in (r.get("keywords") or [])}
    conditions = json.dumps(
        (get_clinic("northgate").get("condition_knowledge") or {})
        .get("conditions") or []
    ).lower()

    missing = []
    for part in re.findall(r"[a-z]+", hs._BODY):
        if any(part in k for k in keywords):
            continue
        # A part that is only ever said as a named CONDITION (sciatica) is
        # answered by the richer library and does not need a region line.
        if part in conditions:
            continue
        missing.append(part)
    assert not missing, (
        f"{missing} can be named by a caller and have no region line — they "
        f"would fall back to the generic acknowledgement this replaces"
    )


@pytest.mark.parametrize("said,expect", [
    ("my ankle", "ankle"),
    ("my left ankle, nothing serious", "ankle"),
    ("my shoulder's been bad", "shoulder"),
    ("it's my knee", "knee"),
    ("my lower back", "back"),
    ("my neck", "neck"),
    ("my elbow", "elbow"),
    ("my wrist", "wrist"),
    ("my hip", "hip"),
    ("my calf", "calf"),
    ("my heel", "heel"),
    ("my hamstring", "hamstring"),
    ("my shin", "shin"),
    ("my jaw", "jaw"),
])
def test_the_openings_that_actually_happen_all_resolve(said, expect):
    """Every one of these is a shape seen in the corpus or a near neighbour."""
    hit = [r for r in _regions()
           if any(k in said for k in (r.get("keywords") or []))]
    assert hit, f"{said!r} matches no region"
    assert expect in " ".join(
        k for r in hit for k in r.get("keywords") or []
    ), (said, [r["name"] for r in hit])


# ── The voice rules, asserted rather than hoped for ─────────────────────────

def test_no_line_would_fit_any_body_part_equally():
    """The stated test for a failed line, applied to the copy itself.

    A region line that never names anything specific to its own region is the
    generic acknowledgement wearing a library entry's clothes.
    """
    generic = []
    for r in _regions():
        und = (r.get("understanding") or "").lower()
        if not any(k.split()[0] in und for k in (r.get("keywords") or [])):
            # It may instead name something specific to the region rather than
            # the part itself ("footwear", "training-load", "clenching").
            if len(und.split()) < 12:
                generic.append(r.get("name"))
    assert not generic, f"these read as generic filler: {generic}"


def test_no_line_diagnoses_the_caller():
    """Non-diagnostic is a hard rule and this copy is clinical copy."""
    banned = re.compile(r"\byou (?:have|'ve got|probably have|likely have)\b"
                        r"|\byour (?:condition|diagnosis) is\b", re.I)
    for r in _regions():
        assert not banned.search(r.get("understanding") or ""), r.get("name")


def test_no_line_argues_with_nothing_serious():
    """The commonest opening in the corpus dismisses the problem. Overruling a
    caller who has told you it is minor is a receptionist overstepping, and the
    block says so — this pins that the copy obeys its own rule."""
    banned = re.compile(r"more than you think|worse than it (?:seems|feels)"
                        r"|don'?t ignore|shouldn'?t ignore", re.I)
    for r in _regions():
        assert not banned.search(r.get("understanding") or ""), r.get("name")
    assert "NEVER ARGUE WITH" in _prompt("northgate")


def test_the_block_forbids_a_clinical_question():
    """The whole point is that this replaces digging, not that it licenses it."""
    text = _prompt("northgate")
    assert "ASK NOTHING CLINICAL" in text
    assert "NEVER ASK A CLINICAL QUESTION IN ORDER TO SATISFY THIS STEP" in text


def test_the_block_holds_the_length_line():
    """This is the caller's FIRST turn and the one they most often talk over.
    Verbosity is the largest open caller-experience item; a region line that
    grows into a paragraph makes it worse, not better."""
    text = _prompt("northgate")
    assert "ONE sentence" in text
    for r in _regions():
        words = len((r.get("understanding") or "").split())
        assert words <= 34, (r.get("name"), words)


# ── Scope ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("clinic_id", ["jv_v1", "vital_edge"])
def test_no_other_clinic_gets_the_block(clinic_id):
    """Clinical copy, so it waits for the practitioner who owns those patients."""
    assert HEADING not in _prompt(clinic_id)


def test_a_clinic_without_the_library_is_never_pointed_at_it():
    """The dangling-pointer trap, and the reason all three clinics are rendered
    rather than reviewed.

    The first draft put the REGION LIBRARY instruction into the shared step 2,
    so jv_v1 — which ships no region library — was told to use a block its
    prompt does not contain. That is the false-premise pattern this file has
    been bitten by three times, most recently when the screening block stopped
    rendering and three sentences went on deferring to it.
    """
    for clinic_id in ("jv_v1", "vital_edge"):
        text = _prompt(clinic_id)
        assert POINTER not in text, clinic_id
    assert POINTER in _prompt("northgate")


def test_jv_keeps_the_wording_it_had():
    """Byte-for-byte, so this change is audibly nothing for JV's callers."""
    assert "brief warm acknowledgement is the RIGHT answer" in _prompt("jv_v1")


def test_the_copy_is_marked_for_review():
    """It has not been read by a physiotherapist yet, and the file says so."""
    raw = json.loads(
        (Path(__file__).resolve().parents[2] / "app" / "clinics" / "northgate"
         / "clinic.json").read_text(encoding="utf-8")
    )
    assert "MARK_REVIEW" in (raw["region_knowledge"].get("_note") or "")


# ── The entries are NOTES, and must stay notes ──────────────────────────────
#
# The first version was written as finished, speakable sentences. Two live
# calls on 2026-09-06 (build 004f838d) read them out almost verbatim:
#
#   entry   "Ankles are often under-rehabbed after a knock, so they grumble
#            on or feel a bit unsteady months later…"
#   spoken  "ankles are often under-rehabbed after a knock, so it's worth
#            getting it properly looked at even when it feels minor"
#
#   entry   "Shoulders stiffen up quickly if they're left alone, so catching
#            one early makes a real difference…"
#   spoken  "shoulders stiffen up quickly if they're left alone, so it's good
#            you're getting it looked at"
#
# Hand a model a good sentence and it says the sentence. The condition library
# escapes this by accident — its entries are long and dense enough that they
# MUST be compressed, and compression forces paraphrase. These are short, so
# the shape has to do that work instead.

SEPARATOR = "·"


@pytest.mark.parametrize("region", _regions(), ids=lambda r: r["name"])
def test_an_entry_is_not_a_speakable_sentence(region):
    """Fragment lists, so there is no sentence available to lift."""
    und = region["understanding"]
    assert SEPARATOR in und, (
        f"{region['name']}: written as prose again. Entries are notes — "
        f"fragments joined by '{SEPARATOR}' — precisely so they cannot be read "
        f"out. A sentence here WILL be spoken verbatim; it happened twice."
    )
    assert not und.rstrip().endswith("."), region["name"]
    assert not und[:1].isupper(), (
        f"{region['name']}: starts like a sentence, which invites reciting it"
    )


#: Words a receptionist would never say to a patient. Kept out of the ENTRIES
#: as well as banned in the block, so that even a lazy lift is harmless —
#: belt and braces, because "under-rehabbed" reached a caller once already.
_CLINICAL_REGISTER = (
    "under-rehabbed", "rehabbed", "rehab", "loading", "overloaded",
    "tendinopathy", "presentation", "pathology", "biomechanic", "modality",
    "conservative management",
)


@pytest.mark.parametrize("region", _regions(), ids=lambda r: r["name"])
def test_no_entry_uses_clinical_register(region):
    low = region["understanding"].lower()
    found = [w for w in _CLINICAL_REGISTER if w in low]
    assert not found, (
        f"{region['name']}: {found} is a word you would only ever see written "
        f"down. Say 'built back up', 'how much you're on it', 'a tendon that's "
        f"had too much'."
    )


def test_the_block_bans_the_clinical_register_out_loud():
    """Belt: the entries avoid it. Braces: the block forbids saying it."""
    text = _prompt("northgate")
    assert "PLAIN WORDS ONLY" in text
    assert "under-rehabbed" in text, (
        "the ban must NAME the word that actually reached a caller — a general "
        "instruction to 'avoid jargon' is what was already in force"
    )


def test_the_block_says_the_entries_are_notes():
    text = _prompt("northgate")
    assert "NOTES, NOT SENTENCES" in text
    assert "NOTES, NOT LINES TO SAY" in text
