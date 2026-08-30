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

# ── The reason question — never asked, in any phrasing ──────────────────────
# Owner decision, 2026-08-07, restated 2026-08-08: Susie must NEVER ask what
# brings the caller in. The reason is recorded only when the caller volunteers
# it, unprompted, in their own words — `first_turn_extractor._extract_reason`
# does that deterministically. An empty reason is a correct outcome, not a gap.
#
# The prompt has said so since susie_system_prompt.py:901 ("REASON IS OPTIONAL —
# do NOT ask the caller what their injury or condition is"). The model asks
# anyway, and asks it at the worst possible point: between the slot and the
# phone step. On CA041352eb (2026-08-08 00:01:04) the whole turn was
#
#     "Before I go ahead and check that day, could I ask what brings you in?"
#     "what's the appointment for?"
#
# Two earlier fixes were drafted and withdrawn because they RE-ORDERED the
# question instead of removing it. Suppression is the fix. See O-5.
#
# Deliberately NOT in the flat list below: stripping this can empty the turn,
# and an empty turn falls through to the deferred Gate-5 fallback, which speaks
# "Sorry, I didn't quite catch that — could you say that again?" — handing the
# caller a non-sequitur for a question they never should have been asked. The
# dead-end substitution at the call site is what makes the strip safe.
#
# Each alternative is a WHOLE sentence match ([^.!?]* both sides) so a reason
# question folded into a longer reply takes only its own sentence with it.
_REASON_QUESTION_RE = re.compile(
    r"[^.!?]*\b(?:"
    r"what(?:'s|\s+is)?\s+(?:brings|bringing)\s+you\s+in"          # what brings you in
    r"|what\s+brings\s+you\s+(?:to|in\s+to)\s+us"
    r"|what(?:'s|\s+is)\s+the\s+appointment\s+for"                 # what's the appointment for
    r"|what(?:'s|\s+is)\s+going\s+on\s+with\s+(?:it|that)"         # what's going on with it
    r"|what(?:'s|\s+is)\s+(?:been\s+)?troubling\s+you"
    r"|what(?:'s|\s+is)\s+the\s+(?:issue|problem|trouble)"
    r"|which\s+(?:area|body\s+part)\s+(?:is\s+)?(?:it|bothering)"
    r"|what\s+(?:are\s+you|do\s+you\s+want\s+to)\s+(?:coming\s+in|be\s+seen)\s+for"
    r")\b[^.!?]*[.!?]?",
    re.IGNORECASE,
)


# The admin framing a reason question hangs off. When the question itself is
# stripped, a preamble like "I'll note the reason on the booking." is left
# introducing something that no longer exists. Deliberately narrow — it
# recognises the admin framing and nothing else. Ends in [.!], never [?], so it
# can never eat a question. Only ever applied INSIDE the branch where a reason
# question was actually removed: a standalone "I've noted the reason on the
# booking" is a statement of fact and must survive.
_REASON_PREAMBLE_RE = re.compile(
    r"[^.!?]*(?:"
    r"\breason\b[^.!?]{0,40}?\b(?:on|for|to)\s+"
    r"(?:the\s+|your\s+|our\s+|this\s+)?"
    r"(?:booking|appointment|file|records?|notes?|system)\b"
    r"|\bfor\s+(?:our|the|your)\s+(?:records?|file|notes?)\b"
    r"|\bso\s+(?:we|i)\s+(?:have|['‘’]ve\s+got|can\s+note)\s+"
    r"(?:it|that|something)\s+(?:on|for)\s+(?:the\s+)?"
    r"(?:booking|appointment|file|records?|notes?)\b"
    r")[^.!?]*[.!]",
    re.IGNORECASE,
)


# The same defect one layer out, and the reason BOTH patterns exist.
#
# _REASON_PREAMBLE_RE above catches admin framing and nothing else, which is
# the right shape for what it catches — and it does not catch a purpose clause
# with no admin noun in it ("Just so Mark has a heads up."). Left behind, the
# caller hears a dangling fragment and then silence, because the turn no longer
# asks anything.
#
# So this is not a third list of phrasings. The call site asks the general
# question instead — does the turn still ASK anything? — and this pattern only
# decides whether the leftover is worth keeping. Anything it does not match
# keeps its text and merely gains a question, which is the safe direction.
#
# Matching the opening connector ALONE is not safe: every slot readback in this
# system opens "So that's Wednesday the 19th of August at ten...", and a
# connector-only rule deleted it. The second half is what makes this a purpose
# clause rather than a sentence that merely starts like one — it has to be
# ABOUT someone being told or made ready.
_REASON_RESIDUE_FRAGMENT_RE = re.compile(
    r"^(?:just\s+so|so\s+that|so|that\s+way|and\s+that\s+way"
    r"|in\s+order\s+(?:to|that)|and|because|then)\b"
    r"[^.!?]*\b(?:know|knows|prepare|prepares|prepared|ready|heads\s+up"
    r"|help|helps|expect|expects|aware|idea)\b",
    re.IGNORECASE,
)


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
    # Job 3c.5 / CAce1457d1: "That's a time preference noted — but could you
    # tell me what…" is form-filling, not speech. Em-dash counts as a boundary
    # so the real follow-up question survives.
    ("time_preference_noted",
     re.compile(
         r"[^.!?—–]*\b(?:that'?s\s+a\s+)?time\s+preference\s+noted\b"
         r"[^.!?—–]*[—.!?—–,]?\s*",
         re.IGNORECASE,
     )),
    ("preference_noted_admin",
     re.compile(
         r"[^.!?—–]*\b(?:day|date|timing|slot)\s+preference\s+noted\b"
         r"[^.!?—–]*[—.!?—–,]?\s*",
         re.IGNORECASE,
     )),
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

    # B-75b - two CLASS rules, both from JV CA9262659c (21 Aug). Susie spoke
    # "Let me look at what I have - the caller confirmed quarter past five" and
    # then "I don't actually have the lookup data or the slot ISO." Both
    # reached ElevenLabs. Neither matched anything: _REASONING_OPENER_RE is an
    # enumerated list of sentence openers holding "Let me work out" and
    # "Looking at the" but not "Let me look at what I have", and
    # internal_identifier_token only ever sees the snake_case form.
    #
    # Adding those two phrasings would be the trap this codebase keeps falling
    # into. These are classes instead:
    #
    # 1. Third-person reference to the person she is TALKING TO. Susie says
    #    "you". "the caller" is the PROMPT's word for them, which is exactly
    #    why the model reaches for it when it narrates instead of speaking.
    #    sanitise_response only ever sees model output - never the greeting,
    #    the whisper, or any code-built line - so no legitimate speech is at
    #    risk. Same principle lookup_reasoning_leak states above for "the
    #    patient".
    ("third_person_caller_reference",
     re.compile(r"[^.!?]*\bthe caller(?:'s)?\b[^.!?]*[.!?]?", re.IGNORECASE)),

    # 2. A code identifier spoken with the underscore read out as a SPACE.
    #    internal_identifier_token catches `slot_iso`; it cannot catch "slot
    #    ISO", which is the same leak said aloud. "ISO" and "lookup" (one word,
    #    as a noun) are machine vocabulary with no receptionist meaning. The
    #    two-word verb "look up your appointment" is untouched, and word
    #    boundaries keep real words containing them safe - both pinned by tests.
    ("spoken_identifier_token",
     re.compile(r"[^.!?]*\b(?:ISO|lookup)\b[^.!?]*[.!?]?", re.IGNORECASE)),
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
    # Job 3c.2 / CAce1457d1: keep "closest/nearest … to [requested window]"
    # (out-of-window acknowledgement). Bare scarcity without "to" still strips.
    ("closest_nearest_opener",
     re.compile(
         r"^[Tt]he (?:closest|nearest)\b(?![^.!?—–]*\bto\b)[^.!?—–]*?"
         r"(?:\bis\s+|\bwould be\s+|[—–]\s*)",
         re.IGNORECASE,
     )),
    # "The closest/nearest I've got / I have / we have / available" —
    # full-sentence strip for forms where the date cannot be rescued by the
    # opener-strip above.  Runs after the opener-strip so cases already rescued
    # by that pattern are never double-processed.
    # ("soonest"/"earliest" intentionally excluded — see note above.)
    # Same 3c.2 exemption: "I've got to [request]" must not be wiped.
    ("closest_ive_got",
     re.compile(
         r"[^.!?]*\b[Tt]he (?:closest|nearest)\s+"
         r"(?:I'?ve\s+got|I\s+have|we\s+have|available)\b"
         r"(?!\s+to\b)"
         r"[^.!?]*[.!]?",
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
    # B-43 — the same narration on the cancel and reschedule paths. The arm
    # below used to read only `I need to book (?:this|it) in now`, and on
    # CA12db707b (3 Aug 2026, 10:39:23) the caller heard "I need to action the
    # cancellation now." Nine plausible phrasings of one internal sentence were
    # spoken aloud and one was caught.
    #
    # Identical shape to B-36 cause 2: a guard scoped to BOOKING while the
    # same failure sits verbatim on the destructive paths. Generalising the verb
    # is the fix; adding two more literals would just be the same bug waiting.
    #
    # Safe to widen here specifically because Gate 5g's structural guards do the
    # containment: "I need to book YOU in now" and "I need to get that sorted
    # for YOU now" both carry a second-person reference and are exempt, so the
    # sentences a caller may legitimately hear are protected by construction.
    # The 40-char bounded gap keeps the match inside one clause.
    r"|I need to\s+(?:book|action|process|cancel|move|reschedule|change"
    r"|sort|complete|make|do|put)\b[^.!?]{0,40}?\bnow"
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
    # B-41 — narrating the caller's DECISION in the third person. CA12db707b
    # (3 Aug 2026, 10:16:19): the caller heard "Their choice is to cancel."
    # aloud. Two reasoning sentences were generated; `lookup_reasoning_leak` is
    # a sentence-level strip and removed only the one carrying "look up the
    # patient details", leaving its sibling to reach TTS.
    #
    # Internal by construction: Susie addresses the caller as "you", so a
    # sentence attributing a choice to them in the third person is the model
    # describing the conversation rather than having it. Same argument the
    # `lookup_reasoning_leak` comment already makes for "the patient", and the
    # `reasoning_the_caller` pattern for "The caller ...".
    #
    # Scoped to DECISION nouns, and deliberately NOT a bare "they/their" arm:
    # the clinic is also "they", and "They close at six." / "They're fully
    # booked that day." are legitimate sentences with no second person and no
    # question mark, so a bare arm would strip them. Under-firing is the right
    # bias — an over-fire here deletes real speech, which is the Gate 5c
    # failure of 2026-06-12.
    r"|their\s+(?:choice|preference|decision|intent|intention|selection|wish)\b"
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


# ── Gate 5g support: the WRITE confirmation, and the question that must come
# before it ─────────────────────────────────────────────────────────────────
#
# Deliberately NARROWER than _BOOKING_OFFER_RE above. That one matches any
# booking OFFER ("would you like to book?"), which is a fine thing to say early
# in a call. This one matches only the final WRITE confirmation — the sentence
# whose "yes" fires book_appointment — because that is the only one that must
# wait for a confirmed phone number.
#
# The three phrasings are the same vocabulary llm_stream._booking_confirmation_asked
# tests to decide whether a caller's "yes" may open the write gate. Kept in step
# with it by test_no_booking_cta_before_phone.py rather than by importing it:
# llm_stream imports THIS module, so the dependency cannot run the other way.
# Every alternative carries a BOOKING verb. "shall i go ahead" on its own is
# NOT enough and must never be added: it is the shared opener for all three
# write families — "shall I go ahead and move it for you?" (reschedule) and
# "shall I go ahead and cancel that?" (cancel) both match it, and this gate
# would replace a legitimate move or cancel confirmation with a request for a
# phone number. Caught in review 2026-08-07 after the first version shipped
# with the bare opener; test_reschedule_and_cancel_ctas_are_untouched pins it.
#
# Deliberately fails toward NOT firing. A booking CTA phrased in some way this
# misses degrades to the previous behaviour, where book_appointment's phone
# backstop still refuses the write. An over-match breaks a different flow
# outright, which is strictly worse.
_BOOKING_CTA_SENTENCE_RE = re.compile(
    r"[^.!?]*\b(?:"
    r"shall i go ahead and book"
    r"|book that in"
    r"|put that request through"
    # NOT "booked in": that matches the phantom CLAIM "You're all booked in",
    # which belongs to the false-confirmation guard further down and must be
    # dropped to empty, not rewritten into a question.
    r")\b[^.!?]*[.!?]?",
    re.IGNORECASE,
)


def _name_known(session: Dict[str, Any]) -> bool:
    """True once a patient name has actually been captured this call.

    Three keys because three paths write it: _v3_try_persist_name sets the
    top-level `patient_name`, and collect_and_store writes into `collected`
    under either spelling depending on how the model labelled it.
    """
    _collected = session.get("collected") or {}
    return bool(
        (session.get("patient_name") or "").strip()
        or (_collected.get("full_name") or "").strip()
        or (_collected.get("name") or "").strip()
    )


def _next_booking_question_for(session: Dict[str, Any]) -> str:
    """The question that should be asked instead of a premature booking CTA.

    NAME BEFORE PHONE. The prompt orders these — name is step 7, phone step 8,
    the readback step 9 — and the first version of Gate 5g knew only about the
    phone. On CA36eb3f (2026-08-07) both were missing when the model reached
    for the CTA; the gate substituted the phone question, the caller typed his
    number, and only THEN was he asked his name — one question before the
    booking, after he had already given the harder answer. It reads as the
    system remembering something at the last second, because that is what it
    is doing.

    Asking whichever step is genuinely outstanding, in the prompt's own order,
    puts the flow back the way it was written.
    """
    if not _name_known(session):
        return (
            "Before I do that — could I take your first name and surname?"
        )
    return _phone_question_for(session)


def _phone_question_for(session: Dict[str, Any]) -> str:
    """The phone question that should have been asked, in the caller's case.

    Mirrors the (a)/(b) split the theorem_v3 prompt already specifies at its
    phone step, so the deterministic substitution and the model's own wording
    cannot diverge:

      (a) a caller ID is held  → offer it, SPEAKING THE DIGITS. Never "is that
          the number you're calling from?" — the caller has not heard what we
          hold, so a blind yes can write a stranger's number to the booking.
      (b) no caller ID         → straight to the keypad.

    Both forms are checked by the sibling test against the two predicates that
    consume them downstream: the keypad line must satisfy
    connection._is_keypad_arming_line (or the typed digits land in a closed
    keypad), and both must satisfy _PHONE_STEP_MARKERS (or the phone step does
    not register as asked and book_appointment's backstop mis-fires).
    """
    _cli = (
        (session.get("twilio_from_local") or "")
        or (session.get("twilio_from") or "")
    ).strip()
    # NB "Before I do that", not "Before I book that in": the replacement is
    # scanned by _BOOKING_CTA_SENTENCE_RE again to remove any SECOND CTA in the
    # same chunk, and a replacement carrying the CTA vocabulary deletes itself.
    if _cli:
        return (
            f"Before I do that — is {' '.join(_cli)} the best number "
            "for you? If so, just say use this number."
        )
    return (
        "I can't see a phone number on this call — could you type the number on your "
        "keypad? You can press the star key to reset at any time."
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
# Gate 5cb — callback promise / retract (CA9d48f8f7ce, Raymond, 2026-08-14)
# ---------------------------------------------------------------------------
# Vital Edge refund call: Susie said "I've passed that on to Jonathan" then,
# after the caller thanked her, contradicted herself with "I need to actually
# log that request properly before I can promise he'll call" — then promised
# again. Twilio shows the CALLBACK SMS did deliver; the spoken contradiction
# is what the judge scored.
#
# Two arms, both required:
#   1. Without callback_write_confirmed — a completion claim ("passed that on",
#      "he'll be in touch", "that's all sent over") is a phantom. Re-steer once
#      per turn to a non-promise hold; further claims drop.
#   2. With callback_write_confirmed — a retract ("need to log… before I can
#      promise") is stripped so she cannot undo a promise that already went out.
#
# Questions / offers stand down ("could I arrange for Jonathan to call you
# back?"). Known same-turn limit as Gate 5f: a claim streamed in the same
# assistant message as the tool_use, before the result lands, can escape.
_FALSE_CALLBACK_PROMISE_RE = re.compile(
    r"""\b(
        (ive|i\s+have)\s+passed\s+that\s+on
      | passed\s+that\s+on\s+to
      | thats\s+all\s+sent(\s+over)?\s+to
      | sent(\s+that|\s+it|\s+this)?\s+(all\s+)?over\s+to
      | (hell|he\s+will|shell|she\s+will)\s+be\s+in\s+touch
      | (hell|he\s+will|shell|she\s+will)\s+(give\s+you\s+a\s+call|call\s+you|ring\s+you)
      | (jonathan|marcus|mark)\s+will\s+(be\s+in\s+touch|call\s+you|ring\s+you)
    )""",
    re.X,
)
_FALSE_CALLBACK_PROMISE_NEG_RE = re.compile(
    r"\b(not|havent|cannot|cant|wont|shall\s+i|would\s+you|can\s+i|could\s+i"
    r"|do\s+you\s+want|want\s+me\s+to|like\s+me\s+to|going\s+to|ill\b|i\s+will"
    r"|let\s+me|need\s+to|before\s+i|to\s+arrange|arrange\s+for)\b"
)
_CALLBACK_RETRACT_RE = re.compile(
    r"[^.!?]*\b(?:"
    r"need\s+to\s+(?:actually\s+)?log"
    r"|log\s+that\s+request\s+properly"
    r"|before\s+i\s+can\s+promise"
    r"|i\s+(?:actually\s+)?need\s+to\s+log"
    r"|let\s+me\s+do\s+that\s+now"
    r")\b[^.!?]*[.!?]?",
    re.IGNORECASE,
)
_FALSE_CALLBACK_RESTEER = (
    "One moment — I'll get that logged for Jonathan now."
)


def _false_callback_promise(text: str) -> bool:
    """True if `text` CLAIMS a callback was already sent / promised."""
    if not text:
        return False
    norm = _norm_for_claim(text)
    if _FALSE_CALLBACK_PROMISE_NEG_RE.search(norm):
        return False
    declarative = _declarative_part(text)
    if not declarative:
        return False
    return bool(_FALSE_CALLBACK_PROMISE_RE.search(_norm_for_claim(declarative)))


def _strip_callback_retract(text: str) -> str:
    """Remove sentences that undo a callback promise already confirmed."""
    if not text or not _CALLBACK_RETRACT_RE.search(text):
        return text
    cleaned = _CALLBACK_RETRACT_RE.sub(" ", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -,")
    return cleaned.strip()


def _apply_callback_promise_gate(text: str, session: Dict[str, Any]) -> str:
    """Gate 5cb: no phantom callback promise; no retract after a real one."""
    if session.get("callback_write_confirmed"):
        cleaned = _strip_callback_retract(text)
        if cleaned != text:
            logger.info(
                "[ms_gate5cb] stripped callback retract after confirmed write: %r → %r",
                text[:80], cleaned[:80],
            )
        return cleaned

    if not _false_callback_promise(text):
        return text

    session["_callback_promise_guard_fired"] = (
        int(session.get("_callback_promise_guard_fired") or 0) + 1
    )
    if not session.get("_callback_promise_resteered"):
        session["_callback_promise_resteered"] = True
        logger.error(
            "[ms_gate5cb] false callback promise with no confirmed write — "
            "re-steering: %r",
            text[:80],
        )
        return _FALSE_CALLBACK_RESTEER
    logger.error(
        "[ms_gate5cb] additional false callback promise dropped: %r",
        text[:80],
    )
    return ""


# Gate 5f — false-confirmation guard (P1 #5 / F-023 / B-36 cause 2)
# ---------------------------------------------------------------------------
# CALL 5: book_appointment was REJECTED (confirmation_required, no calendar
# event) yet the model narrated "All booked" — a phantom appointment the clinic
# has no record of. This detector fires ONLY on a CLAIM that a booking is
# complete, so it can be acted on while no booking has actually succeeded this
# call (session["booking_write_confirmed"] unset).
#
# 2026-08-03 — ARM ON THE REFUSAL, NOT ONLY ON THE FLOW (B-36 cause 2).
# The guard was scoped to booking in all five of its parts: the flow flag, the
# vocabulary, the success signal, the steering rule and the tool name. On
# CA23199d089 a REFUSED reschedule was narrated as done and nothing in this file
# ran at all — `booking_flow_active` has exactly two assignment sites and neither
# fires on a reschedule.
#
# The arm is now `write refused this turn` OR the original
# `booking_flow_active AND NOT booking_write_confirmed`. **OR, not replace** —
# the original arm is the only thing that catches a pure hallucination, where the
# model claims a booking having called no tool at all, and that is arguably the
# commoner failure. Dropping it to "fix" the reschedule hole would have been a
# silent downgrade of a working guard.
#
# Why the refusal arm does not re-open the over-fire trap (cause 2c): a
# SUCCESSFUL reschedule refuses nothing, so on that turn the guard is not armed
# and the legitimate "that's you moved to Thursday" is never examined. The
# original arm cannot reach it either — it is `booking_flow_active`-gated and a
# reschedule never sets that flag. `booking_write_confirmed` therefore stops
# being load-bearing for anything but booking.
#
# KNOWN LIMIT: this is turn-level, not utterance-level. The marker is set when
# the tool result comes back, so a claim streamed in the SAME assistant message
# as the tool_use block is spoken before the refusal is known and escapes. On
# CA23199d089 the speech came in a later iteration, which is the shape in scope.
#
# It must never fire on an offer, a question, a negation, or an in-progress
# statement — the over-fire failure mode stripped a real confirmation and
# abandoned a completed booking once already (Gate 5c, 2026-06-12). Measured
# against both classes in tests/regression/test_false_confirmation_guard.py:
# 18/18 phantom claims caught, 0 false positives across 27 legitimate lines.

# The three write families. Defined here rather than in llm_stream because the
# import runs llm_stream -> turn_handler; llm_stream maps tool names onto these.
WRITE_FAMILY_BOOKING    = "booking"
WRITE_FAMILY_RESCHEDULE = "reschedule"
WRITE_FAMILY_CANCEL     = "cancel"

# Session key: the calendar id of the appointment a SUCCESSFUL cancel removed.
#
# B-65. The reschedule family has carried its target since B-62
# (RESCHEDULE_SUCCEEDED_SLOT_KEY) so a refused move to a DIFFERENT slot is not
# mistaken for a duplicate. Cancel had no equivalent because its success payload
# carried no appointment id at all, and _refusal_is_a_genuine_duplicate says so
# in as many words - "widening this needs executor changes and its own
# evidence". JV CA44046f96321b is that evidence: after a successful cancel the
# model re-issued cancel_appointment while the session _lookup_appointment_id
# had moved on to a DIFFERENT patient, and _match_gcal_event prefers that id
# over the patient_name in the args. Only the consent gate stopped it.
#
# Written by llm_stream._note_write_result on a cancel success; read by
# receptionist_tools._exec_cancel_appointment to refuse a second cancel aimed
# somewhere new. Call-scoped, never cleared per turn.
CANCEL_SUCCEEDED_ID_KEY = "_cancel_succeeded_appointment_id"

# Session key: which families had a gated write REFUSED on the current turn.
# Written by llm_stream._note_write_result, read by _armed_write_families, and
# cleared per turn alongside _false_confirm_resteered.
#
# A dict of family -> True, NEVER a set: the session is persisted to Redis with
# json.dumps (app/storage/redis_store.py), and a set raises TypeError there —
# which on a live call is an unhandled exception in the middle of a booking.
WRITE_REFUSED_KEY = "_write_refused"

# Session key: which write families have SUCCEEDED on this call.
#
# Moved here from llm_stream for B-75 so Gate 5f can read it; the import runs
# llm_stream -> turn_handler, same reason CANCEL_SUCCEEDED_ID_KEY lives here.
#
# CALL-scoped, deliberately — the opposite lifetime to WRITE_REFUSED_KEY, which
# is turn-scoped and cleared at the top of every turn. A completed write is a
# fact about the call. Per family rather than per appointment id: the cancel
# executor's success payload is {"success", "cancelled", "was_at"} with no id
# (receptionist_tools.py `_exec_cancel_appointment`), so an id-keyed latch would
# need the executor changed too. The cost of the coarser key is confined to the
# refusal path — a second, genuine cancellation still runs and still succeeds;
# only a second *refused* one is described as a duplicate rather than a failure,
# and _WRITE_ALREADY_DONE_RULE is worded so that is still true.
#
# A dict of family -> True, NEVER a set: same Redis json.dumps constraint as
# WRITE_REFUSED_KEY above.
WRITE_SUCCEEDED_KEY = "_write_families_succeeded"

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
# B-36 cause 2b — the reschedule phrase family. Judged ONLY on a turn where a
# reschedule write was actually refused, which is what makes this widening safe:
# "we've moved to a new building" is a plausible clinic FAQ answer, and under
# flow-arming it would have been a live false positive. Under refusal-arming that
# sentence can only be reached from a turn where reschedule_appointment was
# blocked, so an FAQ turn never sees this pattern at all.
#
# The object is REQUIRED after the verb — `moved you/that/it to ...`, never a
# bare `moved to ...` — which is precisely what keeps the new-building sentence
# out. Pinned in tests/regression/test_b36_gate5f_write_families.py.
_FALSE_RESCHEDULE_CLAIM_RE = re.compile(
    r"""\b(
        (youre|you\s+are|thats|that\s+is|its|it\s+is)\s+(all\s+|now\s+)?(rescheduled|moved|changed|sorted)
      | (thats|that\s+is)\s+you\s+(rescheduled|moved|changed)
      | (ive|i\s+have|weve|we\s+have)\s+(now\s+)?(rescheduled|moved|changed|switched)\s+(you|that|it|your)
      | your\s+(appointment|booking)\s+(has\s+been|is\s+now|is)\s+(rescheduled|moved|changed)
      | youre\s+(all\s+|now\s+)?(set|in)\s+for
      | (moved|rescheduled|changed|switched)\s+(you|that|it)\s+(over\s+)?to\s
    )""",
    re.X,
)

# B-36 cause 2e — the cancellation phrase family. Cancel is DESTRUCTIVE, so the
# bias here is louder than for booking: a caller told their appointment is
# cancelled when it is not will simply not turn up.
_FALSE_CANCEL_CLAIM_RE = re.compile(
    r"""\b(
        (youre|you\s+are|thats|that\s+is|its|it\s+is)\s+(all\s+|now\s+|been\s+)?(cancelled|canceled)
      | (ive|i\s+have|weve|we\s+have)\s+(now\s+)?(cancelled|canceled)\s+(you|that|it|your|the)
      | your\s+(appointment|booking)\s+(has\s+been|is\s+now|is)\s+(cancelled|canceled)
      | (appointment|booking|slot)\s+(has\s+been\s+|is\s+)?(cancelled|canceled)
      | taken\s+(that|it|you)\s+(off|out\s+of)\s+the\s+(diary|calendar|system)
    )""",
    re.X,
)

# Which patterns judge a chunk for a given family. Every family also gets the
# BOOKING pattern: "booked", "confirmed", "all set" is generic write-completion
# language, and on a turn where a reschedule was refused, "you're all booked in
# for Thursday" is exactly as much a phantom as "you're rescheduled". The
# booking pattern is the well-measured one (18/18, 0/27) and costs nothing to
# reuse, because it too is only reached on a turn with a refused write.
# B-36 R6 — the PROVISIONAL completion claim.
#
# VE acceptance run 2026-08-04, calls 1 and 7: `book_appointment` was refused
# and Susie still said "I've noted your preferred time and sent it to Jonathan
# to confirm". Nothing was written. Gate 5f did not strip it, and the reason is
# structural rather than an oversight: a provisional clinic's prompt BANS the
# words "booked" and "confirmed" (they are not true for that clinic), and those
# are exactly the tokens `_FALSE_CONFIRM_CLAIM_RE` keys on. The two safety
# mechanisms cancelled each other out.
#
# The claim being made is that the REQUEST WAS SENT. That is as false as "all
# booked" when no write happened, and it is what the caller acts on.
#
# Matched on the shape, not on one clinic's sentence: `booking_pending_message`
# is configurable per clinic, so a literal for Vital Edge's wording would fail
# silently for the next provisional clinic. Practitioner-name agnostic for the
# same reason.
#
# Cannot fire on the pre-write CTA ("shall I put that request through to
# Jonathan to confirm?") — that is a question, so `_declarative_part` drops it,
# and "shall i" is in `_FALSE_CONFIRM_NEG_RE` besides. Both are asserted.
_FALSE_PROVISIONAL_CLAIM_RE = re.compile(
    r"\b(?:"
    r"sent\s+(?:it|that|this|them|your\s+(?:request|details|preferred\s+time))"
    r"\s+(?:through\s+)?to\b"
    r"|put\s+(?:that|your|the)\s+request\s+through\b"
    r"|noted\s+your\s+(?:preferred\s+)?time\b"
    r"|request\s+is\s+(?:now\s+)?with\b"
    r")"
)

_FAMILY_CLAIM_RES = {
    # The provisional pattern is added to the BOOKING family only. It is inert
    # for confirmed-booking clinics, whose prompts never produce these phrases,
    # and it is safe on the success path: `booking_write_confirmed` is set on
    # ANY successful book_appointment including a provisional one
    # (llm_stream.py, `if family == WRITE_FAMILY_BOOKING`), so the family
    # disarms and a LEGITIMATE provisional closing is never seen by this gate.
    # That is the over-fire this guard has actually committed before — it
    # abandoned a completed booking on 2026-06-12 — so it is asserted directly.
    WRITE_FAMILY_BOOKING:    (_FALSE_CONFIRM_CLAIM_RE, _FALSE_PROVISIONAL_CLAIM_RE),
    WRITE_FAMILY_RESCHEDULE: (_FALSE_CONFIRM_CLAIM_RE, _FALSE_RESCHEDULE_CLAIM_RE),
    WRITE_FAMILY_CANCEL:     (_FALSE_CONFIRM_CLAIM_RE, _FALSE_CANCEL_CLAIM_RE),
}

# Any of these means the sentence is NOT a completion claim (offer / question /
# negation / intent), so the guard stands down even if a claim token is present.
# `to\s+(move|reschedule|cancel)` extends the original `to\s+book` to the two new
# families ("to move that I'll need your date of birth"). Adding alternatives to
# a STAND-DOWN pattern can only make more sentences stand down, never fewer, so
# the 27 measured legitimate lines cannot regress; none of the 18 measured
# phantoms contains a move/cancel verb, so they cannot start standing down
# either. Both directions are re-run in the guard tests.
_FALSE_CONFIRM_NEG_RE = re.compile(
    r"\b(not|havent|cannot|cant|wont|once|after|before|shall\s+i|would\s+you"
    r"|can\s+i|do\s+you\s+want|want\s+me\s+to|like\s+me\s+to|going\s+to|ill"
    r"|i\s+will|let\s+me|need\s+to|to\s+book|to\s+move|to\s+reschedule"
    r"|to\s+cancel)\b"
)

# What the caller hears instead of a phantom: the recovery CALL 12 did correctly,
# one per family. Each is a question, and free of any claim token, so it can
# never re-trigger the guard.
#
# THEY MUST NOT BE SHARED ACROSS FAMILIES. This return value becomes
# `last_bot_prompt`, and last_bot_prompt is what every write gate in llm_stream
# tests to decide whether its confirmation question has been asked. The booking
# re-steer contains BOTH booking gate literals ("shall i go ahead", "book that
# in"), so firing it on a reschedule phantom would leave a booking CTA on record
# — the caller's next "yes" would then satisfy the booking gate and a phantom
# reschedule could become a REAL booking of a new appointment.
#
# Each string therefore arms its own gate and no other:
#   booking    — "shall i go ahead" + "book that in"  -> booking gate only
#                (no move verb, so _move_confirmation_asked stays False)
#   reschedule — "move it for you"                    -> reschedule gate only
#                (deliberately NOT "shall I go ahead and move it", which would
#                 also match the booking gate's "shall i go ahead" arm)
#   cancel     — "cancel it altogether"               -> cancel gate only
#                (deliberately NOT the prompt's "would you like to reschedule
#                 this appointment, or cancel it altogether", whose reschedule
#                 wording sits one word away from the move gate's ask shapes)
# Pinned in tests/regression/test_b36_gate5f_write_families.py.
_FALSE_CONFIRM_RESTEER = (
    "Sorry — before I confirm anything, shall I go ahead and book that in for you?"
)
_FALSE_RESCHEDULE_RESTEER = (
    "Sorry — before I confirm anything, would you like me to move it for you?"
)
_FALSE_CANCEL_RESTEER = (
    "Sorry — before I confirm anything, would you like to keep this appointment, "
    "or cancel it altogether?"
)
# B-36 R6 — the booking re-steer for a PROVISIONAL clinic.
#
# `_FALSE_CONFIRM_RESTEER` says "shall I go ahead and book that in for you?".
# Said to a Vital Edge caller that is a promise of a CONFIRMED booking, which is
# the one thing VE's entire prompt exists to avoid — so stripping the false
# provisional closing and replacing it with that would swap one untrue sentence
# for a worse one.
#
# It must also still satisfy the booking gate, or the caller's next "yes" is
# evaluated against a CTA that was never asked and the write is refused again.
# It does: `_booking_confirmation_asked` now accepts "put that request through".
# That coupling is asserted in the tests — this string and that predicate have
# to move together.
_FALSE_CONFIRM_RESTEER_PROVISIONAL = (
    "Sorry — before I confirm anything, shall I put that request through for "
    "you?"
)

_FAMILY_RESTEER = {
    WRITE_FAMILY_BOOKING:    _FALSE_CONFIRM_RESTEER,
    WRITE_FAMILY_RESCHEDULE: _FALSE_RESCHEDULE_RESTEER,
    WRITE_FAMILY_CANCEL:     _FALSE_CANCEL_RESTEER,
}


def _clinic_is_provisional(session: Dict[str, Any]) -> bool:
    """True for clinics whose bookings are provisional until a human confirms.

    Read from the clinic contract rather than a clinic-id list — `booking_system
    == "google_calendar_provisional"` is what drives the provisional write path
    and the provisional prompt branch, so it is the same switch, not a parallel
    one that can drift out of step.

    Fails to False on any error: a confirmed-booking re-steer said to a
    provisional caller is wrong, but a provisional re-steer said to a CONFIRMED
    clinic's caller understates a real booking and would not satisfy that
    clinic's gate. False is the direction that preserves today's behaviour for
    the two live confirmed clinics.
    """
    try:
        from app.clinic_config import get_clinic
        clinic = get_clinic(session.get("clinic_id")) or {}
        return clinic.get("booking_system") == "google_calendar_provisional"
    except Exception:
        logger.warning(
            "[ms_gate5f] could not resolve clinic for the re-steer — "
            "defaulting to the confirmed-booking wording",
            exc_info=True,
        )
        return False


def _resteer_for(family: str, session: Dict[str, Any]) -> str:
    """The sentence the caller hears in place of a phantom confirmation.

    Per family (B-36: never share a re-steer across families — this return value
    becomes `last_bot_prompt`, which every write gate reads), and for the
    booking family also per booking model.
    """
    if family == WRITE_FAMILY_BOOKING and _clinic_is_provisional(session):
        return _FALSE_CONFIRM_RESTEER_PROVISIONAL
    return _FAMILY_RESTEER[family]


def _norm_for_claim(text: str) -> str:
    """Lower-case, DELETE apostrophes ("you're" -> "youre") so contractions
    match, blank other punctuation, and pad so \\b patterns see word edges."""
    norm = (text or "").lower().replace("'", "").replace("’", "")
    return " " + re.sub(r"[^a-z0-9 ]+", " ", norm).strip() + " "


def _declarative_part(text: str) -> str:
    """The sentences of `text` that are NOT questions.

    Gate 5f used to stand down on a `?` ANYWHERE in the text. That was correct
    when the unit was assumed to be one sentence — but the unit is a
    ResponseChunker chunk, and the chunker accumulates until MIN_WORDS (15).
    A completion claim is shorter than that, so it is emitted in the SAME chunk
    as the question that follows it, and the prompts *mandate* exactly that
    shape:

        "That's all done - your appointment has been cancelled.
         Is there anything else I can help with?"   -> one chunk

    Verified against the real chunker: both the cancel closing and a booking
    claim followed by "Is there anything else..." emit as a single chunk, and
    both were missed by every family. So the guard stood down on the one wording
    the prompt guarantees. This is very likely why Gate 5f has never fired live
    (see B-36 in docs/plan/REGISTER_B_U.md).

    Dropping only the question sentences preserves the original intent — the
    guard must never fire on a question — while letting the declarative claim
    beside it be judged.

    Reuses Gate 5g's `_SENTENCE_SPLIT_RE` deliberately, rather than declaring
    another one. An earlier draft of this function defined its own with `\\s+`
    instead of `\\s*`; at module scope that silently REBOUND the name for the
    whole file, so Gate 5g stopped splitting the no-space-after-period form the
    model produces ("...for you now.I need to book this in now...") and a
    reasoning leak went unstripped. Caught by
    tests/regression/test_reasoning_never_reaches_tts.py::CA2f0b0707. One
    splitter, one definition.
    """
    parts = [p for p in _SENTENCE_SPLIT_RE.split(text or "") if p.strip()]
    return " ".join(p for p in parts if "?" not in p).strip()


def _false_write_claim(text: str, family: str) -> bool:
    """True if `text` CLAIMS a write of `family` is complete.

    A question stands the guard down for that SENTENCE; any negation/offer/
    intent cue anywhere in the text stands it down entirely.

    The negation check deliberately still runs over the FULL text, including
    question sentences, rather than only the declarative part. That is the
    conservative direction: it preserves every stand-down this guard already
    had, so no line that passes today can start being stripped. This gate's
    documented failure mode is the over-fire — it stripped a real confirmation
    and abandoned a completed booking once (Gate 5c, 2026-06-12) — and the bias
    is stated in its own header: an over-fire deletes real speech.

    The cost is that "Shall I book that in? That's all booked." still stands
    down, because "shall i" appears in the chunk. That shape is not one the
    prompts teach, and accepting it is what keeps this change incapable of
    producing a new false positive.
    """
    if not text:
        return False
    patterns = _FAMILY_CLAIM_RES.get(family)
    if not patterns:
        return False
    if _FALSE_CONFIRM_NEG_RE.search(_norm_for_claim(text)):
        return False
    declarative = _declarative_part(text)
    if not declarative:
        return False
    norm = _norm_for_claim(declarative)
    return any(p.search(norm) for p in patterns)


def _false_confirmation_claim(text: str) -> bool:
    """True if `text` CLAIMS a BOOKING is complete. The original public helper,
    kept as the booking-family entry point (its 40 measured cases live in
    tests/regression/test_false_confirmation_guard.py)."""
    return _false_write_claim(text, WRITE_FAMILY_BOOKING)


def _armed_write_families(session: Dict[str, Any]) -> list:
    """Which write families Gate 5f is watching this turn, most specific first.

    Refused families lead, so that when a reschedule was refused on a turn that
    ALSO satisfies the legacy booking-flow arm, the claim is attributed to the
    reschedule — and gets the reschedule re-steer rather than a booking CTA.
    """
    refused = session.get(WRITE_REFUSED_KEY)
    if not isinstance(refused, dict):
        refused = {}
    succeeded = session.get(WRITE_SUCCEEDED_KEY)
    if not isinstance(succeeded, dict):
        succeeded = {}
    armed = [
        f for f in (WRITE_FAMILY_RESCHEDULE, WRITE_FAMILY_CANCEL, WRITE_FAMILY_BOOKING)
        if refused.get(f)
    ]
    # R1 — OR, not replace. The original arm is the only one that catches a
    # phantom the model produced having called no tool at all.
    #
    # B-75 — a SUCCESSFUL reschedule stands this arm down, exactly as a
    # successful booking does. `booking_flow_active` is set on the booking-ack
    # path for intent=reschedule too (connection.py, "booking ack …
    # intent=reschedule"), and a reschedule latches its own family, never
    # `booking_write_confirmed` — so without this the arm stayed live for the
    # whole of every reschedule call. On JV CA9262659c (21 Aug) the caller asked
    # "have you rescheduled it then", Susie answered "you're booked in for
    # Friday the 28th" — TRUE, the write landed 22s earlier — and this gate
    # replaced it with the booking CTA. That re-steer then became last_bot_prompt
    # and disarmed the move gate, so the retry was blocked too: the file header's
    # risk R5, reached from a success rather than a phantom.
    #
    # Reschedule only, NOT any success. After a cancel no appointment exists, so
    # "you're booked in" is still a phantom and the arm must stay up. Residual,
    # accepted: a reschedule followed by a claim of a SECOND booking made with no
    # tool call at all is no longer caught here — a second booking that is
    # actually attempted and refused still arms via the refusal path above.
    if (
        WRITE_FAMILY_BOOKING not in armed
        and session.get("booking_flow_active")
        and not session.get("booking_write_confirmed")
        and not succeeded.get(WRITE_FAMILY_RESCHEDULE)
    ):
        armed.append(WRITE_FAMILY_BOOKING)
    return armed


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

    # SUPERSEDED (CA6e1024db follow-up, 2 Aug 2026). The one thing the day-set
    # below cannot see: a caller who moves between two days that are BOTH in the
    # current payload. There the abandoned day is still "on offer", so the set
    # test would call the stale phrase current and this gate would spend the
    # rest of the call forcing the day the caller just left — CAb81fe651 exactly.
    #
    # The flag is set from the caller's OWN transcript (the DIFFERENT DAY
    # REQUESTED steer in llm_stream), which is the only signal in this decision
    # Gate 5 cannot rewrite. That matters: this gate edits the spoken text, so
    # anything derived from spoken text feeds its own output back in and
    # self-confirms. Cleared the moment a phrase is captured or refreshed, so it
    # stands the gate down for exactly the turns between "I want a different
    # day" and "so that's <new day>" — and re-arms after, which the old
    # scalar-only behaviour never did.
    if session.get("v3_slot_phrase_superseded"):
        return True

    # PRIMARY: every day currently on offer, not just the first one.
    #
    # v3_last_offered_day_iso is written as days[0] of the payload but was read
    # here as "the day the caller is being offered". Those diverge the moment a
    # payload is multi-day, which date_hint:"any" — i.e. "no preference", the
    # commonest answer a caller gives — guarantees. CA6e1024db: the caller took
    # the third day, the scalar still named the first, and this returned True on
    # a phrase that had never been stale. Four NOT-corrected lines, the gate
    # blind for the whole call.
    #
    # session["available_days"] is assigned the SAME filtered list that goes to
    # the model in the tool result (_filter_same_day_slots, receptionist_tools
    # ~3795), so it is exactly the model's offer surface — and it comes from the
    # tool, so this gate cannot touch it either.
    _days = session.get("available_days")
    if isinstance(_days, (list, tuple)) and _days:
        _seen_any_day = False
        for _d in _days:
            if not isinstance(_d, dict):
                continue
            _iso = str(_d.get("date") or "")
            if len(_iso) < 10:
                continue
            try:
                _md = (int(_iso[5:7]), int(_iso[8:10]))
            except ValueError:
                continue
            _seen_any_day = True
            if _md == (month, day):
                return False      # still on the table — the phrase is current
        # Only call it stale when we actually read some dates and none matched;
        # an unparseable payload falls through to the older signals below.
        if _seen_any_day:
            return True

    # Fallback: the day last offered, which outlives the slot cache. Retained
    # verbatim for any path that never populates available_days.
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


def _clinic_asks_its_own_reason_question(session: Dict[str, Any]) -> bool:
    """True when THIS clinic deliberately asks the caller why they are coming in.

    The owner decision behind Gate 5b-r — Susie never asks what brings the
    caller in — is Theorem's and jv_v1's. Vital Edge is the exception: it asks
    the question on purpose, in its own wording, exactly once, and the whole
    mechanism is already gated on the same key (`902411a`, `bec1b5e`).

    Ungated, Gate 5b-r is a booking-failure landmine for Vital Edge and the
    suite cannot see it. VE's MANDATED wording ("Is there a particular area or
    reason for the massage…") does not match _REASON_QUESTION_RE, so every test
    stays green — but the model improvises, and on CA86c320ef it improvised
    "What's the appointment for?", which the regex DOES strip. The reason is
    then never asked, so `note_reason_question_asked` never latches, so no
    reason is collected, and `book_appointment` refuses for want of one.

    Read through `get_clinic` rather than the session so it follows the same
    source of truth as the prompt renderer, and fail CLOSED — any error means
    the clinic did not opt in, so the suppression still runs. That is the safe
    direction: a clinic that never asked for the reason question keeps the
    protection it has today.
    """
    try:
        from app.clinic_config import get_clinic
        _pf = (get_clinic(session.get("clinic_id")) or {}).get("prompt_facts") or {}
        return bool(_pf.get("reason_question"))
    except Exception:
        return False


# ── The weekday Susie speaks must match the date she speaks ─────────────────
_MONTHS_FOR_SPEECH = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
_WEEKDAY_NAMES_FOR_SPEECH = (
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
)
# "Tuesday 26th August", "Tuesday the 26th of August", "Tuesday 26 August".
_SPOKEN_DATE_RE = re.compile(
    r"\b(" + "|".join(_WEEKDAY_NAMES_FOR_SPEECH) + r")\b"
    r"(\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?"
    # The month is OPTIONAL. On CA7d38fb42 the model said "Wednesday the 27th"
    # with no month at all, and a month-required pattern let it straight
    # through. A bare day is only acted on when the session knows exactly one
    # date with that day-of-month — see _known_dates_for_speech.
    r"(?:\s+(?:of\s+)?(" + "|".join(_MONTHS_FOR_SPEECH) + r")\b)?)",
    re.IGNORECASE,
)


def _known_dates_for_speech(session: Dict[str, Any]) -> Dict[tuple, Any]:
    """(day, month) -> the one date the session knows with that day and month.

    Built ONLY from dates the tool itself produced: the `date` field of each
    entry in `available_days`, plus the requested day stashed by
    `_exec_check_availability` (which is absent from `available_days` by
    definition — it was empty, which is why the sentence exists at all).

    A (day, month) that resolves to more than one distinct date is dropped
    rather than guessed. That can only happen across a year boundary, and a
    spoken date carries no year to break the tie with.
    """
    from datetime import date as _d

    found: Dict[tuple, set] = {}

    def _add(iso):
        if not isinstance(iso, str):
            return
        try:
            d = _d.fromisoformat(iso)
        except Exception:
            return
        found.setdefault((d.day, d.month), set()).add(d)

    try:
        days = session.get("available_days")
        if isinstance(days, list):
            for entry in days:
                if isinstance(entry, dict):
                    _add(entry.get("date"))
        _add(session.get("requested_day_iso"))
    except Exception:
        return {}

    out = {k: next(iter(v)) for k, v in found.items() if len(v) == 1}

    # Day-only keys, for a spoken date that carries no month ("Wednesday the
    # 27th"). Same rule: a day-of-month the session knows exactly ONE date for
    # is usable; two dates sharing a day-of-month across months are not, and
    # are left alone. Keyed (day, None) so it cannot collide with a real month.
    by_day_only: Dict[int, set] = {}
    for (_d, _m), _dt in out.items():
        by_day_only.setdefault(_d, set()).add(_dt)
    for _d, _dts in by_day_only.items():
        if len(_dts) == 1:
            out[(_d, None)] = next(iter(_dts))
    return out


def _correct_weekday_against_known_dates(text, session: Dict[str, Any]):
    """Make the spoken weekday agree with the spoken date.

    Live on Marcus's line twice in two days:

        caller: "um do you have any availability tomorrow tuesday"
        Susie:  "Tuesday 26th August is fully booked, I'm afraid - ..."

    26 August 2026 is a WEDNESDAY, and the very payload the model was reading
    said so — `requested_day_label` is `_spoken_day_label("2026-08-26")` =
    "Wednesday 26th August", the slot-formatter prompt says to use it verbatim,
    and it ships a worked example of the exact template the model then filled
    in with a weekday lifted from the caller's garbled "tomorrow tuesday".

    Telling the model again is not a fix: it already had the right string, the
    instruction, and the example. So this does not depend on the model at all.

    THE WEEKDAY IS CORRECTED TO THE DATE, NEVER THE REVERSE. The date is what
    gets booked — `_resolve_slot_iso` matches `available_days` on the date, and
    a caller who picks "number 2" never speaks a date at all. The weekday is
    decoration on top of it, so rewriting the date to match a hallucinated
    weekday would move a real appointment.

    Deny by default: only a day+month the session already knows is touched, no
    year is ever inferred, and anything unknown or ambiguous is left exactly as
    the model said it. Idempotent — it runs on both the gate chain and the
    assembled slot text.
    """
    if not text or not isinstance(text, str):
        return text
    try:
        known = _known_dates_for_speech(session)
        if not known:
            return text

        def _sub(m):
            said, tail, day_s, month_s = m.group(1), m.group(2), m.group(3), m.group(4)
            # month_s is None when the model spoke a bare day ("Wednesday the
            # 27th"). Fall back to the day-only key, which exists only when the
            # session knows exactly one date with that day-of-month.
            month = (
                _MONTHS_FOR_SPEECH.get(month_s.lower())
                if month_s else None
            )
            if month_s and month is None:
                return m.group(0)
            real = known.get((int(day_s), month))
            if real is None:
                return m.group(0)
            right = _WEEKDAY_NAMES_FOR_SPEECH[real.weekday()]
            if right.lower() == said.lower():
                return m.group(0)
            logger.warning(
                "[ms_gate5] spoken weekday corrected: %r -> %r for %s "
                "(the payload had it right; the model overrode it)",
                said, right, real.isoformat(),
            )
            return right + tail

        return _SPOKEN_DATE_RE.sub(_sub, text)
    except Exception as _e:
        # A live call must never die inside a cosmetic guard.
        logger.error("weekday correction failed: %r - leaving text as spoken", _e)
        return text


def _scarcity_claim_is_supported(session: Dict[str, Any]) -> bool:
    """True when "that's the only one" is a statement of fact, not a sales line.

    `that_is_the_only` bans scarcity framing, and the ban is right whenever the
    claim is unsupported — pressure invented by the model. It is wrong when the
    claim is simply TRUE and the caller has asked for alternatives, because then
    the banned sentence IS the answer, and stripping it leaves Susie re-offering
    the same time as a question.

    CA45357d84 (25 Aug 2026, jv_v1). Tuesday 1 September had exactly one slot.
    The caller asked four times over 40 seconds — "do you have any other slots
    on that day", "i asked if you have any other slots on that tuesday" — and
    three of those turns were answered by a stripped sentence and a re-offer of
    five o'clock. The truthful reply existed each time and never reached them.

    Discriminated on the DATA, deliberately: not on the caller's wording (that
    would be another phrase list, and a caller can ask this a hundred ways) and
    not on any further literal of Susie's speech. The question this answers is
    "is the claim supported?", and `available_days` is what supports it.

    Conservative by construction — it permits the sentence only when the whole
    offer on the table is a single slot on a single day, so a claim about ONE
    day while others are still open keeps today's suppression. That direction is
    the safe one: the failure mode is the current behaviour, not a false claim
    of scarcity.
    """
    try:
        # B-108, CA1b7b2c58 (Theorem, 27 Aug 2026). Every check below passed and
        # the sentence was still misleading, because they all interrogate the
        # DAY and none asks where the day came from.
        #
        # The caller said "do you have anything on tuesday" — a WEEKDAY, no
        # date. _check_availability's `_is_specific_day` arm resolved that to
        # the next occurrence, 1 September, which held exactly one slot. So
        # available_days was one day, slot_times one time, times_found_on_day 1,
        # and this returned True for "That's the only slot on Tuesday 1st
        # September". True about the 1st. The same scan had already returned
        # 7 slots on the 8th, 8 on the 15th and 9 on the 22nd — 24 more
        # Tuesdays slots the caller was never told about, and the sentence
        # tells them Tuesdays are all but gone.
        #
        # The claim has to be judged against the QUESTION, not just the day.
        # When the day was picked for the caller rather than named by them, a
        # scarcity claim about that day answers a question nobody asked, so it
        # goes back to being stripped. A caller who names an actual date
        # ("the 1st", "tomorrow") is unaffected — that arm sets the flag False
        # — and so is CA45357d84, the call this guard was built for, where the
        # caller had been given a date and was asking about that date.
        #
        # Absent flag -> treated as bare-weekday-free, i.e. no change from the
        # pre-B-108 answer. Only an availability call sets it, and only that
        # same call can put a scarcity claim on the table.
        if session.get("day_chosen_from_bare_weekday"):
            return False
        days = session.get("available_days")
        if not isinstance(days, list) or len(days) != 1:
            return False
        day = days[0] or {}
        times = day.get("slot_times")
        if not (isinstance(times, list) and len(times) == 1):
            return False
        # slot_times is what SURVIVED the caller's time-of-day preference, not
        # what the day holds. "the only one we have that day" is a claim about
        # the DAY, so it has to be judged against the day.
        #
        # B-97, CA6fa4b433: Wednesday 2 September had two bookable slots and a
        # caller who had asked for afternoons. One survived, this returned
        # True, and a live caller who had just said the 2pm did not suit was
        # told it was the only one. Counting the survivors made a false
        # sentence look supported.
        #
        # Absent count -> False, deliberately. This function already fails
        # CLOSED into the pre-B-92 behaviour (strip the sentence), and an
        # unverifiable claim is exactly the case the ban exists for.
        found = day.get("times_found_on_day")
        return isinstance(found, int) and found == 1
    except Exception:
        # Fail CLOSED — an unreadable session means the sentence is stripped,
        # which is exactly what happens today.
        return False


#: A ranking claim about availability: "the earliest I have is ...", "the
#: soonest is ...", "the first available is ...". Matches the CLAUSE only, up
#: to and including the copula, so removing it leaves the payload standing.
#:
#: Deliberately NOT an entry in _BANNED_SENTENCE_RE. Every pattern in that
#: table strips a whole sentence, and here the sentence carries the slot
#: readout -- banning it would leave the turn silent, which is a worse defect
#: than the one being fixed.
_EARLIEST_CLAIM_RE = re.compile(
    r"\b(?:[Tt]he\s+)?"
    r"(?:earliest|soonest|first\s+available|next\s+available|very\s+first)"
    r"(?:\s+(?:one|slot|time|appointment|opening))?"
    # `\\s*` after the pronoun, not `\\s+`: "I've got" has no space between
    # them, and requiring one silently dropped the commonest contraction of
    # the six. Longest alternatives first, or `have` matches and `have got`
    # is never tried.
    r"(?:\s+(?:I|we)\s*(?:\'ve\s+got|have\s+got|have|can\s+do|can\s+offer))?"
    r"\s+(?:is|would\s+be|\'s)\s+",
    re.IGNORECASE,
)


def _earliest_claim_is_supported(text: str, session: Dict[str, Any]) -> bool:
    """May Susie say "the earliest I have is X"?

    B-125, CA7182593819eac0a8e87a22928f137eb7. She said it about five past nine
    while eight in the morning sat bookable on the same day -- a time she had
    read out twenty seconds earlier. The caller had asked "what's the soonest
    you've got", which is the most direct question there is, and the answer was
    wrong by an hour.

    Judged the same way `_scarcity_claim_is_supported` judges "the only one we
    have": against the DAY, not against whatever subset the turn happens to be
    holding. The unspoken remainder of a day is a perfectly good thing to offer
    and a false thing to call the earliest.

    Both sides of the comparison are strings this codebase generates --
    `day_label` and `slot_times_spoken[0]` are what the readout is built from --
    so this is containment, not time parsing, and it cannot drift from the
    wording actually spoken.

    Fails CLOSED. An unreadable session, a day that cannot be identified, or a
    payload with no spoken times all return False and the claim is stripped.
    That is the same asymmetry the other two claim predicates use: silence about
    a ranking costs the caller nothing, and a wrong ranking sent a caller past a
    slot that was free.
    """
    try:
        if not _EARLIEST_CLAIM_RE.search(text or ""):
            return True           # no claim made; nothing to support
        days = session.get("available_days") or []
        if not isinstance(days, list) or not days:
            return False
        low = (text or "").lower()

        # Which day is the claim about? The sentence names it, and day_label is
        # the string the readout used. More than one match means the claim is
        # not about a single day and cannot be checked -- fail closed.
        named = [
            d for d in days
            if isinstance(d, dict)
            and str(d.get("day_label") or "").lower() in low
            and str(d.get("day_label") or "").strip()
        ]
        if len(named) != 1:
            return False

        spoken = named[0].get("slot_times_spoken") or []
        if not isinstance(spoken, list) or not spoken:
            return False
        first = str(spoken[0] or "").strip().lower()
        if not first:
            return False

        # The day's earliest bookable time must be the one being called the
        # earliest. If the sentence does not contain it, it is ranking something
        # else first.
        return first in low
    except Exception:
        return False


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

    # ── Gate 5a-d: the weekday must agree with the date ──────────────────────
    # Before any stripping, because a strip can remove the sentence that would
    # otherwise have carried the correction into the caller's ear.
    result = _correct_weekday_against_known_dates(text, session)

    # ── Gate 5a-e: a confirmed time must be one that was offered ─────────────
    # Same rung, same reason: a strip below could remove the sentence carrying
    # the correction. B-95, CA1cd253cb — "the second one please" was read back
    # as option 2's DAY with option 1's TIME, and the caller was asked to agree
    # to a slot that did not exist. Corrects only when the payload leaves no
    # choice; reports and leaves alone otherwise.
    try:
        from app.tools.slot_followup import reconcile_readback_time

        result, _rb_action, _rb_detail = reconcile_readback_time(
            result, session
        )
        if _rb_action == "corrected":
            logger.warning(
                "[ms_gate5] read-back time corrected: %s "
                "(the offer had it right; the model crossed two options)",
                _rb_detail,
            )
        elif _rb_action == "mismatch":
            logger.warning(
                "[ms_gate5] read-back time NOT in the offer and not safely "
                "correctable: %s", _rb_detail,
            )
    except Exception:
        # A confirmation sentence is the last thing that should die in a guard.
        logger.exception("[ms_gate5] read-back reconcile failed — text unchanged")

    # ── Gate 5a-f: a ranking claim must be true of the DAY ───────────────────
    # B-125. "The earliest I have is Tuesday 1st September — Number 1, five past
    # nine" was said while eight in the morning sat bookable on that same day,
    # and had been read out twenty seconds earlier. The caller had asked "what's
    # the soonest you've got".
    #
    # Placed here, with the other claim guards and above the sentence-level
    # strip, for the same reason they are: a strip below could remove the
    # sentence and take the correction with it.
    #
    # Only the CLAUSE goes. The sentence carries the slot readout, so banning it
    # outright would leave the turn with nothing to say — the failure mode this
    # would be trading down to.
    if not _earliest_claim_is_supported(result, session):
        _ranked = _EARLIEST_CLAIM_RE.sub("", result).lstrip()
        if _ranked != result:
            if _ranked:
                _ranked = _ranked[0].upper() + _ranked[1:]
            logger.warning(
                "[ms_gate5] removed an unsupported EARLIEST claim — the day has "
                "a bookable time before the one being called the soonest "
                "(B-125). Kept the times, dropped the ranking: %r -> %r",
                result[:70], _ranked[:70],
            )
            # Never trade a false ranking for silence. If the clause WAS the
            # whole sentence there is nothing left to offer, and the original
            # standing is the smaller fault — the same call the opener strip
            # makes.
            result = _ranked or result

    # ── Gate 5b: sentence-level stripping ────────────────────────────────────

    for desc, pattern in _BANNED_SENTENCE_RE:
        # The one conditional entry in this table. Kept IN the table rather than
        # lifted out of the loop so it stays discoverable: several call sites and
        # tests check proposed wording against _BANNED_SENTENCE_RE, and a pattern
        # hidden elsewhere would not be found by them.
        if desc == "that_is_the_only" and _scarcity_claim_is_supported(session):
            # Log only when there was actually something to keep. This used to
            # log on the way past, so it fired on every turn where the session
            # state merely PERMITTED the claim — nine times in CAdf057714,
            # including on "All booked — you're in for Friday the 28th", which
            # contains no scarcity sentence at all. Behaviour was always right;
            # the line just could not be used to verify it, which is the only
            # job a line like this has.
            if pattern.search(result):
                logger.info(
                    "[ms_gate5] kept scarcity sentence (%s) — one slot on one "
                    "day, the claim is true and the caller is owed it",
                    desc,
                )
            continue
        cleaned = pattern.sub("", result)
        if cleaned != result:
            logger.info("[ms_gate5] removed banned phrase (%s)", desc)
            result = cleaned

    # ── Gate 5b-r: the reason question is never asked ────────────────────────
    # See _REASON_QUESTION_RE. Separate from the flat loop above because the
    # strip can empty the turn, and an empty turn is NOT a safe outcome here:
    # it falls through to the deferred Gate-5 fallback, which speaks "Sorry, I
    # didn't quite catch that — could you say that again?". The caller then gets
    # a non-sequitur in place of a question that should never have existed.
    #
    # On CA041352eb the entire turn was the reason question, twice over, so this
    # is the common case rather than the corner.
    _reason_cleaned = _REASON_QUESTION_RE.sub("", result)
    if _reason_cleaned != result:
        # Runs ONLY inside this branch, i.e. only when a reason question was
        # actually removed. A standalone "I've noted the reason on the booking"
        # is a statement of fact and must survive; it is an orphan only when the
        # thing it introduced has just been deleted.
        _orphan = _REASON_PREAMBLE_RE.sub("", _reason_cleaned)
        if _orphan != _reason_cleaned:
            logger.info("[ms_gate5] removed orphaned reason preamble")
            _reason_cleaned = _orphan
        _reason_cleaned = re.sub(r"\s{2,}", " ", _reason_cleaned).strip()
        # The strip removed the only thing this turn asked. Whatever is left,
        # the caller now has nothing to answer — so the test is not "is the
        # residue empty?" but "does the residue still ASK something?". A turn
        # that asks nothing is dead air, and dead air on a live call reads as a
        # broken system (see _REASON_RESIDUE_FRAGMENT_RE).
        #
        # Substitute at most once per turn: sanitise_response runs per streamed
        # chunk, and without the latch a two-chunk turn could tack the
        # outstanding question on twice.
        _asks_something = "?" in _reason_cleaned
        _already = bool(session.get("_gate5br_substituted"))
        if not _asks_something and not _already:
            _outstanding = _next_booking_question_for(session)
            session["_gate5br_substituted"] = True
            if not _reason_cleaned or _REASON_RESIDUE_FRAGMENT_RE.match(_reason_cleaned):
                # Either the turn WAS the reason question, or all that survived
                # is a clause that only made sense hanging off it ("Just so Mark
                # has a heads up."). Speaking that alone is worse than not
                # speaking it — it answers a question the caller never heard.
                # Replace the whole thing with the step genuinely outstanding,
                # in the prompt's own order.
                logger.info(
                    "[ms_gate5] reason question removed — nothing askable left "
                    "(residue=%r); asked the outstanding step instead: %r",
                    _reason_cleaned[:40], _outstanding[:60],
                )
                _reason_cleaned = _outstanding
            else:
                # Substantive content survived (a slot readback, an empathy
                # line). Keep it — it is the caller's, not the model's
                # scaffolding — and give them something to answer.
                logger.info(
                    "[ms_gate5] removed banned phrase (reason_question); residue "
                    "asked nothing, appended the outstanding step: %r",
                    _outstanding[:60],
                )
                _reason_cleaned = f"{_reason_cleaned} {_outstanding}".strip()
        else:
            logger.info("[ms_gate5] removed banned phrase (reason_question)")
        result = _reason_cleaned

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

    # ── Gate 5g: no booking CTA before the phone is confirmed ────────────────
    # book_appointment's A1 gate requires phone_confirmed. Asking "shall I go
    # ahead and book that in?" before that is asking a question we cannot act
    # on — the caller says yes, the write is refused, and the whole
    # readback-and-confirm procedure has to be run again after the number is
    # collected.
    #
    # CA76b44ae9, 2026-08-07:
    #   08:16:35  "So that's Mark Da'ya, Monday the 10th of August at five…
    #              shall I go ahead and book that in?"      ← no phone yet
    #   08:16:43  caller: "thank you yeah"                  ← he agreed HERE
    #   08:16:45  "Just locking that in now…"
    #   08:16:47  book_appointment BLOCKED — phone step skipped
    #             (the model passed phone="unknown" rather than admit it had none)
    #   08:16:49  "Before I lock that in — could you type your number…"
    #   08:17:14  readback + CTA, 2nd time  → "thank you"  → classified no
    #   08:17:27  CTA, 3rd time             → "yes please" → booked
    #
    # He agreed at 08:16:43 and it happened at 08:17:35 — 52 seconds and two
    # extra confirmations later, on a 173-second call. He was told the booking
    # was being made and then asked for more information.
    #
    # The theorem_v3 prompt already orders this correctly (phone is step 8, the
    # readback step 9) and the model went to 9 anyway. That is why this is a
    # deterministic gate and not more prompt text — and why it lives here, in
    # the per-chunk sanitiser, rather than at the point that arms phone
    # collection: that runs AFTER run_turn, by which time the CTA has been
    # spoken.
    #
    # The readback is KEPT — it is useful and the caller should hear it. Only
    # the question is replaced, with the one that should have been asked.
    # Fires on a missing NAME as well as a missing phone. book_appointment
    # needs both, and holding back only for the phone was what inverted the
    # order on CA36eb3f: the caller typed his number and was asked his name
    # afterwards, one question before the booking.
    _booking_step_missing = (
        not _name_known(session) or not session.get("phone_confirmed")
    )
    if (
        session.get("booking_flow_active")
        and _booking_step_missing
        and _BOOKING_CTA_SENTENCE_RE.search(result)
    ):
        _next_ask = _next_booking_question_for(session)
        # Leading space: the pattern's [^.!?]* swallows the space after the
        # previous sentence, so a bare substitution yields "…evening.Before I".
        # That text becomes last_bot_prompt and conversation_history, where
        # sentence-splitting matchers read it.
        _replaced = _BOOKING_CTA_SENTENCE_RE.sub(" " + _next_ask, result, count=1)
        # Any further CTA in the same chunk goes entirely — one question a turn.
        _replaced = _BOOKING_CTA_SENTENCE_RE.sub("", _replaced)
        _replaced = re.sub(r"\s{2,}", " ", _replaced).strip()
        logger.info(
            "[ms_gate5] booking CTA held back — %s missing; asked for it "
            "instead: %r",
            "name" if not _name_known(session) else "phone",
            _next_ask[:60],
        )
        # ── O-18: this substitution can deadlock the name step ───────────────
        # When the name is what is missing, the sentence being deleted is very
        # often the model's ACKNOWLEDGEMENT of the name the caller just gave
        # ("Thanks Quentin — shall I go ahead and book that in?"). That
        # acknowledgement is the ONLY thing _v3_try_persist_name can read a
        # first name out of: it scans the assistant reply, and the caller's own
        # utterance is used solely to recover a surname.
        #
        # conversation_history stores the SPOKEN text (llm_stream._append_history,
        # deliberate since 2026-08-02), so by the time the persist runs the
        # acknowledgement is gone, nothing is stored, _name_known stays False,
        # and the next turn lands here again. On CA041352eb (2026-08-08) the
        # caller gave his name three times, was asked a fourth, and hung up:
        #
        #   00:01:27  "um yeah that would be quentin rook"
        #   00:01:28  "Before I do that — could I take your first name…?"
        #   00:01:36  "yeah that'll be quentin rook"          → same sentence
        #   00:01:45  "yeah i said that would be quentin rook" → same sentence
        #
        # The flag tells the persist call site that THIS turn's reply was
        # rewritten here, so it may fall back to the raw generation. Narrow on
        # purpose: the raw is consulted only in the situation this gate created,
        # so it does not generally reopen "the model believes things it never
        # said" — the failure _append_history was changed to prevent (CA7d46c2bc).
        if not _name_known(session):
            session["_gate5g_dropped_name_ack"] = True
        result = _replaced

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

    # ── Gate 5cb: callback promise / retract (CA9d48f8f7ce) ──────────────────
    # Runs before Gate 5f so a callback claim is not mis-read as a provisional
    # booking "sent … to Jonathan" under the booking family.
    result = _apply_callback_promise_gate(result, session)
    if not result:
        return ""

    # ── Gate 5f: false-confirmation guard (P1 #5 / F-023 / B-36) ─────────────
    # A chunk that CLAIMS a write is done, on a turn where that write was
    # REFUSED — or, for booking only, while the caller is booking and no booking
    # has succeeded this call — is a phantom. Runs AFTER Gate 5c so the re-steer
    # question, which contains an offer to book/move/cancel, is not itself
    # stripped as a redundant offer.
    #
    # First claim this turn is re-steered to that family's confirmation
    # question; any further claim in the same turn is dropped so the caller
    # never hears it. The once-per-turn latch is deliberately NOT per-family: a
    # turn that refuses two different writes is already pathological, and one
    # re-steer followed by silent drops is both safer and less confusing than
    # two questions in one breath. Dead air is covered by the C8-5 silence
    # guarantee in llm_stream.
    _armed = _armed_write_families(session)
    _claim_family = next(
        (f for f in _armed if _false_write_claim(result, f)), ""
    ) if _armed else ""
    if _claim_family:
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
            #
            # It is also what the write gates read to decide whether their
            # confirmation question has been asked, which is why the re-steer is
            # chosen per family. See _FAMILY_RESTEER.
            logger.error(
                "[ms_gate5f] false %s confirmation with no successful write "
                "(armed=%s) — re-steering to that family's confirmation "
                "question: %r",
                _claim_family, ",".join(_armed), result[:80],
            )
            return _resteer_for(_claim_family, session)
        logger.error(
            "[ms_gate5f] additional false-confirmation chunk dropped (%s): %r",
            _claim_family, result[:80],
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
