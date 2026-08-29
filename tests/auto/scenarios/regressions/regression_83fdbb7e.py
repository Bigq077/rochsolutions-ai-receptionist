"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_83fdbb7e',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_83fdbb7e (score 1, tags ['booking_error', 'wrong_info', "
         "'loop', 'dead_end'])",
 'responses': ['Knee injury from running',
               'Yes please',
               'Any afternoon next week',
               'The second one',
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
                 'text': "Here's what we've got coming up — Number 1, Monday 31st August — half "
                         'past four in the afternoon.'},
                {'role': 'assistant',
                 'text': 'Number 2, Friday 4th September — half past four in the afternoon. Either '
                         "of those suit you? And I've a few others that week if neither works."},
                {'role': 'user', 'text': 'The second one'},
                {'role': 'assistant',
                 'text': "That day I've got half past four in the afternoon — does that work for "
                         'you?'},
                {'role': 'user', 'text': 'The second one'},
                {'role': 'assistant',
                 'text': "So that's Friday the 5th of September at half past four in the afternoon "
                         '— could I take your first name and surname?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? So that's Friday the 5th of September at half "
                         'past four in the afternoon — could I take your first name and surname?'},
                {'role': 'user', 'text': '[NAME] [NAME]'},
                {'role': 'assistant',
                 'text': "Thanks [NAME] — I've got you on oh seven seven, oh oh nine, oh oh seven, "
                         'eight nine — is that the best number for the booking?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': "So that's [NAME] [NAME], Friday the 5th of September at half past four "
                         'in the afternoon — shall I go ahead and book that in?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. So that's [NAME] [NAME], Friday the 5th of September "
                         'at half past four in the afternoon — shall I go ahead and book that in?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant', 'text': 'Right, booking you in —'},
                {'role': 'assistant',
                 'text': "I'm sorry — there was a problem locking that in. Please call back and "
                         "we'll get it sorted for you."}],
 'source': {'call_sid_slug': 'regression_83fdbb7e',
            'quality_score': 1,
            'failure_tags': ['booking_error', 'wrong_info', 'loop', 'dead_end'],
            'rubric_version': 'v2'}}
