"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_e9a87dcc',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_e9a87dcc (score 1, tags ['dead_end', 'loop', 'booking_error', "
         "'wrong_info'])",
 'responses': ['What time do you close?',
               "Yes I'd like to book an appointment",
               'Wrist pain',
               'Yes',
               'No',
               'Any morning this week',
               'The second one',
               'Yes',
               'Yes'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Joint Venture Physiotherapy's AI receptionist — how "
                         'can I help you today?'},
                {'role': 'user', 'text': 'What time do you close?'},
                {'role': 'assistant',
                 'text': "We're open evenings Monday to Friday and Saturday mornings. The last "
                         'slot each day is: Monday to Thursday, half past eight in the evening; '
                         'Friday, half past seven; and Saturday, half past one in the afternoon.'},
                {'role': 'user', 'text': "Yes I'd like to book an appointment"},
                {'role': 'assistant', 'text': 'Right —'},
                {'role': 'assistant', 'text': "What's the appointment for?"},
                {'role': 'user', 'text': 'Wrist pain'},
                {'role': 'assistant', 'text': 'Sorry, still with you —'},
                {'role': 'assistant',
                 'text': "Got it — do you have a preference for when you'd like to come in?"},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant', 'text': 'When would suit you?'},
                {'role': 'user', 'text': 'No'},
                {'role': 'assistant', 'text': 'Still with you —'},
                {'role': 'assistant',
                 'text': "Here's what we've got coming up — Number 1, Monday 24th August — half "
                         'past seven in the evening.'},
                {'role': 'assistant',
                 'text': 'Number 2, Tuesday 25th August — eight in the evening. Either of those '
                         'suit you?'},
                {'role': 'user', 'text': 'Any morning this week'},
                {'role': 'assistant',
                 'text': "We don't have any morning slots this week — Marcus works evenings Monday "
                         'to Friday and Saturday mornings. The next Saturday morning would be the '
                         "30th of August — shall I check what's available then?"},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         'or cancel an appointment? The next Saturday morning would be the 30th of '
                         "August — shall I check what's available then?"},
                {'role': 'user', 'text': 'The second one'},
                {'role': 'assistant', 'text': 'Let me have a look —'},
                {'role': 'assistant',
                 'text': "Saturday 29th August is fully booked, I'm afraid — the next Saturday "
                         'morning I have available is Saturday 5th September — Number 1, half past '
                         'nine in the morning.'},
                {'role': 'assistant',
                 'text': "Number 2, quarter past ten in the morning. Any of those work? And I've a "
                         'few others that day if neither suits.'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': 'Which one works for you — half past nine, or quarter past ten?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Which one works for you — half past nine, or quarter "
                         'past ten?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         'or cancel an appointment? Which one works for you — half past nine, or '
                         'quarter past ten?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': "So that's Saturday the 5th of September at half past nine — could I take "
                         'your first name and surname?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. So that's Saturday the 5th of September at half past "
                         'nine — could I take your first name and surname?'}],
 'source': {'call_sid_slug': 'regression_e9a87dcc',
            'quality_score': 1,
            'failure_tags': ['dead_end', 'loop', 'booking_error', 'wrong_info'],
            'rubric_version': 'v2'}}
