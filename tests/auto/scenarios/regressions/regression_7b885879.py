"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_7b885879',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_7b885879 (score 1, tags ['loop', 'dead_end', 'booking_error', "
         "'caller_frustration'])",
 'responses': ['I need to cancel',
               'Yes this number',
               "Yes that's right",
               'Yes please go ahead',
               'Yes',
               'Yes'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Joint Venture Physiotherapy's AI receptionist — how "
                         'can I help you today?'},
                {'role': 'user', 'text': 'I need to cancel'},
                {'role': 'assistant',
                 'text': "No problem at all. I've got you on oh seven seven, zero zero, nine zero "
                         'zero, four nine three — is that the number the appointment was booked '
                         'under?'},
                {'role': 'assistant', 'text': 'Let me find you —'},
                {'role': 'user', 'text': 'Yes this number'},
                {'role': 'assistant',
                 'text': 'I can see an appointment on Friday the 4th of September at half past '
                         'four in the afternoon — is that the right one?'},
                {'role': 'user', 'text': "Yes that's right"},
                {'role': 'assistant',
                 'text': 'Would you like to reschedule this appointment, or cancel it altogether?'},
                {'role': 'user', 'text': 'Yes please go ahead'},
                {'role': 'assistant', 'text': 'Let me look that up for you.'},
                {'role': 'assistant', 'text': 'Right, pulling you up —'},
                {'role': 'assistant',
                 'text': '" — a bare "yes" is ambiguous here. I need to clarify. Just to check — '
                         'did you want to cancel the appointment altogether, or would you like to '
                         'move it to a different time?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': 'Just to check — would you like to cancel the appointment, or move it to '
                         'a new time?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         'or cancel an appointment? Just to check — would you like to cancel the '
                         'appointment, or move it to a new time?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': 'I want to make sure I get this right for you — did you want to cancel '
                         'the appointment, or reschedule it to a different day?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. I want to make sure I get this right for you — did "
                         'you want to cancel the appointment, or reschedule it to a different '
                         'day?'}],
 'source': {'call_sid_slug': 'regression_7b885879',
            'quality_score': 1,
            'failure_tags': ['loop', 'dead_end', 'booking_error', 'caller_frustration'],
            'rubric_version': 'v2'}}
