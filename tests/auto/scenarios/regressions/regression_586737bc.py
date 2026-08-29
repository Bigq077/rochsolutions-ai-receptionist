"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_586737bc',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_586737bc (score 1, tags ['missed_escalation', "
         "'booking_error', 'loop', 'dead_end'])",
 'responses': ["No I haven't been before",
               'Next week any morning would be fine',
               'The first one please',
               "Yes that's correct",
               '[NAME] [NAME]',
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
                 'text': 'I think there may have been a bit of a mix-up — just to check, do you '
                         'have any numbness around the saddle area between your legs, or any '
                         'changes in your bladder or bowel control?'},
                {'role': 'user', 'text': 'Next week any morning would be fine'},
                {'role': 'assistant',
                 'text': "That's reassuring — good to know. Back pain that's been bothering you is "
                         'something Marcus is well placed to help with. Would you like to book an '
                         'assessment so he can take a proper look?'},
                {'role': 'user', 'text': 'The first one please'},
                {'role': 'assistant', 'text': 'Let me see —'},
                {'role': 'assistant',
                 'text': 'Saturday 29th August — Number 1, half past nine in the morning.'},
                {'role': 'assistant',
                 'text': "Number 2, quarter past ten in the morning. Any of those work? And I've a "
                         'few others that day if neither suits.'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Any of those work?"},
                {'role': 'user', 'text': "Yes that's correct"},
                {'role': 'assistant',
                 'text': "So that's Saturday the 29th of August at half past nine in the morning — "
                         'could I take your first name and surname?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? So that's Saturday the 29th of August at half "
                         'past nine in the morning — could I take your first name and surname?'},
                {'role': 'user', 'text': '[NAME] [NAME]'},
                {'role': 'assistant',
                 'text': "Thanks [NAME] — I've got you on oh seven three eight oh, eight four one, "
                         'four six eight — is that the best number for the booking?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Thanks [NAME] — I've got you on oh seven three eight "
                         'oh, eight four one, four six eight — is that the best number for the '
                         'booking?'},
                {'role': 'user', 'text': 'Yes use this number'},
                {'role': 'assistant',
                 'text': "So that's [NAME] [NAME], Saturday the 29th of August at half past nine "
                         'in the morning — shall I go ahead and book that in?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? So that's [NAME] [NAME], Saturday the 29th of "
                         'August at half past nine in the morning — shall I go ahead and book that '
                         'in?'}],
 'source': {'call_sid_slug': 'regression_586737bc',
            'quality_score': 1,
            'failure_tags': ['missed_escalation', 'booking_error', 'loop', 'dead_end'],
            'rubric_version': 'v2'}}
