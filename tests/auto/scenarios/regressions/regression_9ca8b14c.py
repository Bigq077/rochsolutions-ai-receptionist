"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_9ca8b14c',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_9ca8b14c (score 1, tags ['loop', 'dead_end', "
         "'caller_frustration'])",
 'responses': ['I need to change my appointment',
               'No use a different number',
               'No use a different number [PHONE]',
               'Wednesday mornings',
               'Second one',
               'Yes'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Joint Venture Physiotherapy's AI receptionist — how "
                         'can I help you today?'},
                {'role': 'user', 'text': 'I need to change my appointment'},
                {'role': 'assistant', 'text': 'Sorry, still with you —'},
                {'role': 'assistant', 'text': "Let's get that moved for you."},
                {'role': 'assistant',
                 'text': "I've got you on oh seven three eight oh, eight four one, four six eight "
                         '— is that the number the appointment was booked under?'},
                {'role': 'assistant',
                 'text': 'No problem — please type the number on your keypad now. You can press '
                         'the star key to reset at any time.'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         'or cancel an appointment? Please type the number on your keypad now. You '
                         'can press the star key to reset at any time.'},
                {'role': 'user', 'text': 'No use a different number'},
                {'role': 'assistant',
                 'text': 'No problem — go ahead and type the number on your keypad.'},
                {'role': 'assistant', 'text': 'You can press the star key to reset at any time.'},
                {'role': 'user', 'text': 'No use a different number [PHONE]'},
                {'role': 'assistant', 'text': 'Still with you —'},
                {'role': 'assistant', 'text': 'Got it — let me look that up for you.'},
                {'role': 'assistant',
                 'text': "I couldn't find any upcoming appointment under that number. Are you sure "
                         "that's the number the booking was made under?"},
                {'role': 'user', 'text': 'Wednesday mornings'},
                {'role': 'assistant',
                 'text': 'I think there may be a bit of a mix-up — I need the phone number the '
                         'appointment was booked under, not a day or time. Could you double-check '
                         'the number for me?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Could you double-check the number for me?"},
                {'role': 'user', 'text': 'Second one'},
                {'role': 'assistant', 'text': 'Sorry, still with you —'},
                {'role': 'assistant', 'text': 'Still with you —'},
                {'role': 'assistant',
                 'text': "I'm not quite following — could you confirm the phone number the "
                         "appointment was booked under? If you'd like to type it on your keypad, "
                         'go ahead, and press the star key to reset at any time.'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': 'I want to make sure I find the right appointment for you. Could you let '
                         'me know — are you looking to book, reschedule, or cancel?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         'or cancel an appointment? Could you let me know — are you looking to '
                         'book, reschedule, or cancel?'}],
 'source': {'call_sid_slug': 'regression_9ca8b14c',
            'quality_score': 1,
            'failure_tags': ['loop', 'dead_end', 'caller_frustration'],
            'rubric_version': 'v2'}}
