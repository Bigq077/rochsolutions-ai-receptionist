"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_c1395ba7',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_c1395ba7 (score 1, tags ['loop', 'dead_end', "
         "'caller_frustration'])",
 'responses': ["I'm not coming in for my appointment",
               'Yes use this number',
               "Yes that's right",
               'Yes please go ahead',
               'Yes',
               'Yes'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Joint Venture Physiotherapy's AI receptionist — how "
                         'can I help you today?'},
                {'role': 'user', 'text': "I'm not coming in for my appointment"},
                {'role': 'assistant',
                 'text': "No problem at all. I've got you on oh seven seven, zero zero, nine zero "
                         'zero, four nine one — is that the number the appointment was booked '
                         'under?'},
                {'role': 'assistant', 'text': 'Let me find you —'},
                {'role': 'user', 'text': 'Yes use this number'},
                {'role': 'assistant',
                 'text': 'I can see an appointment on Thursday the 3rd of September at half past '
                         'four in the afternoon — is that the right one?'},
                {'role': 'user', 'text': "Yes that's right"},
                {'role': 'assistant',
                 'text': '[NAME] you like to reschedule this appointment, or cancel it '
                         'altogether?'},
                {'role': 'user', 'text': 'Yes please go ahead'},
                {'role': 'assistant',
                 'text': 'I want to make sure I get this right for you — [NAME] you like to '
                         'reschedule it to a new time, or cancel it altogether?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': 'Just to confirm — [NAME] you like to move it to a different time, or '
                         'cancel it completely?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         'or cancel an appointment? Just to confirm — [NAME] you like to move it '
                         'to a different time, or cancel it completely?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': 'I want to make sure I do the right thing for you — could you say either '
                         '"reschedule" or "cancel" so I know which way to go?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. I want to make sure I do the right thing for you — "
                         'could you say either "reschedule" or "cancel" so I know which way to '
                         'go?'}],
 'source': {'call_sid_slug': 'regression_c1395ba7',
            'quality_score': 1,
            'failure_tags': ['loop', 'dead_end', 'caller_frustration'],
            'rubric_version': 'v2'}}
