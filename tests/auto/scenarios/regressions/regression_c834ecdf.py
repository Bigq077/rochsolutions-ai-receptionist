"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_c834ecdf',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_c834ecdf (score 2, tags ['dead_end', 'loop'])",
 'responses': ["um yeah i'd like to book a physio assessment",
               'um just my left ankle just a small niggle nothing serious',
               'uh yes please',
               'um have you got anything on wednesday by any chance',
               'uh what about the 22nd',
               'do you have any other slots on that day',
               'uh yes please'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Joint Venture Physiotherapy's AI receptionist — how "
                         'can I help you today?'},
                {'role': 'user', 'text': "um yeah i'd like to book a physio assessment"},
                {'role': 'assistant', 'text': 'Right with you…'},
                {'role': 'assistant', 'text': "Right — What's the appointment for?"},
                {'role': 'user',
                 'text': 'um just my left ankle just a small niggle nothing serious'},
                {'role': 'assistant', 'text': 'Just getting that for you…'},
                {'role': 'assistant',
                 'text': "I'm sorry to hear that — an ankle niggle can be surprisingly persistent, "
                         "especially if it's affecting how you're moving around. Would you like to "
                         'book an assessment so Marcus can take a proper look?'},
                {'role': 'user', 'text': 'uh yes please'},
                {'role': 'assistant',
                 'text': "Do you have a preference for when you'd like to come in?"},
                {'role': 'assistant', 'text': 'Let me get that sorted for you.'},
                {'role': 'user', 'text': 'um have you got anything on wednesday by any chance'},
                {'role': 'assistant',
                 'text': 'The available slots for Wednesday 2nd September are — Number 1, half '
                         'past five in the evening.'},
                {'role': 'assistant',
                 'text': 'Number 2, quarter past six in the evening. Any of those work?'},
                {'role': 'user', 'text': 'uh what about the 22nd'},
                {'role': 'assistant', 'text': 'One moment…'},
                {'role': 'assistant',
                 'text': "Wednesday 22nd September is fully booked, I'm afraid — the available "
                         'slots for Wednesday 2nd September are — Number 1, half past five in the '
                         'evening.'},
                {'role': 'assistant',
                 'text': 'Number 2, quarter past six in the evening. Any of those work?'},
                {'role': 'user', 'text': 'do you have any other slots on that day'},
                {'role': 'assistant',
                 'text': "I don't have any further times on that day — would you like me to look "
                         'at a different day?'},
                {'role': 'user', 'text': 'uh yes please'},
                {'role': 'assistant', 'text': 'Just getting that for you…'},
                {'role': 'assistant',
                 'text': "Here's what we've got coming up — Number 1, Thursday 27th August — half "
                         'past seven in the evening.'},
                {'role': 'assistant',
                 'text': 'Number 2, Monday 31st August — quarter to seven in the evening. Either '
                         'of those suit you?'}],
 'source': {'call_sid_slug': 'regression_c834ecdf',
            'quality_score': 2,
            'failure_tags': ['dead_end', 'loop'],
            'rubric_version': 'v2'}}
