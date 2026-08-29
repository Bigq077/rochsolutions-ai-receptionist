"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_35db2812',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_35db2812 (score 2, tags ['wrong_info', 'booking_error', "
         "'dead_end'])",
 'responses': ["uh yeah hi there i'd like to book a physio assessment",
               'um just my left ankle nothing serious',
               'no nothing like that',
               'um anytime next week',
               "and then see how many you've got trapped for those"],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Joint Venture Physiotherapy's AI receptionist — how "
                         'can I help you today?'},
                {'role': 'user', 'text': "uh yeah hi there i'd like to book a physio assessment"},
                {'role': 'assistant', 'text': 'Sorry, still with you —'},
                {'role': 'assistant', 'text': "Right — what's the appointment for?"},
                {'role': 'user', 'text': 'um just my left ankle nothing serious'},
                {'role': 'assistant',
                 'text': 'A left ankle — that sounds like something Marcus can definitely help '
                         "with. There's one routine question I ask before booking this in — is the "
                         'area swollen, warm or red compared with the other side, and have you had '
                         'any recent surgery, illness, or a long journey sitting still?'},
                {'role': 'user', 'text': 'no nothing like that'},
                {'role': 'assistant',
                 'text': "That's reassuring. Do you have a preference for when you'd like to come "
                         'in?'},
                {'role': 'user', 'text': 'um anytime next week'},
                {'role': 'assistant',
                 'text': "Here's what we've got coming up — Number 1, Monday 31st August — half "
                         'past four in the afternoon.'},
                {'role': 'assistant',
                 'text': 'Number 2, Tuesday 1st September — quarter to six in the evening. Either '
                         'of those suit you?'},
                {'role': 'assistant', 'text': 'Sorry about that — you were saying?'},
                {'role': 'assistant', 'text': 'Still with you — which of those would you like?'},
                {'role': 'user', 'text': "and then see how many you've got trapped for those"},
                {'role': 'assistant',
                 'text': "I've got Monday the 31st of August at half past four, or Tuesday the 1st "
                         'of September at five in the evening — either of those work for you?'}],
 'source': {'call_sid_slug': 'regression_35db2812',
            'quality_score': 2,
            'failure_tags': ['wrong_info', 'booking_error', 'dead_end'],
            'rubric_version': 'v2'}}
