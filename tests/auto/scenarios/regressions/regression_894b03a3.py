"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_894b03a3',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_894b03a3 (score 1, tags ['booking_error', 'wrong_info', "
         "'dead_end'])",
 'responses': ['Knee injury from running',
               'Yes please',
               'Any afternoon next week',
               'The second one',
               '[NAME] [NAME]',
               'Yes',
               'Yes'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Joint Venture Physiotherapy's AI receptionist — how "
                         'can I help you today?'},
                {'role': 'user', 'text': 'Knee injury from running'},
                {'role': 'assistant',
                 'text': "I'm sorry to hear that — a running knee injury can really stop you in "
                         'your tracks. Would you like to book an assessment so Marcus can take a '
                         'proper look?'},
                {'role': 'user', 'text': 'Yes please'},
                {'role': 'assistant', 'text': 'Right —'},
                {'role': 'assistant',
                 'text': 'Is there a particular day or time that works best for you?'},
                {'role': 'user', 'text': 'Any afternoon next week'},
                {'role': 'assistant',
                 'text': "Here's what we've got coming up — Number 1, Monday 24th August — six in "
                         'the evening.'},
                {'role': 'assistant',
                 'text': 'Number 2, Wednesday 26th August — quarter past six in the evening. '
                         "Either of those suit you? And I've a few more times that week if neither "
                         'works.'},
                {'role': 'user', 'text': 'The second one'},
                {'role': 'assistant',
                 'text': "That's Wednesday the 27th at quarter past six in the evening — could I "
                         'take your first name and surname?'},
                {'role': 'user', 'text': '[NAME] [NAME]'},
                {'role': 'assistant',
                 'text': "Thanks [NAME] — I've got you on oh seven seven, oh oh, nine oh oh, four "
                         'seven one — is that the best number for the booking?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': "So that's [NAME] [NAME], Wednesday the 27th of August at quarter past "
                         'six in the evening — shall I go ahead and book that in?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? So that's [NAME] [NAME], Wednesday the 27th of "
                         'August at quarter past six in the evening — shall I go ahead and book '
                         'that in?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant', 'text': 'Right, booking you in —'},
                {'role': 'assistant',
                 'text': "I'm sorry — there was a problem locking that in. Please call back and "
                         "we'll get it sorted for you."}],
 'source': {'call_sid_slug': 'regression_894b03a3',
            'quality_score': 1,
            'failure_tags': ['booking_error', 'wrong_info', 'dead_end'],
            'rubric_version': 'v2'}}
