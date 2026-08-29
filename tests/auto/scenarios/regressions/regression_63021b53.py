"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_63021b53',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_63021b53 (score 1, tags ['loop', 'dead_end', 'booking_error', "
         "'caller_frustration'])",
 'responses': ['Can I change the time of my appointment?',
               'Yes use this number',
               "Yes that's right",
               'Any morning next week',
               'The first one',
               'Yes please go ahead',
               'Yes'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Joint Venture Physiotherapy's AI receptionist — how "
                         'can I help you today?'},
                {'role': 'user', 'text': 'Can I change the time of my appointment?'},
                {'role': 'assistant',
                 'text': "Let's get that moved for you. I've got you on oh seven seven, zero zero, "
                         'nine zero zero, four seven three — is that the number the appointment '
                         'was booked under?'},
                {'role': 'assistant', 'text': 'Let me find you —'},
                {'role': 'user', 'text': 'Yes use this number'},
                {'role': 'assistant',
                 'text': 'I can see an appointment on Wednesday the 26th of August at half past '
                         'eight in the evening — is that the right one?'},
                {'role': 'user', 'text': "Yes that's right"},
                {'role': 'assistant',
                 'text': 'Would you like to reschedule this appointment, or cancel it altogether?'},
                {'role': 'user', 'text': 'Any morning next week'},
                {'role': 'assistant',
                 'text': "Do you have a preference for when you'd like to reschedule to? They said "
                         'mornings next week. Let me check availability.'},
                {'role': 'assistant',
                 'text': "I'm afraid I haven't got any morning slots available next week. The next "
                         'availability I have is evening slots — would any of those work for you '
                         'instead?'},
                {'role': 'user', 'text': 'The first one'},
                {'role': 'assistant', 'text': "Right, let's see —"},
                {'role': 'assistant',
                 'text': "Here's what we've got coming up — Number 1, Monday 24th August — six in "
                         'the evening.'},
                {'role': 'assistant',
                 'text': 'Number 2, Wednesday 26th August — half past eight in the evening. Either '
                         'of those suit you?'},
                {'role': 'user', 'text': 'Yes please go ahead'},
                {'role': 'assistant',
                 'text': "I need to check what's available for mornings next week first."},
                {'role': 'assistant', 'text': 'Let me have a look —'},
                {'role': 'assistant',
                 'text': "I'm sorry, but there are no morning slots available next week. The "
                         'earliest availability is Monday the 24th of August at six in the '
                         'evening. Would you like to go ahead with that, or would you prefer a '
                         'different time?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': "Let me check what's available for next week mornings."},
                {'role': 'assistant', 'text': 'Okay, one sec —'},
                {'role': 'assistant',
                 'text': "I'm afraid there are no morning slots available next week. Here's what "
                         "we've got coming up — Number 1, Monday 24th August — six in the "
                         'evening.'},
                {'role': 'assistant',
                 'text': 'Number 2, Wednesday 26th August — half past eight in the evening. Either '
                         'of those suit you?'},
                {'role': 'assistant', 'text': 'Let me do that now.'},
                {'role': 'assistant', 'text': 'Right, pulling you up —'},
                {'role': 'assistant',
                 'text': "I need to check what's available for next week mornings before I can "
                         'proceed with the reschedule.'}],
 'source': {'call_sid_slug': 'regression_63021b53',
            'quality_score': 1,
            'failure_tags': ['loop', 'dead_end', 'booking_error', 'caller_frustration'],
            'rubric_version': 'v2'}}
