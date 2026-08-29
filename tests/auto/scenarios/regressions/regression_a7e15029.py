"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_a7e15029',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_a7e15029 (score 2, tags ['booking_error'])",
 'responses': ["um yeah i'd like to book an appointment",
               'yeah have you got anything on a tuesday',
               'do you have any other slots on that day',
               "no it doesn't"],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Theorem Health's AI receptionist — to speak to Mark "
                         'directly press 1, otherwise how can I help you today?'},
                {'role': 'user', 'text': "um yeah i'd like to book an appointment"},
                {'role': 'assistant', 'text': 'Just getting that for you…'},
                {'role': 'assistant', 'text': 'Right —'},
                {'role': 'assistant', 'text': 'Is this for our Awlstuh or Redditch clinic?'},
                {'role': 'assistant', 'text': 'Alcester.'},
                {'role': 'assistant',
                 'text': 'Is there a particular day or time that works best for you?'},
                {'role': 'user', 'text': 'yeah have you got anything on a tuesday'},
                {'role': 'assistant',
                 'text': 'The available slot for Tuesday 1st September is nine in the morning. '
                         'Does that work?'},
                {'role': 'user', 'text': 'do you have any other slots on that day'},
                {'role': 'assistant', 'text': 'One moment…'},
                {'role': 'assistant',
                 'text': "That's the only slot we have on Tuesday 1st September — nine in the "
                         'morning. Does that work for you?'},
                {'role': 'user', 'text': "no it doesn't"},
                {'role': 'assistant', 'text': 'Let me just check that…'},
                {'role': 'assistant',
                 'text': 'So the next day we have available is Tuesday 8th September — nine in the '
                         'morning. Or ten in the morning — would either of those work?'}],
 'source': {'call_sid_slug': 'regression_a7e15029',
            'quality_score': 2,
            'failure_tags': ['booking_error'],
            'rubric_version': 'v2'}}
