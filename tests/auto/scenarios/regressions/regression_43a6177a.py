"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_43a6177a',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_43a6177a (score 2, tags ['loop', 'dead_end'])",
 'responses': ['yeah have you got anything on tuesday morning',
               'mornings',
               'yeah could you tell me the the slots on the 8th please',
               'yeah do you have anything else that day then',
               'um yeah could you tell me the few others you have',
               'and what about tuesday morning again'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Theorem Health's AI receptionist — to speak to Mark "
                         'directly press 1, otherwise how can I help you today?'},
                {'role': 'user', 'text': 'yeah have you got anything on tuesday morning'},
                {'role': 'assistant',
                 'text': 'Which clinic were you thinking of — Awlstuh or Redditch?'},
                {'role': 'assistant', 'text': 'Alcester.'},
                {'role': 'user', 'text': 'mornings'},
                {'role': 'assistant', 'text': "Checking what's free for you…"},
                {'role': 'assistant',
                 'text': 'The available slot for Tuesday 1st September is nine in the morning. '
                         "Does that work? I've also got other Tuesdays, the 8th, the 15th and the "
                         '22nd, if any of those would suit.'},
                {'role': 'user', 'text': 'yeah could you tell me the the slots on the 8th please'},
                {'role': 'assistant', 'text': 'Let me pull that up now…'},
                {'role': 'assistant',
                 'text': 'The available slots for Tuesday 8th September are — Number 1, nine in '
                         'the morning.'},
                {'role': 'assistant',
                 'text': "Number 2, ten in the morning. And I've a few others that day if neither "
                         'suits. Any of those work?'},
                {'role': 'user', 'text': 'yeah do you have anything else that day then'},
                {'role': 'assistant', 'text': 'Just checking the diary now…'},
                {'role': 'assistant',
                 'text': 'The available slots for Tuesday 8th September are — Number 1, nine in '
                         'the morning.'},
                {'role': 'assistant', 'text': 'Number 2, ten in the morning.'},
                {'role': 'assistant',
                 'text': "Number 3, midday. And I've a few others that day if none of those suit. "
                         'Any of those work?'},
                {'role': 'user', 'text': 'um yeah could you tell me the few others you have'},
                {'role': 'assistant',
                 'text': 'On Tuesday 8th September I also have one in the afternoon, two in the '
                         'afternoon, three in the afternoon, or four in the afternoon. Any of '
                         'those work?'},
                {'role': 'user', 'text': 'and what about tuesday morning again'},
                {'role': 'assistant',
                 'text': 'On Tuesday the 8th of September — nine in the morning, ten in the '
                         'morning, or midday. Any of those suit you?'}],
 'source': {'call_sid_slug': 'regression_43a6177a',
            'quality_score': 2,
            'failure_tags': ['loop', 'dead_end'],
            'rubric_version': 'v2'}}
