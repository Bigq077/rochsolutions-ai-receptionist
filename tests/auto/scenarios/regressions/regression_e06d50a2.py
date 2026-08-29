"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_e06d50a2',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_e06d50a2 (score 1, tags ['loop', 'dead_end', 'booking_error', "
         "'caller_frustration', 'hallucination'])",
 'responses': ['Do you accept AXA health insurance?',
               'Yes please book me in',
               'Yes',
               'No',
               'Next Tuesday',
               'First one',
               'Yes',
               'Emma Wilson',
               'Yes'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Joint Venture Physiotherapy's AI receptionist — how "
                         'can I help you today?'},
                {'role': 'user', 'text': 'Do you accept AXA health insurance?'},
                {'role': 'assistant',
                 'text': 'Yes — we accept private health insurance referrals.'},
                {'role': 'assistant',
                 'text': "One moment — I'll get that logged for Jonathan now."},
                {'role': 'user', 'text': 'Yes please book me in'},
                {'role': 'assistant', 'text': 'Right —'},
                {'role': 'assistant', 'text': "What's the appointment for?"},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? What's the appointment for?"},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant', 'text': "What's the appointment for?"},
                {'role': 'user', 'text': 'No'},
                {'role': 'assistant',
                 'text': "No problem — could you tell me what you'd like to book the appointment "
                         'for, so I can find the right slot for you?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Could you tell me what you'd like to book the "
                         'appointment for, so I can find the right slot for you?'},
                {'role': 'user', 'text': 'Next Tuesday'},
                {'role': 'assistant', 'text': "Got it — next Tuesday. What's the appointment for?"},
                {'role': 'user', 'text': 'First one'},
                {'role': 'assistant',
                 'text': "Thanks — and what's the problem you're coming in for, so I can make sure "
                         'we book the right type of appointment?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? Thanks — and what's the problem you're coming "
                         'in for, so I can make sure we book the right type of appointment?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': "Just to make sure I get the right appointment booked — what's the "
                         "problem or condition you'd like to see Marcus about?"},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Just to make sure I get the right appointment booked "
                         "— what's the problem or condition you'd like to see Marcus about?"},
                {'role': 'user', 'text': 'Emma Wilson'},
                {'role': 'assistant',
                 'text': "Thanks — but I didn't quite catch what the appointment is for. What's "
                         "the problem or condition you'd like to see Marcus about?"},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? What's the problem or condition you'd like to "
                         'see Marcus about?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': "I have your name as Emma Wilson — that's great. Could you tell me what "
                         'the appointment is for, so I can book the right type of session?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Could you tell me what the appointment is for, so I "
                         'can book the right type of session?'}],
 'source': {'call_sid_slug': 'regression_e06d50a2',
            'quality_score': 1,
            'failure_tags': ['loop',
                             'dead_end',
                             'booking_error',
                             'caller_frustration',
                             'hallucination'],
            'rubric_version': 'v2'}}
