"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_213b63f2',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_213b63f2 (score 1, tags ['hallucination', 'dead_end', "
         "'booking_error', 'loop'])",
 'responses': ['Do you accept AXA health insurance?',
               'Yes please book me in',
               'Neck pain',
               'No',
               'Next Tuesday',
               'First one',
               'Yes',
               'Emma [NAME]',
               'Yes'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Joint Venture Physiotherapy's AI receptionist — how "
                         'can I help you today?'},
                {'role': 'user', 'text': 'Do you accept AXA health insurance?'},
                {'role': 'assistant', 'text': "One moment — I'll get that logged for [NAME] now."},
                {'role': 'user', 'text': 'Yes please book me in'},
                {'role': 'assistant', 'text': 'Right —'},
                {'role': 'assistant', 'text': "What's the appointment for?"},
                {'role': 'user', 'text': 'Neck pain'},
                {'role': 'assistant',
                 'text': "There's one routine question I ask before booking neck pain — do you get "
                         'any dizziness, blackouts or double vision when you move your neck, or '
                         'any new clumsiness in your hands or unsteadiness when you walk?'},
                {'role': 'assistant',
                 'text': 'Those symptoms alongside neck pain need a medical review before any '
                         'hands-on neck treatment — please see your GP urgently or contact NHS 111 '
                         "today. Once you've been checked, we'd be glad to help with the rehab "
                         'side.'},
                {'role': 'user', 'text': 'No'},
                {'role': 'assistant',
                 'text': "That's reassuring. Do you have a preference for when you'd like to come "
                         'in?'},
                {'role': 'user', 'text': 'Next Tuesday'},
                {'role': 'assistant',
                 'text': 'The available slot for Tuesday 25th August is eight in the evening. Does '
                         'that work?'},
                {'role': 'user', 'text': 'First one'},
                {'role': 'assistant',
                 'text': "So that's Tuesday the 25th of August at eight in the evening — could I "
                         'take your first name and surname?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? So that's Tuesday the 25th of August at eight "
                         'in the evening — could I take your first name and surname?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant', 'text': 'Thanks [NAME] — and your surname?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Thanks [NAME] — and your surname?"},
                {'role': 'user', 'text': 'Emma [NAME]'},
                {'role': 'assistant',
                 'text': "I've got you on 0 7 7 0 0, 9 0 0, 5 3 2 — is that the best number for "
                         'the booking?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? I've got you on 0 7 7 0 0, 9 0 0, 5 3 2 — is "
                         'that the best number for the booking?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': "So that's [NAME] [NAME], Tuesday the 25th of August at eight in the "
                         'evening — shall I go ahead and book that in?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. So that's [NAME] [NAME], Tuesday the 25th of August "
                         'at eight in the evening — shall I go ahead and book that in?'}],
 'source': {'call_sid_slug': 'regression_213b63f2',
            'quality_score': 1,
            'failure_tags': ['hallucination', 'dead_end', 'booking_error', 'loop'],
            'rubric_version': 'v2'}}
