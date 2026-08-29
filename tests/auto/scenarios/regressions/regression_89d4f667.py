"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_89d4f667',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_89d4f667 (score 1, tags ['loop', 'caller_frustration', "
         "'booking_error'])",
 'responses': ['um yeah can i book an appointment please',
               'um do you have anything tuesday morning',
               'yeah the 8th',
               'uh what else do you have that day',
               'yeah uh what have you got on tuesday the 8th',
               'uh yeah',
               'yeah 4 in the afternoon works',
               'i said 4 in the afternoon works'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Theorem Health's AI receptionist — to speak to Mark "
                         'directly press 1, otherwise how can I help you today?'},
                {'role': 'user', 'text': 'um yeah can i book an appointment please'},
                {'role': 'assistant', 'text': 'Just getting that for you…'},
                {'role': 'assistant', 'text': 'Right —'},
                {'role': 'assistant', 'text': 'Is this for our Awlstuh or Redditch clinic?'},
                {'role': 'assistant', 'text': 'Alcester.'},
                {'role': 'assistant',
                 'text': 'Is there a particular day or time that works best for you?'},
                {'role': 'user', 'text': 'um do you have anything tuesday morning'},
                {'role': 'assistant',
                 'text': 'The available slot for Tuesday 1st September is nine in the morning. '
                         "Does that work? I've also got other Tuesdays, the 8th, the 15th and the "
                         '22nd, if any of those would suit.'},
                {'role': 'user', 'text': 'yeah the 8th'},
                {'role': 'assistant', 'text': "Let me have a look at what we've got…"},
                {'role': 'assistant',
                 'text': 'The available slots for Tuesday 8th September are — Number 1, nine in '
                         'the morning.'},
                {'role': 'assistant',
                 'text': "Number 2, ten in the morning. And I've a few others that day if neither "
                         'suits. Any of those work?'},
                {'role': 'user', 'text': 'uh what else do you have that day'},
                {'role': 'assistant', 'text': 'Let me pull that up now…'},
                {'role': 'assistant',
                 'text': "I've given you all the mornings I have that day, I'm afraid. Tuesday 8th "
                         'September — Number 1, midday.'},
                {'role': 'assistant', 'text': 'Number 2, one in the afternoon.'},
                {'role': 'assistant',
                 'text': "Number 3, two in the afternoon. And I've a few others that day if none "
                         'of those suit. Any of those work?'},
                {'role': 'user', 'text': 'yeah uh what have you got on tuesday the 8th'},
                {'role': 'assistant', 'text': 'Right with you…'},
                {'role': 'assistant',
                 'text': 'Tuesday 8th September — Number 1, nine in the morning.'},
                {'role': 'assistant', 'text': 'Number 2, three in the afternoon.'},
                {'role': 'assistant',
                 'text': 'Number 3, four in the afternoon. Any of those work?'},
                {'role': 'user', 'text': 'uh yeah'},
                {'role': 'assistant',
                 'text': 'The available slots for Tuesday 8th September are — Number 1, nine in '
                         'the morning.'},
                {'role': 'user', 'text': 'yeah 4 in the afternoon works'},
                {'role': 'assistant', 'text': 'Number 2, ten in the morning.'},
                {'role': 'assistant', 'text': 'Number 3, midday. Any of those work?'},
                {'role': 'assistant', 'text': 'Just getting that for you…'},
                {'role': 'assistant',
                 'text': "Four in the afternoon is available on Tuesday 8th September. And I've a "
                         'few others that day if none of those suit. Before I do that — could I '
                         'take your first name and surname?'},
                {'role': 'user', 'text': 'i said 4 in the afternoon works'},
                {'role': 'assistant',
                 'text': "So that's Tuesday the 8th of September at four in the afternoon — could "
                         'I take your first name and surname?'}],
 'source': {'call_sid_slug': 'regression_89d4f667',
            'quality_score': 1,
            'failure_tags': ['loop', 'caller_frustration', 'booking_error'],
            'rubric_version': 'v2'}}
