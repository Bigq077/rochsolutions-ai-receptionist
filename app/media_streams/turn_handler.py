# app/media_streams/turn_handler.py
"""
Compatibility shim — all conversation logic has moved to flow.py.

The FlowEngine in flow.py is the single source of truth for what
Susie says next.  This file exists only to avoid breaking imports
in llm_stream.py which still uses sanitise_response().
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict

# Re-export FlowEngine and FLOW for any code that imports them from here
from .flow import FlowEngine, FLOW  # noqa: F401

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Gate 5b — sentence-level banned-phrase patterns
# Each pattern strips the matching sentence from the chunk; surrounding text
# is preserved.  Applied AFTER the chunk-drop check (Gate 5a).
# ---------------------------------------------------------------------------

_BANNED_SENTENCE_RE = [
    # ── Markdown artefacts (A1, 2026-07-29) ─────────────────────────────────
    # CAbad8422e read a booking readback aloud as markdown: "**Patient name:**
    # Jewel". The emphasis markers are pure formatting — they carry no meaning
    # and TTS has no idea what to do with them. Strip the MARKERS inline, never
    # the sentence: the readback content (name, phone, slot) is exactly what the
    # caller needs to hear. Runs first so downstream patterns see clean text.
    ("markdown_emphasis", re.compile(r"\*+")),

    # ── Internal identifier tokens (A1, 2026-07-29) ─────────────────────────
    # A snake_case token is machine vocabulary — a tool name, a field name, an
    # enum. It cannot occur in English speech, so its presence means the model
    # is narrating its own plumbing. CA76bc921f spoke the literal string
    # "slot_iso" to a caller, plus "book_appointment" and
    # "msk_initial_assessment".
    #
    # This is a CLASS rule, not another phrase: it catches every tool and field
    # name that exists now or is added later, without anyone remembering to
    # update this list. Strips the containing sentence — a sentence built around
    # an identifier has no salvageable content.
    ("internal_identifier_token",
     re.compile(r"[^.!?]*\b[a-z][a-z0-9]*_[a-z0-9_]+\b[^.!?]*[.!?]?", re.IGNORECASE)),

    # ── Banned opener words ──────────────────────────────────────────────────
    # Strip sycophantic/robotic openers from the very first token(s) of any
    # LLM chunk.  Anchored to ^ so it ONLY fires at the start of a chunk —
    # never mid-sentence.  Strips the word plus its connector (comma, dash,
    # exclamation) and any trailing space, leaving the substantive reply intact.
    # e.g. "Of course — which clinic?" → "Which clinic?"
    #      "Perfect, could I get your name?" → "Could I get your name?"
    #      "Absolutely, we offer acupuncture" → "We offer acupuncture"
    # Runs first so downstream patterns see clean text.
    ("banned_opener",
     re.compile(
         r"^(?:Absolutely|Certainly|Of course|Sure thing|Sure"
         r"|Wonderful|Fantastic|Exactly|Indeed|Definitely|Totally"
         r"|Obviously|Clearly|Lovely|Right so|Perfect|Great)"
         r"\s*[,!\-—–]\s*",
         re.IGNORECASE,
     )),
    ("bear_with_me",  re.compile(r"[^.!?]*\bbear with me\b[^.!?]*[.!?]?",        re.IGNORECASE)),
    ("bare_with_me",  re.compile(r"[^.!?]*\bbare with me\b[^.!?]*[.!?]?",        re.IGNORECASE)),
    ("just_a_moment", re.compile(r"[^.!?]*\bjust a moment\b[^.!?]*[.!?]?",       re.IGNORECASE)),
    ("one_moment",    re.compile(r"[^.!?]*\bone moment please\b[^.!?]*[.!?]?",   re.IGNORECASE)),
    ("are_you_there", re.compile(r"[^.!?]*\bare you still there\b[^.!?]*[.!?]?", re.IGNORECASE)),
    ("still_there",   re.compile(r"[^.!?]*\bstill there\b[^.!?]*[.!?]?",         re.IGNORECASE)),
    # Robotic generic sign-offs — strip as standalone sentences so they never
    # reach TTS.  A premium clinic receptionist ends an answer with the answer,
    # not a scripted closer.  Matches both "Is there anything else I can help
    # with?" and "Is there anything else I can help you with?" variants.
    ("is_there_anything_else",
     re.compile(r"[^.!?]*\bis there anything else (?:I can|you(?:'d like me to)?) help\b[^.!?]*[.!?]?",
                re.IGNORECASE)),
    # "Would you like to arrange an appointment?" — booking-push variant not
    # already covered by Gate 5c's _BOOKING_OFFER_RE.  Strip globally (the
    # prompt controls when and how a natural booking offer is made).
    ("would_you_like_to_arrange",
     re.compile(r"[^.!?]*\bwould you like to arrange an appointment\b[^.!?]*[.!?]?",
                re.IGNORECASE)),
    ("would_you_like_to_book_appt",
     re.compile(r"[^.!?]*\bwould you like to book an appointment\b[^.!?]*[.!?]?",
                re.IGNORECASE)),
    # "Lovely, [name]" acknowledgement — patronising opener, banned everywhere
    ("lovely_opener", re.compile(r"^[Ll]ovely[,\s!]+",                            re.IGNORECASE)),
    # Internal/meta orchestration text — must never reach caller TTS
    ("lookup_already_done",   re.compile(r"[^.!?]*\blookup (?:has )?already (?:been )?done\b[^.!?]*[.!?]?", re.IGNORECASE)),
    ("let_me_confirm_caller", re.compile(r"[^.!?]*\blet me confirm this with the caller\b[^.!?]*[.!?]?",    re.IGNORECASE)),
    ("lookup_already_ran",    re.compile(r"[^.!?]*\blookup(?:_appointment)? already ran\b[^.!?]*[.!?]?",    re.IGNORECASE)),
    # Internal reasoning spoken aloud on the cancel/reschedule path (GLOBAL FAIL 6),
    # observed Call 7 (2026-06-27): "I need to look up the patient details first
    # before confirming…". "look up the patient" / "look up the/your details" is
    # internal-only vocabulary — Susie addresses the caller, never "the patient" —
    # so stripping the whole sentence is safe. (Legit "look up your appointment"
    # is NOT matched.)
    ("lookup_reasoning_leak", re.compile(r"[^.!?]*\blook up (?:the |your )?(?:patient|details)\b[^.!?]*[.!?]?", re.IGNORECASE)),
    ("rc_stage_leak",         re.compile(r"[^.!?]*\brc_stage\b[^.!?]*[.!?]?",                               re.IGNORECASE)),
    # CALL STATE internal labels (BOOKING FLOW ACTIVE, CTA COUNT, etc.) spoken
    # aloud — Sonnet occasionally paraphrases the injected CALL STATE block into
    # speech (observed Call 4, 2026-06-17: "The booking flow is already active
    # for Redditch."). These tokens are pure internal vocabulary; Susie never
    # says them to a caller. Strip the offending sentence, leaving the rest
    # (e.g. the follow-up question) intact.
    ("internal_call_state_leak", re.compile(r"[^.!?]*\b(?:booking flow|call state|cta count)\b[^.!?]*[.!?]?", re.IGNORECASE)),
    # Tool/system mechanics narrated aloud — Susie must never reference "the
    # system", repeated lookups, or "the same availability" to a caller.
    # Observed Call 1 (2026-06-18, "next week" cascade): "I'm getting the same
    # availability — it looks like the system [is showing the same days]."
    # Strip the whole offending sentence; the substantive alternative that
    # follows (a real next-available day) is in a separate sentence/turn and is
    # preserved.  "the system" is pure internal vocabulary — Susie would never
    # say it to a patient — so matching it (in a diary/availability context) is
    # safe.
    ("system_availability_narration",
     re.compile(
         r"[^.!?]*\b(?:it looks like the system|the system (?:is|keeps|seems|appears)"
         r"|getting the same availability|the same availability(?: as before)?)\b[^.!?]*[.!?]?",
         re.IGNORECASE,
     )),
    # "let me try a different search / another search" — internal retry
    # narration (Call 1: "Let me try a different search for the following
    # week.").  The caller never needs to hear that a lookup is being retried.
    ("different_search_narration",
     re.compile(
         r"[^.!?]*\b(?:try (?:a|another) different search|a different search)\b[^.!?]*[.!?]?",
         re.IGNORECASE,
     )),

    # ── LLM internal reasoning narration ────────────────────────────────────
    # These patterns match full sentences containing internal chain-of-thought
    # that the LLM occasionally speaks aloud instead of acting silently.
    # Each strips the entire offending sentence, leaving surrounding text intact.
    #
    # "The caller said/is/was/has/mentioned..." — state narration
    ("reasoning_the_caller",
     re.compile(
         r"[^.!?]*\bThe caller (?:said|is|was|has|mentioned|seems|appears|told me|wants|would like|appears to)\b[^.!?]*[.!?]?",
         re.IGNORECASE,
     )),
    # "The results within/show/are..." — tool-output narration
    ("reasoning_the_results",
     re.compile(
         r"[^.!?]*\bThe results? (?:within|show|shows|are|that|from|of the)\b[^.!?]*[.!?]?",
         re.IGNORECASE,
     )),
    # "I'll pick/choose/select/present three/give them..." — selection narration
    ("reasoning_ill_select",
     re.compile(
         r"[^.!?]*\bI'?ll (?:pick|choose|select|present three|give them three|note that|flag)\b[^.!?]*[.!?]?",
         re.IGNORECASE,
     )),
    # "I should..." — intention narration
    ("reasoning_i_should",
     re.compile(
         r"[^.!?]*\bI should\b[^.!?]*[.!?]?",
         re.IGNORECASE,
     )),
    # "I'd be happy to..." — call-centre filler, banned in all output
    ("id_be_happy_to",
     re.compile(
         r"[^.!?]*\bI'?d be happy to\b[^.!?]*[.!?]?",
         re.IGNORECASE,
     )),
    # "let me pull/grab those up" variants — filler implying a fetch when the
    # data is already present; strip silently so the next substantive chunk
    # plays immediately.
    ("pull_those_up",
     re.compile(
         r"[^.!?]*\b(?:let me (?:just )?(?:pull those up|grab those)|pulling those up)\b[^.!?]*[.!?]?",
         re.IGNORECASE,
     )),
    # "(N slots)" annotation — raw slot-count parentheticals from tool result
    # narration (e.g. "Tuesday 12th (4 slots)"). Strip inline without removing
    # surrounding text so the day/time label survives.
    ("slot_count_annotation",
     re.compile(r"\s*\(\d+\s*slots?\)", re.IGNORECASE)),

    # ── Deflection openers ───────────────────────────────────────────────────
    # "That's one for the [role]", "that's one for the calendar",
    # "that's something to discuss with", "that's a question for".
    # These push the patient's question to someone else without engaging;
    # they are never appropriate as an opener.
    #
    # NARROW MATCH (fixes C4-2 / C5-T13 / C6-1):
    # The old pattern ended each opener with `[^—–.!?]*` which matches commas
    # and EVERY character up to the first dash / period — so an answer phrased
    # as "That's one for the practitioner to assess, but generally physio helps
    # with slip discs…" had its entire substantive clause eaten, leaving only
    # the booking push (caller got no real answer; had to re-ask).
    #
    # Fix: each opener now only matches when the deflection target word is
    # IMMEDIATELY followed by a clean separator — an em/en-dash (the intended
    # "deflect — then pivot" form), a sentence terminator (pure deflection with
    # nothing after), or end-of-chunk.  `_SEP` below is shared by all five.
    # If a comma or a continuing clause follows the target instead, the opener
    # is part of a substantive sentence and is left fully intact.
    #
    #   "That's one for the practitioner — Mark can help."  → "Mark can help."
    #   "That's one for the practitioner."                  → ""  (pure deflect)
    #   "That's one for the practitioner to assess, but…"   → unchanged ✓
    #   "That's one for the practitioner, but we can…"      → unchanged ✓
    ("one_for_practitioner",
     re.compile(
         r"^[Tt]hat'?s one for (?:the )?(?:practitioner|therapist|clinician)\b"
         r"(?:\s*[—–]\s*|\s*[.!?]\s*|\s*$)",
         re.IGNORECASE,
     )),
    ("one_for_calendar",
     re.compile(
         r"^[Tt]hat'?s one for (?:the )?calendar\b"
         r"(?:\s*[—–]\s*|\s*[.!?]\s*|\s*$)",
         re.IGNORECASE,
     )),
    # "that's one for mark" — named-person / general deflection fallback.
    # (?:the )? handles "that's one for the admin" as well as "for mark".
    # Runs after the specific-role entries so it only catches residual cases.
    ("one_for_name",
     re.compile(
         r"^[Tt]hat'?s one for (?:the )?[a-zA-Z]+\b"
         r"(?:\s*[—–]\s*|\s*[.!?]\s*|\s*$)",
         re.IGNORECASE,
     )),
    ("something_to_discuss",
     re.compile(
         r"^[Tt]hat'?s something to discuss with(?: (?:the |your )?[a-zA-Z]+)?\b"
         r"(?:\s*[—–]\s*|\s*[.!?]\s*|\s*$)",
         re.IGNORECASE,
     )),
    ("a_question_for",
     re.compile(
         r"^[Tt]hat'?s a question for(?: (?:the |your )?[a-zA-Z]+)?\b"
         r"(?:\s*[—–]\s*|\s*[.!?]\s*|\s*$)",
         re.IGNORECASE,
     )),

    # ── Prompt C — scarcity-signalling openers ───────────────────────────────
    # "The closest/nearest X is/would be/— " — strip the preamble, keep the
    # date/time.  Anchored to start-of-chunk; strips up to and including the
    # first recognisable connector so the slot info plays directly.
    # NOTE: "soonest"/"earliest" were DELIBERATELY removed here (2026-06-15,
    # owner decision) — "The earliest I have is …" is now the WELCOME warm
    # lead-in for ASAP slot requests (SLOT_FORMATTER lead_in="earliest"), not
    # scarcity.  Only the robotic "closest"/"nearest" openers are still stripped.
    ("closest_nearest_opener",
     re.compile(
         r"^[Tt]he (?:closest|nearest)\b[^.!?—–]*?"
         r"(?:\bis\s+|\bwould be\s+|[—–]\s*)",
         re.IGNORECASE,
     )),
    # "The closest/nearest I've got / I have / we have / available" —
    # full-sentence strip for forms where the date cannot be rescued by the
    # opener-strip above.  Runs after the opener-strip so cases already rescued
    # by that pattern are never double-processed.
    # ("soonest"/"earliest" intentionally excluded — see note above.)
    ("closest_ive_got",
     re.compile(
         r"[^.!?]*\b[Tt]he (?:closest|nearest)\s+"
         r"(?:I'?ve\s+got|I\s+have|we\s+have|available)\b"
         r"[^.!?]*[.!?]?",
         re.IGNORECASE,
     )),

    # "That is the only" / "That's the only" — full-sentence strip.
    # Scarcity framing with no recoverable date info; strip the whole sentence.
    ("that_is_the_only",
     re.compile(
         r"[^.!?]*\b[Tt]hat(?:'s| is) the only\b[^.!?]*[.!?]?",
         re.IGNORECASE,
     )),

    # "The only [slot/day/time/...] [modifier] is ..." — strip just the
    # scarcity opener, keeping the date/time info that follows.
    # Mirrors closest_nearest_opener: recovers actionable slot info instead
    # of wiping the entire sentence.
    #
    # Before (full-sentence strip — BUG): "The only day available is Wednesday
    # the 17th — does that work?" → stripped to "" → fallback fires.
    # After  (prefix strip — FIXED):     "The only day available is " →
    # stripped, leaving "Wednesday the 17th — does that work?" → plays fine.
    #
    # Handles: "The only slot available is", "The only day we have is",
    # "The only available appointment is", "The only time I've got is", etc.
    ("the_only_slot_scarcity",
     re.compile(
         r"\b[Tt]he only\s+"
         r"(?:available\s+)?"
         r"(?:slot|day|time|option|appointment|[a-zA-Z]+day)"
         r"(?:\s+(?:available|that\s+(?:is\s+)?available"
         r"|we\s+have(?:\s+available)?|I(?:'ve)?\s+(?:got|have)(?:\s+available)?))?"
         r"\s+(?:is|are|would be)\s*",
         re.IGNORECASE,
     )),
]

# ---------------------------------------------------------------------------
# Gate 5g — self-narration strip (A1, 2026-07-29)
# ---------------------------------------------------------------------------
# Gate 5b is ~40 patterns, each added after one observed call. It enumerates
# past leaks; it does not detect the class. Replaying the five A1 calls through
# the real chunker showed Gate 5 firing ONCE across all five — every new
# phrasing the model invents walks straight through.
#
# The class those leaks share is not a vocabulary. It is that the model is
# talking to ITSELF — deliberating, classifying the caller's last answer,
# correcting its own course — rather than to the caller. So the test is
# structural, applied per sentence:
#
#     matches a deliberation pattern
#       AND contains no second-person reference   (not addressed to the caller)
#       AND is not a question                     (not asking the caller anything)
#
# Both guards are load-bearing, and this is why the rule could not live in the
# flat _BANNED_SENTENCE_RE list. These two lines share the phrase "I have
# everything I need":
#
#   "I have everything I need to get that booked — shall I go ahead?"  KEEP
#   "I need to book this in now — I have everything I need."           DROP
#
# A flat pattern strips both, and stripping the first hands the caller a dead
# end mid-booking. That failure has already cost this system a completed
# booking once (see the Gate 5c note below, 2026-06-12).
_SELF_NARRATION_RE = re.compile(
    r"\b(?:"
    # internal readiness / intent, stated about itself
    r"I have everything I need"
    r"|I need to book (?:this|it) in now"
    r"|let me (?:review what I know|get back on track|start again)"
    # self-correction mid-turn — CAfe6a4162 ran the wrong clinical screen and
    # narrated the recovery instead of just recovering
    r"|that'?s the wrong (?:screen|question|one|flow)"
    r"|back on track"
    r"|scratch that"
    # classifying the caller's answer rather than responding to it —
    # CA198906b4: "That's a soft affirmative to the booking offer — good."
    r"|(?:soft|implicit|explicit)\s+affirmative"
    r"|affirmative"
    # referring to the conversation as data it is reading rather than a
    # conversation it is having — CA76bc921f: "…explicitly stated in this
    # conversation fragment."
    r"|conversation (?:fragment|context|history)"
    # stating a precondition it must satisfy before acting — CA198906b4:
    # "Now I need a timing preference before checking availability."
    r"|I need (?:a|an|the)\s+[a-z ]{0,30}?before\s+(?:check|book|confirm|look)\w*"
    r")\b",
    re.IGNORECASE,
)

# Any second-person reference means the sentence is addressed to the caller.
_SECOND_PERSON_RE = re.compile(r"\b(?:you|your|yours|you'?re|you'?ve|you'?d)\b",
                               re.IGNORECASE)

# Split after sentence terminators. Handles the no-space-after-period form the
# model produces when it runs reasoning and speech together ("...for you now.I
# need to book this in now...").
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s*")


def _is_self_narration(sentence: str) -> bool:
    """True if `sentence` is the model talking to itself, not to the caller."""
    if not sentence.strip():
        return False
    if "?" in sentence:
        return False
    if _SECOND_PERSON_RE.search(sentence):
        return False
    return _SELF_NARRATION_RE.search(sentence) is not None


def _strip_self_narration(text: str) -> str:
    """Drop self-narrating sentences, keep everything addressed to the caller.

    Returns `text` UNCHANGED when nothing is dropped. That guard matters: the
    split/rejoin normalises whitespace as a side effect (the model often runs
    sentences together as "...for you now.So that's Tom..."), and an audit over
    all 656 recorded assistant turns showed that touching every multi-sentence
    turn is a far wider blast radius than this fix is entitled to. Rewrite only
    the turns we actually strip something from.
    """
    parts = _SENTENCE_SPLIT_RE.split(text)
    kept = [p for p in parts if not _is_self_narration(p)]
    if len(kept) == len(parts):
        return text
    return " ".join(s for s in (p.strip() for p in kept) if s)


_MULTI_SPACE_RE  = re.compile(r" {2,}")
# Also strips leading ': ' colon artefacts left when a reasoning sentence that
# ended with a colon is removed and the continuation chunk starts with ": ".
_LEADING_JUNK_RE = re.compile(r"^[\s:,—–\-]+")

# ---------------------------------------------------------------------------
# Gate 5c — redundant booking-offer strip (CONDITIONAL on booking_flow_active)
# ---------------------------------------------------------------------------
# Once the caller has agreed to book (booking_flow_active set), the answer to
# "would you like to book?" is already yes.  If the LLM appends a booking
# offer anyway — e.g. after answering a treatment question mid-flow ("Sports
# massage is within Mark's toolkit … Would you like to book one?") — it is
# redundant and pushy.  This strips the offer sentence; the booking flow
# continues via its own question injection / watchdog.
#
# This pattern is applied ONLY when session["booking_flow_active"] is True, so
# pre-booking FAQ turns (where the CTA is the desired close) are untouched.
# It matches booking-OFFER phrasings only — never the legitimate booking-flow
# questions ("Is there a day or time…?", "Which clinic…?"), which contain none
# of these tokens.
_BOOKING_OFFER_RE = re.compile(
    r"[^.!?]*\b(?:"
    r"would you like (?:me )?to book"
    r"|would you like to go ahead"
    r"|shall i (?:go ahead and )?book"
    r"|shall i find you a slot"
    r"|shall i check availability"
    r"|do you want to book"
    r"|want me to book"
    r"|like to book (?:an|a|one)\b"
    r")\b[^.!?]*[.!?]?",
    re.IGNORECASE,
)


# Gate 5e — diagnostic assertions about the CALLER'S OWN case. Standard-tier
# template clinics are non-diagnostic; if the model asserts what the caller
# HAS, strip that sentence. Deliberately conservative: matches second-person
# case assertions ("you've probably got a disc bulge", "it sounds like you
# have sciatica") — never general education ("sciatica is very common") and
# never the screening questions themselves ("do you have any numbness…?",
# which is a question, not an assertion; the trailing [.!] excludes '?').
_DIAGNOSIS_LEAK_RE = re.compile(
    r"[^.!?]*\b(?:"
    r"you (?:probably|likely|definitely|almost certainly) have"
    r"|you(?:'ve| have) (?:probably |likely |definitely )?got a"
    r"|it sounds like you(?:'ve| have)"
    r"|sounds like you(?:'ve| have) got"
    r"|that(?:'s| is) (?:probably|likely|definitely) (?:a|an|your)"
    r"|you must have (?:a|an|torn|pulled|slipped)"
    r"|my diagnosis"
    r"|i(?:'d| would) diagnose"
    r")\b[^.!?]*[.!]?",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Gate 5a — chunk-level reasoning drop patterns
# If ANY of these match, the ENTIRE chunk is discarded and never reaches TTS.
# Applied BEFORE sentence-level stripping.  Matches internal reasoning output
# that should never reach the spoken layer.
# ---------------------------------------------------------------------------

# ✓ ✗ check-mark / cross symbols — hallmark of reasoning tables
_REASONING_TICK_CROSS_RE = re.compile(r"[✓✗]")

# HH:MM timestamp — >1 in a single chunk indicates a reasoning timestamp table
_REASONING_HH_MM_RE = re.compile(r"\d{2}:\d{2}")

# Sentences that open with explicit reasoning narration openers.
# Anchored to start-of-string OR after sentence-ending punctuation.
#
# NOTE: "I should" was DELIBERATELY removed here (2026-06-18, Call 5 over-drop).
# As a chunk-level trigger it discarded the ENTIRE chunk, so a conversational
# lead-in like "I should mention — at Alcester we have Thursday slots. Number 1,
# Thursday 2nd July…" lost the real slots alongside the preface, leaving the
# caller with a spurious "Sorry, I didn't quite catch that". "I should" still
# lives in Gate 5b (reasoning_i_should, sentence-level strip) — the identical
# token — so genuine reasoning ("I should pick the morning slots") is still
# removed, but surgically (its sentence only), preserving adjacent slot text.
_REASONING_OPENER_RE = re.compile(
    r"(?:^|(?<=[.!?])\s*)"
    r"(?:Filtering|Checking|Skipping|The rule says|I'?ll need to|With only|"
    r"Let me work out|Looking at the|Calculating|So I need to)\b",
    re.IGNORECASE,
)

# Internal label / flag words that only appear in reasoning, never in speech
_REASONING_LABEL_RE = re.compile(
    r"\b(?:single-slot|lead-time|late afternoon only|working out|decision)\b",
    re.IGNORECASE,
)

# High-density time references: >3 per chunk = a reasoning table, not speech.
# Matches patterns like "5pm", "17:00", "five in the afternoon" etc.
_TIME_DENSITY_RE = re.compile(
    r"\d+(?::\d+)?\s*(?:am|pm|in the morning|in the afternoon|in the evening)",
    re.IGNORECASE,
)


def _get_reasoning_drop_reason(text: str) -> str:
    """
    Returns a non-empty description string if `text` should be dropped
    entirely as internal reasoning output.  Returns "" if the chunk is safe.

    Called per-chunk in Gate 5a, before any sentence-level stripping.
    """
    if _REASONING_TICK_CROSS_RE.search(text):
        return "tick_cross_symbol"
    if len(_REASONING_HH_MM_RE.findall(text)) > 1:
        return "multiple_hhmm_timestamps"
    if _REASONING_OPENER_RE.search(text):
        return "reasoning_sentence_opener"
    if _REASONING_LABEL_RE.search(text):
        return "internal_label_word"
    if len(_TIME_DENSITY_RE.findall(text)) > 3:
        return "high_time_density"
    return ""


# ---------------------------------------------------------------------------
# Gate 5f — false-confirmation guard (P1 #5 / F-023)
# ---------------------------------------------------------------------------
# CALL 5: book_appointment was REJECTED (confirmation_required, no calendar
# event) yet the model narrated "All booked" — a phantom appointment the clinic
# has no record of. This detector fires ONLY on a CLAIM that a booking is
# complete, so it can be acted on while no booking has actually succeeded this
# call (session["booking_write_confirmed"] unset).
#
# It must never fire on an offer, a question, a negation, or an in-progress
# statement — the over-fire failure mode stripped a real confirmation and
# abandoned a completed booking once already (Gate 5c, 2026-06-12). Measured
# against both classes in tests/regression/test_false_confirmation_guard.py:
# 18/18 phantom claims caught, 0 false positives across 27 legitimate lines.
_FALSE_CONFIRM_CLAIM_RE = re.compile(
    r"""\b(
        (youre|you\s+are|thats|that\s+is|its|it\s+is)\s+(all\s+|now\s+)?(booked|confirmed)(\s+in)?
      | youre\s+booked\s+in
      | all\s+booked\b(?!\s+up)
      | (ive|i\s+have|weve|we\s+have)\s+(now\s+)?booked\s+(you|that|it|your)
      | (ive|i\s+have)\s+got\s+you\s+booked
      | (got|put)\s+you\s+(booked\s+)?(in|down)\s+for
      | your\s+(appointment|booking)\s+is\s+(booked|confirmed|all\s+set|sorted)
    )""",
    re.X,
)
# Any of these means the sentence is NOT a completion claim (offer / question /
# negation / intent), so the guard stands down even if a claim token is present.
_FALSE_CONFIRM_NEG_RE = re.compile(
    r"\b(not|havent|cannot|cant|wont|once|after|before|shall\s+i|would\s+you"
    r"|can\s+i|do\s+you\s+want|want\s+me\s+to|like\s+me\s+to|going\s+to|ill"
    r"|i\s+will|let\s+me|need\s+to|to\s+book)\b"
)

# What the caller hears instead of a phantom "all booked": the same recovery
# CALL 12 did correctly. A question, and free of any claim token, so it can
# never re-trigger the guard.
_FALSE_CONFIRM_RESTEER = (
    "Sorry — before I confirm anything, shall I go ahead and book that in for you?"
)


def _false_confirmation_claim(text: str) -> bool:
    """True if `text` CLAIMS a booking is complete (not offers/asks/intends).

    Normalisation mirrors the screening layer: lower-case and DELETE apostrophes
    ("you're" -> "youre") so contractions match, then blank other punctuation.
    A question (`?`) or any negation/offer/intent cue stands the guard down.
    """
    if not text or "?" in text:
        return False
    norm = text.lower().replace("'", "").replace("’", "")
    norm = " " + re.sub(r"[^a-z0-9 ]+", " ", norm).strip() + " "
    if _FALSE_CONFIRM_NEG_RE.search(norm):
        return False
    return _FALSE_CONFIRM_CLAIM_RE.search(norm) is not None


# ---------------------------------------------------------------------------
# sanitise_response — public API, called per-chunk from llm_stream.py
# ---------------------------------------------------------------------------

_GATE5_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
_GATE5_DAY_MONTH_RE = re.compile(
    r"the\s+(\d{1,2})(?:st|nd|rd|th)\s+of\s+([A-Za-z]+)", re.IGNORECASE
)


def _confirmed_slot_is_stale(conf_slot: str, session: Dict[str, Any]) -> bool:
    """True when v3_confirmed_slot_phrase names a day the caller is no longer
    being offered — i.e. they have changed day since it was captured.

    Judged against the day last OFFERED, never against the spoken transcript —
    this gate rewrites that transcript, so using it would let one rewrite make the
    spoken date agree with the stale phrase and the check would defeat itself.

    PRIMARY SIGNAL: v3_last_offered_day_iso. It is set whenever slots are
    presented, deliberately preserved across slot-map clears, and dropped only on
    a successful booking — so it survives a turn that clears last_offered_slots.

    CAb81fe651/CA42486ff4/CAec93b032 and then CA6dce36c8 (31 Jul). The first
    version of this check used last_offered_slots alone, and the logs show
    precisely what that cost:

        01:56:33  NOT corrected — stood down, Wednesday list spoken correctly
        01:56:46  [ms_llm] slot cache cleared: day iso='2026-08-05'
        01:57:20  corrected to confirmed slot: 'Tuesday the 4th of August'

    Clearing the slot cache nulls last_offered_slots, this returned "not stale" by
    design, and the gate went straight back to forcing the abandoned day. The
    fail-safe direction re-armed the bug mid-call.

    Returns False — "not stale", keep the existing behaviour — whenever it cannot
    be sure: no offered day recorded at all, an unparseable phrase, or slots in a
    shape it does not recognise. Standing down wrongly re-opens the 2026-07-07
    drift defect, so genuine uncertainty must keep the correction.
    """
    m = _GATE5_DAY_MONTH_RE.search(conf_slot or "")
    if not m:
        return False
    month = _GATE5_MONTHS.get(m.group(2).lower())
    if not month:
        return False
    day = int(m.group(1))

    # Primary: the day last offered, which outlives the slot cache.
    day_iso = str(session.get("v3_last_offered_day_iso") or "")
    if len(day_iso) >= 10:
        try:
            if (int(day_iso[5:7]), int(day_iso[8:10])) != (month, day):
                return True
            return False        # confirmed phrase IS the day on offer
        except ValueError:
            pass

    # Fallback: the offered batch itself, for any path that never set the day.
    offered = session.get("last_offered_slots")
    if not offered or not isinstance(offered, (list, tuple)):
        return False

    seen_any = False
    for slot in offered:
        iso = ""
        if isinstance(slot, dict):
            iso = str(slot.get("start") or slot.get("iso") or slot.get("date") or "")
        elif isinstance(slot, str):
            iso = slot
        if len(iso) < 10:
            continue
        try:
            _y, _mo, _d = int(iso[0:4]), int(iso[5:7]), int(iso[8:10])
        except ValueError:
            continue
        seen_any = True
        if (_mo, _d) == (month, day):
            return False          # still on the table — the phrase is current
    # Only call it stale when we actually read some dates and none matched.
    return seen_any


def sanitise_response(text: str, session: Dict[str, Any]) -> str:
    """
    Clean LLM output before it reaches tts_text_queue.

    Gate 5a — chunk-level reasoning drop:
      Discards the ENTIRE chunk when internal-reasoning patterns are detected.
      Logs the drop and increments session["_gate5_reasoning_drops"].
      The pipeline continues normally — only the individual chunk is gone.
      Slot map, DTMF standby, and watchdog state are unaffected.

    Gate 5b — sentence-level stripping:
      Strips individual banned phrases / sentences while leaving surrounding
      text intact (existing behaviour, unchanged).

    Called per-chunk so the pipeline stays streaming.
    """
    if not text or not text.strip():
        return text

    # ── Gate 5a: whole-chunk reasoning drop ──────────────────────────────────
    _drop_reason = _get_reasoning_drop_reason(text)
    # Slot-presentation exemption: the post-check_availability slot list is
    # legitimately dense with spoken times ("five in the evening, six 05 in the
    # evening, seven 10 in the evening, eight 15 in the evening"). On a day with
    # 4+ slots that trips high_time_density (>3 time phrases) and the WHOLE slot
    # chunk is dropped as "reasoning" → no speech → the caller hears "Sorry,
    # could you say that again?" and has to repeat (JV neuro call 2026-07-07
    # 13:20). During the slot pass, ignore high_time_density ONLY — every other
    # reasoning signal (24h HH:MM timestamps, tick/cross, internal openers and
    # labels) still drops normally.
    if _drop_reason == "high_time_density" and session.get("_slot_buf_active"):
        _drop_reason = ""
    if _drop_reason:
        _preview = (text[:50] + "...") if len(text) > 50 else text
        logger.info(
            "[ms_gate5] dropped reasoning chunk (%s): %r",
            _drop_reason,
            _preview,
        )
        session["_gate5_reasoning_drops"] = (
            int(session.get("_gate5_reasoning_drops") or 0) + 1
        )
        return ""

    # ── Gate 5b: sentence-level stripping ────────────────────────────────────
    result = text

    for desc, pattern in _BANNED_SENTENCE_RE:
        cleaned = pattern.sub("", result)
        if cleaned != result:
            logger.info("[ms_gate5] removed banned phrase (%s)", desc)
            result = cleaned

    # ── Gate 5g: self-narration strip ────────────────────────────────────────
    # Runs here, adjacent to 5b, because it is the same kind of operation —
    # sentence-level, never chunk-level. Sentence-level is deliberate: the
    # chunk-level equivalent already caused an over-drop once, when an "I
    # should" trigger discarded a whole chunk and took real slot text with it
    # (2026-06-18, see the note on _REASONING_OPENER_RE).
    _narr_cleaned = _strip_self_narration(result)
    if _narr_cleaned != result:
        logger.info(
            "[ms_gate5g] removed self-narration: %r -> %r",
            result[:80], _narr_cleaned[:80],
        )
        result = _narr_cleaned

    # ── Gate 5c: redundant booking-offer strip during active booking ─────────
    # Only fires when the caller is already booking — never on pre-booking FAQ
    # turns where the CTA is the desired close.
    if session.get("booking_flow_active"):
        _offer_cleaned = _BOOKING_OFFER_RE.sub("", result)
        if _offer_cleaned != result:
            # Gate 5c removes a REDUNDANT trailing CTA tacked onto an FAQ answer
            # mid-booking (e.g. "...eighty parking spaces. Would you like to
            # book?").  It must NOT eat the legitimate closing confirmation
            # ("shall I go ahead and book that in?").  _BOOKING_OFFER_RE spans
            # [^.!?]* on both sides, so when that confirmation is the whole
            # response it matches end-to-end and strips to empty — the caller
            # then hears the deaf-sounding "Sorry, I didn't quite catch that"
            # fallback, killing a completed booking (Test 1, 2026-06-12: caller
            # entered phone via DTMF, confirmation stripped → abandoned).
            # Only strip when substantive content remains AND that remainder
            # still carries a question. If removing the offer would leave a
            # statement with no question, the caller is handed a dead-end they
            # can't act on — so KEEP the offer; forward motion beats mild
            # redundancy. (2026-07-04: a condition-mention turn mid-booking —
            # "…Marcus will assess and set a plan. Would you like to get booked
            # in?" — had its only question stripped, leaving 7s of silence and
            # the caller hung up.) The empty-remainder case (the closing
            # confirmation IS the whole response) is also caught here.
            if _offer_cleaned.strip() and "?" in _offer_cleaned:
                logger.info(
                    "[ms_gate5] removed redundant booking offer "
                    "(booking_flow_active)"
                )
                result = _offer_cleaned
            else:
                logger.info(
                    "[ms_gate5] booking offer KEPT — stripping would leave no "
                    "question (avoid dead-end)"
                )

    # ── Gate 5d: repeated FAQ booking-CTA strip (P4) ─────────────────────────
    # Pre-booking, the model tacks a booking CTA onto nearly every FAQ answer
    # despite the prompt's "offer once" rule (observed Call 4, 2026-06-27: CTA on
    # 4 consecutive service answers). v3_cta_count is incremented per turn
    # (connection.py:8087); once a CTA has already been offered this call (>=1),
    # strip any further CTA tail. The FIRST offer is preserved (count 0). elif so
    # it never runs during booking_flow (5c owns that path). Only strip when
    # substantive content remains, so a turn that is itself the booking offer is
    # never nuked (same empty-guard as 5c).
    elif int(session.get("v3_cta_count") or 0) >= 1:
        _offer_cleaned = _BOOKING_OFFER_RE.sub("", result)
        if _offer_cleaned != result and _offer_cleaned.strip():
            logger.info(
                "[ms_gate5] removed repeated FAQ booking offer (v3_cta_count=%s)",
                session.get("v3_cta_count"),
            )
            result = _offer_cleaned

    # ── Booking-readback DATE enforcement ────────────────────────────────────
    # The final confirmation ("So that's <name>, <slot> — shall I go ahead and
    # book that in?") is model free-text and occasionally drifts the DATE away
    # from the slot the caller actually confirmed at the name-request readback
    # (Call 2026-07-07: confirmed "Wednesday the 15th" but the booking readback
    # spoke "the 16th"). Once the phone is confirmed we are in the booking-
    # readback phase; if a chunk carries a "<weekday> the <ordinal> of <month>"
    # date, force it to the confirmed slot's date. The booking itself is already
    # protected by _resolve_slot_iso (a hallucinated slot is rejected and forced
    # back to a real offered slot) — this only keeps the SPOKEN date consistent
    # with what was agreed. Runs per-chunk; the readback date sits in one chunk.
    #
    # ...UNLESS the caller has since moved to a different day (2026-07-31).
    # v3_confirmed_slot_phrase is captured ONCE, at the name request. A caller who
    # changes day afterwards never refreshes it, so this enforcement spent the
    # rest of the call forcing the ABANDONED day over the correct one. Three
    # callers (CAb81fe651, CA42486ff4, CAec93b032) chose Tuesday, moved to
    # Wednesday, and heard "Tuesday the 4th of August at quarter past six" —
    # Wednesday's time on Tuesday's date, an appointment on no calendar. All
    # three hung up. The model had generated the correct sentence every time.
    #
    # Staleness is judged against last_offered_slots — the batch the tool most
    # recently returned. Deliberately NOT against last_spoken_slot_date, which is
    # derived from the SPOKEN text: this gate rewrites that text, so after one
    # rewrite the spoken date would agree with the stale phrase and the check
    # would defeat itself. The offered slots come from check_availability, not
    # from anything this gate can touch.
    #
    # Correcting a drifted date is this gate's job; overriding a decision is not.
    # When the confirmed phrase names a day the caller is demonstrably no longer
    # being offered, it cannot know which is right — and rewriting a correct day
    # into a wrong one is strictly worse than leaving a typo alone. It stands down.
    _READBACK_DATE_RE = r"[A-Za-z]+day\s+the\s+\d{1,2}(?:st|nd|rd|th)\s+of\s+[A-Za-z]+"
    _conf_slot = session.get("v3_confirmed_slot_phrase") or ""
    if _conf_slot and session.get("phone_confirmed"):
        _dm = re.search(_READBACK_DATE_RE, _conf_slot)
        if _dm and _confirmed_slot_is_stale(_conf_slot, session):
            logger.warning(
                "[ms_gate5] booking readback date NOT corrected — "
                "v3_confirmed_slot_phrase %r names a day the caller is no longer "
                "being offered; leaving the model's date alone",
                _conf_slot,
            )
            _dm = None
        if _dm:
            _canon_date = _dm.group(0)
            _date_corrected = re.sub(
                _READBACK_DATE_RE, lambda _m: _canon_date, result
            )
            if _date_corrected != result:
                logger.info(
                    "[ms_gate5] booking readback date corrected to confirmed "
                    "slot: %r",
                    _canon_date,
                )
                result = _date_corrected

    # ── Gate 5e: diagnosis-leak strip (standard clinical tier only) ─────────
    # Template clinics on clinical_depth='standard' are clinically fluent but
    # NON-diagnostic: the model must never assert what the caller HAS. If a
    # diagnostic sentence leaks past the prompt rules, strip that sentence
    # (conservative patterns — assertions about the caller's own case only,
    # never general education like "sciatica is common"). Deep tier ('deep',
    # post sign-off) is exempt — naming the likely cause is its whole point.
    # The depth lookup is resolved once per session and cached on the session
    # dict so this per-chunk hot path never re-reads clinic config.
    _depth_cached = session.get("_clinical_depth_cache")
    if _depth_cached is None:
        _depth_cached = ""
        try:
            from app.clinic_config import get_clinic as _g5e_get_clinic
            from app.prompts.clinic_template_prompt import (
                _clinical_depth as _g5e_depth,
            )
            _g5e_clinic = _g5e_get_clinic(session.get("clinic_id"))
            if _g5e_clinic.get("prompt_engine") == "template_v1":
                _depth_cached = _g5e_depth(_g5e_clinic)
        except Exception:
            _depth_cached = ""
        session["_clinical_depth_cache"] = _depth_cached
    if _depth_cached == "standard":
        _diag_cleaned = _DIAGNOSIS_LEAK_RE.sub("", result)
        if _diag_cleaned != result and _diag_cleaned.strip():
            logger.info("[ms_gate5] removed diagnostic assertion (standard tier)")
            result = _diag_cleaned

    # ── Gate 5f: false-confirmation guard (P1 #5 / F-023) ────────────────────
    # While the caller is booking but NO booking has actually succeeded this call
    # (booking_write_confirmed is set only by _note_book_write_result on a
    # success:True from book_appointment), a chunk that CLAIMS the booking is
    # done is a phantom. Runs AFTER Gate 5c so the re-steer question — which
    # contains "shall I book" — is not itself stripped as a redundant offer.
    # First claim this turn is re-steered to the confirmation question; any
    # further claim in the same turn is dropped so the caller never hears it.
    if (
        session.get("booking_flow_active")
        and not session.get("booking_write_confirmed")
        and _false_confirmation_claim(result)
    ):
        session["_false_confirm_guard_fired"] = (
            int(session.get("_false_confirm_guard_fired") or 0) + 1
        )
        if not session.get("_false_confirm_resteered"):
            session["_false_confirm_resteered"] = True
            # This return value IS what the caller hears; llm_stream records it
            # via _record_spoken and derives last_bot_prompt and the model's own
            # history from it. Nothing needs stashing on the session — that was
            # the earlier patch, and it only papered over the second stateful
            # invocation of this gate at turn end.
            logger.error(
                "[ms_gate5f] false booking confirmation with no successful "
                "book_appointment — re-steering to the confirmation question: %r",
                result[:80],
            )
            return _FALSE_CONFIRM_RESTEER
        logger.error(
            "[ms_gate5f] additional false-confirmation chunk dropped: %r",
            result[:80],
        )
        return ""

    result = result.replace("\n", " ")
    result = _MULTI_SPACE_RE.sub(" ", result)
    result = _LEADING_JUNK_RE.sub("", result)
    result = result.strip()
    # ── Fix A: re-capitalise after opener strip ──────────────────────────────
    # Banned opener patterns (lovely_opener) strip the leading phrase and
    # leave the rest of the sentence starting with a lower-case continuation
    # word.  Restore sentence-initial capitalisation.
    if result:
        result = result[0].upper() + result[1:]
    return result
