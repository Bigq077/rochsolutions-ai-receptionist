"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_ff10f738',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_ff10f738 (score 2, tags ['loop'])",
 'responses': ["yeah hi i'd like to book an appointment",
               "um anytime really i'm flexible",
               'um what else have you got on the tuesday',
               'do you have any other availability on that day'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Theorem Health's AI receptionist — to speak to Mark "
                         'directly press 1, otherwise how can I help you today?'},
                {'role': 'user', 'text': "yeah hi i'd like to book an appointment"},
                {'role': 'assistant', 'text': 'Right with you…'},
                {'role': 'assistant', 'text': 'Just getting that for you…'},
                {'role': 'assistant', 'text': 'Right —'},
                {'role': 'assistant', 'text': 'Is this for our Awlstuh or Redditch clinic?'},
                {'role': 'assistant', 'text': 'Alcester.'},
                {'role': 'assistant',
                 'text': 'Is there a particular day or time that works best for you?'},
                {'role': 'user', 'text': "um anytime really i'm flexible"},
                {'role': 'assistant',
                 'text': "Here's what we've got coming up — Number 1, Friday 28th August — midday. "
                         'Or two in the afternoon.'},
                {'role': 'assistant',
                 'text': 'Number 2, Tuesday 1st September — nine in the morning.'},
                {'role': 'assistant',
                 'text': 'Number 3, Wednesday 2nd September — ten in the morning. Or two in the '
                         'afternoon. Any of those suit you?'},
                {'role': 'user', 'text': 'um what else have you got on the tuesday'},
                {'role': 'assistant', 'text': "I'll take a look at the schedule for you…"},
                {'role': 'assistant',
                 'text': 'The available slot for Tuesday 1st September is nine in the morning. '
                         'Does that work?'},
                {'role': 'user', 'text': 'do you have any other availability on that day'},
                {'role': 'assistant', 'text': 'Let me just check that…'},
                {'role': 'assistant',
                 'text': "That's the only slot we have on Tuesday the 1st of September — would "
                         'another day suit you?'}],
 'source': {'call_sid_slug': 'regression_ff10f738',
            'quality_score': 2,
            'failure_tags': ['loop'],
            'rubric_version': 'v2'}}
