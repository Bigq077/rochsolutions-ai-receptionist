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

BOOKING_FLOW response order for theorem_v2 (new patient, no returning branch):
  1.  intent            → ASK_LOCATION fires
  2.  location          → COLLECT_REASON  ("what brings you in today?")
  3.  medical reason    → CONFIRM_ASSESSMENT  (LLM empathy + "does that sound OK?")
  4.  "Yes"             → NEW_OR_RETURNING  ("have you been with us before?")
  5.  "No"              → PRESENT_DAYS  (check_availability, days presented)
  6.  day preference    → PRESENT_TIMES  (LLM presents slots for chosen day)
  7.  slot selection    → slot_pending_confirmation ("Just to confirm… is that right?")
  8.  "Yes"             → COLLECT_NAME  ("who am I booking in today?")
  9.  name              → CONFIRM_PHONE  ("shall I use this number?")
  10. "Yes"             → CONFIRM_BOOKING  → AUTO-CONFIRMED in test mode → DONE

Step 8 ("Yes" confirming the slot) is mandatory — confirmed by Phase 5 tests.
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
            "I want to book an appointment",    # → ASK_LOCATION
            "One",                              # → COLLECT_REASON
            "I have lower back pain",           # → CONFIRM_ASSESSMENT
            "Yes",                              # → NEW_OR_RETURNING
            "No",                               # → PRESENT_DAYS
            "Next week mornings",               # → PRESENT_TIMES (presents slots)
            "The first one",                    # → slot_pending_confirmation
            "Yes",                              # → COLLECT_NAME
            "Alice Walker",                     # → CONFIRM_PHONE
            "Yes",                              # → CONFIRM_BOOKING (auto-confirmed)
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
            "I'd like to book an appointment please",  # → ASK_LOCATION
            "Two",                                     # → COLLECT_REASON
            "My knee has been hurting",                # → CONFIRM_ASSESSMENT
            "Yes",                                     # → NEW_OR_RETURNING
            "No",                                      # → PRESENT_DAYS
            "Any morning next week",                   # → PRESENT_TIMES (presents slots)
            "The second one",                          # → slot_pending_confirmation
            "Yes",                                     # → COLLECT_NAME
            "Ben Harris",                              # → CONFIRM_PHONE
            "Yes",                                     # → CONFIRM_BOOKING (auto-confirmed)
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
            "I want to book",       # → ASK_LOCATION
            "Alcester please",      # → COLLECT_REASON
            "I have shoulder pain", # → CONFIRM_ASSESSMENT
            "Yes",                  # → NEW_OR_RETURNING
            "No",                   # → PRESENT_DAYS
            "Next week",            # → PRESENT_TIMES (presents slots)
            "First one",            # → slot_pending_confirmation
            "Yes",                  # → COLLECT_NAME
            "Carol Thomas",         # → CONFIRM_PHONE
            "Yes",                  # → CONFIRM_BOOKING (auto-confirmed)
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
            "Can I book an appointment?",  # → ASK_LOCATION
            "Redditch",                    # → COLLECT_REASON
            "I've been having neck pain",  # → CONFIRM_ASSESSMENT
            "Yes",                         # → NEW_OR_RETURNING
            "No",                          # → PRESENT_DAYS
            "Mornings next week",          # → PRESENT_TIMES (presents slots)
            "First one",                   # → slot_pending_confirmation
            "Yes",                         # → COLLECT_NAME
            "David Lee",                   # → CONFIRM_PHONE
            "Yes",                         # → CONFIRM_BOOKING (auto-confirmed)
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
            "I want to book",                              # → ASK_LOCATION
            "The one near me, I don't know the name",      # → ASK_LOCATION re-asks (no match)
            "One, Alcester",                               # → COLLECT_REASON
            "I have hip pain",                             # → CONFIRM_ASSESSMENT
            "Yes",                                         # → NEW_OR_RETURNING
            "No",                                          # → PRESENT_DAYS
            "Next week",                                   # → PRESENT_TIMES (presents slots)
            "First one",                                   # → slot_pending_confirmation
            "Yes",                                         # → COLLECT_NAME
            "Sam Peters",                                  # → CONFIRM_PHONE
            "Yes",                                         # → CONFIRM_BOOKING (auto-confirmed)
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
        # Tangent injected at CONFIRM_ASSESSMENT (use_llm=True handles off-topic,
        # re-asks "does that sound OK?" — then "Yes" advances the step).
        "responses": [
            "I want to book",                                  # → ASK_LOCATION
            "One",                                             # → COLLECT_REASON
            "I have back pain",                                # → CONFIRM_ASSESSMENT
            "What's the weather like near the clinic?",        # tangent at CONFIRM_ASSESSMENT
            "Yes",                                             # → NEW_OR_RETURNING
            "No",                                              # → PRESENT_DAYS
            "Next week mornings",                              # → PRESENT_TIMES (presents slots)
            "First one",                                       # → slot_pending_confirmation
            "Yes",                                             # → COLLECT_NAME
            "Peter Grant",                                     # → CONFIRM_PHONE
            "Yes",                                             # → CONFIRM_BOOKING (auto-confirmed)
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
        # Diagnosis question injected at CONFIRM_ASSESSMENT (use_llm=True handles it,
        # re-asks "does that sound OK?" — then "Yes" advances).
        "responses": [
            "I want to book",                                  # → ASK_LOCATION
            "Two",                                             # → COLLECT_REASON
            "I have back pain",                                # → CONFIRM_ASSESSMENT
            "Do you think it could be a slipped disc?",        # tangent at CONFIRM_ASSESSMENT
            "Yes",                                             # → NEW_OR_RETURNING
            "No",                                              # → PRESENT_DAYS
            "Any morning",                                     # → PRESENT_TIMES (presents slots)
            "First one",                                       # → slot_pending_confirmation
            "Yes",                                             # → COLLECT_NAME
            "Lisa Brown",                                      # → CONFIRM_PHONE
            "Yes",                                             # → CONFIRM_BOOKING (auto-confirmed)
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
            "Do you do dental work?",                          # FAQ / DETECT_INTENT
            "No I mean physio, I want to book an appointment", # → ASK_LOCATION
            "One",                                             # → COLLECT_REASON
            "I have wrist pain",                               # → CONFIRM_ASSESSMENT
            "Yes",                                             # → NEW_OR_RETURNING
            "No",                                              # → PRESENT_DAYS
            "Next week",                                       # → PRESENT_TIMES (presents slots)
            "First one",                                       # → slot_pending_confirmation
            "Yes",                                             # → COLLECT_NAME
            "Mark Jones",                                      # → CONFIRM_PHONE
            "Yes",                                             # → CONFIRM_BOOKING (auto-confirmed)
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
        # Pricing tangent at CONFIRM_ASSESSMENT (use_llm=True, re-asks after addressing price).
        "responses": [
            "I want to book",                  # → ASK_LOCATION
            "One",                             # → COLLECT_REASON
            "I have knee pain",                # → CONFIRM_ASSESSMENT
            "How much does it cost?",          # tangent at CONFIRM_ASSESSMENT
            "OK that's fine, yes",             # → NEW_OR_RETURNING
            "No",                              # → PRESENT_DAYS
            "Next week mornings",              # → PRESENT_TIMES (presents slots)
            "First one",                       # → slot_pending_confirmation
            "Yes",                             # → COLLECT_NAME
            "James Collins",                   # → CONFIRM_PHONE
            "Yes",                             # → CONFIRM_BOOKING (auto-confirmed)
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
            # Long rambling opener — intent=booking detected despite no clear keyword
            "I've had this problem since last year when I was on holiday and hurt my back, seen loads of doctors and nobody has helped, my friend recommended you, so I thought I'd call",
            "Alcester",                        # → COLLECT_REASON
            "I have back pain as I mentioned", # → CONFIRM_ASSESSMENT
            "Yes",                             # → NEW_OR_RETURNING
            "No",                              # → PRESENT_DAYS
            "Any morning",                     # → PRESENT_TIMES (presents slots)
            "First one",                       # → slot_pending_confirmation
            "Yes",                             # → COLLECT_NAME
            "Helen Clarke",                    # → CONFIRM_PHONE
            "Yes",                             # → CONFIRM_BOOKING (auto-confirmed)
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
            "Is there another physio nearby that might be cheaper?",  # FAQ
            "OK fine I'll book with you",      # → ASK_LOCATION
            "Two",                             # → COLLECT_REASON
            "I have foot pain",                # → CONFIRM_ASSESSMENT
            "Yes",                             # → NEW_OR_RETURNING
            "No",                              # → PRESENT_DAYS
            "Next week",                       # → PRESENT_TIMES (presents slots)
            "First one",                       # → slot_pending_confirmation
            "Yes",                             # → COLLECT_NAME
            "Tom Evans",                       # → CONFIRM_PHONE
            "Yes",                             # → CONFIRM_BOOKING (auto-confirmed)
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
        # Caller mentions booking + cancel; Susie focuses on booking.
        # "Let's do the booking first" doesn't match a location → ASK_LOCATION re-asks.
        "responses": [
            "I want to book an appointment and I also need to cancel a different one",
            "Let's do the booking first",      # → ASK_LOCATION (no location match, re-asked)
            "One",                             # → COLLECT_REASON
            "I have ankle pain",               # → CONFIRM_ASSESSMENT
            "Yes",                             # → NEW_OR_RETURNING
            "No",                              # → PRESENT_DAYS
            "Mornings",                        # → PRESENT_TIMES (presents slots)
            "First one",                       # → slot_pending_confirmation
            "Yes",                             # → COLLECT_NAME
            "Anna White",                      # → CONFIRM_PHONE
            "Yes",                             # → CONFIRM_BOOKING (auto-confirmed)
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
        # Anger expressed at PRESENT_DAYS — PRESENT_DAYS uses extract=any so the
        # impatient day-preference advances the step.  PRESENT_TIMES LLM extracts
        # "next week" from the outburst and presents available times.
        "responses": [
            "I want to book",                                           # → ASK_LOCATION
            "One",                                                      # → COLLECT_REASON
            "I have back pain",                                         # → CONFIRM_ASSESSMENT
            "Yes",                                                      # → NEW_OR_RETURNING
            "No",                                                       # → PRESENT_DAYS
            "This is taking way too long, can you just put me in for next week?",
            "First one",                                                # → slot_pending_confirmation
            "Yes",                                                      # → COLLECT_NAME
            "Gary Webb",                                                # → CONFIRM_PHONE
            "Yes",                                                      # → CONFIRM_BOOKING (auto-confirmed)
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
        # Two pricing tangents at CONFIRM_ASSESSMENT (use_llm=True, classified as
        # "unsure" each time → LLM re-asks).  Third response contains "yes" → advances.
        "responses": [
            "I want to book",                                 # → ASK_LOCATION
            "Alcester",                                       # → COLLECT_REASON
            "I have a bad back",                              # → CONFIRM_ASSESSMENT
            "How much is it?",                                # tangent 1 at CONFIRM_ASSESSMENT
            "That's really expensive, is there a discount?",  # tangent 2 at CONFIRM_ASSESSMENT
            "Fine, I'll do it — yes",                         # → NEW_OR_RETURNING
            "No",                                             # → PRESENT_DAYS
            "Next week mornings",                             # → PRESENT_TIMES (presents slots)
            "First one",                                      # → slot_pending_confirmation
            "Yes",                                            # → COLLECT_NAME
            "Oliver Hunt",                                    # → CONFIRM_PHONE
            "Yes",                                            # → CONFIRM_BOOKING (auto-confirmed)
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
            "I just want to book as quickly as possible, stop asking questions",  # → ASK_LOCATION
            "Alcester",                  # → COLLECT_REASON
            "I have back pain",          # → CONFIRM_ASSESSMENT
            "Yes",                       # → NEW_OR_RETURNING
            "No",                        # → PRESENT_DAYS
            "Whatever you've got",       # → PRESENT_TIMES (presents slots)
            "First one",                 # → slot_pending_confirmation
            "Yes",                       # → COLLECT_NAME
            "Nick Stone",                # → CONFIRM_PHONE
            "Yes",                       # → CONFIRM_BOOKING (auto-confirmed)
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
        # "Only Sunday at midnight" → PRESENT_DAYS extract=any → advances to PRESENT_TIMES.
        # PRESENT_TIMES LLM sees impossible day and offers alternatives;
        # "OK fine, first one" selects the first available slot.
        "responses": [
            "I want to book",                                  # → ASK_LOCATION
            "Two",                                             # → COLLECT_REASON
            "I have a sports injury",                          # → CONFIRM_ASSESSMENT
            "Yes",                                             # → NEW_OR_RETURNING
            "No",                                              # → PRESENT_DAYS
            "Only Sunday at midnight works for me",            # → PRESENT_TIMES (any advances)
            "OK fine, first one",                              # → slot_pending_confirmation
            "Yes",                                             # → COLLECT_NAME
            "Diane Rush",                                      # → CONFIRM_PHONE
            "Yes",                                             # → CONFIRM_BOOKING (auto-confirmed)
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
        # Vague at every step: unclear location (ASK_LOCATION re-asks), vague reason
        # (extract=any accepts it), vague yes at CONFIRM_ASSESSMENT, eventually commits.
        "responses": [
            "I want to book",                                      # → ASK_LOCATION
            "I'm not sure which one",                              # → ASK_LOCATION re-asks
            "The first one I suppose, yes Alcester",               # → COLLECT_REASON
            "I've had back pain on and off, not sure really",      # → CONFIRM_ASSESSMENT
            "Yes I suppose physio could help",                     # → NEW_OR_RETURNING
            "No, first time here",                                 # → PRESENT_DAYS
            "Some time next week I guess",                         # → PRESENT_TIMES (presents slots)
            "The middle one",                                      # → slot_pending_confirmation
            "Yes",                                                 # → COLLECT_NAME
            "Susan Day",                                           # → CONFIRM_PHONE
            "Yes",                                                 # → CONFIRM_BOOKING (auto-confirmed)
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
            "I want to book",                          # → ASK_LOCATION
            "Two — no wait, one, I mean Alcester",     # → COLLECT_REASON (Alcester extracted)
            "I have wrist pain",                       # → CONFIRM_ASSESSMENT
            "Yes",                                     # → NEW_OR_RETURNING
            "No",                                      # → PRESENT_DAYS
            "Next week",                               # → PRESENT_TIMES (presents slots)
            "First one",                               # → slot_pending_confirmation
            "Yes",                                     # → COLLECT_NAME
            "Paul King",                               # → CONFIRM_PHONE
            "Yes",                                     # → CONFIRM_BOOKING (auto-confirmed)
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
            "If this takes much longer I'm going to another clinic",  # → ASK_LOCATION
            "One",                   # → COLLECT_REASON
            "I have back pain",      # → CONFIRM_ASSESSMENT
            "Yes",                   # → NEW_OR_RETURNING
            "No",                    # → PRESENT_DAYS
            "Next week mornings",    # → PRESENT_TIMES (presents slots)
            "First one",             # → slot_pending_confirmation
            "Yes",                   # → COLLECT_NAME
            "Laura Grey",            # → CONFIRM_PHONE
            "Yes",                   # → CONFIRM_BOOKING (auto-confirmed)
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
            "book pls",       # → ASK_LOCATION
            "one",            # → COLLECT_REASON
            "back pain",      # → CONFIRM_ASSESSMENT
            "yeah",           # → NEW_OR_RETURNING
            "nah",            # → PRESENT_DAYS  (new patient)
            "next week am",   # → PRESENT_TIMES (presents slots)
            "first",          # → slot_pending_confirmation
            "yeah",           # → COLLECT_NAME
            "Jay Smith",      # → CONFIRM_PHONE
            "yeah",           # → CONFIRM_BOOKING (auto-confirmed)
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "no_technical_error": True,
        },
    },

]
