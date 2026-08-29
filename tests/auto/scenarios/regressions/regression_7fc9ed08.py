"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_7fc9ed08',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_7fc9ed08 (score 1, tags ['loop', 'booking_error', 'dead_end', "
         "'hallucination'])",
 'responses': ['No', 'Next week', 'Second one', 'Yes', 'Yes'],
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
                {'role': 'user', 'text': 'No'},
                {'role': 'assistant',
                 'text': "That's reassuring. Would you like to book an assessment so Marcus can "
                         'take a proper look?'},
                {'role': 'user', 'text': 'Next week'},
                {'role': 'assistant', 'text': 'Right —'},
                {'role': 'user', 'text': 'Second one'},
                {'role': 'assistant', 'text': "What's the appointment for?"},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. What's the appointment for?"},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant', 'text': 'Let me see —'},
                {'role': 'assistant',
                 'text': "Here's what we've got coming up — Number 1, Monday 24th August — half "
                         'past four in the afternoon.'},
                {'role': 'assistant',
                 'text': 'Number 2, Tuesday 25th August — half past six in the evening. Either of '
                         'those suit you?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         'or cancel an appointment? Either of those suit you?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant', 'text': "Right, let's see —"},
                {'role': 'assistant',
                 'text': 'Which one would you prefer? Number 1, Monday 24th August at half past '
                         'four in the afternoon, or'},
                {'role': 'assistant',
                 'text': 'Number 2, Tuesday 25th August at half past six in the evening?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Number 1, Monday 24th August at half past four in the "
                         'afternoon, or Number 2, Tuesday 25th August at half past six in the '
                         'evening?'}],
 'source': {'call_sid_slug': 'regression_7fc9ed08',
            'quality_score': 1,
            'failure_tags': ['loop', 'booking_error', 'dead_end', 'hallucination'],
            'rubric_version': 'v2'}}
