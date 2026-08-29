"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_2e42f07c',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_2e42f07c (score 1, tags ['loop', 'booking_error', "
         "'dead_end'])",
 'responses': ['I need to reschedule my appointment',
               "Yes that's right",
               'Any morning next week',
               'The first one',
               'Yes please go ahead',
               'Yes'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Joint Venture Physiotherapy's AI receptionist — how "
                         'can I help you today?'},
                {'role': 'user', 'text': 'I need to reschedule my appointment'},
                {'role': 'assistant',
                 'text': "Let's get that moved for you. I've got you on oh seven seven, zero zero, "
                         'nine zero oh, eight seven eight — is that the number the appointment was '
                         'booked under?'},
                {'role': 'assistant', 'text': 'Let me find you —'},
                {'role': 'user', 'text': "Yes that's right"},
                {'role': 'assistant',
                 'text': 'I can see an appointment on Tuesday the 25th of August at twenty past '
                         'seven in the evening — is that the right one?'},
                {'role': 'user', 'text': 'Any morning next week'},
                {'role': 'assistant',
                 'text': 'Now they\'ve said "any morning next week" — this is their timing '
                         'preference for the reschedule. Let me check availability for next week '
                         'mornings.'},
                {'role': 'assistant',
                 'text': "I'm afraid I haven't got any morning slots available next week. The next "
                         'availability I have is Monday the 24th of August at five past five in '
                         'the evening, or Tuesday the 25th at twenty past seven in the evening — '
                         'either of those work for you?'},
                {'role': 'user', 'text': 'The first one'},
                {'role': 'assistant',
                 'text': 'Let me check what mornings are available next week.'},
                {'role': 'assistant', 'text': "Right, let's see —"},
                {'role': 'assistant',
                 'text': "I'm afraid there are no morning slots available next week. The next "
                         'availability I have is Monday the 24th of August at five past five in '
                         'the evening, or Tuesday the 25th at twenty past seven in the evening — '
                         'either of those work for you?'},
                {'role': 'user', 'text': 'Yes please go ahead'},
                {'role': 'assistant',
                 'text': 'I need to check what service and duration the original appointment was '
                         'for to complete the reschedule. Let me look that up.'},
                {'role': 'assistant', 'text': 'Let me look you up —'},
                {'role': 'assistant',
                 'text': "Just to confirm — I'm moving your appointment to Monday the 24th of "
                         'August at five past five in the evening.'},
                {'role': 'assistant', 'text': 'Shall I go ahead and move it for you?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant', 'text': 'Moving that across —'},
                {'role': 'assistant', 'text': 'Let me check availability first.'},
                {'role': 'assistant',
                 'text': 'I need to look up your original appointment details to complete the '
                         'reschedule properly.'},
                {'role': 'assistant',
                 'text': 'It may already have been cancelled, or booked under a different number '
                         'or name. I can check another number for you, or book you a new '
                         'appointment — which would you prefer?'},
                {'role': 'assistant',
                 'text': 'I already have everything I need. Let me get that moved for you now.'},
                {'role': 'assistant', 'text': 'Right, pulling you up —'}],
 'source': {'call_sid_slug': 'regression_2e42f07c',
            'quality_score': 1,
            'failure_tags': ['loop', 'booking_error', 'dead_end'],
            'rubric_version': 'v2'}}
