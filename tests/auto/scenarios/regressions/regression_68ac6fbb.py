"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_68ac6fbb',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_68ac6fbb (score 1, tags ['wrong_info', 'booking_error', "
         "'dead_end'])",
 'responses': ['hi there',
               'cancel appointment',
               'onsdag',
               'yes',
               'yes',
               'cancel altogether',
               'thank you'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Theorem Health's AI receptionist — to speak to Mark "
                         'directly press 1, otherwise how can I help you today?'},
                {'role': 'user', 'text': 'hi there'},
                {'role': 'assistant', 'text': 'Let me just check that…'},
                {'role': 'assistant', 'text': 'Hi — how can I help you today?'},
                {'role': 'user', 'text': 'cancel appointment'},
                {'role': 'assistant',
                 'text': "No problem at all. Was the appointment you'd like to cancel at our "
                         'Awlstuh or Redditch clinic?'},
                {'role': 'user', 'text': 'onsdag'},
                {'role': 'assistant', 'text': 'Right with you…'},
                {'role': 'assistant',
                 'text': "Right, Awlstuh. I've got you on zero seven seven nine three, one nine "
                         'five, five seven six — is that the number the appointment was booked '
                         'under?'},
                {'role': 'user', 'text': 'yes'},
                {'role': 'assistant', 'text': 'Right with you…'},
                {'role': 'assistant',
                 'text': "I can see an appointment on Friday the 28th of August at twelve o'clock "
                         '— is that the right one?'},
                {'role': 'user', 'text': 'yes'},
                {'role': 'assistant',
                 'text': 'Would you like to reschedule this appointment, or cancel it altogether?'},
                {'role': 'user', 'text': 'cancel altogether'},
                {'role': 'assistant', 'text': 'Let me just check that…'},
                {'role': 'assistant',
                 'text': "That's all done — your appointment has been cancelled. Confirmation text "
                         'on its way.'},
                {'role': 'user', 'text': 'thank you'},
                {'role': 'assistant', 'text': 'Right with you…'},
                {'role': 'assistant', 'text': 'Let me go ahead and action that cancellation now.'},
                {'role': 'assistant',
                 'text': "I'm sorry — it looks like I'm not finding an upcoming appointment under "
                         'that number. It may already have been cancelled, or it could be under a '
                         'different number.'}],
 'source': {'call_sid_slug': 'regression_68ac6fbb',
            'quality_score': 1,
            'failure_tags': ['wrong_info', 'booking_error', 'dead_end'],
            'rubric_version': 'v2'}}
