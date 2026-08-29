"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_0cd0f895',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_0cd0f895 (score 2, tags ['booking_error', 'dead_end'])",
 'responses': ["um yeah that's to book an appointment",
               'um have you got any availability on wednesday by any chance',
               'um giovanni baby um what about the 22nd by any chance',
               'uh could you repeat them please'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Theorem Health's AI receptionist — to speak to Mark "
                         'directly press 1, otherwise how can I help you today?'},
                {'role': 'user', 'text': "um yeah that's to book an appointment"},
                {'role': 'assistant', 'text': 'One moment…'},
                {'role': 'assistant', 'text': 'Right —'},
                {'role': 'assistant', 'text': 'Is this for our Awlstuh or Redditch clinic?'},
                {'role': 'assistant', 'text': 'Alcester.'},
                {'role': 'assistant',
                 'text': 'Is there a particular day or time that works best for you?'},
                {'role': 'user',
                 'text': 'um have you got any availability on wednesday by any chance'},
                {'role': 'assistant',
                 'text': 'The available slots for Wednesday 2nd September are — Number 1, ten in '
                         'the morning.'},
                {'role': 'assistant', 'text': 'Number 2, two in the afternoon. Any of those work?'},
                {'role': 'user', 'text': 'um giovanni baby um what about the 22nd by any chance'},
                {'role': 'assistant', 'text': 'Let me see what we have available…'},
                {'role': 'assistant',
                 'text': 'Tuesday 22nd September — Number 1, nine in the morning.'},
                {'role': 'assistant',
                 'text': "Number 2, ten in the morning. And I've a few others that day if neither "
                         'suits. Any of those work?'},
                {'role': 'user', 'text': 'uh could you repeat them please'},
                {'role': 'assistant',
                 'text': 'Tuesday the 22nd of September — Number 1, nine in the morning. Number 2, '
                         'ten in the morning. Any of those suit you?'}],
 'source': {'call_sid_slug': 'regression_0cd0f895',
            'quality_score': 2,
            'failure_tags': ['booking_error', 'dead_end'],
            'rubric_version': 'v2'}}
