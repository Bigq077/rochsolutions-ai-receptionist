"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_3db736b7',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_3db736b7 (score 1, tags ['dead_end', 'loop', 'booking_error', "
         "'wrong_info', 'caller_frustration'])",
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
                 'text': "We're open different hours each day — Monday, Tuesday, Thursday and "
                         'Friday we run evening appointments, and Saturdays we do morning slots. '
                         'What day were you thinking?'},
                {'role': 'user', 'text': "Yes I'd like to book an appointment"},
                {'role': 'assistant', 'text': 'Right —'},
                {'role': 'assistant', 'text': "What's the appointment for?"},
                {'role': 'user', 'text': 'Wrist pain'},
                {'role': 'assistant',
                 'text': "Got it — do you have a preference for when you'd like to come in?"},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant', 'text': 'When would suit you?'},
                {'role': 'user', 'text': 'No'},
                {'role': 'assistant', 'text': 'Let me see —'},
                {'role': 'assistant',
                 'text': "Here's what we've got coming up — Number 1, Monday 24th August — half "
                         'past seven in the evening.'},
                {'role': 'assistant',
                 'text': 'Number 2, Tuesday 25th August — eight in the evening. Either of those '
                         'suit you?'},
                {'role': 'user', 'text': 'Any morning this week'},
                {'role': 'assistant',
                 'text': "We don't have any morning slots this week — our Saturday morning slots "
                         'are the only mornings we offer, and this Saturday has already passed. '
                         'The next Saturday morning would be the 29th of August. Would that work, '
                         'or would you like me to check what else is available next week?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         'or cancel an appointment? Would that work, or would you like me to check '
                         'what else is available next week?'},
                {'role': 'user', 'text': 'The second one'},
                {'role': 'assistant',
                 'text': "Let me check what's available on Saturday the 29th of August."},
                {'role': 'assistant', 'text': "Right, let's see —"},
                {'role': 'assistant',
                 'text': "Saturday 29th August is fully booked, I'm afraid — Number 1, half past "
                         'nine in the morning.'},
                {'role': 'assistant',
                 'text': "Number 2, quarter past ten in the morning. Any of those work? And I've a "
                         'few others that day if neither suits.'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant', 'text': "Let me check what's available."},
                {'role': 'assistant', 'text': 'Let me have a look —'},
                {'role': 'assistant',
                 'text': "Saturday 29th August is fully booked, I'm afraid — Number 1, half past "
                         'nine in the morning.'},
                {'role': 'assistant',
                 'text': "Number 2, quarter past ten in the morning. Any of those work? And I've a "
                         'few others that day if neither suits.'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Any of those work?"},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': 'Which one works for you — half past nine, or quarter past ten?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         'or cancel an appointment? Which one works for you — half past nine, or '
                         'quarter past ten?'}],
 'source': {'call_sid_slug': 'regression_3db736b7',
            'quality_score': 1,
            'failure_tags': ['dead_end',
                             'loop',
                             'booking_error',
                             'wrong_info',
                             'caller_frustration'],
            'rubric_version': 'v2'}}
