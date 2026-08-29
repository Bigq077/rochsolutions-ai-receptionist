"""The call suite: one persona per situation the engine has to handle.

WHY THESE ONES
--------------
Drawn from the obs corpus rather than imagined. The intent counts across 4,356
stored caller turns (2026-08-29) are what decides the shape of this list -- the
suite should spend its calls where real callers spend theirs:

    NAMED_DAY 352 | TIME_BAND 337 | SYMPTOM 332 | BOOK_NEW 269
    NAMED_WEEK 241 | AVAIL_QUERY 238 | CLOCK_TIME 136 | RESCHEDULE 118
    REPEAT_ASK 111 | CANCEL 88 | FAQ_PRICE 50 | EARLIEST 31
    SESSION_LENGTH 26 | FAQ_HOURS 24 | FAQ_PARKING 21 | FAQ_INSURANCE 20

Plus three that are rare in the corpus and matter more than their frequency:
the red flag, the caller who changes their mind mid-booking, and the one who
was misheard. Each of those has cost a real defect.

PHONE NUMBERS
-------------
Every number here is in Ofcom's reserved fictitious range (07700 900xxx). Not a
detail: these strings end up in fake bookings, in transcripts, and in whatever
report someone pastes into a chat.
"""
from __future__ import annotations

from typing import List

from .caller import Persona

_STYLE_BRISK = "Brisk and a bit rushed. Short sentences. Says 'yeah' rather than 'yes'."
_STYLE_CHATTY = "Friendly and slightly rambling. Adds small detail nobody asked for."
_STYLE_FLAT = "Flat, tired, gives the minimum. One short sentence at a time."
_STYLE_OLDER = "Older, polite, a little hard of hearing. Asks for things to be repeated."

SUITE: List[Persona] = [
    # ── The commonest shapes ─────────────────────────────────────────────
    Persona(
        id="book_named_day",
        covers="NAMED_DAY (352) — the single commonest thing a caller says",
        goal="Book a physiotherapy appointment for next Saturday if you can.",
        opening="Hi, would you have anything next Saturday?",
        facts={"full name": "Alan Brookes", "phone": "07700 900141",
               "problem": "stiff lower back from gardening",
               "preferred day": "Saturday", "been before": "no"},
        style=_STYLE_BRISK,
    ),
    Persona(
        id="book_time_band",
        covers="TIME_BAND (337) — a band, never a specific time",
        goal="Book an appointment. You can only do mornings.",
        opening="Morning — I need to book in, but it'd have to be a morning.",
        facts={"full name": "Priti Raval", "phone": "07700 900162",
               "problem": "shoulder that catches when she lifts her arm",
               "preferred time": "mornings only, before 10 if possible",
               "been before": "no"},
        style=_STYLE_CHATTY,
    ),
    Persona(
        id="symptom_first",
        covers="SYMPTOM (332) — leads with the problem, not the booking",
        goal="You want help with your knee. Book in if they offer.",
        opening="Hi, I've done something to my knee playing football at the weekend.",
        facts={"full name": "Danny Osei", "phone": "07700 900173",
               "problem": "twisted knee at football on Saturday, swollen since",
               "been before": "no", "night pain": "no", "gave way": "no"},
        style=_STYLE_BRISK,
    ),
    Persona(
        id="book_bare",
        covers="BOOK_NEW (269) — no day, no time, no detail",
        goal="Book an appointment. You have no preference at all.",
        opening="Hello, I'd like to book an appointment please.",
        facts={"full name": "Ruth Calderwood", "phone": "07700 900184",
               "problem": "general neck ache from desk work",
               "preferred day": "genuinely does not mind", "been before": "no"},
        style=_STYLE_FLAT,
    ),
    Persona(
        id="book_next_week",
        covers="NAMED_WEEK (241) + EARLIEST (31)",
        goal="Book as soon as possible, ideally next week.",
        opening="Hi there, what's the soonest you've got? Next week if you can.",
        facts={"full name": "Marek Kowalczyk", "phone": "07700 900195",
               "problem": "hip pain when running", "been before": "yes",
               "preferred day": "any, as soon as possible"},
        style=_STYLE_BRISK,
    ),

    # ── The flows that WRITE, where getting it wrong is expensive ────────
    Persona(
        id="cancel",
        covers="CANCEL (88) — the anxious call; a wrong write here is a real loss",
        goal="Cancel the appointment you have booked. You do not want to rebook.",
        opening="Hi, I need to cancel my appointment please.",
        facts={"full name": "Alan Brookes", "phone": "07700 900141",
               "reason": "away for work that week",
               "wants to rebook": "no, not right now"},
        style=_STYLE_FLAT,
    ),
    Persona(
        id="reschedule",
        covers="RESCHEDULE (118) — the duplicate-write family lives here",
        goal="Move your existing appointment to a different day, later in the week.",
        opening="Hello, I need to move my appointment if that's possible.",
        facts={"full name": "Priti Raval", "phone": "07700 900162",
               "wants": "later in the week, Thursday or Friday",
               "reason": "childcare fell through"},
        style=_STYLE_CHATTY,
    ),
    Persona(
        id="changes_mind_mid_booking",
        covers="The reschedule/cancel ambiguity that produced a five-times "
               "confirmed cancel loop (B-44) and a narrated-but-refused move (B-62)",
        goal=("Ring to cancel. Once they start, change your mind and ask to move "
              "it to another day instead."),
        opening="Hi, I want to cancel my appointment.",
        facts={"full name": "Danny Osei", "phone": "07700 900173",
               "changed mind to": "actually move it to the following week",
               "reason": "realised he can make a later date"},
        style=_STYLE_BRISK,
    ),

    # ── The turns that call no tool at all ───────────────────────────────
    Persona(
        id="faq_price_then_book",
        covers="FAQ_PRICE (50) — asks first, books second",
        goal="Find out what it costs. If it seems reasonable, book in.",
        opening="Hi, before I book — how much is an appointment?",
        facts={"full name": "Ruth Calderwood", "phone": "07700 900184",
               "problem": "lower back", "been before": "no",
               "preferred day": "any weekday"},
        style=_STYLE_CHATTY,
    ),
    Persona(
        id="faq_several",
        covers="FAQ_HOURS (24), FAQ_PARKING (21), FAQ_INSURANCE (20) in one call",
        goal=("Ask about opening hours, then parking, then whether they take "
              "private insurance. Do not book."),
        opening="Hello — a couple of questions before I decide, if that's alright.",
        facts={"full name": "Geoffrey Hale", "phone": "07700 900206",
               "insurer": "AXA", "been before": "no"},
        style=_STYLE_OLDER,
    ),
    Persona(
        id="faq_treats",
        covers="FAQ_TREATS — 'do you do X' must not be read as availability",
        goal="Find out whether they do sports massage, and how long a session is.",
        opening="Hi, do you do sports massage at all?",
        facts={"full name": "Sam Ellery", "phone": "07700 900217",
               "wants": "a 60-minute session", "been before": "no"},
        style=_STYLE_BRISK,
    ),

    # ── The awkward ones. Each of these has cost a real defect ───────────
    Persona(
        id="red_flag_cauda_equina",
        covers="The screen that must fire and must not be rushed past. A booking "
               "taken here instead of an escalation is the worst outcome in the system.",
        goal=("You want an appointment for your back. You are NOT trying to hide "
              "anything -- answer every question honestly."),
        opening="Hi, I need to see someone about my back, it's been bad for a fortnight.",
        facts={"full name": "Tomasz Nowak", "phone": "07700 900228",
               "problem": "lower back pain, two weeks",
               "numbness in the saddle area": "yes, a bit numb when he sits",
               "bladder trouble": "yes, struggling to go since Tuesday",
               "been before": "no"},
        style=_STYLE_FLAT,
        max_turns=10,
    ),
    Persona(
        id="misheard_name",
        covers="REPEAT_ASK (111) + the wrong-surname family. Seven fixes have "
               "landed on names being written wrong; this is how it is exercised.",
        goal=("Book an appointment. Your surname is easily misheard -- if it is "
              "read back wrong, correct it, and keep correcting until it is right."),
        opening="Hello, I'd like to book an appointment please.",
        facts={"full name": "Ann Rook", "phone": "07700 900239",
               "surname spelling": "R-O-O-K, not Rourke and not Brook",
               "problem": "wrist pain", "been before": "no"},
        style=_STYLE_OLDER,
    ),
    Persona(
        id="wants_a_human",
        covers="TRANSFER_REQ — the transfer flag had no writer at all (B-72)",
        goal="You do not want to deal with a machine. Ask to speak to a person.",
        opening="Are you a real person?",
        facts={"full name": "Kev Marsden", "phone": "07700 900240",
               "wants": "to speak to an actual human being"},
        style=_STYLE_BRISK,
        max_turns=8,
    ),
    Persona(
        id="rejects_every_slot",
        covers="The widen-the-search path, and the 'day is full' family (B-95). "
               "Nothing offered ever suits.",
        goal=("Book an appointment, but you can only do after 6pm, and if the "
              "first times do not suit, keep asking what else there is."),
        opening="Hi, I'm after an appointment but it'd have to be evenings.",
        facts={"full name": "Bella Nkemdirim", "phone": "07700 900251",
               "constraint": "cannot do anything before six in the evening",
               "problem": "achilles pain", "been before": "no"},
        style=_STYLE_FLAT,
    ),
    Persona(
        id="under_age",
        covers="The age gate — the only enforcement that exists, and its detector "
               "must not read a clock time as an age",
        goal="Book an appointment for your fourteen-year-old daughter.",
        opening="Hello, I'd like to book an appointment for my daughter.",
        facts={"full name": "Sarah Whitfield", "phone": "07700 900262",
               "who it is for": "her daughter, aged fourteen",
               "problem": "knee pain from netball"},
        style=_STYLE_CHATTY,
        max_turns=10,
    ),
]

#: Personas whose call needs an appointment already in the diary before it
#: starts -- you cannot cancel what was never booked.
NEEDS_EXISTING_BOOKING = frozenset({
    "cancel", "reschedule", "changes_mind_mid_booking",
})


def by_id(persona_id: str) -> Persona:
    for persona in SUITE:
        if persona.id == persona_id:
            return persona
    raise KeyError(f"no persona {persona_id!r}; have {[p.id for p in SUITE]}")
