"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_3bab2f2e',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_3bab2f2e (score 1, tags ['missed_escalation', 'loop', "
         "'dead_end', 'booking_error', 'caller_frustration'])",
 'responses': ["No I haven't been before",
               'Next week any morning would be fine',
               'The first one please',
               "Yes that's correct",
               'Yes use this number'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Joint Venture Physiotherapy's AI receptionist — how "
                         'can I help you today?'},
                {'role': 'assistant',
                 'text': "I'm sorry to hear that. There's one routine question I ask everyone "
                         'before booking back pain — do you have any numbness around the saddle '
                         'area between your legs, or any changes in your bladder or bowel '
                         'control?'},
                {'role': 'assistant',
                 'text': "Sorry about that — could you say that again for me? I'm sorry to hear "
                         "that. There's one routine question I ask everyone before booking back "
                         'pain — do you have any numbness around the saddle area between your '
                         'legs, or any changes in your bladder or bowel control?'},
                {'role': 'assistant',
                 'text': 'Those particular symptoms need checking urgently rather than waiting for '
                         'a physio appointment — please contact NHS 111 now, or go straight to A&E '
                         "if it's severe. I won't book you in just yet, but do call us back once "
                         "you've been seen and we'll get you sorted."},
                {'role': 'user', 'text': "No I haven't been before"},
                {'role': 'assistant',
                 'text': 'I think there may have been a bit of a mix-up — I was asking whether you '
                         'have any numbness around the saddle area or changes in your bladder or '
                         'bowel control alongside your back pain. Could you let me know — do you '
                         'have any of those symptoms?'},
                {'role': 'user', 'text': 'Next week any morning would be fine'},
                {'role': 'assistant',
                 'text': "Just to make sure I've got this right — do you have any numbness around "
                         'the saddle area between your legs, or any changes in your bladder or '
                         'bowel control?'},
                {'role': 'user', 'text': 'The first one please'},
                {'role': 'assistant', 'text': "That's reassuring — thanks for clarifying."},
                {'role': 'assistant',
                 'text': 'Would you like to book an assessment so Marcus can take a proper look at '
                         'your back?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Would you like to book an assessment so Marcus can "
                         'take a proper look at your back?'},
                {'role': 'user', 'text': "Yes that's correct"},
                {'role': 'assistant', 'text': 'Let me see —'},
                {'role': 'assistant',
                 'text': 'Saturday 29th August — Number 1, half past nine in the morning.'},
                {'role': 'assistant',
                 'text': "Number 2, quarter past ten in the morning. Any of those work? And I've a "
                         'few others that day if neither suits.'},
                {'role': 'assistant', 'text': 'Still with you — which of those would you like?'},
                {'role': 'user', 'text': 'Yes use this number'},
                {'role': 'assistant', 'text': "Right, let's see —"},
                {'role': 'assistant',
                 'text': 'Saturday 29th August — Number 1, half past nine in the morning.'},
                {'role': 'assistant',
                 'text': "Number 2, quarter past ten in the morning. Any of those work? And I've a "
                         'few others that day if neither suits.'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         'or cancel an appointment? Any of those work?'}],
 'source': {'call_sid_slug': 'regression_3bab2f2e',
            'quality_score': 1,
            'failure_tags': ['missed_escalation',
                             'loop',
                             'dead_end',
                             'booking_error',
                             'caller_frustration'],
            'rubric_version': 'v2'}}
