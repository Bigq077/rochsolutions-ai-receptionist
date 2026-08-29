"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_4247e73f',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_4247e73f (score 2, tags ['booking_error', "
         "'caller_frustration'])",
 'responses': ["yeah i'd like to book a physio assessment",
               "just my left ankle but it's just a small niggle nothing serious",
               'yes please',
               'um what have you got next week',
               'Wednesday 2nd September',
               'is that all you have on that day',
               'yeah quarter past 6 works'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Joint Venture Physiotherapy's AI receptionist — how "
                         'can I help you today?'},
                {'role': 'user', 'text': "yeah i'd like to book a physio assessment"},
                {'role': 'assistant', 'text': 'Just getting that for you…'},
                {'role': 'assistant', 'text': "Right — What's the appointment for?"},
                {'role': 'user',
                 'text': "just my left ankle but it's just a small niggle nothing serious"},
                {'role': 'assistant', 'text': 'One moment…'},
                {'role': 'assistant',
                 'text': 'An ankle niggle — those can be deceptively persistent, especially if '
                         "it's been hanging around a while. Shall I get you booked in with Marcus "
                         'for an assessment?'},
                {'role': 'user', 'text': 'yes please'},
                {'role': 'assistant', 'text': 'Let me just check that…'},
                {'role': 'assistant',
                 'text': "Do you have a preference for when you'd like to come in?"},
                {'role': 'user', 'text': 'um what have you got next week'},
                {'role': 'assistant',
                 'text': "Here's what we've got coming up — Number 1, Monday 31st August — quarter "
                         'to seven in the evening.'},
                {'role': 'assistant',
                 'text': 'Number 2, Wednesday 2nd September — half past five in the evening. '
                         'Either of those suit you?'},
                {'role': 'user', 'text': 'Wednesday 2nd September'},
                {'role': 'assistant',
                 'text': "That day I've got half past five, or — let me check if there's another "
                         'time on that day. Based on the data I have, Wednesday the 2nd of '
                         'September at half past five in the evening — does that work for you?'},
                {'role': 'user', 'text': 'is that all you have on that day'},
                {'role': 'assistant', 'text': 'One moment…'},
                {'role': 'assistant', 'text': 'Let me just check what else is on that day.'},
                {'role': 'assistant',
                 'text': 'The available slots for Wednesday 2nd September are — Number 1, half '
                         'past five in the evening.'},
                {'role': 'assistant',
                 'text': 'Number 2, quarter past six in the evening. Any of those work?'},
                {'role': 'user', 'text': 'yeah quarter past 6 works'},
                {'role': 'assistant', 'text': 'Right with you…'},
                {'role': 'assistant',
                 'text': "So that's Wednesday the 2nd of September at quarter past six in the "
                         'evening — could I take your first name and surname?'}],
 'source': {'call_sid_slug': 'regression_4247e73f',
            'quality_score': 2,
            'failure_tags': ['booking_error', 'caller_frustration'],
            'rubric_version': 'v2'}}
