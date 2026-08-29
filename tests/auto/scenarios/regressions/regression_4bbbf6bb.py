"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_4bbbf6bb',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_4bbbf6bb (score 2, tags ['dead_end', 'booking_error'])",
 'responses': ["um yeah i'd like to book an appointment",
               'um do you have anything next tuesday by any chance',
               "no that doesn't work"],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Theorem Health's AI receptionist — to speak to Mark "
                         'directly press 1, otherwise how can I help you today?'},
                {'role': 'user', 'text': "um yeah i'd like to book an appointment"},
                {'role': 'assistant', 'text': 'One moment…'},
                {'role': 'assistant', 'text': 'Right —'},
                {'role': 'assistant', 'text': 'Is this for our Awlstuh or Redditch clinic?'},
                {'role': 'assistant', 'text': 'Alcester.'},
                {'role': 'assistant',
                 'text': 'Is there a particular day or time that works best for you?'},
                {'role': 'user', 'text': 'um do you have anything next tuesday by any chance'},
                {'role': 'assistant',
                 'text': 'The available slot for Tuesday 1st September is nine in the morning. '
                         'Does that work?'},
                {'role': 'user', 'text': "no that doesn't work"},
                {'role': 'assistant', 'text': 'Just checking the diary now…'},
                {'role': 'assistant',
                 'text': "Here's what we've got coming up — Number 1, Wednesday 2nd September — "
                         'ten in the morning.'},
                {'role': 'assistant',
                 'text': 'Number 2, Friday 4th September — nine in the morning. Or eleven in the '
                         'morning. Either of those suit you?'}],
 'source': {'call_sid_slug': 'regression_4bbbf6bb',
            'quality_score': 2,
            'failure_tags': ['dead_end', 'booking_error'],
            'rubric_version': 'v2'}}
