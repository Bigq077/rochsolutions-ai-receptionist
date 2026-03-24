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
        # No responses — just check Susie answers and delivers correct greeting.
        # Greeting is BOOKING_OPEN: "Of course you can book an appointment —
        # what brings you in today?" — no clinic selection, no location question.
        "expected": {
            "answered_within_seconds": 5,
            "greeting_contains": [
                "what brings you in",
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
            "first_susie_turn_contains": "what brings you in",
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
            "",          # silence on first question
            "",          # silence on re-ask
            "back pain", # answer on third attempt
        ],
        "expected": {
            "susie_reasks": True,
            "reask_contains": "didn't quite catch",
            "flow_continues_after_silence": True,
            "no_dead_air_over_seconds": 6,
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
            "About two weeks",
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
                "what brings you in",
                "how long have you had",
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
            "Three weeks",
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
            "A few months",
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
            "Two weeks",
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
            "Six months",
            "Yes",
            "Yeah I have been before",
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
            "A good few months",
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
            "Weeks",
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
            "About two weeks",
            "Yes",
            "No",
            "Next week",
            "First",
            "Yes",
            "John Smith",
            "Yes",
        ],
        "expected": {
            "empathy_response_present": True,
            "empathy_contains_condition": True,
            "duration_question_asked": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "3.2",
        "phase": "Phase 3 — Empathy Turn",
        "name": "Unusual condition — headaches",
        "responses": [
            "I've been having really bad headaches",
            "A few weeks",
            "Yes",
            "No",
            "Next week",
            "First",
            "Yes",
            "Alice Smith",
            "Yes",
        ],
        "expected": {
            "empathy_response_present": True,
            "duration_question_asked": True,
            "no_technical_error": True,
        },
    },

    {
        "id": "3.3",
        "phase": "Phase 3 — Empathy Turn",
        "name": "Vague reason",
        "responses": [
            "I'm just not feeling right",
            "A while",
            "Yes",
            "No",
            "Any morning",
            "One",
            "Yes",
            "Bob Jones",
            "Yes",
        ],
        "expected": {
            "duration_question_asked": True,
            "no_technical_error": True,
            "flow_completed": True,
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
            "Two weeks",
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
            "Two weeks",
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
            "Weeks",
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
            "A month",
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
            "Three weeks",
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
            "Two weeks",
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
            "Two weeks",
            "Yes",
            "No",
            "Next week",
            "First one",
            "Yes",
            "John Smith",
            "No use a different number",
            "07700900123",
        ],
        "expected": {
            "flow_completed": True,
            "booking_confirmed": True,
        },
    },

    {
        "id": "5.3",
        "phase": "Phase 5 — Phone Number",
        "name": "Number read back correctly",
        "responses": [
            "Back pain",
            "Two weeks",
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
            "Two weeks",
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
            "Two weeks",
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
            "Two weeks",
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
            "Two weeks",
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
            "Two weeks",
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
            "Two weeks",
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
            "Two weeks",
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
            "Two weeks",
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
            "About two weeks",
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
            # Threshold 25 s: transcript-injection injects at TURN_WAIT=22 s
            # intervals, so max_gap between consecutive injects is ~22 s.
            # 25 s passes normally but catches scenarios where turns are dropped
            # (gap jumps to 44+ s).  A threshold of 5 s is only appropriate for
            # real-time audio analysis where Susie's response latency is measured.
            "no_dead_air_over_seconds": 25,
        },
    },

    {
        "id": "8.2",
        "phase": "Phase 8 — Full End to End",
        "name": "Clean run 2 — returning knee pain",
        "responses": [
            "Knee injury from running",
            "Three months",
            "Yes please",
            "Yeah I've been before",
            "Any afternoon next week",
            "The second one",
            "Yes",
            "Sarah Johnson",
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
        "id": "8.3",
        "phase": "Phase 8 — Full End to End",
        "name": "Clean run 3 — new informal",
        "responses": [
            "My shoulder's been killing me",
            "Few weeks",
            "Yeah",
            "Nah",
            "Next week mornings",
            "Last one",
            "Yeah",
            "Mike Brown",
            "Yep",
        ],
        "expected": {
            "flow_completed": True,
            "no_question_asked_twice": True,
            "no_technical_error": True,
            "booking_confirmed": True,
        },
    },

    {
        "id": "8.4",
        "phase": "Phase 8 — Full End to End",
        "name": "Clean run 4 — new patient hip",
        "responses": [
            "Hip pain when walking",
            "About a month",
            "Yes",
            "No never",
            "Wednesday morning",
            "Second one",
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
            "On and off for a year",
            "Yes that sounds fine",
            "Yes I have been before",
            "Friday afternoon please",
            "The first one",
            "Yes that's correct",
            "David Wilson",
            "Yes please",
        ],
        "expected": {
            "flow_completed": True,
            "no_question_asked_twice": True,
            "no_technical_error": True,
            "booking_confirmed": True,
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
