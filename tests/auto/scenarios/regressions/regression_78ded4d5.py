"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_78ded4d5',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_78ded4d5 (score 1, tags ['booking_error', 'loop', "
         "'dead_end'])",
 'responses': ['No',
               'Next week',
               'Next week First',
               'Yes',
               '[NAME] [NAME]',
               'Yes',
               'No',
               '[PHONE]',
               'Yes',
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
                {'role': 'assistant', 'text': 'Let me see —'},
                {'role': 'assistant',
                 'text': "Here's what we've got coming up — Number 1, Monday 24th August — half "
                         'past seven in the evening.'},
                {'role': 'assistant',
                 'text': 'Number 2, Tuesday 25th August — eight in the evening. Either of those '
                         "suit you? And I've more options that week if neither works."},
                {'role': 'user', 'text': 'Next week First'},
                {'role': 'assistant', 'text': "Right, let's see —"},
                {'role': 'assistant',
                 'text': 'The available slots for Monday 24th August are — Number 1, half past '
                         'seven in the evening.'},
                {'role': 'assistant',
                 'text': 'Number 2, quarter past eight in the evening. Any of those work?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': "So that's Monday the 24th of August at half past seven in the evening — "
                         'could I take your first name and surname?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. So that's Monday the 24th of August at half past "
                         'seven in the evening — could I take your first name and surname?'},
                {'role': 'user', 'text': '[NAME] [NAME]'},
                {'role': 'assistant',
                 'text': "Thanks [NAME] — I've got you on oh seven seven, zero zero, nine zero "
                         'zero, three eight three — is that the best number for the booking?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? Thanks [NAME] — I've got you on oh seven "
                         'seven, zero zero, nine zero zero, three eight three — is that the best '
                         'number for the booking?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': "So that's [NAME] [NAME], Monday the 24th of August at half past seven in "
                         'the evening — shall I go ahead and book that in?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. So that's [NAME] [NAME], Monday the 24th of August at "
                         'half past seven in the evening — shall I go ahead and book that in?'},
                {'role': 'user', 'text': 'No'},
                {'role': 'assistant', 'text': 'No problem — what would you like to change?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         'or cancel an appointment? What would you like to change?'},
                {'role': 'user', 'text': '[PHONE]'},
                {'role': 'assistant',
                 'text': "Got it — so that's [NAME] [NAME], Monday the 24th of August at half past "
                         'seven in the evening — shall I go ahead and book that in?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Got it — so that's [NAME] [NAME], Monday the 24th of "
                         'August at half past seven in the evening — shall I go ahead and book '
                         'that in?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant', 'text': 'Getting that in the diary —'},
                {'role': 'assistant',
                 'text': "I'm sorry — there was a problem locking that in. Please call back and "
                         "we'll get it sorted for you."},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant', 'text': 'Let me try booking that in for you now.'},
                {'role': 'assistant',
                 'text': "So that's [NAME] [NAME], Monday the 24th of August at half past seven in "
                         'the evening — shall I go ahead and book that in?'}],
 'source': {'call_sid_slug': 'regression_78ded4d5',
            'quality_score': 1,
            'failure_tags': ['booking_error', 'loop', 'dead_end'],
            'rubric_version': 'v2'}}
