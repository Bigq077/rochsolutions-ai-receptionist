"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_1118b4bb',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_1118b4bb (score 1, tags ['loop', 'booking_error', "
         "'wrong_info', 'dead_end', 'caller_frustration'])",
 'responses': ['Knee injury from running',
               'Yes please',
               'Any afternoon next week',
               'The second one',
               '[NAME] [NAME]',
               'Yes',
               'Yes',
               'Yes',
               'Yes',
               'Yes',
               'Yes',
               'Yes',
               'Yes',
               'Yes',
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
                 'text': "I'm afraid next week's afternoons are fully booked — but I do have some "
                         "evening slots available. Here's what we've got coming up — Number 1, "
                         'Monday 24th August — half past seven in the evening.'},
                {'role': 'assistant',
                 'text': 'Number 2, Tuesday 25th August — eight in the evening. Either of those '
                         'suit you?'},
                {'role': 'user', 'text': 'The second one'},
                {'role': 'assistant',
                 'text': "That's Tuesday the 26th at eight in the evening — could I take your "
                         'first name and surname?'},
                {'role': 'user', 'text': '[NAME] [NAME]'},
                {'role': 'assistant',
                 'text': "Thanks [NAME] — I've got you on oh seven seven, zero zero, nine zero "
                         'zero, three four two — is that the best number for the booking?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': "So that's [NAME] [NAME], Tuesday the 26th of August at eight in the "
                         'evening — shall I go ahead and book that in?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? So that's [NAME] [NAME], Tuesday the 26th of "
                         'August at eight in the evening — shall I go ahead and book that in?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant', 'text': 'Right, booking you in —'},
                {'role': 'assistant',
                 'text': 'The slot I have is actually Tuesday the 25th of August at eight in the '
                         "evening. Is that the one you'd like?"},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': 'Just to double-check — the slot is Tuesday the 25th of August at eight '
                         "in the evening. Is that the one you'd like?"},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Is that the one you'd like?"},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant', 'text': 'Still with you —'},
                {'role': 'assistant',
                 'text': 'Just to confirm — the slot is Tuesday the 25th of August at eight in the '
                         "evening. Is that the one you'd like?"},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': "I've got you on 0 7 7 0 0, 9 0 0, 3 4 2 — is that the best number for "
                         'the booking?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? I've got you on 0 7 7 0 0, 9 0 0, 3 4 2 — is "
                         'that the best number for the booking?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': "So that's [NAME] [NAME], Tuesday the 26th of August at eight in the "
                         'evening — shall I go ahead and book that in?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. So that's [NAME] [NAME], Tuesday the 26th of August "
                         'at eight in the evening — shall I go ahead and book that in?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant', 'text': 'Getting that in the diary —'},
                {'role': 'assistant',
                 'text': "Just to make sure we've got the right date — the slot I have is Tuesday "
                         "the 25th of August at eight in the evening. Is that the one you'd like?"},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': "So that's [NAME] [NAME], Tuesday the 26th of August at eight in the "
                         'evening — shall I go ahead and book that in?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? So that's [NAME] [NAME], Tuesday the 26th of "
                         'August at eight in the evening — shall I go ahead and book that in?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant', 'text': 'Right, booking you in —'},
                {'role': 'assistant',
                 'text': 'Just to be clear before I lock it in — the slot I have is Tuesday the '
                         "25th of August at eight in the evening. Is that the one you'd like?"},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': "Just to make sure we've got this right — the slot is Tuesday the 25th of "
                         "August at eight in the evening. Is that the one you'd like?"},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Is that the one you'd like?"},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': "I've got you on oh seven seven zero zero, nine zero zero, three four two "
                         '— is that the best number for the booking?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? I've got you on oh seven seven zero zero, nine "
                         'zero zero, three four two — is that the best number for the booking?'}],
 'source': {'call_sid_slug': 'regression_1118b4bb',
            'quality_score': 1,
            'failure_tags': ['loop',
                             'booking_error',
                             'wrong_info',
                             'dead_end',
                             'caller_frustration'],
            'rubric_version': 'v2'}}
