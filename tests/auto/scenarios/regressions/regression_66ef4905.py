"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_66ef4905',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_66ef4905 (score 1, tags ['missed_escalation', 'loop', "
         "'dead_end', 'caller_frustration'])",
 'responses': ['yeah i do',
               'yeah',
               'yeah hello',
               "what's happening",
               "yeah i said i had a problem with my bowel and my bladder control what's happening"],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Joint Venture Physiotherapy's AI receptionist — how "
                         'can I help you today?'},
                {'role': 'assistant',
                 'text': "I'm sorry to hear that. Before we look at the next step, can I ask — do "
                         'you have any numbness around the saddle area between your legs, or any '
                         'changes in your bladder or bowel control?'},
                {'role': 'user', 'text': 'yeah i do'},
                {'role': 'assistant',
                 'text': 'Sorry, I had a bit of a blip there -- could you give me just a moment '
                         'and try again?'},
                {'role': 'assistant', 'text': 'Right with you…'},
                {'role': 'assistant', 'text': 'One moment…'},
                {'role': 'assistant',
                 'text': "Sorry, I can't quite hear you — how can I help today?"},
                {'role': 'user', 'text': 'yeah'},
                {'role': 'assistant',
                 'text': 'Sorry, I had a bit of a blip there -- could you give me just a moment '
                         'and try again?'},
                {'role': 'user', 'text': 'yeah hello'},
                {'role': 'assistant',
                 'text': 'Sorry, I had a bit of a blip there -- could you give me just a moment '
                         'and try again?'},
                {'role': 'user', 'text': "what's happening"},
                {'role': 'assistant',
                 'text': 'Sorry, I had a bit of a blip there -- could you give me just a moment '
                         'and try again?'},
                {'role': 'user',
                 'text': "yeah i said i had a problem with my bowel and my bladder control what's "
                         'happening'},
                {'role': 'assistant',
                 'text': 'Sorry, I had a bit of a blip there -- could you give me just a moment '
                         'and try again?'},
                {'role': 'assistant', 'text': 'One moment…'},
                {'role': 'assistant', 'text': 'Just getting that for you…'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't catch that. Anything else you'd like to know?"}],
 'source': {'call_sid_slug': 'regression_66ef4905',
            'quality_score': 1,
            'failure_tags': ['missed_escalation', 'loop', 'dead_end', 'caller_frustration'],
            'rubric_version': 'v2'}}
