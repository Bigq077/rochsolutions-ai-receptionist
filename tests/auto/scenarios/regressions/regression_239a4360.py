"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_239a4360',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_239a4360 (score 1, tags ['hallucination', 'wrong_info', "
         "'loop', 'booking_error', 'dead_end'])",
 'responses': ['No',
               'Next week',
               'First one',
               'Yes',
               'No use a different number',
               'No use a different number [PHONE]',
               'Yes'],
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
                {'role': 'user', 'text': 'First one'},
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
                {'role': 'user', 'text': 'No use a different number'},
                {'role': 'assistant',
                 'text': 'No problem — go ahead and type the number on your keypad. You can press '
                         'the star key to reset at any time.'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. You can press the star key to reset at any time."},
                {'role': 'user', 'text': 'No use a different number [PHONE]'},
                {'role': 'assistant',
                 'text': "Got it — let me check what's available next week for you."},
                {'role': 'assistant', 'text': "Right, let's see —"},
                {'role': 'assistant',
                 'text': "Here's what we've got coming up — Number 1, Monday 24th August — half "
                         'past four in the afternoon.'},
                {'role': 'assistant',
                 'text': 'Number 2, Tuesday 25th August — half past six in the evening. Either of '
                         'those suit you?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': "So that's Monday the 24th of August at half past four in the afternoon — "
                         'could I take your first name and surname?'},
                {'role': 'assistant',
                 'text': "I'm having trouble hearing you — you might be breaking up. Could I take "
                         'your first name and surname?'}],
 'source': {'call_sid_slug': 'regression_239a4360',
            'quality_score': 1,
            'failure_tags': ['hallucination', 'wrong_info', 'loop', 'booking_error', 'dead_end'],
            'rubric_version': 'v2'}}
