"""
all_scenarios.py — Every test scenario for the Susie voice pipeline.

Each scenario dict:
    id        : unique identifier e.g. "1.1"
    name      : descriptive name
    phase     : phase label used for grouping in reports
    responses : ordered list of strings the test caller says (one per turn)
                Empty string "" means silence on that turn.
    expected  : dict of assertions — evaluated by the Claude evaluator
                and by rule-based checks in run_tests.py
"""

SCENARIOS = [

    # ============================================================
    # PHASE 1 — CONNECTION
    # ============================================================

    {
        "id": "1.1",
        "phase": "Phase 1 — Connection",
        "name": "Basic pickup and greeting",
        "responses": [],
        # Greeting: "Hi there, this is Susie, Theorem Health's AI receptionist,
        # how can I help you?"
        "expected": {
            "answered_within_seconds": 5,
            "greeting_contains": [
                "susie",
                "how can i help",
            ],
            "greeting_not_contains": [
                "alcester", "redditch", "say one", "say two",
            ],
        },
    },

    {
        "id": "1.2",
        "phase": "Phase 1 — Connection",
        "name": "Greeting wording exact",
        "responses": [],
        "expected": {
            "first_susie_turn_contains": "theorem health",
            "first_susie_turn_not_contains": [
                "alcester", "redditch",
                "say one", "say two",
            ],
        },
    },

    {
        "id": "1.3",
        "phase": "Phase 1 — Connection",
        "name": "Silence handling — re-ask fires",
        "responses": [
            "",             # silence on first question → re-ask fires
            "back pain",    # patient responds after re-ask
            "Yes",
            "No",
            "Next week mornings",
            "First one",
            "Yes",
            "John Smith",
            "Yes",
        ],
        "expected": {
            "susie_reasks": True,
            "reask_contains": "didn't quite catch",
            "flow_continues_after_silence": True,
            # 35 s matches TURN_WAIT_SECONDS (25 s) + inject overhead + margin.
            # With 9 non-blank responses the consecutive-inject gap is ~25-26 s.
            "no_dead_air_over_seconds": 35,
        },
    },

    # ============================================================
    # PHASE 2 — INTAKE FLOW
    # ============================================================

    {
        "id": "2.1",
        "phase": "Phase 2 — Intake Flow",
        "name": "Full clean run — clear answers",
        "responses": [
            "I have back pain",
            "Yes that sounds good",
            "No I haven't been before",
            "Next week any morning would be fine",
            "The first one please",
            "Yes that's correct",
            "John Smith",
            "Yes use this number",
        ],
        "expected": {
            "flow_completed": True,
            "no_question_asked_twice": True,
            "correct_order": [
                "physiotherapy assessment",
                "been with us before",
                "days or times",
                "available slots",
                "full name",
                "number to reach",
            ],
            "booking_confirmed": True,
        },
    },

    {
        "id": "2.2",
        "phase": "Phase 2 — Intake Flow",
        "name": "New patient recognition — formal",
        "responses": [
            "Shoulder pain",
            "Yes",
            "No I have not been before",
            "Any afternoon next week",
            "Second one",
            "Yes",
            "Jane Doe",
            "Yes",
        ],
        "expected": {
            "new_or_returning_correct": "new",
            "not_said": ["welcome back"],
            "flow_completed": True,
        },
    },

    {
        "id": "2.3",
        "phase": "Phase 2 — Intake Flow",
        "name": "New patient recognition — informal nah",
        "responses": [
            "Knee pain",
            "Yeah go on then",
            "Nah never been",
            "Mornings next week",
            "Last one",
            "Yeah",
            "Tom Jones",
            "Yeah use this number",
        ],
        "expected": {
            "new_or_returning_correct": "new",
            "not_said": ["welcome back"],
            "flow_completed": True,
        },
    },

    {
        "id": "2.4",
        "phase": "Phase 2 — Intake Flow",
        "name": "New patient — yeah i havent",
        "responses": [
            "Hip pain",
            "Yes",
            "Yeah I haven't been",
            "Next week Tuesday",
            "First one",
            "Yes",
            "Sarah Connor",
            "Yes",
        ],
        "expected": {
            "new_or_returning_correct": "new",
            "not_said": ["welcome back"],
            "flow_completed": True,
        },
    },

    {
        "id": "2.5",
        "phase": "Phase 2 — Intake Flow",
        "name": "Returning patient recognition",
        "responses": [
            "Lower back pain",
            "Yes",
            "Yeah I have been before",
            "A while ago",           # RETURNING_RECENCY → long_ago → skip treatment plan
            "Wednesday afternoon",
            "Second one",
            "Yes",
            "Michael Brown",
            "Yes",
        ],
        "expected": {
            "new_or_returning_correct": "returning",
            "flow_completed": True,
        },
    },

    {
        "id": "2.6",
        "phase": "Phase 2 — Intake Flow",
        "name": "Informal answers throughout",
        "responses": [
            "My knee's been giving me grief",
            "Yeah go on then",
            "Nah",
            "Anytime next week really",
            "The last one",
            "Yeah that's the one",
            "Sarah",
            "Yep",
        ],
        "expected": {
            "flow_completed": True,
            "no_question_asked_twice": True,
        },
    },

    {
        "id": "2.7",
        "phase": "Phase 2 — Intake Flow",
        "name": "Shortest possible answers",
        "responses": [
            "Shoulder",
            "Yeah",
            "No",
            "Mornings",
            "Second",
            "Yes",
            "Tom Jones",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "no_question_asked_twice": True,
        },
    },

    # ============================================================
    # PHASE 3 — EMPATHY TURN
    # ============================================================

    {
        "id": "3.1",
        "phase": "Phase 3 — Empathy Turn",
        "name": "Common condition — back pain",
        "responses": [
            "I've got lower back pain",
            "Yes",
            "No",
            "Next week",
            "First",
            "Yes",
            "John Smith",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "3.2",
        "phase": "Phase 3 — Empathy Turn",
        "name": "Unusual condition — headaches",
        "responses": [
            "I've been having really bad headaches",
            "Yes",
            "No",
            "Next week",
            "First",
            "Yes",
            "Alice Smith",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "3.3",
        "phase": "Phase 3 — Empathy Turn",
        "name": "Vague reason",
        "responses": [
            "I'm just not feeling right",
            "Yes",
            "No",
            "Any morning",
            "One",
            "Yes",
            "Bob Jones",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "no_technical_error": True,
        },
    },

    # ============================================================
    # PHASE 4 — SLOT SELECTION
    # ============================================================

    {
        "id": "4.1",
        "phase": "Phase 4 — Slot Selection",
        "name": "Select first slot",
        "responses": [
            "Back pain",
            "Yes",
            "No",
            "Next week mornings",
            "The first one please",
            "Yes that's right",
            "John Smith",
            "Yes",
        ],
        "expected": {
            "slot_confirmed": True,
            "confirmation_contains": "first",
            "flow_completed": True,
        },
    },

    {
        "id": "4.2",
        "phase": "Phase 4 — Slot Selection",
        "name": "Select second slot",
        "responses": [
            "Back pain",
            "Yes",
            "No",
            "Next week",
            "Second one",
            "Yes",
            "John Smith",
            "Yes",
        ],
        "expected": {
            "slot_confirmed": True,
            "flow_completed": True,
        },
    },

    {
        "id": "4.3",
        "phase": "Phase 4 — Slot Selection",
        "name": "Select last slot",
        "responses": [
            "Knee pain",
            "Yes",
            "No",
            "Next week",
            "The last one",
            "Yes",
            "Jane Doe",
            "Yes",
        ],
        "expected": {
            "slot_confirmed": True,
            "flow_completed": True,
        },
    },

    {
        "id": "4.4",
        "phase": "Phase 4 — Slot Selection",
        "name": "Select by number — three",
        "responses": [
            "Hip pain",
            "Yes",
            "No",
            "Any day next week",
            "Three",
            "Yes",
            "Mike Brown",
            "Yes",
        ],
        "expected": {
            "slot_confirmed": True,
            "flow_completed": True,
        },
    },

    {
        "id": "4.5",
        "phase": "Phase 4 — Slot Selection",
        "name": "Select middle one",
        "responses": [
            "Shoulder injury",
            "Yes",
            "No",
            "Morning next week",
            "That middle one",
            "Yes",
            "Sarah Jones",
            "Yes",
        ],
        "expected": {
            "slot_confirmed": True,
            "flow_completed": True,
        },
    },

    # ============================================================
    # PHASE 5 — PHONE NUMBER
    # ============================================================

    {
        "id": "5.1",
        "phase": "Phase 5 — Phone Number",
        "name": "Accept Twilio number",
        "responses": [
            "Back pain",
            "Yes",
            "No",
            "Next week",
            "First one",
            "Yes",
            "John Smith",
            "Yes use this number",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
        },
    },

    {
        "id": "5.2",
        "phase": "Phase 5 — Phone Number",
        "name": "Reject Twilio number give new",
        "responses": [
            "Back pain",
            "Yes",
            "No",
            "Next week",
            "First one",
            "Yes",
            "John Smith",
            "No use a different number",
            "07700900123",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
        },
        "checkpoints": [
            {"after_turn": 7, "field": "state_after",  "expected": "COLLECT_PHONE"},
            {"after_turn": 7, "field": "handled_by",   "expected": "confirm_phone_no"},
            {"after_turn": 8, "field": "state_after",  "expected": "CONFIRM_PHONE"},
            {"after_turn": 8, "field": "handled_by",   "expected": "collect_phone_full_digits"},
            {"after_turn": 8, "field": "assistant_response_emitted", "expected": True},
            {"after_turn": 9, "field": "handled_by",              "expected": "confirm_phone_yes"},
            {"after_turn": 9, "field": "booking_confirmed_after", "expected": True},
        ],
    },

    {
        "id": "5.3",
        "phase": "Phase 5 — Phone Number",
        "name": "Number read back correctly",
        "responses": [
            "Back pain",
            "Yes",
            "No",
            "Next week",
            "First one",
            "Yes",
            "John Smith",
            "Yes",
        ],
        "expected": {
            "number_confirmed_verbally": True,
            "flow_completed": True,
        },
    },

    # ============================================================
    # PHASE 6 — SILENCE AND RECOVERY
    # ============================================================

    {
        "id": "6.1",
        "phase": "Phase 6 — Silence",
        "name": "4 second silence then re-ask",
        "responses": [
            "",
            "Back pain",
            "Yes",
            "No",
            "Next week",
            "First",
            "Yes",
            "John Smith",
            "Yes",
        ],
        "expected": {
            "reask_fires": True,
            "reask_contains": "didn't quite catch",
            "flow_continues": True,
            "flow_completed": True,
        },
    },

    {
        "id": "6.2",
        "phase": "Phase 6 — Silence",
        "name": "Second silence different phrase",
        "responses": [
            "",
            "",
            "Back pain",
            "Yes",
            "No",
            "Next week",
            "First",
            "Yes",
            "John Smith",
            "Yes",
        ],
        "expected": {
            "second_reask_fires": True,
            "second_reask_contains": "sorry about that",
            "flow_completed": True,
        },
    },

    {
        "id": "6.3",
        "phase": "Phase 6 — Silence",
        "name": "Third silence triggers transfer",
        "responses": [
            "",
            "",
            "",
        ],
        "expected": {
            "transfer_message_played": True,
            "transfer_message_contains": [
                "trouble hearing",
                "transfer",
            ],
        },
    },

    {
        "id": "6.4",
        "phase": "Phase 6 — Silence",
        "name": "Recover after silence",
        "responses": [
            "",
            "Back pain",
            "Yes",
            "No",
            "Next week",
            "First",
            "Yes",
            "John Smith",
            "Yes",
        ],
        "expected": {
            "reask_fires": True,
            "flow_completed": True,
            "no_state_corruption": True,
        },
    },

    # ============================================================
    # PHASE 7 — EDGE CASES
    # ============================================================

    {
        "id": "7.1",
        "phase": "Phase 7 — Edge Cases",
        "name": "Caller corrects reason",
        "responses": [
            "Back pain actually no it's my shoulder",
            "Yes",
            "No",
            "Next week",
            "First",
            "Yes",
            "John Smith",
            "Yes",
        ],
        "expected": {
            "no_crash": True,
            "flow_completed": True,
        },
    },

    {
        "id": "7.2",
        "phase": "Phase 7 — Edge Cases",
        "name": "Caller asks question mid flow",
        "responses": [
            "Back pain",
            "How much does an assessment cost?",
            "Yes",
            "No",
            "Next week",
            "First",
            "Yes",
            "John Smith",
            "Yes",
        ],
        "expected": {
            "no_crash": True,
            "flow_completed": True,
        },
    },

    {
        "id": "7.3",
        "phase": "Phase 7 — Edge Cases",
        "name": "Caller wants to cancel mid booking",
        "responses": [
            "Back pain",
            "Actually never mind",
        ],
        "expected": {
            "no_crash": True,
            "graceful_end": True,
            "not_said": ["error", "technical issue"],
        },
    },

    {
        "id": "7.4",
        "phase": "Phase 7 — Edge Cases",
        "name": "Banned phrases never appear",
        "responses": [
            "Back pain",
            "Yes",
            "No",
            "Next week",
            "First",
            "Yes",
            "John Smith",
            "Yes",
        ],
        "expected": {
            "not_said": [
                "absolutely",
                "certainly",
                "of course!",
                "sure thing",
                "bear with me",
                "i am waiting",
                "are you still there",
                "welcome back",
            ],
            "flow_completed": True,
        },
    },

    {
        "id": "7.5",
        "phase": "Phase 7 — Edge Cases",
        "name": "Background noise throughout",
        "responses": [
            "Back pain",
            "Yes",
            "No",
            "Next week",
            "First",
            "Yes",
            "John Smith",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
        },
    },

    # ============================================================
    # PHASE 8 — FULL END TO END
    # ============================================================

    {
        "id": "8.1",
        "phase": "Phase 8 — Full End to End",
        "name": "Clean run 1 — new patient back pain",
        "responses": [
            "I have back pain",
            "Yes that sounds good",
            "No I haven't been before",
            "Next week any morning",
            "The first one",
            "Yes that's correct",
            "John Smith",
            "Yes use this number",
        ],
        "expected": {
            "flow_completed": True,
            "no_question_asked_twice": True,
            "no_technical_error": True,
            "booking_confirmed": True,
            # Threshold 35 s: injects run at TURN_WAIT=22 s so normal gap is ~22 s.
            # Raised from 25→35 to absorb Render cold-start latency (observed
            # 28–30 s on slow boots) while still catching dropped turns (44+ s).
            "no_dead_air_over_seconds": 35,
        },
    },

    {
        "id": "8.2",
        "phase": "Phase 8 — Full End to End",
        "name": "Clean run 2 — returning knee pain",
        "responses": [
            "Knee injury from running",
            "Yes please",
            "Yeah I've been before",
            "A while ago",           # RETURNING_RECENCY → long_ago → skip treatment plan
            "Any afternoon next week",  # vague → presents 2 specific options
            "The second one",           # ordinal → picks second available day
            "Yes",                      # confirms time slot
            "Sarah Johnson",            # COLLECT_NAME
            "Yes",                      # name readback confirm → CONFIRM_PHONE
            "Yes",                      # CONFIRM_PHONE → CONFIRM_BOOKING → booking_confirmed
        ],
        "checkpoints": [
            {"after_turn": 9, "field": "booking_confirmed_after", "expected": True},
        ],
        "expected": {
            "flow_completed": True,
            "no_question_asked_twice": True,
            "no_technical_error": True,
            "booking_confirmed": True,
        },
    },

    {
        "id": "8.3",
        "phase": "Phase 8 — Full End to End",
        "name": "Clean run 3 — new informal",
        "responses": [
            "My shoulder's been killing me",
            "Yeah",
            "Nah",
            "Next week mornings",
            "Last one",
            "Yeah",
            "Mike Brown",
            "Yep",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "no_question_asked_twice": True,
            "no_technical_error": True,
            "booking_confirmed": True,
        },
        "checkpoints": [
            {"after_turn": 7, "field": "state_after",           "expected": "CONFIRM_PHONE"},
            {"after_turn": 8, "field": "handled_by",            "expected": "confirm_phone_yes"},
            {"after_turn": 8, "field": "booking_confirmed_after", "expected": True},
        ],
    },

    {
        "id": "8.4",
        "phase": "Phase 8 — Full End to End",
        "name": "Clean run 4 — new patient hip",
        "responses": [
            "Hip pain when walking",
            "Yes",
            "No never",
            "Wednesday morning",
            "The first one",
            "Yes",
            "Emma Davis",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "no_question_asked_twice": True,
            "no_technical_error": True,
            "booking_confirmed": True,
        },
    },

    {
        "id": "8.5",
        "phase": "Phase 8 — Full End to End",
        "name": "Clean run 5 — returning formal",
        "responses": [
            "I have a recurring ankle problem",
            "Yes that sounds fine",
            "Yes I have been before",
            "A while ago",           # RETURNING_RECENCY → long_ago → skip treatment plan
            "Friday afternoon please",
            "The first one",
            "Yes that's correct",
            "David Wilson",
            "Yes please",            # name readback confirmation
            "Yes",                   # phone / booking confirmation
        ],
        "checkpoints": [
            {"after_turn": 10, "field": "booking_confirmed_after", "expected": True},
        ],
        "expected": {
            "flow_completed": True,
            "no_question_asked_twice": True,
            "no_technical_error": True,
            "booking_confirmed": True,
        },
    },

    # ============================================================
    # PHASE 9 — RESCHEDULE
    # ============================================================

    {
        "id": "9.1",
        "phase": "Phase 9 — Reschedule",
        "name": "Reschedule — use caller number",
        "responses": [
            "I need to reschedule my appointment",
            "John Smith",
            "Yes use this number",
            "Any morning next week",
            "The first one",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "asked_for_name": True,
            "asked_for_availability": True,
            "reschedule_confirmed": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "9.2",
        "phase": "Phase 9 — Reschedule",
        "name": "Reschedule — provide different number",
        "responses": [
            "I need to change my appointment",
            "Jane Doe",
            "No use a different number",
            "07700900456",
            "Wednesday mornings",
            "Second one",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "reschedule_confirmed": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "9.3",
        "phase": "Phase 9 — Reschedule",
        "name": "Reschedule — move my appointment phrasing",
        "responses": [
            "I need to move my appointment to a different day",
            "Tom Jones",
            "Yes this number is fine",
            "Thursday afternoon",
            "First one",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "reschedule_confirmed": True,
            "asked_for_name": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "9.4",
        "phase": "Phase 9 — Reschedule",
        "name": "Reschedule — rebook phrasing",
        "responses": [
            "I'd like to rebook my appointment please",
            "Sarah Connor",
            "Yes use this number",
            "Any Friday morning",
            "Second one",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "reschedule_confirmed": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "9.5",
        "phase": "Phase 9 — Reschedule",
        "name": "Reschedule — change the time phrasing",
        "responses": [
            "Can I change the time of my appointment?",
            "Michael Brown",
            "Yes use this number",
            "Any morning next week",
            "The first one",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "reschedule_confirmed": True,
            "no_technical_error": True,
        },
    },

    # ============================================================
    # PHASE 10 — CANCEL
    # ============================================================

    {
        "id": "10.1",
        "phase": "Phase 10 — Cancel",
        "name": "Cancel appointment — formal",
        "responses": [
            "I want to cancel my appointment",
            "John Smith",
            "Yes use this number",
        ],
        "expected": {
            "flow_completed": True,
            "cancel_confirmed": True,
            "asked_for_name": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "10.2",
        "phase": "Phase 10 — Cancel",
        "name": "Cancel appointment — won't be able to make it",
        "responses": [
            "I won't be able to make my appointment",
            "Jane Doe",
            "Yeah this number's fine",
        ],
        "expected": {
            "flow_completed": True,
            "cancel_confirmed": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "10.3",
        "phase": "Phase 10 — Cancel",
        "name": "Cancel appointment — not coming in phrasing",
        "responses": [
            "I'm not coming in for my appointment",
            "Tom Jones",
            "Yes use this number",
        ],
        "expected": {
            "flow_completed": True,
            "cancel_confirmed": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "10.4",
        "phase": "Phase 10 — Cancel",
        "name": "Cancel appointment — provide different number",
        "responses": [
            "I need to cancel my appointment",
            "Sarah Connor",
            "No use a different number",
            "07700900789",
        ],
        "expected": {
            "flow_completed": True,
            "cancel_confirmed": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "10.5",
        "phase": "Phase 10 — Cancel",
        "name": "Cancel appointment — need to cancel phrasing",
        "responses": [
            "I need to cancel",
            "Michael Brown",
            "Yes this number",
        ],
        "expected": {
            "flow_completed": True,
            "cancel_confirmed": True,
            "no_technical_error": True,
        },
    },

    # ============================================================
    # PHASE 11 — FAQ (PRICES, INSURANCE, HOURS, LOCATION, SERVICES)
    # ============================================================

    {
        "id": "11.1",
        "phase": "Phase 11 — FAQ",
        "name": "Prices — how much does it cost",
        "responses": [
            "How much does a physiotherapy assessment cost?",
            "No thank you",
        ],
        "expected": {
            "flow_completed": True,
            "offered_booking": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "11.2",
        "phase": "Phase 11 — FAQ",
        "name": "Prices — fee enquiry",
        "responses": [
            "What are your fees?",
            "No thanks",
        ],
        "expected": {
            "flow_completed": True,
            "offered_booking": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "11.3",
        "phase": "Phase 11 — FAQ",
        "name": "Prices — then books",
        "responses": [
            "How much does it cost?",
            "Yes I'd like to book please",
            "Back pain",
            "Yes",
            "No",
            "Next week mornings",
            "First one",
            "Yes",
            "Jane Smith",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "11.4",
        "phase": "Phase 11 — FAQ",
        "name": "Insurance — BUPA",
        "responses": [
            "Do you accept BUPA insurance?",
            "No thanks",
        ],
        "expected": {
            "flow_completed": True,
            "offered_booking": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "11.5",
        "phase": "Phase 11 — FAQ",
        "name": "Insurance — AXA",
        "responses": [
            "Do you take AXA health insurance?",
            "No thank you",
        ],
        "expected": {
            "flow_completed": True,
            "offered_booking": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "11.6",
        "phase": "Phase 11 — FAQ",
        "name": "Insurance — Vitality",
        "responses": [
            "Do you accept Vitality insurance?",
            "No thank you",
        ],
        "expected": {
            "flow_completed": True,
            "offered_booking": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "11.7",
        "phase": "Phase 11 — FAQ",
        "name": "Insurance — then books",
        "responses": [
            "Do you take AXA health insurance?",
            "Yes I would like to book",
            "Back pain",
            "Yes",
            "No",
            "Next week mornings",
            "First one",
            "Yes",
            "John Smith",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "11.8",
        "phase": "Phase 11 — FAQ",
        "name": "Opening hours question",
        "responses": [
            "What are your opening hours?",
            "No thanks",
        ],
        "expected": {
            "flow_completed": True,
            "offered_booking": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "11.9",
        "phase": "Phase 11 — FAQ",
        "name": "Opening hours — when do you close",
        "responses": [
            "What time do you close?",
            "No thank you",
        ],
        "expected": {
            "flow_completed": True,
            "offered_booking": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "11.10",
        "phase": "Phase 11 — FAQ",
        "name": "Location — where are you",
        "responses": [
            "Where are you located?",
            "No thank you",
        ],
        "expected": {
            "flow_completed": True,
            "offered_booking": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "11.11",
        "phase": "Phase 11 — FAQ",
        "name": "Location — address and parking",
        "responses": [
            "What's your address and is there parking?",
            "No thanks",
        ],
        "expected": {
            "flow_completed": True,
            "offered_booking": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "11.12",
        "phase": "Phase 11 — FAQ",
        "name": "Services — what treatments do you offer",
        "responses": [
            "What treatments do you offer?",
            "No thanks",
        ],
        "expected": {
            "flow_completed": True,
            "offered_booking": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "11.13",
        "phase": "Phase 11 — FAQ",
        "name": "Services — what conditions do you treat",
        "responses": [
            "What conditions can you help with?",
            "No thank you",
        ],
        "expected": {
            "flow_completed": True,
            "offered_booking": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "11.14",
        "phase": "Phase 11 — FAQ",
        "name": "Services — then books",
        "responses": [
            "What services do you offer?",
            "Yes I'd like to book",
            "Shoulder pain",
            "Yes",
            "No",
            "Next week mornings",
            "First one",
            "Yes",
            "Tom Jones",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "no_technical_error": True,
        },
    },

    # ============================================================
    # PHASE 12 — FULL NON-BOOKING END TO END
    # ============================================================

    {
        "id": "12.1",
        "phase": "Phase 12 — Full Non-Booking End to End",
        "name": "Full clean reschedule",
        "responses": [
            "I need to reschedule my physiotherapy appointment",
            "Sarah",
            "Yes use this number",
            "Any morning next week",
            "The first one",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "asked_for_name": True,
            "asked_for_availability": True,
            "reschedule_confirmed": True,
            "no_question_asked_twice": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "12.2",
        "phase": "Phase 12 — Full Non-Booking End to End",
        "name": "Full clean cancel",
        "responses": [
            "I need to cancel my physiotherapy appointment",
            "Sarah",
            "Yes use this number",
        ],
        "expected": {
            "flow_completed": True,
            "asked_for_name": True,
            "cancel_confirmed": True,
            "no_question_asked_twice": True,
        },
    },

    {
        "id": "12.3",
        "phase": "Phase 12 — Full Non-Booking End to End",
        "name": "Full clean FAQ prices",
        "responses": [
            "What's the price for an initial assessment?",
            "No thank you",
        ],
        "expected": {
            "flow_completed": True,
            "offered_booking": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "12.4",
        "phase": "Phase 12 — Full Non-Booking End to End",
        "name": "Full clean FAQ hours",
        "responses": [
            "What are your opening hours?",
            "Yes actually I would like to book",
            "Knee pain",
            "Yes",
            "No",
            "Next week",
            "First one",
            "Yes",
            "Peter White",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "no_technical_error": True,
        },
    },

    # ============================================================
    # PHASE 13 — EDGE CASES
    # ============================================================

    {
        "id": "13.1",
        "phase": "Phase 13 — Edge Cases",
        "name": "Transfer to human — explicit request",
        "responses": [
            "Can I speak to a real person please?",
        ],
        "expected": {
            "no_technical_error": True,
            "no_crash": True,
            "transfer_message_contains": ["straight through"],
        },
    },

    {
        "id": "13.2",
        "phase": "Phase 13 — Edge Cases",
        "name": "Transfer to human — staff member phrasing",
        "responses": [
            "I'd like to speak to a member of staff",
        ],
        "expected": {
            "no_technical_error": True,
            "no_crash": True,
            "transfer_message_contains": ["straight through"],
        },
    },

    {
        "id": "13.3",
        "phase": "Phase 13 — Edge Cases",
        "name": "Location FAQ then books",
        "responses": [
            "Where are you located?",
            "Yes I'd like to book please",
            "Hip pain",
            "Yes",
            "No",
            "Next week mornings",
            "First one",
            "Yes",
            "Sarah Mitchell",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "13.4",
        "phase": "Phase 13 — Edge Cases",
        "name": "Hours FAQ then books",
        "responses": [
            "What time do you close?",
            "Yes I'd like to book an appointment",
            "Wrist pain",
            "Yes",
            "No",
            "Any morning this week",
            "The second one",
            "Yes",
            "Daniel Brown",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "13.5",
        "phase": "Phase 13 — Edge Cases",
        "name": "Insurance FAQ then books",
        "responses": [
            "Do you accept AXA health insurance?",
            "Yes please book me in",
            "Neck pain",
            "Yes",
            "No",
            "Next Tuesday",
            "First one",
            "Yes",
            "Emma Wilson",
            "Yes",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
            "no_technical_error": True,
        },
    },

    # ============================================================
    # PHASE 14 — STT GARBAGE FILTER
    # Responses with {"text": "...", "via_filter": True} are routed through
    # _is_garbage_transcript() on the server.  Noise-only text is dropped
    # and Susie fires a silence re-ask — just as with real STT garbage.
    # ============================================================

    {
        "id": "14.1",
        "phase": "Phase 14 — STT Garbage Filter",
        "name": "Noise-only input → re-ask → real answer",
        "responses": [
            {"text": "mm", "via_filter": True},   # filtered → silence → re-ask
            "I have back pain",                    # real answer after re-ask
            "Yes",
            "No",
            "Next week mornings",
            "First one",
            "Yes",
            "John Smith",
            "Yes",
        ],
        "expected": {
            "susie_reasks": True,
            "reask_contains": "catch",
            "flow_completed": True,
            "booking_confirmed": True,
        },
    },

    {
        "id": "14.2",
        "phase": "Phase 14 — STT Garbage Filter",
        "name": "Filler sounds filtered, flow still completes",
        "responses": [
            {"text": "uh huh", "via_filter": True},  # filtered
            "Shoulder pain",
            "Yeah",
            "No",
            "Any morning next week",
            "Second one",
            "Yes",
            "Jane Doe",
            "Yes",
        ],
        "expected": {
            "susie_reasks": True,
            "flow_completed": True,
        },
    },

    {
        "id": "14.3",
        "phase": "Phase 14 — STT Garbage Filter",
        "name": "Phone number survives filter (digit-heavy allowed through)",
        "responses": [
            "Back pain",
            "Yes",
            "No",
            "Next week",
            "First",
            "Yes",
            "Alice Brown",
            {"text": "07502 112233", "via_filter": True},  # digits → NOT filtered
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
        },
    },

    {
        "id": "14.4",
        "phase": "Phase 14 — STT Garbage Filter",
        "name": "Multiple noise turns then real answer",
        "responses": [
            {"text": "hmm", "via_filter": True},   # filtered
            {"text": "er", "via_filter": True},    # filtered again
            "Knee pain",
            "Yes",
            "No",
            "Mornings",
            "First",
            "Yes",
            "Tom Wilson",
            "Yes",
        ],
        "expected": {
            "susie_reasks": True,
            "flow_completed": True,
        },
    },

]

# ---------------------------------------------------------------------------
# Master list — used by run_tests.py
# ---------------------------------------------------------------------------

ALL_SCENARIOS: list[dict] = SCENARIOS


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def get_scenario_by_id(scenario_id: str) -> dict | None:
    for s in ALL_SCENARIOS:
        if s["id"] == scenario_id:
            return s
    return None


def get_scenarios_by_phase(phase_number: int) -> list[dict]:
    prefix = f"Phase {phase_number}"
    return [s for s in ALL_SCENARIOS if s["phase"].startswith(prefix)]


def get_phase_names() -> list[str]:
    seen: list[str] = []
    for s in ALL_SCENARIOS:
        if s["phase"] not in seen:
            seen.append(s["phase"])
    return seen
