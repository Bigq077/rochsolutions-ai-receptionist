"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_6bf07c75',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_6bf07c75 (score 1, tags ['loop', 'dead_end', 'booking_error', "
         "'caller_frustration'])",
 'responses': ["yeah i'd like to book an appointment",
               'uh afternoons',
               'do you have anything on wednesday the 2nd of september',
               'uh yeah what are the others do you have that day',
               'yeah what are the few others you have that day'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Theorem Health's AI receptionist — to speak to Mark "
                         'directly press 1, otherwise how can I help you today?'},
                {'role': 'user', 'text': "yeah i'd like to book an appointment"},
                {'role': 'assistant', 'text': 'Right —'},
                {'role': 'assistant', 'text': 'Is this for our Awlstuh or Redditch clinic?'},
                {'role': 'assistant', 'text': 'Alcester.'},
                {'role': 'assistant',
                 'text': 'Is there a particular day or time that works best for you?'},
                {'role': 'user', 'text': 'uh afternoons'},
                {'role': 'assistant',
                 'text': "Here's what we've got coming up — Number 1, Friday 28th August — two in "
                         'the afternoon.'},
                {'role': 'assistant',
                 'text': 'Number 2, Wednesday 2nd September — two in the afternoon.'},
                {'role': 'assistant',
                 'text': 'Number 3, Friday 4th September — one in the afternoon. Any of those suit '
                         'you?'},
                {'role': 'user', 'text': 'do you have anything on wednesday the 2nd of september'},
                {'role': 'assistant', 'text': 'Let me just check that…'},
                {'role': 'assistant',
                 'text': "Wednesday 2nd September — two in the afternoon. And I've a few others "
                         "that day if that doesn't suit. Does that work?"},
                {'role': 'user', 'text': 'uh yeah what are the others do you have that day'},
                {'role': 'assistant', 'text': 'Just getting that for you…'},
                {'role': 'assistant',
                 'text': "Wednesday 2nd September — two in the afternoon. And I've a few others "
                         "that day if that doesn't suit. Does that work?"},
                {'role': 'user', 'text': 'yeah what are the few others you have that day'},
                {'role': 'assistant', 'text': 'Just getting that for you…'},
                {'role': 'assistant',
                 'text': "Wednesday 2nd September — two in the afternoon. And I've a few others "
                         "that day if that doesn't suit. Does that work?"}],
 'source': {'call_sid_slug': 'regression_6bf07c75',
            'quality_score': 1,
            'failure_tags': ['loop', 'dead_end', 'booking_error', 'caller_frustration'],
            'rubric_version': 'v2'}}
