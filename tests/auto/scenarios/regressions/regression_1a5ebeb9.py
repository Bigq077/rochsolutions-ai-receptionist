"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_1a5ebeb9',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_1a5ebeb9 (score 1, tags ['wrong_info', 'loop', "
         "'booking_error', 'dead_end'])",
 'responses': ['No', 'Next week mornings', 'First one', 'Yes', 'Yes'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Joint Venture Physiotherapy's AI receptionist — how "
                         'can I help you today?'},
                {'role': 'assistant',
                 'text': "I'm having trouble hearing you — you might be breaking up — how can I "
                         'help today?'},
                {'role': 'assistant',
                 'text': "Sorry, I can't quite hear you — how can I help today?"},
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
                {'role': 'user', 'text': 'No'},
                {'role': 'assistant',
                 'text': "That's reassuring. Would you like to book an assessment so Marcus can "
                         'take a proper look?'},
                {'role': 'user', 'text': 'Next week mornings'},
                {'role': 'assistant', 'text': 'Let me see —'},
                {'role': 'assistant',
                 'text': "I'm afraid I haven't got any morning slots available next week — the "
                         'earliest I have are evenings. Would an evening work instead?'},
                {'role': 'user', 'text': 'First one'},
                {'role': 'assistant', 'text': 'Still with you —'},
                {'role': 'assistant',
                 'text': "Here's what we've got coming up — Number 1, Monday 24th August — half "
                         'past seven in the evening.'},
                {'role': 'assistant',
                 'text': 'Number 2, Tuesday 25th August — eight in the evening. Either of those '
                         'suit you?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': 'Just to confirm — [NAME] one would you like, Monday the 24th at half '
                         'past seven, or Tuesday the 25th at eight in the evening?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Just to confirm — [NAME] one would you like, Monday "
                         'the 24th at half past seven, or Tuesday the 25th at eight in the '
                         'evening?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         'or cancel an appointment? Just to confirm — [NAME] one would you like, '
                         'Monday the 24th at half past seven, or Tuesday the 25th at eight in the '
                         'evening?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': '[NAME] one works best — Monday the 24th at half past seven, or Tuesday '
                         'the 25th at eight in the evening?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. [NAME] one works best — Monday the 24th at half past "
                         'seven, or Tuesday the 25th at eight in the evening?'}],
 'source': {'call_sid_slug': 'regression_1a5ebeb9',
            'quality_score': 1,
            'failure_tags': ['wrong_info', 'loop', 'booking_error', 'dead_end'],
            'rubric_version': 'v2'}}
