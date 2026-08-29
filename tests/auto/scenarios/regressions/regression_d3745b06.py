"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_d3745b06',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_d3745b06 (score 2, tags ['booking_error', 'loop', "
         "'caller_frustration'])",
 'responses': ["um yeah hi there i'd like to book an appointment",
               'um anything tuesday morning',
               'yeah the 8th works for me',
               'aye yeah what else do you have that day',
               'uh what have you got on tuesday the 8th',
               'uh yeah 4 in the afternoon works'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Theorem Health's AI receptionist — to speak to Mark "
                         'directly press 1, otherwise how can I help you today?'},
                {'role': 'user', 'text': "um yeah hi there i'd like to book an appointment"},
                {'role': 'assistant', 'text': 'Right —'},
                {'role': 'assistant', 'text': 'Is this for our Awlstuh or Redditch clinic?'},
                {'role': 'assistant', 'text': 'Alcester.'},
                {'role': 'assistant',
                 'text': 'Is there a particular day or time that works best for you?'},
                {'role': 'user', 'text': 'um anything tuesday morning'},
                {'role': 'assistant',
                 'text': 'The available slot for Tuesday 1st September is nine in the morning. '
                         "Does that work? I've also got other Tuesdays, the 8th, the 15th and the "
                         '22nd, if any of those would suit.'},
                {'role': 'user', 'text': 'yeah the 8th works for me'},
                {'role': 'assistant', 'text': 'One moment…'},
                {'role': 'assistant',
                 'text': 'The available slots for Tuesday 8th September are — Number 1, nine in '
                         'the morning.'},
                {'role': 'assistant',
                 'text': "Number 2, ten in the morning. And I've a few others that day if neither "
                         'suits. Any of those work?'},
                {'role': 'user', 'text': 'aye yeah what else do you have that day'},
                {'role': 'assistant', 'text': 'One moment while I check that for you…'},
                {'role': 'assistant',
                 'text': "I've given you all the mornings I have that day, I'm afraid. Tuesday 8th "
                         'September — Number 1, midday.'},
                {'role': 'assistant', 'text': 'Number 2, one in the afternoon.'},
                {'role': 'assistant',
                 'text': "Number 3, two in the afternoon. And I've a few others that day if none "
                         'of those suit. Any of those work?'},
                {'role': 'user', 'text': 'uh what have you got on tuesday the 8th'},
                {'role': 'assistant',
                 'text': 'Tuesday 8th September — Number 1, three in the afternoon.'},
                {'role': 'assistant',
                 'text': 'Number 2, four in the afternoon. Any of those work?'},
                {'role': 'user', 'text': 'uh yeah 4 in the afternoon works'},
                {'role': 'assistant',
                 'text': "So that's Tuesday the 8th of September at four in the afternoon — could "
                         'I take your first name and surname?'}],
 'source': {'call_sid_slug': 'regression_d3745b06',
            'quality_score': 2,
            'failure_tags': ['booking_error', 'loop', 'caller_frustration'],
            'rubric_version': 'v2'}}
