"""
two_clinic_scenarios.py — Phases 15, 16, 17.

Phase 15 — Two-Clinic Location Guards
    Verify theorem_v2 asks which clinic at the right moment (after intent,
    never in the greeting, never during a pure FAQ call) and that both
    Alcester and Redditch route correctly through the full booking flow.

Phase 16 — Off-Track Callers
    Caller goes sideways: random questions, tangents, changes of mind,
    combined requests.  Susie must redirect without crashing or losing state.

Phase 17 — Angry / Difficult Callers
    Caller is annoyed, rude, impatient, or contradictory.  Susie must stay
    calm, complete the task where possible, and exit gracefully when not.

All scenarios in this file target +447366530580 (theorem_v2 — two-clinic line).
The call_runner reads the "twilio_to" field and routes each scenario to the
correct number, leaving the Phase 1-14 scenarios on the original theorem line.
"""

_V2 = "+447366530580"  # theorem_v2 test line — two-clinic guards active

TWO_CLINIC_SCENARIOS = [

    # ============================================================
    # PHASE 15 — TWO-CLINIC LOCATION GUARDS
    # ============================================================

    {
        "id": "15.1",
        "phase": "Phase 15 — Two-Clinic Location Guards",
        "twilio_to": _V2,
        "name": "Greeting never mentions clinic names",
        "responses": [],
        "expected": {
            "greeting_not_contains": [
                "alcester", "redditch", "say one", "say two",
            ],
            "greeting_contains": ["theorem health", "susie"],
        },
    },

    {
        "id": "15.2",
        "phase": "Phase 15 — Two-Clinic Location Guards",
        "twilio_to": _V2,
        "name": "Full booking — Alcester via 'one'",
        "responses": [
            "I want to book an appointment",
            "One",
            "No I haven't been before",
            "Next week mornings",
            "The first one",
            "Yes",
            "Alice Walker",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "asked_which_clinic": True,
            "greeting_not_contains": ["alcester", "redditch", "say one", "say two"],
            "no_technical_error": True,
            "no_question_asked_twice": True,
        },
    },

    {
        "id": "15.3",
        "phase": "Phase 15 — Two-Clinic Location Guards",
        "twilio_to": _V2,
        "name": "Full booking — Redditch via 'two'",
        "responses": [
            "I'd like to book an appointment please",
            "Two",
            "No",
            "Any morning next week",
            "The second one",
            "Yes",
            "Ben Harris",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "asked_which_clinic": True,
            "no_technical_error": True,
            "no_question_asked_twice": True,
        },
    },

    {
        "id": "15.4",
        "phase": "Phase 15 — Two-Clinic Location Guards",
        "twilio_to": _V2,
        "name": "Full booking — caller says 'Alcester' by name",
        "responses": [
            "I want to book",
            "Alcester please",
            "No",
            "Next week",
            "First one",
            "Yes",
            "Carol Thomas",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "asked_which_clinic": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "15.5",
        "phase": "Phase 15 — Two-Clinic Location Guards",
        "twilio_to": _V2,
        "name": "Full booking — caller says 'Redditch' by name",
        "responses": [
            "Can I book an appointment?",
            "Redditch",
            "No",
            "Mornings next week",
            "First one",
            "Yes",
            "David Lee",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "asked_which_clinic": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "15.6",
        "phase": "Phase 15 — Two-Clinic Location Guards",
        "twilio_to": _V2,
        "name": "FAQ — opening hours — no clinic question asked",
        "responses": [
            "What are your opening hours?",
        ],
        "expected": {
            "location_not_asked": True,
            "no_technical_error": True,
            "no_crash": True,
        },
    },

    {
        "id": "15.7",
        "phase": "Phase 15 — Two-Clinic Location Guards",
        "twilio_to": _V2,
        "name": "FAQ — address — no clinic question asked",
        "responses": [
            "Where are you located?",
        ],
        "expected": {
            "location_not_asked": True,
            "no_technical_error": True,
            "no_crash": True,
        },
    },

    {
        "id": "15.8",
        "phase": "Phase 15 — Two-Clinic Location Guards",
        "twilio_to": _V2,
        "name": "Reschedule — clinic asked first, before name or phone",
        "responses": [
            "I need to reschedule my appointment",
            "Alcester",
            "John Smith",
            "Yes use this number",
            "Any morning next week",
            "The first one",
            "Yes",
        ],
        "expected": {
            "asked_which_clinic": True,
            "no_technical_error": True,
            "no_crash": True,
        },
    },

    {
        "id": "15.9",
        "phase": "Phase 15 — Two-Clinic Location Guards",
        "twilio_to": _V2,
        "name": "Cancel — clinic asked first, before name or phone",
        "responses": [
            "I want to cancel my appointment",
            "Redditch",
            "Jane Doe",
            "Yes this number",
            "Yes",
        ],
        "expected": {
            "asked_which_clinic": True,
            "no_technical_error": True,
            "no_crash": True,
        },
    },

    {
        "id": "15.10",
        "phase": "Phase 15 — Two-Clinic Location Guards",
        "twilio_to": _V2,
        "name": "Unclear location answer — Susie re-asks then continues",
        "responses": [
            "I want to book",
            "The one near me, I don't know the name",
            "One, Alcester",
            "No",
            "Next week",
            "First one",
            "Yes",
            "Sam Peters",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "asked_which_clinic": True,
            "no_state_corruption": True,
            "no_technical_error": True,
        },
    },

    # ============================================================
    # PHASE 16 — OFF-TRACK CALLERS
    # ============================================================

    {
        "id": "16.1",
        "phase": "Phase 16 — Off-Track Callers",
        "twilio_to": _V2,
        "name": "Random tangent mid-booking — weather question",
        "responses": [
            "I want to book",
            "One",
            "No",
            "What's the weather like near the clinic?",
            "Next week mornings",
            "First one",
            "Yes",
            "Peter Grant",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "no_state_corruption": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "16.2",
        "phase": "Phase 16 — Off-Track Callers",
        "twilio_to": _V2,
        "name": "Asks for medical diagnosis mid-flow",
        "responses": [
            "I want to book",
            "Two",
            "No",
            "Do you think it could be a slipped disc?",
            "Any morning",
            "First one",
            "Yes",
            "Lisa Brown",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "no_state_corruption": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "16.3",
        "phase": "Phase 16 — Off-Track Callers",
        "twilio_to": _V2,
        "name": "Asks about completely wrong service (dental)",
        "responses": [
            "Do you do dental work?",
            "No I mean physio, I want to book an appointment",
            "One",
            "No",
            "Next week",
            "First one",
            "Yes",
            "Mark Jones",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "16.4",
        "phase": "Phase 16 — Off-Track Callers",
        "twilio_to": _V2,
        "name": "Asks about pricing mid-booking flow then continues",
        "responses": [
            "I want to book",
            "One",
            "No",
            "How much does it cost?",
            "OK that's fine, next week mornings",
            "First one",
            "Yes",
            "James Collins",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "no_state_corruption": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "16.5",
        "phase": "Phase 16 — Off-Track Callers",
        "twilio_to": _V2,
        "name": "Changes mind mid-booking — abandons call",
        "responses": [
            "I want to book",
            "One",
            "Actually never mind, I'll sort it online",
        ],
        "expected": {
            "no_crash": True,
            "graceful_end": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "16.6",
        "phase": "Phase 16 — Off-Track Callers",
        "twilio_to": _V2,
        "name": "Long rambling opener then gets to booking",
        "responses": [
            "I've had this problem since last year when I was on holiday and hurt my back, seen loads of doctors and nobody has helped, my friend recommended you, so I thought I'd call",
            "Alcester",
            "No",
            "Any morning",
            "First one",
            "Yes",
            "Helen Clarke",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "no_state_corruption": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "16.7",
        "phase": "Phase 16 — Off-Track Callers",
        "twilio_to": _V2,
        "name": "Asks about competitor clinic then books",
        "responses": [
            "Is there another physio nearby that might be cheaper?",
            "OK fine I'll book with you",
            "Two",
            "No",
            "Next week",
            "First one",
            "Yes",
            "Tom Evans",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "16.8",
        "phase": "Phase 16 — Off-Track Callers",
        "twilio_to": _V2,
        "name": "Caller tries to handle two things at once",
        "responses": [
            "I want to book an appointment and I also need to cancel a different one",
            "Let's do the booking first",
            "One",
            "No",
            "Mornings",
            "First one",
            "Yes",
            "Anna White",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "no_technical_error": True,
            "no_state_corruption": True,
        },
    },

    # ============================================================
    # PHASE 17 — ANGRY / DIFFICULT CALLERS
    # ============================================================

    {
        "id": "17.1",
        "phase": "Phase 17 — Angry / Difficult Callers",
        "twilio_to": _V2,
        "name": "Immediately annoyed at speaking to AI",
        "responses": [
            "I don't want to speak to a robot, I want a real person",
        ],
        "expected": {
            "no_crash": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "17.2",
        "phase": "Phase 17 — Angry / Difficult Callers",
        "twilio_to": _V2,
        "name": "Annoyed mid-booking — 'this is taking forever'",
        "responses": [
            "I want to book",
            "One",
            "No",
            "This is taking way too long, can you just put me in for next week?",
            "First one",
            "Yes",
            "Gary Webb",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "no_state_corruption": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "17.3",
        "phase": "Phase 17 — Angry / Difficult Callers",
        "twilio_to": _V2,
        "name": "Complains price is too expensive mid-flow",
        "responses": [
            "I want to book",
            "Alcester",
            "No",
            "How much is it?",
            "That's really expensive, is there a discount?",
            "Fine, I'll do it — next week mornings",
            "First one",
            "Yes",
            "Oliver Hunt",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "no_state_corruption": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "17.4",
        "phase": "Phase 17 — Angry / Difficult Callers",
        "twilio_to": _V2,
        "name": "Impatient — 'stop asking questions just book me in'",
        "responses": [
            "I just want to book as quickly as possible, stop asking questions",
            "Alcester",
            "No",
            "Whatever you've got",
            "First one",
            "Yes",
            "Nick Stone",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "17.5",
        "phase": "Phase 17 — Angry / Difficult Callers",
        "twilio_to": _V2,
        "name": "Frustrated — preferred days unavailable",
        "responses": [
            "I want to book",
            "Two",
            "No",
            "Only Sunday at midnight works for me",
            "OK fine, what have you got next week?",
            "First one",
            "Yes",
            "Diane Rush",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "no_state_corruption": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "17.6",
        "phase": "Phase 17 — Angry / Difficult Callers",
        "twilio_to": _V2,
        "name": "Says 'this is useless' and hangs up",
        "responses": [
            "I want to book",
            "One",
            "This is useless, forget it",
        ],
        "expected": {
            "no_crash": True,
            "graceful_end": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "17.7",
        "phase": "Phase 17 — Angry / Difficult Callers",
        "twilio_to": _V2,
        "name": "Repeatedly unclear then eventually commits",
        "responses": [
            "I want to book",
            "I'm not sure which one",
            "The first one I suppose, yes Alcester",
            "Maybe, I haven't been I don't think",
            "Some time next week I guess",
            "The middle one",
            "Yes OK",
            "Susan Day",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "no_state_corruption": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "17.8",
        "phase": "Phase 17 — Angry / Difficult Callers",
        "twilio_to": _V2,
        "name": "Corrects themselves aggressively mid-location answer",
        "responses": [
            "I want to book",
            "Two — no wait, one, I mean Alcester",
            "No",
            "Next week",
            "First one",
            "Yes",
            "Paul King",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "no_state_corruption": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "17.9",
        "phase": "Phase 17 — Angry / Difficult Callers",
        "twilio_to": _V2,
        "name": "Threatens to go to a competitor",
        "responses": [
            "If this takes much longer I'm going to another clinic",
            "One",
            "No",
            "Next week mornings",
            "First one",
            "Yes",
            "Laura Grey",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "17.10",
        "phase": "Phase 17 — Angry / Difficult Callers",
        "twilio_to": _V2,
        "name": "Speaks entirely informally — abbreviated answers",
        "responses": [
            "book pls",
            "one",
            "nah",
            "next week am",
            "first",
            "yeah",
            "Jay Smith",
            "yeah",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "no_technical_error": True,
        },
    },

]
