"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_5f09cb46',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_5f09cb46 (score 1, tags ['missed_escalation', "
         "'booking_error', 'loop'])",
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
                 'text': "Just to check — are you saying you've already been seen by a doctor or "
                         "NHS 111 about this, and you're now ready to book in with us?"},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? Just to check — are you saying you've already "
                         "been seen by a doctor or NHS 111 about this, and you're now ready to "
                         'book in with us?'},
                {'role': 'user', 'text': 'A while ago'},
                {'role': 'assistant',
                 'text': "Got it — so you've already had that checked out. That's reassuring. "
                         'Would you like to book an assessment so Marcus can take a proper look at '
                         'your lower back?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Would you like to book an assessment so Marcus can "
                         'take a proper look at your lower back?'},
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
                 'text': "I'm having trouble hearing you — you might be breaking up. Could I take "
                         'your first name and surname?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant', 'text': 'Could I take your first name and surname?'},
                {'role': 'assistant',
                 'text': 'Just your first name is fine — what should I call you?'},
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
 'source': {'call_sid_slug': 'regression_5f09cb46',
            'quality_score': 1,
            'failure_tags': ['missed_escalation', 'booking_error', 'loop'],
            'rubric_version': 'v2'}}
