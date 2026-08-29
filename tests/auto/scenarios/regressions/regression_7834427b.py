"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_7834427b',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_7834427b (score 1, tags ['hallucination', 'wrong_info', "
         "'loop', 'booking_error', 'dead_end'])",
 'responses': ['No', 'Next week', 'Next week First', 'Yes', 'Yes'],
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
                 'text': "That's reassuring. Back pain that tightens up when you've been still and "
                         'eases as you get moving is a really common pattern, and it usually '
                         'responds well with the right guidance. Would you like to book an '
                         'assessment so Marcus can take a proper look?'},
                {'role': 'user', 'text': 'Next week'},
                {'role': 'assistant',
                 'text': "Do you have a preference for when next week you'd like to come in?"},
                {'role': 'user', 'text': 'Next week First'},
                {'role': 'assistant', 'text': 'Let me see —'},
                {'role': 'assistant',
                 'text': "Here's what we've got coming up — Number 1, Monday 24th August — half "
                         'past four in the afternoon.'},
                {'role': 'assistant',
                 'text': 'Number 2, Tuesday 25th August — half past six in the evening. Either of '
                         'those suit you?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Either of those suit you?"},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant', 'text': "Right, let's see —"},
                {'role': 'assistant',
                 'text': "Here's what we've got coming up — Number 1, Monday 24th August — half "
                         'past four in the afternoon.'},
                {'role': 'assistant',
                 'text': 'Number 2, Tuesday 25th August — half past six in the evening. Either of '
                         'those suit you?'},
                {'role': 'assistant', 'text': 'Still with you — which of those would you like?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': 'Just to confirm — Monday the 24th of August at half past four in the '
                         'afternoon — could I take your first name and surname?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         'or cancel an appointment? Just to confirm — Monday the 24th of August at '
                         'half past four in the afternoon — could I take your first name and '
                         'surname?'}],
 'source': {'call_sid_slug': 'regression_7834427b',
            'quality_score': 1,
            'failure_tags': ['hallucination', 'wrong_info', 'loop', 'booking_error', 'dead_end'],
            'rubric_version': 'v2'}}
