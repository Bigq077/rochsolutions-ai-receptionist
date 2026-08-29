"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_25b39d95',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_25b39d95 (score 1, tags ['loop', 'dead_end', "
         "'caller_frustration'])",
 'responses': ['I want to cancel my appointment',
               'Yes use this number',
               "Yes that's right",
               'Yes please go ahead',
               'Yes'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Joint Venture Physiotherapy's AI receptionist — how "
                         'can I help you today?'},
                {'role': 'user', 'text': 'I want to cancel my appointment'},
                {'role': 'assistant',
                 'text': "No problem at all. I've got you on oh seven seven, zero zero, nine zero "
                         'zero, two zero one — is that the number the appointment was booked '
                         'under?'},
                {'role': 'assistant', 'text': 'Let me find you —'},
                {'role': 'user', 'text': 'Yes use this number'},
                {'role': 'assistant',
                 'text': 'I can see an appointment on Saturday the 29th of August at twenty-five '
                         'past twelve — is that the right one?'},
                {'role': 'user', 'text': "Yes that's right"},
                {'role': 'assistant',
                 'text': 'Would you like to reschedule this appointment, or cancel it altogether?'},
                {'role': 'user', 'text': 'Yes please go ahead'},
                {'role': 'assistant',
                 'text': 'I want to make sure I get this right for you — would you like to '
                         'reschedule it to a new time, or cancel it altogether?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': 'Just to make sure I action the right thing for you — would you like to '
                         'move the appointment to a new time, or cancel it completely?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         'or cancel an appointment? Just to make sure I action the right thing for '
                         'you — would you like to move the appointment to a new time, or cancel it '
                         'completely?'}],
 'source': {'call_sid_slug': 'regression_25b39d95',
            'quality_score': 1,
            'failure_tags': ['loop', 'dead_end', 'caller_frustration'],
            'rubric_version': 'v2'}}
