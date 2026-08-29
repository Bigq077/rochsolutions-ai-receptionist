"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_242d5496',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_242d5496 (score 1, tags ['loop', 'booking_error', "
         "'caller_frustration', 'wrong_info', 'dead_end'])",
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
                         'your tracks. Pain around the kneecap that builds with mileage is one of '
                         'the most common patterns we see in runners, and it responds really well '
                         "once Marcus has had a proper look at what's driving it. Would you like "
                         'to book an assessment so he'},
                {'role': 'assistant', 'text': 'Can take a proper look?'},
                {'role': 'user', 'text': 'Yes please'},
                {'role': 'assistant', 'text': 'Right —'},
                {'role': 'assistant',
                 'text': 'Is there a particular day or time that works best for you?'},
                {'role': 'user', 'text': 'Any afternoon next week'},
                {'role': 'assistant',
                 'text': "The earliest I can get you in is evening slots. Here's what we've got "
                         'coming up — Number 1, Monday 24th August — half past seven in the '
                         'evening.'},
                {'role': 'assistant',
                 'text': 'Number 2, Tuesday 25th August — eight in the evening. Either of those '
                         "suit you? And If not, I've got more options later in the week."},
                {'role': 'user', 'text': 'The second one'},
                {'role': 'assistant',
                 'text': "So that's Tuesday the 26th of August at eight in the evening — could I "
                         'take your first name and surname?'},
                {'role': 'user', 'text': '[NAME] [NAME]'},
                {'role': 'assistant',
                 'text': "Thanks [NAME] — I've got you on oh seven seven, zero zero, nine zero "
                         'zero, three four one — is that the best number for the booking?'},
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
                 'text': 'The slot is actually Tuesday the 26th of August at eight in the evening. '
                         "Is that the one you'd like?"},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': 'Just to double-check — the slot is Tuesday the 26th of August at eight '
                         "in the evening. Is that the one you'd like?"},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Is that the one you'd like?"},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant', 'text': 'Still with you —'},
                {'role': 'assistant',
                 'text': 'The slot I have is actually Tuesday the 26th of August at eight in the '
                         "evening. Is that the one you'd like?"},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': "I've got you on 0 7 7 0 0, 9 0 0, 3 4 1 — is that the best number for "
                         'the booking?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? I've got you on 0 7 7 0 0, 9 0 0, 3 4 1 — is "
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
                 'text': "Just to make sure we've got the right date — the slot I have is actually "
                         'Tuesday the 26th of August at eight in the evening. Is that the one '
                         "you'd like?"},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': 'Just to double-check before I lock that in — the slot I have is Tuesday '
                         "the 26th of August at eight in the evening. Is that the one you'd like?"},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? Is that the one you'd like?"},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant', 'text': 'Still with you —'},
                {'role': 'assistant',
                 'text': "Just to make sure we've got this right — the slot I have is actually "
                         'Tuesday the 26th of August at eight in the evening. Is that the one '
                         "you'd like?"},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': "Just to make sure we've got this right — the slot I have is Tuesday the "
                         "26th of August at eight in the evening. Is that the one you'd like?"},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Is that the one you'd like?"},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': "So that's [NAME] [NAME], Tuesday the 26th of August at eight in the "
                         'evening — shall I go ahead and book that in?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? So that's [NAME] [NAME], Tuesday the 26th of "
                         'August at eight in the evening — shall I go ahead and book that in?'}],
 'source': {'call_sid_slug': 'regression_242d5496',
            'quality_score': 1,
            'failure_tags': ['loop',
                             'booking_error',
                             'caller_frustration',
                             'wrong_info',
                             'dead_end'],
            'rubric_version': 'v2'}}
