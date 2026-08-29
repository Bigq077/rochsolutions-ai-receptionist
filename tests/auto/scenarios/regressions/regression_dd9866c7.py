"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_dd9866c7',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_dd9866c7 (score 1, tags ['missed_escalation', "
         "'booking_error', 'loop', 'dead_end'])",
 'responses': ['Yeah I have been before',
               'A while ago',
               'Wednesday afternoon',
               'Second one',
               'Yes',
               '[NAME] [NAME]',
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
                {'role': 'assistant',
                 'text': 'Sorry about that — could you say that again? Those particular symptoms '
                         'need checking urgently rather than waiting for a physio appointment — '
                         "please contact NHS 111 now, or go straight to A&E if it's severe. I "
                         "won't book you in just yet, but do call us back once you've been seen "
                         "and we'll get you sorted."},
                {'role': 'user', 'text': 'Yeah I have been before'},
                {'role': 'assistant',
                 'text': "Could you help me understand — are you saying you've had those symptoms "
                         "before but they've resolved, or are you currently experiencing numbness "
                         'or changes in bladder or bowel control?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         'or cancel an appointment? Could you help me understand — are you saying '
                         "you've had those symptoms before but they've resolved, or are you "
                         'currently experiencing numbness or changes in bladder or bowel control?'},
                {'role': 'user', 'text': 'A while ago'},
                {'role': 'assistant',
                 'text': "That's reassuring — if those symptoms have fully resolved and you're not "
                         'experiencing them now, we can go ahead.'},
                {'role': 'assistant',
                 'text': 'Lower back pain that flares and tightens up is something Marcus sees a '
                         'lot of, and it usually responds really well with the right guidance. '
                         'Would you like to book an assessment so he can take a proper look?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Would you like to book an assessment so he can take a "
                         'proper look?'},
                {'role': 'user', 'text': 'Wednesday afternoon'},
                {'role': 'assistant', 'text': 'Let me see —'},
                {'role': 'assistant',
                 'text': 'Wednesday 26th August — Number 1, half past five in the evening.'},
                {'role': 'assistant',
                 'text': "Number 2, quarter past six in the evening. Any of those work? And I've a "
                         'few others that day if neither suits.'},
                {'role': 'user', 'text': 'Second one'},
                {'role': 'assistant',
                 'text': "So that's Wednesday the 26th of August at quarter past six in the "
                         'evening — could I take your first name and surname?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? So that's Wednesday the 26th of August at "
                         'quarter past six in the evening — could I take your first name and '
                         'surname?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant', 'text': 'Could I take your first name and surname?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Could I take your first name and surname?"},
                {'role': 'user', 'text': '[NAME] [NAME]'},
                {'role': 'assistant',
                 'text': "Thanks [NAME] — I've got you on oh seven three eight oh, eight four one, "
                         'four six eight — is that the best number for the booking?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? Thanks [NAME] — I've got you on oh seven three "
                         'eight oh, eight four one, four six eight — is that the best number for '
                         'the booking?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': "So that's [NAME] [NAME], Wednesday the 26th of August at quarter past "
                         'six in the evening — shall I go ahead and book that in?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. So that's [NAME] [NAME], Wednesday the 26th of August "
                         'at quarter past six in the evening — shall I go ahead and book that '
                         'in?'}],
 'source': {'call_sid_slug': 'regression_dd9866c7',
            'quality_score': 1,
            'failure_tags': ['missed_escalation', 'booking_error', 'loop', 'dead_end'],
            'rubric_version': 'v2'}}
