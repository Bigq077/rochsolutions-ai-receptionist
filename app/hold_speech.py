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
    #: The second rung only -- a stall that has already had one head and is
    #: still going at LLM_FILLER_SECOND_STALL_MS. The only kind allowed to
    #: apologise, because by then the wait is real.
    LONG_WAIT = "long_wait"
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
    # "Midday on Monday the 14th — shall I put that one in for you?" is how
    # the read-back CTA is actually spoken on the demo line (6-12 Sep 2026
    # corpus); a yes to it is a booking write in flight.
    "put that one in": WorkKind.WRITE_BOOK,
    "put that in": WorkKind.WRITE_BOOK,
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
    # These used to be "Sorry, still with you -" / "Still with you -": an
    # APOLOGY for a wait, spoken at 2.75s. Measured on the 100 calls stored
    # 6-12 Sep 2026: 45 turns in 30 calls heard one, 6 of them as the first
    # thing Susie said after the caller's opening sentence, and 41 of the 45
    # stood alone with the answer arriving as a later turn. The owner's
    # complaint ("sorry, still here ... too generic") was that phrase.
    #
    # What was wrong was the SPEECH ACT, not the timing. At 2.75s nothing has
    # gone wrong yet, and a receptionist typing at 2.75s does not apologise --
    # she acknowledges receipt. So this pool is a RECEIPT: it claims no work
    # (the contentless rule still holds, and _self_check still enforces it),
    # apologises for nothing, and is honest on any turn where the caller has
    # just said something. The apology moved to LONG_WAIT, the second rung,
    # where the wait is real.
    #
    # Still contentless on purpose: this is the only kind that can be wrong
    # about the work, so it says nothing about the work.
    WorkKind.UNKNOWN_SLOW: [
        f"Got that {EM_DASH}",
        f"Okay, got you {EM_DASH}",
        f"Right, got that {EM_DASH}",
    ],
    # Rung 2 only (`llm_stream._delayed_filler`, LLM_FILLER_SECOND_STALL_MS).
    # By now one head has played and the model has still said nothing, so the
    # caller has waited 7-10s: the professional thing is to say sorry. Its own
    # family, so `_second_filler_text`'s N4 test (same family twice = stuck
    # line) does not refuse it the way it refused a second UNKNOWN_SLOW --
    # which is why, before this pool existed, an uncovered turn heard one
    # apology at 2.75s and then nothing at all, ever.
    WorkKind.LONG_WAIT: [
        f"Sorry, this is taking a moment {EM_DASH}",
        f"Sorry, still working on that for you {EM_DASH}",
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


def hold_speech_enabled(session) -> bool:
    """Does THIS clinic route its hold phrases through the arbiter?

    Defaults to FALSE, which is the pre-arbiter behaviour every live clinic
    runs today. That default is what makes folding a clinic branch onto
    canonical audibly neutral: the arbiter is canonical-only work, it changes
    what a caller hears while waiting, and it has not yet been heard on a
    patient line. A clinic opts in with `operational.hold_speech: true` once
    someone has listened to it — one key, no code, no branch.

    The OFF path is not silence and not an approximation: each producer falls
    back to exactly the code it ran before cbde450e. A third behaviour that no
    clinic has ever run would be worse than either of the two real ones.

    Never raises, and fails to False — the safe side is the behaviour that is
    already live.
    """
    try:
        from app.clinic_config import get_clinic

        return bool((get_clinic(session.get("clinic_id")) or {}).get("hold_speech"))
    except Exception:  # pragma: no cover - defensive; live call path
        return False


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


def legacy_head(session, *, override: str = "") -> str:
    """The phrase a producer would have spoken BEFORE the arbiter existed.

    Sites A and B (`connection.py`) both did `random.choice(FILLER_PHRASES)`.
    Site C additionally tried `confirm_write_filler` first, which it passes in
    as ``override``. Reproduced here rather than at four call sites so the two
    largest files in the repo gain one argument each instead of a second branch
    in the middle of the turn loop.
    """
    if override:
        return override
    import random

    # config.py, not filler_phrases.py — this is the list the pre-arbiter
    # producers actually drew from (connection.py imported it from there).
    from app.media_streams.config import FILLER_PHRASES

    return random.choice(FILLER_PHRASES) if FILLER_PHRASES else ""


def decide_hold(
    *,
    kind: WorkKind,
    head_already_spoken: bool,
    caller_is_waiting: bool = True,
    practitioner: str = "",
    heads_used: int = 0,
    legacy: bool = False,
    legacy_override: str = "",
    session=None,
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
    # A clinic that has not opted in gets the behaviour it runs today: speak,
    # every time, with no cross-producer latch and no reasoning about the work.
    # That is the pre-arbiter answer, and it is deliberately NOT an improved
    # version of it -- the point of the switch is that folding a clinic branch
    # onto canonical changes nothing a caller hears until someone chooses it.
    if legacy:
        head = legacy_head(session, override=legacy_override)
        if not head:
            return HoldDecision(False, kind=kind, reason="legacy: no phrase")
        return HoldDecision(True, head=head, kind=kind, reason="legacy")

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


# ── What the CALLER asked for ────────────────────────────────────────────────
# WorkKind says what the system is doing. It is keyed to five tool names, so it
# can only speak when a tool runs -- which is why "can I cancel my appointment",
# a price question, a symptom and "sorry, what?" all got either silence or a
# generic phrase that lied.
#
# Measured on the 753-call corpus (2026-08-29), and this is the finding the
# whole taxonomy rests on: the wait is NOT the provider. check_availability has
# a p50 round-trip of 319ms and a p90 of 607ms; lookup_patient 210ms. Turn
# time-to-first-audio over the same calls is p50 1,938ms, p90 3,171ms. The dead
# air is the model. EVERY turn has roughly two seconds of it, not just the ones
# that call a tool, so every turn can be owed a head.
#
# The second finding is what makes the wording safe to choose deterministically:
# the model ALREADY writes the right opener, it just arrives 1.9s late. Stored
# payloads open with "I'm sorry to hear that -", "No problem at all.", "Let's
# get that moved for you.", "Got it -", "Thanks Quentin -", "Apologies for that
# -". So a head is not an invented filler phrase. It is the opener the model was
# going to say anyway, said earlier, with its duplicate stripped -- which is
# what makes it part of the sentence rather than a phrase in front of one.


class Intent(str, Enum):
    """What the caller asked for, read from the transcript at STT-final."""

    # Register -- the social turns.
    SYMPTOM = "symptom"
    CANCEL_REQ = "cancel_req"
    RESCHEDULE_REQ = "reschedule_req"
    REPEAT_ASK = "repeat_ask"
    TRANSFER_REQ = "transfer_req"
    #: The caller has just CHOSEN one of the slots read out. Register, not
    #: diary: it claims no lookup, so it is safe while they are answering.
    SLOT_PICKED = "slot_picked"
    # Topic -- FAQ turns, where no tool runs at all.
    FAQ_PRICE = "faq_price"
    FAQ_INSURANCE = "faq_insurance"
    FAQ_HOURS = "faq_hours"
    FAQ_PARKING = "faq_parking"
    FAQ_LOCATION = "faq_location"
    FAQ_TREATS = "faq_treats"
    FAQ_FIRSTTIME = "faq_firsttime"
    FAQ_PRACTITIONER = "faq_practitioner"
    # Diary -- these carry a subject the head can name.
    EARLIEST = "earliest"
    SESSION_LENGTH = "session_length"
    NAMED_DAY = "named_day"
    NAMED_WEEK = "named_week"
    TIME_BAND = "time_band"
    AVAIL_QUERY = "avail_query"
    BOOK_NEW = "book_new"
    #: "what have you got around twelve" -- a clock time the caller wants to
    #: be NEAR, which is a diary read with a subject no other intent carried.
    TIME_AROUND = "time_around"
    # Register, second family -- the caller reacting to what is on the table.
    #: "hello?" / "are you there?" mid-call. Confirms presence, claims nothing.
    CHECK_IN = "check_in"
    #: "that's not soon enough" / "I made a mistake". Every diary intent is
    #: rightly suppressed on a refusal, and this is what speaks instead.
    REFUSAL = "refusal"
    #: "can you let Marcus know" / "I'm running late".
    MESSAGE_REQ = "message_req"
    # Answer moments -- the caller is ANSWERING, so the moment is defined by
    # what Susie asked rather than by anything in the caller's words. These
    # fill the gap that used to be the contentless apology: 53 of the 178
    # headless caller turns in the 6-12 Sep corpus were a "yes"/"no" to a
    # question Susie had just put, and 15 more were a name.
    NUMBER_CONFIRMED = "number_confirmed"
    NAME_GIVEN = "name_given"
    CLINIC_CHOSEN = "clinic_chosen"


#: Intents that assert a diary read. Only these are suppressed while the caller
#: is answering a confirm question -- sympathy and an apology stay correct there.
_DIARY_INTENTS = frozenset({
    Intent.NAMED_DAY, Intent.NAMED_WEEK, Intent.TIME_BAND, Intent.SESSION_LENGTH,
    Intent.EARLIEST, Intent.AVAIL_QUERY, Intent.BOOK_NEW, Intent.TIME_AROUND,
})

_DAY = r"(?:mon|tues|wednes|thurs|fri|satur|sun)day"
_BAND = r"(?:morning|afternoon|evening|lunchtime)"
_BODY = (r"(?:knee|ankle|shoulder|hip|back|neck|wrist|elbow|foot|feet|calf|"
         r"hamstring|sciatic|groin|thigh|spine|arm|leg|hand|glute|quad|achilles)")
# Injury is often described with no word for pain at all -- "done my ankle",
# "went over on it", "it gave way". The screening triggers learned the same
# lesson the hard way (a caller saying "my ankle ... I twisted it" armed no
# screen): adding more synonyms is the trap, the SHAPE of the matcher is the bug.
# The symptom vocabulary. Mechanical complaints were covered; the NEUROLOGICAL
# class was absent entirely, and it is the clinically weightiest one.
#
# B-143, CA6b241e20 / CAcb51bc27 (5 Sep 2026, northgate). "my lower back's
# been really bad and my leg's gone numb" corroborated on _BODY (back, leg)
# and matched no trigger, so classify_intent returned [] and the caller got
# the arbiter's contentless 'Sorry, still with you —' instead of the symptom
# head 'Sorry to hear that —'. A caller reporting a numb leg heard a STALL
# PHRASE as the first thing back, three calls running.
#
# Adding a missing CLASS, not lengthening a phrase list. Every term below is
# a sensory/neurological sign; each still needs a body part to corroborate
# and is still blocked by a trailing '?', so "is numbness normal after
# surgery?" does not arm a sympathy head.
_HURT = (r"(?:pain|painful|injur\w*|sprain\w*|strain\w*|ache|aching|stiff\w*|"
         r"sore|tension|pulled|tight\w*|hurt\w*|niggl\w*|twist\w*|roll\w*|"
         r"went over|gave way|giving way|done (?:my|in)|popped|locked|swollen|"
         r"seized|numb\w*|tingl\w*|pins and needles|shooting)")
_SERVICE = (r"(?:acupuncture|massage|shockwave|physio\w*|sports|dry.?needl\w*|"
            r"laser|rehab\w*|pilates|osteo\w*|treatment|therapy|service)")
# "to book an appointment" is a PURPOSE, not a want-verb, and it is how a
# caller answers "how can I help?" more often than not: "um yeah that's to book
# an appointment", "um to book an appointment mate" (theorem, 9 Sep 2026 --
# the N4 call). Neither carried a want-verb, so BOOK_NEW's corroborator failed
# and the first thing Susie said was the stall apology. The infinitive and the
# "for a/an" purpose shapes are added as SHAPES; no noun was added.
_WANT = (r"(?:like to|want to|need to|can i|could i|looking to|wanting to|"
         r"make|get|do|to book|for an? (?:appointment|booking|session))")

#: A spoken clock hour, for `subject_for` and TIME_AROUND. Digits or words,
#: optional minutes / am-pm / o'clock, plus the two nouns people use for 12.
_CLOCK = (r"(?:(?:1[0-2]|[1-9])(?:[:.][0-5]\d)?(?!\d)\s*(?:am|pm|o'?\s*clock)?|"
          r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
          r"(?:\s+o'?\s*clock)?|midday|noon|lunchtime)")
_HOUR_WORD = {"1": "one", "2": "two", "3": "three", "4": "four", "5": "five",
              "6": "six", "7": "seven", "8": "eight", "9": "nine", "10": "ten",
              "11": "eleven", "12": "twelve"}


def _rx(pattern: str):
    return re.compile(pattern, re.IGNORECASE)


#: "hello?" / "are you there?" after the call is under way. Whole utterance,
#: four words at most, so "hello, I'd like to book" is a request and not this.
#: "hello you still there" (demo call CA5c69c585, 12 Sep 2026, first live
#: run) is a greeting word IN FRONT of the check -- so the greeting is an
#: optional prefix, not one of the alternatives.
_CHECK_IN = _rx(r"^\s*(?:(?:hello|hi|hiya|hey|sorry)[\s,]+)?"
                r"(?:hello|hi|hiya|hey|are you (?:still )?there|you (?:still )?there|"
                r"can you hear me|still there|anyone there|you still with me)"
                r"[\s?.!]*$")

#: Susie's own greeting, matched against her PREVIOUS turn. Every clinic's
#: opener introduces her by name, so this is the one clinic-agnostic marker of
#: "this is the caller's first sentence".
_GREETING = _rx(r"\bi'?m susie\b|\bai receptionist\b")

#: A yes, on its own or with a courtesy. Deny-by-default against `_NEGATED`
#: where it is used, so "yes but not tuesday" is not a plain yes.
_AFFIRM = _rx(r"^\s*(?:(?:um+|uh+|er+|erm+|ah+|oh)[\s,]+)*"
              r"(?:yes|yeah|yep|yup|please|sure|ok|okay|go on|go ahead|"
              r"that'?s (?:right|correct|fine|it)|it is|that'?d be (?:great|good|"
              r"lovely)|correct|absolutely|do that|let'?s do (?:that|it))\b")

#: The caller ruling something out or taking something back. Corrections that
#: name a day or a clock time are `utterance_refuses_an_offer`; these are the
#: ones that name nothing concrete, which that predicate deliberately leaves
#: alone. Here a head is still owed, because every diary intent has just been
#: suppressed and the alternative is the contentless fallback.
_CORRECTION = _rx(r"\b(?:not soon enough|too (?:late|early|soon|far|long)|"
                  r"doesn'?t (?:work|suit)|can'?t (?:do|make) (?:that|it|those)|"
                  r"no good|that'?s wrong|made a mistake|got (?:that|it) wrong|"
                  r"cut off|not right|start again|scrap that|actually no|"
                  r"none of those|neither of those)\b")

# ── What Susie asked -- the answer moments ───────────────────────────────────
# Matched against her PREVIOUS turn. `_CONFIRM_Q` and `question_asks_the_reason`
# set the precedent: the reply to a question is defined by the question.
_BOOK_OFFER_Q = _rx(r"\b(?:would you like to (?:book|get booked|come in|arrange)|"
                    r"shall i (?:get you|book you|go ahead and (?:get you|book))|"
                    r"like to book in|want to book|get you booked in|"
                    r"book (?:you|that) in\b[^?]{0,20}\?)")
_NUMBER_Q = _rx(r"\b(?:best number|number (?:the (?:booking|appointment) (?:is|was) "
                r"(?:booked )?under|for the booking|to reach you|you'?re calling "
                r"(?:on|from)|okay to use|to use for)|is (?:this|that) "
                r"(?:the )?(?:right|best|correct) number|the number you'?re "
                r"calling|associated with your|use this number)")
_NAME_Q = _rx(r"\b(?:first name|surname|full name|your name|name for the "
              r"booking|who am i speaking)\b")
#: Susie reading a name back: "Did you say Sandrine — is that right?", "and
#: your surname, is that Roch?". A yes here is a name confirmed, not a slot.
_NAME_CONFIRM_Q = _rx(r"\b(?:did you say|surname, is that|is that spelt|"
                      r"have i got that right)\b")
_PICK_Q = _rx(r"\b(?:does that work|do (?:any|either) of those work|any of those "
              r"work|which (?:one|of those)|works best for you|suits? you|"
              r"number one, two|would you like\?|which would you (?:like|prefer)|"
              r"is that the right one|would a different day work|"
              r"shall i (?:put|pop) that (?:one )?in|either of those work|"
              # A readout: "Number 1, eight in the morning. Number 2, ...". Only
              # as the PREVIOUS turn, and only for a pick shape (yes / clock /
              # ordinal) with no request word in it -- unlike the old
              # `number <digit>` in _CONFIRM_Q, this suppresses nothing.
              r"number (?:1|one),)")
_PREF_Q = _rx(r"\b(?:preference for when|particular day or time|when would "
              r"(?:suit|work)|when (?:would you like|works)|day or time that "
              r"works|what day|which day|when'?s good)")
#: "Is this for our X or Y clinic?" -- the two options are lifted from Susie's
#: own sentence, so no clinic name lives in engine code.
_OR_CHOICE = _rx(r"\b(?:for (?:our|the) )?([A-Za-z]+) or (?:the )?([A-Za-z]+)"
                 r"(?: (?:clinic|site|branch))?\?")
#: A pick that is a REQUEST in disguise -- "what about around twelve" -- must
#: not be read as choosing something.
_REQUEST_SHAPE = _rx(r"\b(?:what|anything|any|around|about|close|near|after|"
                     r"before|earlier|later|instead|other|else|different|"
                     r"soonest|earliest|next)\b")
#: An ordinal or index the caller uses to point at a slot from a readout.
_ORDINAL_PICK = _rx(r"\b(?:number (?:one|two|three|four|\d)|the (?:first|second|"
                    r"third|last|latter|earlier|later) one|first one|last one|"
                    r"option (?:one|two|three|\d))\b")


# A bare answer is an answer, not a request: there is nothing for a head to
# stand in front of.
_BARE_ANSWER = _rx(r"^\s*(?:um|uh|er|erm|ah|oh|yeah|yes|yep|no|nope|nah|"
                   r"not really|none|nothing)\b(?:[\s,]|$)")
_NEGATED = _rx(r"\b(?:no|not|nothing|none|haven'?t|hasn'?t|isn'?t|don'?t|didn'?t)\b")

#: Throat-clearing that carries no answer. Stripped before the bare-answer
#: test so a request is not mistaken for one -- see classify_intent.
_LEADING_DISFLUENCY = _rx(r"^(?:(?:um+|uh+|er+|erm+|ah+)[\s,]+)+")

#: A readback or confirm question. The reply to one is a SELECTION, so a diary
#: head in front of it promises a lookup that is not happening -- the corpus
#: defect rebuilt in a new place.
#:
#: `number <digit>` USED TO BE IN THIS LIST and is deliberately gone. It was
#: reaching for "the caller is choosing from the options" and it matched the
#: wrong sentence to get there: a slot readout always says "Number 1, ...
#: Number 2, ...", so from the readout onwards every later turn looked like an
#: answer to a confirm question and every diary intent was dropped for the
#: rest of the call. 244 suppressions across the corpus, 186 of them from that
#: one token, and on the demo call of 2026-08-30 it silenced "um what do you
#: have next tuesday" and "actually what's the soonest you've got".
#:
#: Deleting it outright would have been wrong too -- most of the 186 ARE
#: selections and should stay silent. The answer is that the engine already
#: decides this on DATA (B-90: is the utterance one of the labels just
#: offered?), so `classify_intent` now takes that verdict as `slot_selection`
#: instead of inferring a worse version of it from the previous sentence.
_CONFIRM_Q = _rx(r"\b(?:did you mean|is that (?:right|correct|the right one)|"
                 r"shall i (?:go ahead|book)|just to confirm|does that work|"
                 r"is that the best number|which (?:one|of those))\b")

#: A clinical screen question, matched against what Susie said LAST. The reply
#: to one is a red-flag answer, and it is the worst moment in the call to guess:
#: no head may fire there, whatever else matches.
_SCREEN_Q = _rx(r"\b(?:swollen|warm or red|numbness|tingl\w*|bladder|bowel|"
                r"saddle|unexplained weight|night pain|fever|calf|"
                r"pins and needles|give way|cauda|chest pain|breathless)\b")

#: "What is the appointment for?" in every shape it has actually been asked.
#:
#: ONE owner. `llm_stream._note_reason_question_asked` used to carry this list
#: inline and calls it from here now, because two copies of a matcher is two
#: answers to "was the reason question asked", and this family has already been
#: wrong twice for matching the literals seen so far rather than the shape:
#: B-36, and CAea8abdb on 2 Sep where Vital Edge asked "Is there a particular
#: area or CONCERN you're looking to address?", the caller answered it in full,
#: and the latch stayed open so the question was asked again a turn later.
#:
#: PURE and text-only. The caller-facing decision about whether this clinic
#: asks a reason at all stays in llm_stream with the clinic config.
_REASON_Q_PATTERNS = (
    # "what's the appointment for", "what is it for", "what's it for"
    r"\bwhat(?:'?s| is)\b[^?]{0,40}\bfor\b[^?]{0,20}\?",
    # "what's the reason for ...", "is there a particular reason for ..."
    r"\breason for\b[^?]{0,60}\?",
    # "what brings you in", "what's brought you to us"
    r"\bwhat\b[^?]{0,20}\bbrings? you (?:in|to)\b",
    # The SHAPE, not the wording: "area or reason / concern / issue / problem".
    r"\barea or (?:reason|concern|issue|problem)\b",
    r"\blooking to address\b",
)
_REASON_Q = tuple(re.compile(p, re.IGNORECASE) for p in _REASON_Q_PATTERNS)


def question_asks_the_reason(spoken: str) -> bool:
    """Did Susie just ask what the appointment is for? PURE, text only.

    Says nothing about whether this clinic SHOULD ask -- that gate lives with
    the clinic config in `llm_stream._note_reason_question_asked`, which calls
    this for the text half.
    """
    if not spoken or "?" not in spoken:
        return False
    return any(rx.search(spoken) for rx in _REASON_Q)


#: (intent, trigger, corroborator or None, blocker or None).
#:
#: A trigger alone never fires. Deny-by-default throughout: an utterance that
#: matches nothing gets SILENCE, which is exactly today's behaviour, so the
#: failure mode of a bad rule is "no change" rather than "confident and wrong".
#: The corroborators are not decoration -- without them AVAIL_QUERY fired on
#: "no, nothing like that, it's not swollen, I haven't been on any long
#: journeys", a DVT screening answer, and FAQ_TREATS swallowed every "do you
#: have anything on Friday".
_INTENT_RULES = [
    (Intent.REPEAT_ASK, _rx(r"\b(?:i said|say (?:that|them|those|it) again|"
                            r"repeat (?:that|them|those)|(?:read|go through) "
                            r"(?:them|those|that) (?:out )?again|one more time|"
                            r"didn'?t (?:hear|catch)|that'?s not what i|"
                            r"you got that wrong)\b"), None, None),
    (Intent.SYMPTOM, _rx(_HURT), _rx(_BODY), _rx(r"\?\s*$")),
    (Intent.CANCEL_REQ, _rx(r"\bcancel\w*\b"),
     _rx(r"\b(?:appointment|booking|it|that|my|session)\b"), None),
    (Intent.RESCHEDULE_REQ,
     _rx(r"\b(?:reschedul\w*|rebook\w*|move|change|shift|push)\b"),
     _rx(r"\b(?:appointment|booking|it|that|my|day|time|date)\b"), None),
    (Intent.TRANSFER_REQ,
     _rx(r"\b(?:speak to|talk to|put me through|call me back|ring me)\b"),
     _rx(r"\b(?:someone|human|person|back|later)\b"), None),
    # "can you let Marcus know" / "hi I'm running late" (jv_v1, 8 Sep 2026,
    # both stalled into the apology). The head claims no relay -- "Not a
    # problem -" -- because whether a message is actually taken is decided by
    # the model and the tools, not here.
    (Intent.MESSAGE_REQ,
     _rx(r"\b(?:let \w+ know|tell \w+ (?:that|i)|pass (?:it|that|this|a message) "
         r"(?:on|along)|leave (?:him|her|them|a) message|message for|"
         r"running (?:a bit |a little |slightly )?late|(?:i'?ll|gonna|going to) "
         r"be late|be there (?:in|shortly))\b"),
     None, _rx(r"\b(?:cancel|reschedul|rebook)\w*\b")),

    (Intent.FAQ_PRICE, _rx(r"\b(?:how much|cost\w*|price\w*|fee|charge|expensive)\b"),
     None, None),
    (Intent.FAQ_INSURANCE,
     _rx(r"\b(?:axa|bupa|vitality|insurance|insured|nhs|self.?pay)\b"), None, None),
    (Intent.FAQ_PARKING, _rx(r"\bpark(?:ing)?\b"), None, None),
    (Intent.FAQ_LOCATION, _rx(r"\b(?:where are you|whereabouts|address|postcode|"
                              r"how do i (?:get|find)|which clinic)\b"), None, None),
    # "opening hours" / "what time do you close" -- never a bare "slots open",
    # which is what a plain \bopen\b matched.
    (Intent.FAQ_HOURS, _rx(r"\bopening (?:hours|times)\b|"
                           r"\bwhat time do you (?:open|close)\b|\bare you open\b|"
                           r"\bhow late (?:are|do) you\b|\byour hours\b"), None, None),
    (Intent.FAQ_FIRSTTIME, _rx(r"\b(?:first (?:time|appointment|visit)|never been|"
                               r"referral|what should i (?:bring|wear)|"
                               r"before (?:i|my) (?:come|first|visit|appointment))\b"),
     None, None),
    (Intent.FAQ_TREATS, _rx(r"\bdo(?:es)? (?:you|they)\b|\bcan you (?:help|treat)\b"),
     _rx(r"\b(?:do|treat|offer|cover|provide|specialis\w*)\b.{0,30}" + _SERVICE +
         r"|" + _SERVICE),
     _rx(r"\b(?:free|available|slot|anything on|any)\b")),
    (Intent.FAQ_PRACTITIONER, _rx(r"\b(?:who (?:would|will|do) i|"
                                  r"which (?:physio|therapist)|"
                                  r"same (?:person|physio))\b"), None, None),

    (Intent.EARLIEST, _rx(r"\b(?:soonest|earliest|as soon as possible|asap|"
                          r"next available|first available)\b"), None, None),
    (Intent.NAMED_DAY, _rx(_DAY), None, None),
    (Intent.NAMED_WEEK, _rx(r"\b(?:next week|this week|following week|week after|"
                            r"next month|tomorrow)\b"), None, None),
    (Intent.TIME_BAND, _rx(_BAND), None, None),
    # "what have you got around 12" / "as close as possible to 12 please" --
    # four stalls on the demo line, 8-11 Sep 2026. A clock time the caller
    # wants to be NEAR is a diary read with a subject, and no intent above
    # carried it: AVAIL_QUERY needs a diary noun, TIME_BAND needs a band word.
    # After NAMED_DAY/NAMED_WEEK/TIME_BAND so "wednesday around twelve" keeps
    # the day head, which names more of what they said.
    (Intent.TIME_AROUND,
     _rx(r"\b(?:around|about|close(?:st)? (?:as possible )?to|near(?:er|est)? to|"
         r"nearer|after|before|from|by)\s+" + _CLOCK),
     None, _NEGATED),
    (Intent.AVAIL_QUERY, _rx(r"\b(?:anything|any|something|what|what'?s|got)\b"),
     _rx(r"\b(?:free|available|availability|slot|slots|opening|appointment|times?)\b"),
     _NEGATED),
    # "um what have you got" -- the corroborator above wants a diary noun the
    # caller did not say, because the whole question IS the diary noun. The
    # verb phrase is the shape; it cannot be an answer to anything.
    (Intent.AVAIL_QUERY,
     _rx(r"\bwhat (?:else )?(?:have|do|would) you (?:got|have)\b|"
         r"\bwhat'?s (?:free|available|open)\b"),
     None, _NEGATED),
    (Intent.BOOK_NEW, _rx(r"\b(?:book|booking|appointment)\b"), _rx(_WANT),
     _rx(r"\b(?:cancel|reschedul|rebook|move|change)\w*\b")),

    # SESSION_LENGTH is LAST, and it is the only rule whose head promises
    # more than its trigger asks for unless a corroborator is required.
    # Measured over the 737-call corpus on 2026-08-30: of its 20 heads, TEN
    # were followed by a question rather than by times -- "60 minute session
    # please" answered with "do you have a preference for when you'd like to
    # come in?". Naming a duration SUPPLIES A PARAMETER; it does not ask
    # anyone to open a diary, and "Let me see where a sixty-minute session
    # fits -" says a diary is being opened. So a bare duration now yields
    # silence, which is the pre-arbiter behaviour, and the head returns only
    # when the caller actually asks where it fits.
    #
    # The blocker is the live case: "do you do 90-minute sessions"
    # (2026-08-29, CA7454c983a10dd3db7caee7dba3b06238) is a CAPABILITY
    # question, and the head answered it with "Let me see where a
    # ninety-minute session fits -" about a length this clinic does not
    # sell. A head must never assert that something exists. "do you HAVE any
    # 90 minute slots" is deliberately NOT blocked -- that one is a real
    # availability question and deserves its head.
    #
    # Last in the list so a better-fitting intent takes hits[0]: a caller who
    # says "can I get a 60 minute on Thursday" wants NAMED_DAY's head, which
    # names their day.
    (Intent.SESSION_LENGTH,
     _rx(r"\b(?:30|60|90|thirty|sixty|ninety)[\s-]?(?:minute|min)\b"),
     _rx(r"\b(?:fit|fits|available|availability|free|slot|slots|when|"
         r"earliest|soonest)\b"),
     _rx(r"\b(?:do|does) (?:you|they) (?:do|offer)\b")),
]


#: Asking the clinic ABOUT a service. Blocks the arm below outright: "Let's get
#: you booked in -" in front of a price question is a head promising the wrong
#: work, which is this family's oldest failure.
_SERVICE_ENQUIRY = _rx(
    r"\b(?:ask(?:ing)?|know|enquir\w*|inquir\w*|find\s+out|wonder(?:ing)?|"
    r"question|price[sd]?|cost[s]?|how\s+much|how\s+long|worth\s+it|"
    r"do\s+you\s+(?:do|offer|have)|d'?you|does\s+it|is\s+it|what\s+is)\b"
)

#: The caller asking FOR something, in the first person. Second- and
#: third-person framings are questions about the clinic and stay out: "can I
#: have a sports massage" is a request, "can you do sports massage" is not.
_SERVICE_REQUEST = _rx(
    r"\b(?:(?:can|could|may)\s+i\b|i(?:'?d)?\s+(?:like|want|need|fancy)\b|"
    r"i'?m\s+after\b|(?:book|get)\s+me\b|i\s+wanted\b|"
    r"(?:like|want|need)\s+(?:a|an|some)\b)"
)


#: Intents that change the SUBJECT of the call rather than answer the question
#: on the table. Each one makes Susie acknowledge a request to do something
#: other than what she just asked about, so each is wrong by construction while
#: she is capturing a name.
_TOPIC_SWITCH_INTENTS = frozenset({
    Intent.CANCEL_REQ, Intent.RESCHEDULE_REQ, Intent.TRANSFER_REQ,
})


def classify_intent(
    text,
    prev_assistant="",
    *,
    screen_pending=False,
    slot_selection=False,
    service_named=False,
    offer_refused=False,
    name_pending=False,
):
    """Every intent this utterance corroborates, most specific first. PURE.

    Returns [] for silence -- the pre-arbiter behaviour -- whenever nothing
    matches, the caller is merely answering, or a clinical screen is in play.

    ``prev_assistant`` is what Susie said last. ``screen_pending`` is the
    session's own view of whether a screen is armed and unanswered; both are
    checked because either alone has been wrong. ``slot_selection`` is the
    engine's B-90 verdict -- is THIS utterance one of the slot labels just
    read out? -- and it is a fact about the caller's own words rather than an
    inference from Susie's, which is why it replaced the `number <digit>`
    proxy that used to sit in `_CONFIRM_Q`. A caller picking a slot is not
    waiting for a lookup; a caller asking something new in the same window is. A stored call shows why the
    session flag is needed as well as the text: "just book me in for Tuesday"
    was followed not by the diary but by "do you have any numbness around the
    saddle area" -- a head saying "Let me see what Tuesday looks like" in front
    of a cauda equina screen is the promised-work defect at its worst.
    """
    utterance = (text or "").strip()
    if not utterance:
        return []
    if screen_pending or _SCREEN_Q.search(prev_assistant or ""):
        return []
    # A disfluency on the front is not part of the answer, and it must not be
    # counted as one of the four words either. "uh what about mornings" is a
    # request, and it was read as a bare answer twice over: "uh" matched the
    # opener AND padded the utterance to the four-word limit. Live 2026-08-29,
    # CA7454c983a10dd3db7caee7dba3b06238 -- that turn and "uh the 60-minute
    # session" both got silence where the caller had asked for something.
    #
    # Only true disfluencies are stripped. "oh", "no", "yeah", "well" and "so"
    # stay in _BARE_ANSWER's own list because each of them CAN be the whole
    # answer; "um" and "uh" never can.
    # A body part named in ANSWER to "what's the appointment for?" is a
    # complaint, whether or not the caller used a word for pain.
    #
    # `Intent.SYMPTOM` triggers on `_HURT` and corroborates with `_BODY`, and
    # the comment above `_HURT` already warns that "injury is often described
    # with no word for pain at all -- 'done my ankle', 'went over on it', 'it
    # gave way'... adding more synonyms is the trap, the SHAPE of the matcher
    # is the bug". Requiring `_HURT` as the TRIGGER is that shape.
    #
    # Live 2026-09-03 01:28:16 on the demo line: "um just my left ankle nothing
    # serious" answered the reason question, matched `_BODY` and nothing in
    # `_HURT`, and got no head -- then 3.6s of silence and `UNKNOWN_SLOW`'s
    # "Still with you --", which apologises for a wait instead of acknowledging
    # what the caller just said.
    #
    # The question is asked of Susie's own previous turn, not guessed from the
    # answer, so this cannot fire on a body part mentioned anywhere else in the
    # call. Same root as the screening-trigger bigram defect.
    #
    # The greeting is the other question that asks the reason. "How can I help
    # you today?" fails every `_REASON_Q_PATTERNS` shape, and a caller's first
    # sentence is where they say what is wrong more often than anywhere else:
    # "a little bit of a problem with my left ankle, it's nothing serious"
    # (northgate, 10 Sep 2026) -- body part, no pain word, greeting before it
    # -- got the apology as the first thing Susie said. Four of the six
    # opening-turn stalls in the 6-12 Sep corpus were this shape.
    _prev = prev_assistant or ""
    _reason_answer = bool(
        (question_asks_the_reason(_prev) or _GREETING.search(_prev))
        and re.search(_BODY, utterance, re.IGNORECASE)
        and not re.search(r"\?\s*$", utterance)
    )

    _answer_probe = _LEADING_DISFLUENCY.sub("", utterance)
    # "hello?" mid-call is the caller checking we are still on the line, and
    # the two things it used to get were both wrong: the contentless apology,
    # and then the model's own prompt-driven "still here -" on top of it, so
    # the caller heard "still with you ... still here". Not on the first turn
    # -- "hello" after the greeting is just a hello.
    if (
        _CHECK_IN.match(_answer_probe)
        and len(_answer_probe.split()) <= 4
        and _prev
        and not _GREETING.search(_prev)
    ):
        return [Intent.CHECK_IN]
    if _BARE_ANSWER.match(_answer_probe) and len(_answer_probe.split()) <= 4 \
            and not _reason_answer \
            and not _answers_a_preference_question(_prev, _answer_probe):
        # This used to read: "a bare answer names no day, so it cannot reach
        # SLOT_PICKED either". **That premise is false**, and it cost the fix
        # below its whole point on the night it shipped.
        #
        # "uh yeah monday works" strips to "yeah monday works" -- opens with a
        # bare-answer word, three words, AND names a day. The early return
        # fired and SLOT_PICKED at the end of this function was unreachable.
        # Live 2026-09-03 01:26:49 on the demo line: no head at all. The very
        # next call said "yeah monday the 7th at 10 in the morning" -- nine
        # words, past the limit -- and the head fired. Same pick, same intent,
        # opposite behaviour, decided by word count.
        #
        # A short pick is the COMMON way to answer a readout, so this was not
        # an edge: it was most of them.
        #
        # The exemption is deliberately as narrow as the arm it feeds. Both
        # conditions of SLOT_PICKED must already hold -- the engine's own B-90
        # verdict that this utterance is a selection, and a named day -- so
        # nothing reaches the loop below that would not have reached the arm
        # at the end anyway. Band-only picks ("yeah ten in the morning") name
        # no day, still return here, and keep the silence that
        # `test_choosing_a_slot_still_gets_silence` decided on 30 Aug.
        if not (slot_selection and re.search(_DAY, _answer_probe, re.IGNORECASE)):
            # A bare answer IS an answer -- so the moment it belongs to is the
            # question Susie asked, and that is where its head comes from.
            return _answer_moment(_prev, _answer_probe, slot_selection=slot_selection)
    # Either route means the caller is answering rather than asking: an
    # explicit confirm question from Susie, or -- the case the readout proxy
    # was reaching for and getting wrong -- this utterance being one of the
    # slot labels just offered.
    # `offer_refused` suppresses the diary intents exactly as answering does,
    # and DELIBERATELY does not touch `slot_selection`: that argument also
    # enables the SLOT_PICKED arm below, and "Tuesday it is -" spoken over
    # "tuesday doesn't work" would be worse than the lie it replaces (B-149).
    answering = bool(slot_selection) or bool(offer_refused) or bool(
        _CONFIRM_Q.search(prev_assistant or "")
    )
    hits = []
    for intent, trigger, corroborator, blocker in _INTENT_RULES:
        if not trigger.search(utterance):
            continue
        if corroborator is not None and not corroborator.search(utterance):
            continue
        if blocker is not None and blocker.search(utterance):
            continue
        if answering and intent in _DIARY_INTENTS:
            continue
        # A name is not an intent.
        #
        # CAffe1e087 (northgate, 8 Sep 2026, build 4d68f3d8). Susie asked "could
        # I take your first name and surname?", the caller answered, and STT
        # split it. A short fragment arrived -- 'canceling it' -- and the engine
        # KEPT it on purpose:
        #
        #   [ms_conn] same-breath straggler KEPT (name collection, short
        #             fragment - likely surname): 'canceling it'
        #
        # ...and then this table read the same fragment as a request: the
        # CANCEL_REQ trigger `\bcancel\w*\b` with `it` as its corroborator.
        # Susie said "Yes, no problem -", acknowledging a cancellation nobody
        # had asked for. That went into history, the model committed, looked the
        # number up, found a REAL earlier booking and offered to cancel it. The
        # booking under way was never made and the caller hung up.
        #
        # Two rules, each right on its own, that disagree about what the
        # fragment IS: one says surname candidate, the other says intent. The
        # straggler rule is the load-bearing one -- it exists so a surname is
        # not dropped -- so this is the side that yields.
        #
        # Narrow deliberately. It suppresses the HEAD, not the model: a caller
        # who genuinely wants to cancel mid-name is still heard, they just do
        # not get the acknowledgement before anything has decided. And it is
        # only the topic-switch family -- SYMPTOM, SLOT_PICKED and the FAQ
        # intents stay live, because none of them changes what Susie is doing.
        #
        # Self-inflicted, and datable: 790f4604 (29 Aug) let the caller's raw
        # text choose the head at all. Before it the head came from the tool
        # being invoked, so a cancellation could not be announced before
        # something had decided to cancel. Zero prior instances in 921 stored
        # calls -- every name-ask turn in the corpus was checked.
        if name_pending and intent in _TOPIC_SWITCH_INTENTS:
            continue
        hits.append(intent)
    if (
        service_named
        and not answering
        and Intent.BOOK_NEW not in hits
        and _SERVICE_REQUEST.search(utterance)
        and not _SERVICE_ENQUIRY.search(utterance)
        and not _NEGATED.search(utterance)
        and not _rx(r"\b(?:cancel|reschedul|rebook|move|change)\w*\b").search(utterance)
    ):
        # B-146, CA5c65cb4b, northgate, 2026-09-05 23:13:07. The caller's FIRST
        # sentence was "yeah can i have a good sports massage please".
        #
        #     treatment mention (FAQ, no booking intent)
        #     filler phrase triggered: 'Sorry, still with you -'   <- no head
        #     LAT turn_seq=19 llm_ttft_ms=4606 content_ttfa_ms=5328
        #
        # `Intent.BOOK_NEW` triggers on `book|booking|appointment`. The caller
        # named a SERVICE and a want verb and never said "book", so nothing
        # matched, nothing spoke at 600ms, the model took 4.6s and UNKNOWN_SLOW
        # apologised for a wait -- on the opening turn of the call.
        #
        # Adding "massage" to the trigger is the trap this file warns about
        # twice already: the matcher SHAPE is the bug. The shape is right --
        # a request verb corroborated by what is being asked for -- and the
        # corroborator was the half that could only see the word "appointment".
        # So the SERVICE half is asked of the engine, which already decided it
        # one line earlier (`_is_treatment_specific_booking`), exactly as
        # `slot_selection` asks it for B-90's verdict rather than guessing.
        #
        # Deny by default, in FIRST PERSON only, because the head asserts what
        # happens next. "can I have a sports massage" is a request; "can you do
        # sports massage", "how much is a sports massage" and "i'd like to know
        # about sports massage" are questions, and "Let's get you booked in -"
        # in front of any of them promises work nobody asked for.
        #
        # `answering` is honoured like every other diary intent, so this cannot
        # fire while the caller is answering a confirm question or picking a
        # slot. It changes only what is SAID while they wait: it does not touch
        # `booking_flow_active`, which reaches the write gates and whose FAQ
        # false-positive is BUG-7, an owner-signed decision in connection.py.
        hits.append(Intent.BOOK_NEW)
    if not hits and _reason_answer:
        # Nothing matched, and the caller has just told us what is wrong with
        # them. SYMPTOM is the only honest head here: it promises no work and
        # acknowledges what they said, which is what the 3.5s apology does not.
        hits.append(Intent.SYMPTOM)
    if not hits and slot_selection and re.search(_DAY, utterance, re.IGNORECASE):
        # Every diary intent this pick corroborated was just suppressed, which
        # is correct and used to leave nothing. `_kind` then fell through to
        # UNKNOWN_SLOW, whose own comment says to prefer something specific
        # "unless the CALLER told us what this turn is about" -- and a pick is
        # exactly that.
        #
        # A DAY, specifically, for two reasons that happen to agree:
        #
        #   * `subject_for` capitalises a day but lower-cases a band, so
        #     "{subject} it is -" reads as "Monday it is -" but "afternoon it
        #     is -" -- an opener in lower case, which is not speech anyone
        #     writes; and
        #   * every case pinned by `test_choosing_a_slot_still_gets_silence`
        #     (30 Aug) and `test_a_resolved_pick_silences_the_lookup_head` is a
        #     band-only pick. Those tests decided that a pick gets SILENCE, and
        #     their stated reason -- "a head in front of it would promise" a
        #     lookup -- is not this head, which promises nothing. But that is a
        #     decision to reopen deliberately, not to overturn as a side effect
        #     of fixing a different phrase, so this stays inside it.
        #
        #   REOPENED 12 Sep 2026, by the owner, deliberately. Band-only and
        #   clock-time picks get "That one works -" (subject-free, see
        #   `render_intent_head`), because the silence the 30 Aug tests
        #   decided on had become the contentless apology: 7 of the 45 stalls
        #   in the 6-12 Sep corpus were "yeah ten past twelve works" shapes.
        #   The day pick keeps "{Monday} it is -"; nothing else names a time,
        #   because the read-back that follows names the ENGINE's accepted
        #   slot (D-r/D-s) and a head must not pre-empt it.
        hits.append(Intent.SLOT_PICKED)
    if not hits and (offer_refused or _CORRECTION.search(utterance)):
        # Every diary intent was suppressed because the caller is ruling
        # something out. Right -- and what speaks instead used to be nothing,
        # then the apology. "Not to worry -" claims no work and does not
        # argue with them.
        hits.append(Intent.REFUSAL)
    if not hits:
        hits.extend(_answer_moment(_prev, _answer_probe, slot_selection=slot_selection))
    return hits


def _answers_a_preference_question(prev_assistant: str, probe: str) -> bool:
    """"yeah anytime next week" is a preference, not a bare answer. PURE.

    Four words, opens with "yeah": the bare-answer return swallowed it and
    NAMED_WEEK never ran (northgate, 9 Sep 2026 -- the apology followed). The
    exemption is only for a reply to Susie's own preference question that
    names a day, a week or a band, so nothing reaches the diary intents that
    the caller did not ask for.
    """
    if not prev_assistant or not _PREF_Q.search(prev_assistant):
        return False
    return bool(re.search(
        _DAY + r"|" + _BAND + r"|\b(?:next week|this week|week after|tomorrow)\b",
        probe, re.IGNORECASE,
    ))


def _answer_moment(prev_assistant: str, probe: str, *, slot_selection: bool = False):
    """The head owed to an ANSWER, read from the question Susie asked. PURE.

    `classify_intent` reads the caller's words for a REQUEST. When they are
    answering rather than asking, their words carry nothing -- "yes", "um yes
    it is", "that'll be Quentin" -- and the moment is entirely defined by what
    Susie said last. 53 of the 178 headless caller turns in the 6-12 Sep 2026
    corpus were a yes/no to her question, 15 more were a name, 31 a pick with
    no day in it; each fell to the contentless apology when the model was slow.

    Ordered, first match wins, deny by default. Every head here claims no
    work: it acknowledges the answer, and the reply is joined onto it.
    """
    prev = prev_assistant or ""
    if not prev or _SCREEN_Q.search(prev):
        return []
    if _NEGATED.search(probe) and not _AFFIRM.match(probe):
        # A "no" is a refusal of whatever was asked; that has its own arm.
        return [Intent.REFUSAL] if _CONFIRM_Q.search(prev) or _BOOK_OFFER_Q.search(prev) \
            or _PICK_Q.search(prev) else []
    # A yes is SHORT. "um yeah that'll be quentin rock" opens with a yes-word
    # and is a name; four words is the same limit the bare-answer test uses.
    affirmed = bool(_AFFIRM.match(probe)) and len(probe.split()) <= 4
    # 1. "Would you like to book in?" -> yes. Same head as asking to book.
    if _BOOK_OFFER_Q.search(prev) and affirmed:
        return [Intent.BOOK_NEW]
    # 2. "Is that the best number for the booking?" -> yes / the digits /
    #    "use this number" (the prompt's own scripted reply).
    if _NUMBER_Q.search(prev) and (
        affirmed or re.search(r"\d{3,}|\b(?:oh|zero|nought)\b|use this number", probe, re.IGNORECASE)
    ):
        return [Intent.NUMBER_CONFIRMED]
    # 2b. "Did you say Sandrine — is that right?" -> yes. A name, confirmed.
    if _NAME_CONFIRM_Q.search(prev) and affirmed:
        return [Intent.NAME_GIVEN]
    # 3. A slot from a readout, chosen by yes, clock time or ordinal -- never
    #    a request in disguise ("what about around twelve").
    if (slot_selection or _PICK_Q.search(prev)) and not _REQUEST_SHAPE.search(probe):
        names_a_day = bool(re.search(_DAY, probe, re.IGNORECASE))
        if names_a_day and not slot_selection:
            # A DAY without the engine's verdict is a request about a day we
            # may not have offered -- `test_the_exemption_needs_the_engines_
            # verdict_too`. Only the engine may turn a named day into a pick.
            return []
        if affirmed or _ACCEPTS.search(probe) or _CLOCKISH.search(probe) \
                or _ORDINAL_PICK.search(probe) or re.search(r"\b" + _CLOCK + r"\b", probe, re.IGNORECASE):
            return [Intent.SLOT_PICKED]
    # 4. "Which clinic, X or Y?" -> one of them. The options come from her
    #    sentence, so no clinic name is written here.
    choice = _OR_CHOICE.search(prev)
    if choice and any(re.search(r"\b" + re.escape(opt) + r"\b", probe, re.IGNORECASE)
                      for opt in choice.groups()):
        return [Intent.CLINIC_CHOSEN]
    # 5. Her name question, answered with anything that is not a question.
    if _NAME_Q.search(prev) and "?" in prev and not probe.rstrip().endswith("?") \
            and not affirmed and len(probe.split()) <= 8:
        # "yes" to "could I take your name?" has not given one yet; a long
        # reply is a story, not a name.
        return [Intent.NAME_GIVEN]
    return []


#: A sign-off. Anchoring this to the START of the utterance was the first
#: attempt and it failed in both directions at once: it missed "Alright. I'll
#: ring 111 then. Thanks." -- the exact call that prompted the fix -- while
#: matching "Thanks, could you check Thursday for me?", which is a request
#: with a courtesy on the front and the turn that needs a head most.
_CLOSING_MARKER = re.compile(
    r"\b(?:thanks?|thank you|cheers|ta|bye|goodbye|see you|that\'?s all|that\'?ll do|nothing else|i\'?m all set|i\'?ll (?:ring|call|leave it|think about it|give you a (?:ring|call)))\b",
    re.IGNORECASE,
)

#: An unambiguous sign-off. Nothing follows one of these.
_FAREWELL = re.compile(
    r"\b(?:bye|goodbye|see you|take care|speak soon)\b",
    re.IGNORECASE,
)

#: Anything that means the caller still wants something. Its presence beats
#: any number of pleasantries.
_STILL_WANTS = re.compile(
    r"\?"
    r"|\b(?:can|could|would|will|do) (?:you|i|we)\b"
    r"|\b(?:check|book|move|cancel|change|reschedul\w*|look|find|have you got|got any)\b"
    r"|\b(?:mon|tues|wednes|thurs|fri|satur|sun)day\b"
    r"|\b(?:morning|afternoon|evening|next week|tomorrow)\b",
    re.IGNORECASE,
)


def is_closing(text: str) -> bool:
    """Is the caller saying goodbye rather than waiting for something? PURE.

    A hold phrase exists to cover a wait. Nobody who has just said "thanks,
    I'll ring 111 then" is waiting for a lookup, so a head there is the
    promised-work defect in its purest form -- it was simply unreachable on
    these turns until heads began firing on them.

    Found by the adaptive-caller suite on the red-flag call, which is the worst
    possible place for it: the caller had just been told to contact NHS 111 and
    heard "Sorry, still with you -- Take care of yourself."

    Deny-by-default in the direction that matters: an utterance that still asks
    for something is NOT closing, however politely it is phrased, so the cost of
    a miss is a head the caller did not need rather than silence on a turn that
    wanted one.
    """
    utterance = (text or "").strip()
    if not utterance or len(utterance.split()) > 14:
        return False
    if "?" in utterance:
        return False
    # An explicit farewell settles it, and has to be checked BEFORE
    # _STILL_WANTS: "see you Friday" contains a weekday and is a goodbye,
    # not a request for Friday.
    if _FAREWELL.search(utterance):
        return True
    if _STILL_WANTS.search(utterance):
        return False
    return bool(_CLOSING_MARKER.search(utterance))


#: A clock time, spoken the way a caller says one back. Bands are deliberately
#: NOT here -- see `utterance_accepts_an_offer` for why "mornings work better"
#: must stay outside this.
_CLOCKISH = re.compile(
    r"\b(?:"
    r"\d{1,2}[:.]\d{2}"                                    # 8:30
    r"|\d{1,2}\s*(?:am|pm)\b"                              # 8 am
    r"|\d{1,2}\s*o'?\s*clock"                              # 5 o'clock
    r"|(?:half|quarter|five|ten|twenty|twenty[-\s]five)\s+(?:past|to)\s+\S+"
    r"|(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    r"\s+(?:in\s+the\s+)?(?:morning|afternoon|evening)"
    r"|\d{1,2}\s+(?:in\s+the\s+)?(?:morning|afternoon|evening)"
    r")",
    re.IGNORECASE,
)

#: The caller saying yes to something on the table.
_ACCEPTS = re.compile(
    r"\b(?:works?|suits?|suitable|i'?ll\s+take|i'?ll\s+have|let'?s\s+do|"
    r"go\s+for|happy\s+with|that'?ll\s+do|perfect|ideal|lovely|brilliant|"
    r"grand|great|fine|that'?s\s+(?:good|fine|great|perfect))\b",
    re.IGNORECASE,
)

#: Any negator at all. Same wholesale rule, and for the same reason, as
#: `_DAY_REFUSE_RE` in slot_followup: every word in `_ACCEPTS` can be carrying
#: a refusal ("that doesn't work", "not great"), and a negated acceptance is
#: never clean enough to act on.
_REFUSES = re.compile(
    r"\b(?:no|nope|nah|not|none|never|cannot|rather)\b|n'?t\b",
    re.IGNORECASE,
)


def utterance_accepts_an_offer(text: str) -> bool:
    """Is the caller plainly ACCEPTING something already offered? PURE.

    A backstop for `slot_selection`, not a replacement for it. B-145b,
    `CAa0389cae`, northgate, 2026-09-05 23:10:24:

        caller: 'um 10 past 5 in the evening suits'
        Susie:  situational head (time_band): "Let me see what I've got in the
                evening -"
        Susie:  "so that's Monday the 7th of September at ten past five in the
                evening"

    She promised a lookup to a caller who had just chosen, and then did not do
    one -- she confirmed. Fourth instance of the promised-work defect on a pick.

    Every existing reader of `slot_selection` RESOLVES: containment against the
    spoken labels, `slot_accepted_by_caller`, `day_accepted_by_caller`. Each can
    decline for a reason that has nothing to do with whether the caller picked
    -- here all three did, because the offer still held three dates. Resolving
    is the right way to decide WHICH slot; it is the wrong way to decide whether
    to promise a lookup, because the cost of a decline is a false promise.

    So this asks the cheaper question and answers it deny-by-default:

      1. an acceptance word, and NO negator anywhere;
      2. corroborated by a DAY or a CLOCK TIME -- something concrete from the
         offer, echoed back.

    A BAND alone is deliberately excluded. "mornings work better" is a
    PREFERENCE, the lookup it asks for really does happen, and suppressing
    "Let me see what I've got in the morning -" there would remove a head that
    is telling the truth. That is the direction this family must not fail in.

    The caller of this decides WHEN to ask it -- only while an offer is on the
    table. Guard on the MAP, per the rule `connection.py` already states.
    """
    utterance = (text or "").strip()
    if not utterance:
        return False
    if _REFUSES.search(utterance):
        return False
    if not _ACCEPTS.search(utterance):
        return False
    return bool(
        re.search(_DAY, utterance, re.IGNORECASE) or _CLOCKISH.search(utterance)
    )


def utterance_refuses_an_offer(text: str) -> bool:
    """Is the caller plainly RULING OUT something already offered? PURE.

    The mirror of `utterance_accepts_an_offer`, and needed for the same reason
    from the opposite direction. B-149, `CA176b7a0d`, northgate, 2026-09-06
    22:07:59, on the call that verified B-147:

        caller: 'um tuesday doesn't work'
        Susie:  situational head (named_day): "Let me see what Tuesday looks
                like -"
        Susie:  "So sticking with Monday, I've got eight in the morning or ten
                past five in the evening"

    She promised a Tuesday lookup and then talked about Monday. Fifth instance
    of the promised-work defect, and the first on a refusal: `_DAY` triggers
    NAMED_DAY on any weekday, and naming a day to rule it out is the one case
    where the caller wants LESS of that day, not a lookup of it.

    `utterance_accepts_an_offer` correctly says False here -- a refusal is not
    an acceptance -- so the backstop added for B-145b could not cover it. Same
    shape, opposite sign, kept as a separate predicate rather than folded in,
    because the two must feed DIFFERENT arguments: an acceptance sets
    `slot_selection`, which also enables the SLOT_PICKED head, and rendering
    "Tuesday it is -" over "tuesday doesn't work" would be far worse than the
    lie it replaced.

    Deny by default: a refusal marker, corroborated by a DAY or a CLOCK TIME so
    it is about something concrete that was on the table. A bare "no" or "that
    doesn't work" names nothing and is left alone.
    """
    utterance = (text or "").strip()
    if not utterance:
        return False
    if not _REFUSES.search(utterance):
        return False
    return bool(
        re.search(_DAY, utterance, re.IGNORECASE) or _CLOCKISH.search(utterance)
    )


def subject_for(text: str) -> str:
    """The noun a head may name, or "" when nothing is safe to say. PURE.

    Only ever echoes back something the caller actually said. A head must never
    name a day the caller did not, which is why this returns "" rather than a
    guess -- ``render_intent_head`` then picks a subject-free member of the pool.
    """
    utterance = (text or "").strip()
    match = re.search(_DAY + r"\s+" + _BAND, utterance, re.IGNORECASE)
    if match:
        return match.group(0).lower().capitalize()
    match = re.search(_DAY, utterance, re.IGNORECASE)
    if match:
        return match.group(0).capitalize()
    match = re.search(r"\b(next week|this week|the week after|following week|tomorrow)\b",
                      utterance, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    match = re.search(r"\b(" + _BAND + r")s?\b", utterance, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    match = re.search(r"\b(30|60|90|thirty|sixty|ninety)[\s-]?(?:minute|min)\b",
                      utterance, re.IGNORECASE)
    if match:
        spoken = {"30": "thirty", "60": "sixty", "90": "ninety"}
        return f"{spoken.get(match.group(1), match.group(1))}-minute"
    # A clock hour, last, for TIME_AROUND: "around 12" -> "twelve", "12:30" ->
    # "twelve thirty". Only after every other subject, so "wednesday at 12"
    # still names Wednesday. Words are returned as spoken, never digits, so
    # the voice does not read "12" as a number on its own.
    match = re.search(
        r"\b(?:around|about|close(?:st)? (?:as possible )?to|near(?:er|est)? to|"
        r"nearer|after|before|from|by|at)\s+(" + _CLOCK + r")",
        utterance, re.IGNORECASE,
    )
    if match:
        raw = match.group(1).lower().strip()
        if raw in ("midday", "noon"):
            return "midday"
        if raw == "lunchtime":
            return "lunchtime"
        m = re.match(r"(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?", raw)
        if m:
            hour = _HOUR_WORD.get(m.group(1), "")
            if not hour:
                return ""
            mins = m.group(2)
            if mins and mins != "00":
                mins_word = {"15": "fifteen", "30": "thirty", "45": "forty-five"}.get(mins, "")
                if not mins_word:
                    return ""
                return f"{hour} {mins_word}"
            return hour
        word = re.match(r"(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)", raw)
        if word:
            return word.group(1)
    return ""


#: One head per intent. Every pool that uses ``{subject}`` also carries a
#: subject-free member, the precedent ``render_head`` already sets for
#: ``{practitioner}``: a head must never say "Let me see what  looks like".
#:
#: The topic heads name the topic and nothing else, so they claim no work and
#: cannot promise a lookup. The register heads are the model's own words,
#: verbatim from stored payloads, which is what lets the stripper remove its
#: duplicate without leaving a hole.
INTENT_HEADS = {
    # TOPIC. "On insurance -" was the first attempt and it reads like an index
    # entry, not a person: heard live on 2026-08-29 and reported as lacking the
    # human feel. A receptionist does not say "on insurance", she says "in
    # regards to insurance". The two lead-in families below are what people
    # actually use on the phone, alternated so a caller who asks two questions
    # does not hear the same construction twice.
    Intent.FAQ_PRICE:        [f"In terms of pricing {EM_DASH}",
                              f"So, on our prices {EM_DASH}"],
    Intent.FAQ_INSURANCE:    [f"In regards to insurance {EM_DASH}",
                              f"As for insurance {EM_DASH}"],
    Intent.FAQ_HOURS:        [f"In terms of our opening hours {EM_DASH}",
                              f"So, on our hours {EM_DASH}"],
    Intent.FAQ_PARKING:      [f"In regards to parking {EM_DASH}",
                              f"As for parking {EM_DASH}"],
    Intent.FAQ_LOCATION:     [f"In terms of where we are {EM_DASH}",
                              f"So, on where we're based {EM_DASH}"],
    Intent.FAQ_TREATS:       [f"In regards to what we treat {EM_DASH}",
                              f"As for what we cover {EM_DASH}"],
    Intent.FAQ_FIRSTTIME:    [f"For your first visit {EM_DASH}",
                              f"So, on your first appointment {EM_DASH}"],
    Intent.FAQ_PRACTITIONER: [f"In terms of who you'd see {EM_DASH}",
                              f"As for who you'd be seeing {EM_DASH}"],

    # REGISTER. These were already the model's own words, verbatim from stored
    # payloads, which is why they needed no rewriting -- they are what a person
    # says because a person said them.
    Intent.SYMPTOM:          [f"Sorry to hear that {EM_DASH}",
                              f"Oh, sorry to hear that {EM_DASH}"],
    Intent.CANCEL_REQ:       [f"No problem at all {EM_DASH}",
                              f"Yes, no problem {EM_DASH}"],
    # A caller who has just picked a slot. Neither of the two things Susie
    # used to say here was true: the diary heads promised a lookup nobody was
    # doing ("Let me see what I've got in the afternoon -", 2 Sep 09:09), and
    # suppressing those dropped the turn into the UNKNOWN_SLOW fallback, which
    # apologises for a wait the caller is not in ("Sorry, still with you -",
    # CA3dff2f4b, 3 Sep 00:24:21, three seconds after the engine had already
    # pinned their choice). This claims no work and names what they chose.
    Intent.SLOT_PICKED:      [f"{{subject}} it is {EM_DASH}",
                              f"That one works {EM_DASH}"],
    Intent.RESCHEDULE_REQ:   [f"Let's get that moved for you {EM_DASH}",
                              f"Yes, let's get that moved {EM_DASH}"],
    Intent.REPEAT_ASK:       [f"Sorry about that {EM_DASH}",
                              f"Apologies for that {EM_DASH}"],
    Intent.TRANSFER_REQ:     [f"Not a problem {EM_DASH}",
                              f"Yes, not a problem {EM_DASH}"],
    # The second register family (12 Sep 2026). Each replaces a turn that fell
    # to the contentless apology; each claims no work. Wording is the owner's
    # to change -- HOLD_SPEECH_REVIEW_PACK_2026-09-12.md carries the rows.
    Intent.CHECK_IN:         [f"Yes, I'm here {EM_DASH}",
                              f"I'm here, yes {EM_DASH}"],
    Intent.REFUSAL:          [f"Not to worry {EM_DASH}",
                              f"No problem {EM_DASH}"],
    Intent.MESSAGE_REQ:      [f"Not a problem {EM_DASH}",
                              f"No problem at all {EM_DASH}"],
    # Answer moments. Never the caller's own words back -- a name or a phone
    # number is STT output, and echoing a misheard one is worse than saying
    # nothing about it.
    Intent.NUMBER_CONFIRMED: [f"Thanks for that {EM_DASH}",
                              f"That's noted {EM_DASH}"],
    Intent.NAME_GIVEN:       [f"Thank you {EM_DASH}",
                              f"Thanks, got that {EM_DASH}"],
    Intent.CLINIC_CHOSEN:    [f"Right you are {EM_DASH}",
                              f"That's the one {EM_DASH}"],

    # DIARY. "Let me find you the soonest -" and "where a sixty-minute fits -"
    # both read as clipped: a person says the noun.
    Intent.NAMED_DAY:        [f"Let me see what {{subject}} looks like {EM_DASH}",
                              f"Let me have a look at {{subject}} for you {EM_DASH}",
                              f"Let me see {EM_DASH}"],
    Intent.NAMED_WEEK:       [f"Let me look at {{subject}} for you {EM_DASH}",
                              f"Let me see what {{subject}} looks like {EM_DASH}",
                              f"Let me see {EM_DASH}"],
    Intent.TIME_BAND:        [f"Let me see what I've got in the {{subject}} {EM_DASH}",
                              f"Let me have a look at the {{subject}}s for you {EM_DASH}",
                              f"Let me see {EM_DASH}"],
    Intent.SESSION_LENGTH:   [f"Let me see where a {{subject}} session fits {EM_DASH}",
                              f"Let me look for a {{subject}} for you {EM_DASH}",
                              f"Let me see {EM_DASH}"],
    Intent.EARLIEST:         [f"Let me find the soonest I've got {EM_DASH}",
                              f"Let me see what the earliest is {EM_DASH}"],
    Intent.AVAIL_QUERY:      [f"Let me see what we've got {EM_DASH}",
                              f"Let me have a look for you {EM_DASH}"],
    Intent.TIME_AROUND:      [f"Let me see what I've got around {{subject}} {EM_DASH}",
                              f"Let me look near {{subject}} for you {EM_DASH}",
                              f"Let me see what we've got {EM_DASH}"],
    Intent.BOOK_NEW:         [f"Let's get you booked in {EM_DASH}",
                              f"Yes, let's get that sorted {EM_DASH}"],
}


def render_intent_head(
    intent, *, subject: str = "", index: int = 0, avoid: str = ""
) -> str:
    """One head for ``intent``, rotated by ``index``. PURE.

    The subject-free member of a pool is a FALLBACK, not a rotation partner.
    Rotating across the whole pool made the caller's own words disappear on the
    second head of a call: NAMED_DAY is
    ["Let me see what {subject} looks like -", "Let me see -"], so index 1
    answered "would you have Saturday" with a bare "Let me see -" even though
    Saturday was right there. Naming what they asked for is the entire point of
    a situational head, so the choice is made among the members that CAN carry
    a subject whenever one is available, and among the rest when none is.

    ``avoid`` is what the assistant said last. The rotation counts only the
    heads WE have spoken -- ``len(session["used_fillers"])`` -- and the model's
    own openers are not in that count, so on the demo line at 03:35 on 9 Sep
    2026 the caller heard

        03:35:44  head:   "No problem at all -"
        03:35:58  model:  "No problem at all."

    CANCEL_REQ's pool holds both that phrase and "Yes, no problem -", so a
    second wording was available and the rotation had no way to know it was
    wanted. This gives it one. It only ever CHOOSES differently: nothing the
    model wrote is stripped, edited or suppressed, and matching happens against
    this module's own constants rather than against a literal lifted out of
    model speech. When every member has been used it returns the rotation's own
    answer -- going quiet is worse than repeating.
    """
    pool = INTENT_HEADS.get(intent) or []
    if not pool:
        return ""
    if intent is Intent.SLOT_PICKED and subject and not re.search(_DAY, subject, re.IGNORECASE):
        # Only a DAY is ever echoed on a pick. A band reads as "afternoon it
        # is -" (lower case, not speech), and a clock time would pre-empt the
        # read-back that names the engine's accepted slot (D-r/D-s). Owner
        # decision 12 Sep 2026: "That one works -" for every other pick.
        subject = ""
    with_subject = [h for h in pool if "{subject}" in h]
    without = [h for h in pool if "{subject}" not in h]
    usable = (with_subject if (subject and with_subject) else without) or pool
    head = usable[index % len(usable)]
    if len(usable) > 1:
        try:
            recent = (avoid or "").lower()
        except Exception:  # pragma: no cover - a head must never break a call
            recent = ""
        if recent:
            for step in range(len(usable)):
                candidate = usable[(index + step) % len(usable)]
                probe = candidate.replace("{subject}", subject or "").strip()
                probe = probe.rstrip(EM_DASH + " -").strip().lower()
                if probe and probe not in recent:
                    head = candidate
                    break
    if "{subject}" in head:
        # Only reachable when the pool has nothing else -- keep the guard, since
        # "Let me see what  looks like" is the failure it exists to prevent.
        if not subject:
            return without[0] if without else ""
        head = head.replace("{subject}", subject)
    return head


def head_families(text) -> frozenset:
    """Which pool this hold phrase came from, or None. PURE.

    N4. `WorkKind.UNKNOWN_SLOW` holds two members that differ only by a leading
    "Sorry, ", and `_second_filler_text` rotates by `len(used_fillers)` -- so a
    turn that stalls twice is GUARANTEED to produce the other one:

        10:03  caller: "um to book an appointment mate"
        10:03  Susie:  "Sorry, still with you -"
        10:03  Susie:  "Still with you -"

    Theorem `CAf9e32e638f07efa25f06c01103bab3ac`, 9 Sep 2026. The caller hung
    up and the judge tagged the call `dead_end`, quoting both phrases. The
    timing was right -- turn 1 stalled 12.7s, so the re-arm was doing its job --
    and only the wording was wrong.

    `_second_filler_text`'s rule 3 already forbade this in words ("Never a
    verbatim repeat. Hearing the identical phrase twice reads as a stuck line
    rather than a hold") and tested it with `==`, which two wordings of one
    sentence walk straight past. Third instance of an equality test standing in
    for "is this the same thing again?" -- see the head echo (`66b8c209` ->
    `ff0fa987`) and D5 (`9cff40aa`).

    Returns a SET, because one wording can belong to several pools: "Let me
    see -" is the subject-free fallback of `diary_read`, `named_day`,
    `named_week`, `time_band` and `session_length` alike. Two phrases are "the
    same thing again" when their sets INTERSECT, which is the question the
    caller of this actually has.

    Built from the pools themselves rather than a hand-kept list, for the same
    reason `_head_pattern` is: a copy of this mapping is exactly the thing that
    goes stale the first time a head is reworded. Never raises -- a hold phrase
    is a nicety and the answer to "what family is this?" must not fail a turn.
    """
    found: set = set()
    try:
        probe = str(text or "").strip().lower()
        if not probe:
            return frozenset()
        for pools in (HEADS, INTENT_HEADS):
            for key, members in pools.items():
                for head in members:
                    if "{" in head:
                        continue    # needs a subject; matched by _HEAD_RE
                    if head.strip().lower() == probe:
                        found.add(getattr(key, "value", str(key)))
    except Exception:  # pragma: no cover - a filler must never break a call
        return frozenset()
    return frozenset(found)


def _head_pattern():
    """Every head, as one regex, with the placeholders opened out.

    Built at import from the pools themselves so it cannot drift from them --
    a hand-maintained copy of this list is exactly the kind of thing that goes
    stale the first time a head is reworded.
    """
    parts = []
    for pool in list(INTENT_HEADS.values()) + list(HEADS.values()):
        for head in pool:
            literal = re.escape(head)
            literal = literal.replace(re.escape("{subject}"), r".{1,40}")
            literal = literal.replace(re.escape("{practitioner}"), r".{1,30}")
            parts.append(literal)
    return re.compile(r"^\s*(?:" + "|".join(parts) + r")\s*$", re.IGNORECASE)


_HEAD_RE = _head_pattern()


def is_hold_head(text: str) -> bool:
    """Is this chunk a hold head rather than ordinary speech? PURE.

    The TTS layer reads it to pace the head like a person instead of rushing
    it. A head is a ten-to-forty-character fragment with no sentence around it,
    and ElevenLabs flash gives it no prosodic context, so at the call's default
    speed it comes out noticeably faster than everything else. Reported on the
    first live call that heard one: "spoke too quickly compared to how Susie
    speaks".

    Matched against the pools rather than by shape ("short and ends in a dash"),
    because the chunker legitimately emits short dash-terminated fragments of
    model speech and slowing those would change the whole call's cadence.
    """
    return bool(text) and bool(_HEAD_RE.match(text.strip()))


#: A discourse marker standing on its own, with nothing behind it.
#:
#: The prompt MANDATES one of these at booking step 1 -- "acknowledge simply:
#: 'Right -' and NOTHING ELSE", because connection.py then injects the next
#: question. So the marker is not a model quirk to be reworded away; it is a
#: deliberate stub, and it reads correctly when it is the only acknowledgement
#: the caller hears.
#:
#: A CLOSED set, matched only when it is the WHOLE chunk. Both halves matter.
#: The set is closed because the phrase is our own prompt's, not the model's
#: invention -- the same argument ACK_OPENER_RE makes for being an allow-list,
#: and the shape-based version it rejects would be worse here, where the
#: chunker legitimately emits short dash-terminated fragments of real speech.
#: Requiring the whole chunk is what keeps "Right - Tuesday at ten is free"
#: out of it: only a chunk that says nothing at all can be dropped for saying
#: nothing.
#:
#: Deliberately NOT ACK_OPENER_RE, which is a wider family ("of course",
#: "got it", "no problem at all") serving `strip_head_echo` at a different
#: layer. Reusing it here would drop 67 chunks across the stored corpus where
#: the evidenced defect is 32, and widening blast radius on the barge-in/TTS
#: path is not a thing to do for free.
BARE_MARKER_RE = re.compile(
    r"^\s*(?:right|so|okay|ok|alright|all right|now|well)"
    r"\s*[,.!—–-]*\s*$",
    re.IGNORECASE,
)


def is_bare_discourse_marker(text: str) -> bool:
    """Is this chunk a discourse marker and nothing else? PURE.

    Read by connection.py's TTS loop to decide whether a chunk is worth
    synthesising once a hold phrase has already performed the same speech act.
    """
    return bool(text) and bool(BARE_MARKER_RE.match(text.strip()))


#: The same markers, LEADING a chunk that has real speech behind them.
#:
#: Em/en dash only, never the comma form. "Right, Alcester." and "Right, that's
#: the number confirmed" are Susie AGREEING with something the caller just
#: said -- the marker is carrying the agreement, and cutting it turns a
#: confirmation into a bare assertion. The dash form carries nothing: it is the
#: booking-step-1 stub with the question welded onto it.
_LEAD_MARKER_RE = re.compile(
    r"^\s*(?:right|so|okay|ok|alright|all right|now|well)\s*[—–]\s*(?=\S)",
    re.IGNORECASE,
)


def strip_marker_before_question(text: str) -> str:
    """Drop a leading discourse marker when a QUESTION follows it. PURE.

    Owner instruction, 2026-09-01: asking to book and being answered "Right --
    what's the appointment for?" should just be the question. The marker is the
    prompt's mandated booking-step-1 acknowledgement (see BARE_MARKER_RE); when
    the model welds the question onto it rather than stopping, the caller hears
    an acknowledgement they did not need in front of the only thing that
    matters.

    Requiring a QUESTION behind it is what keeps this off the ten stored cases
    where the marker is doing real work -- "Right -- mornings it is.",
    "Right -- Thursday afternoon.", "Right -- so you'd like to come in next
    week." Those acknowledge what the caller SAID, and are statements. Over the
    798-call corpus this rewrites 201 chunks, every one of them a booking-flow
    question, and leaves those ten alone.

    Returns ``text`` unchanged when it does not apply, and never returns empty:
    a chunk that is NOTHING but the marker has no question behind it and so
    never matches here -- that case belongs to ``is_bare_discourse_marker``.
    """
    if not text:
        return text
    match = _LEAD_MARKER_RE.match(text)
    if not match:
        return text
    rest = text[match.end():].lstrip()
    if not rest or not rest.rstrip().endswith("?"):
        return text
    return rest[0].upper() + rest[1:]


#: Susie's own acknowledgement and hold openers, for removal once a head has
#: already performed that speech act.
#:
#: An ALLOW-list, and deliberately so. A shape-based version was tried first --
#: "a short leading clause with no digits or dates is an acknowledgement" -- and
#: was far worse: on the stored corpus it ate "I've got you on oh three three"
#: (half a phone number, read out as words, so no digits to see) and "a rolled
#: ankle like that can be really sore". What is being removed is ONE speech act
#: the head has already performed, and that is a closed set, because it is our
#: own prompt that produces it.
ACK_OPENER_RE = re.compile(
    r"^\s*(?:"
    r"(?:right|okay|ok|lovely|great|perfect|brilliant|sure|certainly|absolutely)"
    r"|(?:of course)|(?:got (?:it|that|you))|(?:no problem(?: at all)?)|(?:not to worry)"
    # "still here" as well as "I'm here": the prompt example was changed to
    # the head's wording, and the model wrote "still here" anyway on both
    # live check-ins of 12 Sep 2026 (CAe541a6a9, CA5c69c585) -- so the caller
    # heard "I'm here, yes — still here — could I take your name?". The
    # opener is stripped by this allow-list, not by matching the head.
    r"|(?:(?:yes, )?(?:i'm |still )here)|(?:thanks?(?: for that)?)|(?:thank you)"
    r"|(?:no worries)|(?:that's (?:fine|no problem|absolutely fine))"
    r"|(?:i'm sorry to hear (?:that|about that))|(?:sorry to hear (?:that|about that))"
    r"|(?:(?:my )?apologies(?: for (?:that|the confusion))?)"
    r"|(?:sorry(?: about (?:that|the confusion))?)"
    r"|(?:let's get that (?:moved|sorted|changed)(?: for you)?)"
    r"|(?:(?:just )?(?:one|a) moment(?: while i (?:check|look|find)[^.!?—-]{0,40})?)"
    r"|(?:let me (?:just )?(?:check|look|see|find)[^.!?—-]{0,40})"
    r"|(?:just (?:checking|looking)[^.!?—-]{0,40})"
    r"|(?:right with you)|(?:just getting that for you)"
    r")\s*(?:[,.!?—-]|$)\s*",
    re.IGNORECASE,
)


def strip_head_echo(chunk: str, head: str) -> str:
    """Drop the model's own opener once ``head`` has already said it. PURE.

    The head is chosen to be what the model was going to say anyway, so its
    opener is the second time the caller hears it. At most ONE clause goes, and
    only one that matches ``ACK_OPENER_RE``.

    Never returns empty: a reply that is nothing BUT an opener is left alone,
    because a head with nothing behind it is the dead-end defect this whole
    change exists to remove.
    """
    if not chunk or not head:
        return chunk
    match = ACK_OPENER_RE.match(chunk)
    if not match:
        return chunk
    rest = chunk[match.end():].lstrip()
    return rest or chunk


# ── Import-time guarantees ───────────────────────────────────────────────────
# A bad head must be UNDEPLOYABLE, not merely unspoken. Deterministic hold
# phrases bypass sanitise_response entirely (see app/filler_phrases.py), which is
# how "Getting that all booked in for you..." once reached callers as a completed
# booking claim. Checking here costs nothing on the hot path and turns that class
# of mistake into a startup failure in CI rather than a sentence a patient hears.

_OPEN_CLAUSE = (EM_DASH, ",", "-")

#: Talk about the wait itself. Banned from every pool but LONG_WAIT.
_GENERIC_WAIT = re.compile(
    r"\b(?:still (?:with you|here)|one sec\w*|one second|one moment|a moment|"
    r"bear with|just a sec\w*|hold on|hang on|right with you|"
    r"just getting that)\b",
    re.IGNORECASE,
)

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

    # 5. No first-rung pool may talk about the WAIT. "Sorry, still with you -"
    #    at 2.75s was the owner's complaint of 12 Sep 2026 (45 turns in 30 of
    #    the last 100 calls); an apology for a wait belongs to LONG_WAIT and
    #    nowhere else, and "one sec" / "one moment" / "bear with" are the
    #    generic-filler register this whole module exists to replace.
    for pools in (HEADS, INTENT_HEADS):
        for key, pool in pools.items():
            if key is WorkKind.LONG_WAIT:
                continue
            for head in pool:
                assert not _GENERIC_WAIT.search(head), (
                    f"{getattr(key, 'value', key)} talks about the wait, which "
                    f"only LONG_WAIT may do: {head!r}"
                )


    # ── The intent heads ─────────────────────────────────────────────────
    # Same four guarantees as the work heads above, plus the three that only
    # apply once a head can carry a subject the caller supplied.
    for intent, pool in INTENT_HEADS.items():
        assert pool, f"{intent} has no heads"

        # A pool that can name a subject must also be able to say nothing about
        # one, or a caller who named no day hears "Let me see what  looks like".
        # Same rule, and the same reason, as {practitioner} in render_head.
        if any("{subject}" in h for h in pool):
            assert any("{subject}" not in h for h in pool), (
                f"{intent} can only render WITH a subject, so a caller who "
                f"named none gets a head with a hole in it: {pool!r}"
            )
            assert "{" not in render_intent_head(intent, subject=""), (
                f"{intent} leaks a placeholder when the caller named nothing"
            )

        for head in pool:
            rendered = head.replace("{subject}", "Tuesday")

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
            #    be saying either.
            for name, rx in _BANNED_SENTENCE_RE:
                assert not rx.search(rendered), (
                    f"head {head!r} is deleted by Gate 5b/{name}"
                )

            # 3. Not a bare discourse marker. "Right -" in front of an instant
            #    reply failed on live calls in three separate ways and could not
            #    have been fixed by rewording -- the model opens with the same
            #    marker, so the caller hears "Right. Right, what's...". Two words
            #    minimum is what separates a head from a noise.
            words = rendered.rstrip(" " + "".join(_OPEN_CLAUSE)).split()
            assert len(words) >= 2, (
                f"a one-word head is a discourse marker, not a head: {head!r}"
            )

    # 4. A topic head answers a question; no tool runs on those turns at all.
    #    So it must not name work, for the same reason UNKNOWN_SLOW must not:
    #    175 of the 322 stored hold phrases promised a lookup nobody was doing,
    #    and an FAQ turn is the single largest group of them.
    for intent, pool in INTENT_HEADS.items():
        if not intent.value.startswith("faq_"):
            continue
        for head in pool:
            assert not _NAMES_THE_WORK.search(head), (
                f"a topic head stands in front of an ANSWER, not a lookup, so "
                f"it must name no work: {head!r}"
            )

_self_check()
