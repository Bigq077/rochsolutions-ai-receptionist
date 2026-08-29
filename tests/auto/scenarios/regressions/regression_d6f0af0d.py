"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_d6f0af0d',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_d6f0af0d (score 2, tags ['dead_end', 'booking_error'])",
 'responses': ["um yeah i'd like to book an appointment",
               'um have you got anything on a tuesday',
               'uh yeah go on then can you show me the dates on the 8th then please',
               'no none of those work'],
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
                {'role': 'user', 'text': 'um have you got anything on a tuesday'},
                {'role': 'assistant',
                 'text': 'The available slot for Tuesday 1st September is nine in the morning. '
                         "Does that work? I've also got other Tuesdays, the 8th, the 15th and the "
                         '22nd, if any of those would suit.'},
                {'role': 'user',
                 'text': 'uh yeah go on then can you show me the dates on the 8th then please'},
                {'role': 'assistant', 'text': 'Just getting that for you…'},
                {'role': 'assistant',
                 'text': 'The available slots for Tuesday 8th September are — Number 1, nine in '
                         'the morning.'},
                {'role': 'assistant', 'text': 'Number 2, ten in the morning. Any of those work?'},
                {'role': 'user', 'text': 'no none of those work'},
                {'role': 'assistant', 'text': 'One moment…'},
                {'role': 'assistant',
                 'text': 'No problem — would the week of the 14th of September suit you better?'}],
 'source': {'call_sid_slug': 'regression_d6f0af0d',
            'quality_score': 2,
            'failure_tags': ['dead_end', 'booking_error'],
            'rubric_version': 'v2'}}
