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


#: Intents that assert a diary read. Only these are suppressed while the caller
#: is answering a confirm question -- sympathy and an apology stay correct there.
_DIARY_INTENTS = frozenset({
    Intent.NAMED_DAY, Intent.NAMED_WEEK, Intent.TIME_BAND, Intent.SESSION_LENGTH,
    Intent.EARLIEST, Intent.AVAIL_QUERY, Intent.BOOK_NEW,
})

_DAY = r"(?:mon|tues|wednes|thurs|fri|satur|sun)day"
_BAND = r"(?:morning|afternoon|evening|lunchtime)"
_BODY = (r"(?:knee|ankle|shoulder|hip|back|neck|wrist|elbow|foot|feet|calf|"
         r"hamstring|sciatic|groin|thigh|spine|arm|leg|hand|glute|quad|achilles)")
# Injury is often described with no word for pain at all -- "done my ankle",
# "went over on it", "it gave way". The screening triggers learned the same
# lesson the hard way (a caller saying "my ankle ... I twisted it" armed no
# screen): adding more synonyms is the trap, the SHAPE of the matcher is the bug.
_HURT = (r"(?:pain|painful|injur\w*|sprain\w*|strain\w*|ache|aching|stiff\w*|"
         r"sore|tension|pulled|tight\w*|hurt\w*|niggl\w*|twist\w*|roll\w*|"
         r"went over|gave way|done (?:my|in)|popped|locked|swollen|seized)")
_SERVICE = (r"(?:acupuncture|massage|shockwave|physio\w*|sports|dry.?needl\w*|"
            r"laser|rehab\w*|pilates|osteo\w*|treatment|therapy|service)")
_WANT = r"(?:like to|want to|need to|can i|could i|looking to|wanting to|make|get|do)"


def _rx(pattern: str):
    return re.compile(pattern, re.IGNORECASE)


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
_CONFIRM_Q = _rx(r"\b(?:did you mean|is that (?:right|correct|the right one)|"
                 r"shall i (?:go ahead|book)|just to confirm|does that work|"
                 r"is that the best number|which (?:one|of those)|number \d)\b")

#: A clinical screen question, matched against what Susie said LAST. The reply
#: to one is a red-flag answer, and it is the worst moment in the call to guess:
#: no head may fire there, whatever else matches.
_SCREEN_Q = _rx(r"\b(?:swollen|warm or red|numbness|tingl\w*|bladder|bowel|"
                r"saddle|unexplained weight|night pain|fever|calf|"
                r"pins and needles|give way|cauda|chest pain|breathless)\b")

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
    (Intent.REPEAT_ASK, _rx(r"\b(?:i said|say that again|repeat that|"
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
                               r"referral|what should i (?:bring|wear))\b"), None, None),
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
    (Intent.AVAIL_QUERY, _rx(r"\b(?:anything|any|something|what|what'?s|got)\b"),
     _rx(r"\b(?:free|available|availability|slot|slots|opening|appointment|times?)\b"),
     _NEGATED),
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


def classify_intent(text, prev_assistant="", *, screen_pending=False):
    """Every intent this utterance corroborates, most specific first. PURE.

    Returns [] for silence -- the pre-arbiter behaviour -- whenever nothing
    matches, the caller is merely answering, or a clinical screen is in play.

    ``prev_assistant`` is what Susie said last. ``screen_pending`` is the
    session's own view of whether a screen is armed and unanswered; both are
    checked because either alone has been wrong. A stored call shows why the
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
    _answer_probe = _LEADING_DISFLUENCY.sub("", utterance)
    if _BARE_ANSWER.match(_answer_probe) and len(_answer_probe.split()) <= 4:
        return []
    answering = bool(_CONFIRM_Q.search(prev_assistant or ""))
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
        hits.append(intent)
    return hits


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
    Intent.RESCHEDULE_REQ:   [f"Let's get that moved for you {EM_DASH}",
                              f"Yes, let's get that moved {EM_DASH}"],
    Intent.REPEAT_ASK:       [f"Sorry about that {EM_DASH}",
                              f"Apologies for that {EM_DASH}"],
    Intent.TRANSFER_REQ:     [f"Not a problem {EM_DASH}",
                              f"Yes, not a problem {EM_DASH}"],

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
    Intent.BOOK_NEW:         [f"Let's get you booked in {EM_DASH}",
                              f"Yes, let's get that sorted {EM_DASH}"],
}


def render_intent_head(intent, *, subject: str = "", index: int = 0) -> str:
    """One head for ``intent``, rotated by ``index``. PURE.

    The subject-free member of a pool is a FALLBACK, not a rotation partner.
    Rotating across the whole pool made the caller's own words disappear on the
    second head of a call: NAMED_DAY is
    ["Let me see what {subject} looks like -", "Let me see -"], so index 1
    answered "would you have Saturday" with a bare "Let me see -" even though
    Saturday was right there. Naming what they asked for is the entire point of
    a situational head, so the choice is made among the members that CAN carry
    a subject whenever one is available, and among the rest when none is.
    """
    pool = INTENT_HEADS.get(intent) or []
    if not pool:
        return ""
    with_subject = [h for h in pool if "{subject}" in h]
    without = [h for h in pool if "{subject}" not in h]
    usable = (with_subject if (subject and with_subject) else without) or pool
    head = usable[index % len(usable)]
    if "{subject}" in head:
        # Only reachable when the pool has nothing else -- keep the guard, since
        # "Let me see what  looks like" is the failure it exists to prevent.
        if not subject:
            return without[0] if without else ""
        head = head.replace("{subject}", subject)
    return head


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
    r"|(?:of course)|(?:got it)|(?:no problem(?: at all)?)|(?:not to worry)"
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
