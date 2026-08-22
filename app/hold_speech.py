"""Hold speech: the one place that decides what the caller hears while waiting.

WHY THIS EXISTS
---------------
Six producers used to answer that question independently — the FillerGuard clip,
llm_stream's delayed ack, ``with_filler``'s tool wrapper, the phone-confirm
branch, the FAQ bridge, and flow.py (dead). They shared no state beyond two
cooldown clocks, one on the session and one on the LLMStream instance, and the
result was audible on the 323 stored calls (25 Jul - 21 Aug 2026):

  * 354 hold phrases across 98 calls; one call contained 17.
  * 175 of 322 were followed by a QUESTION rather than looked-up data — the
    phrase promised a lookup that never happened. The clearest case is a caller
    asking "are you a robot?" and hearing "Just getting that for you..." before
    "No - I'm Susie, Theorem Health's AI receptionist."
  * 32 dead-ends: the phrase was the last thing said before the caller spoke
    again or the call ended.
  * Runs of two and three with no caller turn between them, e.g. "Right with
    you... / Of course - just pulling your appointment up... / That's absolutely
    fine - sorting that for you now..." and only then the answer.

THE TWO RULES
-------------
1. ONE HEAD PER TURN. Not "a cooldown makes a second one unlikely" — one, by
   construction, because there is one decision and one latch. Stacking is not
   mitigated here, it is unrepresentable.

2. A HEAD NEVER CLAIMS WORK THAT IS NOT HAPPENING. The wording is chosen from
   the work, so it cannot describe a diary read when nothing is being read. When
   the work is unknown, the head says nothing about work at all.

Rule 2 is why the 1800ms ack filler was wrong in principle and not merely
mistuned: at 1800ms the tool call has not yet arrived in the LLM stream, so
nothing can know whether a tool is coming, and "Let me just check that..." is a
guess the corpus says was wrong 54% of the time. A phrase that guesses cannot be
fixed by moving it earlier or later; it has to stop guessing.

GRAMMAR
-------
Every head is an unfinished clause ending in a dash or a comma, never a full
stop and never the ellipsis. ElevenLabs renders a terminal ellipsis as a falling
contour plus a trailing pause, and that contour IS the canned-filler sound. An
open head lets the reply complete the sentence:

    "Let me see - Friday the fourteenth at ten's free."

rather than the two disconnected utterances the corpus is full of:

    "Right with you..."  <pause>  "Friday 14th August at ten in the morning is
    available."

``llm_stream.join_after_head`` does the joining; this module supplies the head
and the guarantee that it ends open.

PURITY
------
``decide_hold`` takes plain values and returns a decision. No session, no queue,
no clock, no I/O — the discipline ``expect_slot_presentation`` follows, and for
the same reason: this decides what a patient hears, and the whole stored corpus
can be replayed through a pure function offline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List

EM_DASH = "—"
ELLIPSIS = "…"


class WorkKind(str, Enum):
    """What is actually happening while the caller waits.

    Not a tool name: ``book_appointment`` on a provisional clinic is a
    PENDING_REQUEST, and saying "booking you in" there is a lie the caller only
    discovers one sentence later.
    """

    DIARY_READ = "diary_read"
    PATIENT_LOOKUP = "patient_lookup"
    WRITE_BOOK = "write_book"
    WRITE_MOVE = "write_move"
    WRITE_CANCEL = "write_cancel"
    PENDING_REQUEST = "pending_request"
    UNKNOWN_SLOW = "unknown_slow"
    NONE = "none"


#: Tool name -> the work it does. ``book_appointment`` is resolved through
#: ``work_for_tool``, because its answer depends on the clinic's booking model.
_WORK_BY_TOOL: Dict[str, WorkKind] = {
    "check_availability": WorkKind.DIARY_READ,
    "lookup_patient": WorkKind.PATIENT_LOOKUP,
    "book_appointment": WorkKind.WRITE_BOOK,
    "reschedule_appointment": WorkKind.WRITE_MOVE,
    "cancel_appointment": WorkKind.WRITE_CANCEL,
}


def work_for_tool(tool_name: str, *, provisional: bool = False) -> WorkKind:
    """The work a tool does, as the caller should hear it described.

    ``provisional`` comes from ``turn_handler._clinic_is_provisional``, which
    reads ``booking_system == "google_calendar_provisional"`` — the same switch
    that drives the provisional write path and the provisional prompt branch, so
    this cannot drift out of step with them. That is why there is no new
    clinic.json key here: a second switch for the same fact is a second switch
    to forget.

    Three stored Vital Edge calls say "Just locking that in now..." and then
    "I've noted your preferred time and sent it to Jonathan. Your appointment is
    subject to his confirmation." Nothing was locked in. Routing the booking
    write through here makes that contradiction unreachable rather than a thing
    to remember.
    """
    kind = _WORK_BY_TOOL.get(tool_name, WorkKind.NONE)
    if kind is WorkKind.WRITE_BOOK and provisional:
        return WorkKind.PENDING_REQUEST
    return kind


#: The locked confirm CTAs, and the write each one commits to. Matched against
#: the PREVIOUS assistant turn — see confirm_write_kind.
_CONFIRM_CTA: Dict[str, WorkKind] = {
    "book that in for you": WorkKind.WRITE_BOOK,
    "book that in": WorkKind.WRITE_BOOK,
    "move it for you": WorkKind.WRITE_MOVE,
    "move that": WorkKind.WRITE_MOVE,
    "put that request through": WorkKind.PENDING_REQUEST,
}


def confirm_write_kind(
    last_assistant: str,
    caller_confirmed: bool,
    *,
    provisional: bool = False,
) -> WorkKind:
    """The write in flight on the turn right after the caller says yes. PURE.

    This is the one moment the engine knows the work BEFORE the LLM stream opens:
    the previous assistant turn was the locked confirm CTA and the caller agreed,
    so a write is about to run. Everywhere else at that point in the turn, the
    work is genuinely unknown.

    Why it is not enough that the CTA was asked (FM-25, JV live call 22 Jul): a
    "no" or an ambiguous reply used to hear "Just locking that in now…" and
    believe they had been booked against their wishes. ``caller_confirmed`` is
    the consent check, and it mirrors the FM-01 book gate — verify consent, not
    just that the question was put.

    Cancel is deliberately absent. Its go-ahead is the ambiguous
    reschedule-or-cancel retention question, so "yes" there does not identify a
    write; the cancel branch is designed to run with no readback.

    Returns NONE when no write is identifiable, which decide_hold turns into
    silence rather than a guess.
    """
    if not caller_confirmed:
        return WorkKind.NONE
    low = (last_assistant or "").lower()
    if not low:
        return WorkKind.NONE
    for cta, kind in _CONFIRM_CTA.items():
        if cta in low:
            if kind is WorkKind.WRITE_BOOK and provisional:
                return WorkKind.PENDING_REQUEST
            return kind
    return WorkKind.NONE


# ── The heads ────────────────────────────────────────────────────────────────
# Every one is an introductory clause a data payload can complete, and none
# asserts an outcome. Enforced at import time below, not by review.
#
# UNKNOWN_SLOW is deliberately contentless. It is the only kind that can be
# wrong about the work, so it says nothing about the work — which is what makes
# "Right - I'm Susie, Theorem Health's AI receptionist" correct where "Just
# getting that for you... I'm Susie..." was absurd.
HEADS: Dict[WorkKind, List[str]] = {
    WorkKind.DIARY_READ: [
        f"Let me see {EM_DASH}",
        f"Right, let's see {EM_DASH}",
        f"Let me have a look {EM_DASH}",
        f"Okay, one sec {EM_DASH}",
    ],
    WorkKind.PATIENT_LOOKUP: [
        f"Let me find you {EM_DASH}",
        f"Right, pulling you up {EM_DASH}",
        f"Let me look you up {EM_DASH}",
    ],
    WorkKind.WRITE_BOOK: [
        f"Right, booking you in {EM_DASH}",
        f"Popping that in for you {EM_DASH}",
        f"Getting that in the diary {EM_DASH}",
    ],
    WorkKind.WRITE_MOVE: [
        f"Moving that across {EM_DASH}",
        f"Right, shifting that {EM_DASH}",
        f"Getting that changed {EM_DASH}",
    ],
    WorkKind.WRITE_CANCEL: [
        f"Taking care of that {EM_DASH}",
        f"Right, sorting that {EM_DASH}",
        f"Getting that sorted {EM_DASH}",
    ],
    WorkKind.PENDING_REQUEST: [
        f"Sending that over to {{practitioner}} {EM_DASH}",
        f"Putting that request in {EM_DASH}",
        f"Passing that to {{practitioner}} {EM_DASH}",
    ],
    # Only ever heard on a GENUINE stall — 3500ms, the measured knee, which is
    # 8% of turns. It used to be 1800ms and these used to be bare discourse
    # markers ("Right —", "So —", "Okay —"), which failed on live calls in three
    # separate ways and could not have been fixed by rewording:
    #
    #   * "what are your opening hours" -> "So —" -> the answer. An empty
    #     marker in front of an instant reply is worse than the silence it
    #     replaced.
    #   * "I'd like to book" -> "Right —" -> "Right — what's the appointment
    #     for?" The model opens with the same marker, so the caller hears
    #     "Right. Right, what's..." — a duplicate no filler-stripper catches,
    #     because a discourse marker is not a filler phrase.
    #   * One was the last thing said on the call before a transfer: a head
    #     with nothing behind it.
    #
    # At 3500ms the caller has been waiting long enough that the honest thing is
    # to say so. These acknowledge the WAIT, which is real, rather than gesturing
    # at work that may not exist.
    WorkKind.UNKNOWN_SLOW: [
        f"Sorry, still with you {EM_DASH}",
        f"Still with you {EM_DASH}",
    ],
}


def clinic_facts(session) -> "tuple[bool, str]":
    """IMPURE, and the only impure thing here: ``(provisional, practitioner)``.

    Isolated so ``decide_hold`` can stay a pure function of plain values while
    the two clinic facts it needs are read in one place rather than at each call
    site. ``provisional`` reads the same ``booking_system`` switch as
    ``turn_handler._clinic_is_provisional``, deliberately — a second switch for
    one fact is a second switch to forget.

    Never raises, and fails to ``(False, "")``. A hold phrase must not be able to
    break a call: False keeps today's confirmed-booking wording, and an empty
    practitioner makes ``render_head`` choose a head that needs no name.
    """
    try:
        from app.clinic_config import get_clinic

        clinic = get_clinic(session.get("clinic_id")) or {}
        return (
            clinic.get("booking_system") == "google_calendar_provisional",
            str(clinic.get("practitioner") or ""),
        )
    except Exception:  # pragma: no cover - defensive; live call path
        return (False, "")


@dataclass(frozen=True)
class HoldDecision:
    """What to do while the caller waits. ``speak=False`` means stay silent."""

    speak: bool
    head: str = ""
    kind: WorkKind = WorkKind.NONE
    reason: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.speak


SILENT = HoldDecision(speak=False, reason="nothing to say")


def render_head(kind: WorkKind, *, practitioner: str = "", index: int = 0) -> str:
    """One head for ``kind``, rotated by ``index``.

    ``index`` rather than a random choice, so replaying the stored corpus is
    deterministic and a test can assert exact wording. Callers pass the number of
    heads already spoken this call, which is what ``pick_filler`` does with
    ``session["used_fillers"]``.
    """
    pool = HEADS.get(kind) or []
    if not pool:
        return ""
    head = pool[index % len(pool)]
    if "{practitioner}" in head:
        # A clinic with no named practitioner must never say "Sending that over
        # to  -". Fall back to a head from the same pool that needs no name.
        if not practitioner:
            plain = [h for h in pool if "{practitioner}" not in h]
            return plain[0] if plain else f"Putting that request in {EM_DASH}"
        head = head.replace("{practitioner}", practitioner)
    return head


def decide_hold(
    *,
    kind: WorkKind,
    head_already_spoken: bool,
    caller_is_waiting: bool = True,
    practitioner: str = "",
    heads_used: int = 0,
) -> HoldDecision:
    """Whether to speak a hold phrase, and which one. PURE.

    ``head_already_spoken`` is the one-per-turn latch and is checked FIRST.
    Every stacked run in the corpus is a second producer deciding it had
    something worth adding. It never does: the caller has already been told to
    hold on, and a second phrase only delays the answer it is standing in for.

    ``kind is NONE`` means no work is in flight, and silence is the correct
    output. A turn that answers immediately needs no hold phrase, and 175 of the
    stored ones were spoken on exactly such turns.
    """
    if head_already_spoken:
        return HoldDecision(False, kind=kind, reason="one head per turn")
    if not caller_is_waiting:
        return HoldDecision(False, kind=kind, reason="caller not waiting")
    if kind is WorkKind.NONE:
        return HoldDecision(False, kind=kind, reason="no work in flight")

    head = render_head(kind, practitioner=practitioner, index=heads_used)
    if not head:
        return HoldDecision(False, kind=kind, reason="no head for kind")
    return HoldDecision(True, head=head, kind=kind, reason=str(kind.value))


# ── Import-time guarantees ───────────────────────────────────────────────────
# A bad head must be UNDEPLOYABLE, not merely unspoken. Deterministic hold
# phrases bypass sanitise_response entirely (see app/filler_phrases.py), which is
# how "Getting that all booked in for you..." once reached callers as a completed
# booking claim. Checking here costs nothing on the hot path and turns that class
# of mistake into a startup failure in CI rather than a sentence a patient hears.

_OPEN_CLAUSE = (EM_DASH, ",", "-")

# Verbs that name the work. UNKNOWN_SLOW must contain none of them.
_NAMES_THE_WORK = re.compile(
    r"\b(check|look|find|pull|diary|schedule|availab|book|cancel|move|shift|"
    r"sort|lock|get)",
    re.IGNORECASE,
)


def _self_check() -> None:
    from app.filler_phrases import is_write_filler
    from app.media_streams.turn_handler import _BANNED_SENTENCE_RE

    for kind, pool in HEADS.items():
        assert pool, f"{kind} has no heads"
        for head in pool:
            rendered = head.replace("{practitioner}", "Jonathan")

            # 1. Open clause: the reply has to be able to complete it.
            assert rendered.rstrip()[-1:] in _OPEN_CLAUSE, (
                f"head is a closed sentence, so the reply cannot continue it: "
                f"{head!r}"
            )
            assert ELLIPSIS not in rendered, (
                f"the ellipsis is the falling contour this work removes: {head!r}"
            )

            # 2. Survives the gates that police model speech. A hold phrase the
            #    engine would delete from the model is one the engine should not
            #    be saying either — that asymmetry is how "just a moment" stayed
            #    reachable for months after it was banned.
            for name, rx in _BANNED_SENTENCE_RE:
                assert not rx.search(rendered), (
                    f"head {head!r} is deleted by Gate 5b/{name}"
                )

            # 3. A pending-confirmation clinic never claims a write.
            if kind is WorkKind.PENDING_REQUEST:
                assert not is_write_filler(rendered), (
                    f"a provisional clinic must not claim a write: {head!r}"
                )

    # 4. The contentless kind stays contentless. This is the whole defence
    #    against the 175 phrases that promised a lookup nobody was doing.
    for head in HEADS[WorkKind.UNKNOWN_SLOW]:
        assert not _NAMES_THE_WORK.search(head), (
            f"UNKNOWN_SLOW cannot know what the work is, so it must not name "
            f"any: {head!r}"
        )


_self_check()
