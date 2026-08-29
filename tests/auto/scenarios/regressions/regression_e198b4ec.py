"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_e198b4ec',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_e198b4ec (score 1, tags ['booking_error', 'loop', 'dead_end', "
         "'hallucination'])",
 'responses': ['How much does it cost?',
               "Yes I'd like to book please",
               'No',
               'Next week mornings',
               'First one',
               'Yes',
               '[NAME] [NAME]',
               'Yes'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Joint Venture Physiotherapy's AI receptionist — how "
                         'can I help you today?'},
                {'role': 'user', 'text': 'How much does it cost?'},
                {'role': 'assistant',
                 'text': 'An initial MSK assessment is 52 pounds for 40 minutes. Follow-up '
                         "sessions are 46 pounds for 30 minutes. We're also 10 to 20 percent more "
                         'affordable than other local physiotherapy clinics — full pricing is on '
                         'our website.'},
                {'role': 'user', 'text': "Yes I'd like to book please"},
                {'role': 'assistant', 'text': "What's the appointment for?"},
                {'role': 'assistant',
                 'text': "I'm sorry to hear that. There's one routine question I ask everyone "
                         'before booking back pain — do you have any numbness around the saddle '
                         'area between your legs, or any changes in your bladder or bowel '
                         'control?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? I'm sorry to hear that. There's one routine "
                         'question I ask everyone before booking back pain — do you have any '
                         'numbness around the saddle area between your legs, or any changes in '
                         'your bladder or bowel control?'},
                {'role': 'assistant',
                 'text': 'Those particular symptoms need checking urgently rather than waiting for '
                         'a physio appointment — please contact NHS 111 now, or go straight to A&E '
                         "if it's severe. I won't book you in just yet, but do call us back once "
                         "you've been seen and we'll get you sorted."},
                {'role': 'user', 'text': 'No'},
                {'role': 'assistant',
                 'text': "That's reassuring. Do you have a preference for when you'd like to come "
                         'in?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Do you have a preference for when you'd like to come "
                         'in?'},
                {'role': 'user', 'text': 'Next week mornings'},
                {'role': 'assistant', 'text': 'Let me see —'},
                {'role': 'assistant',
                 'text': 'The available slots for Saturday 29th August are — Number 1, half past '
                         'nine in the morning.'},
                {'role': 'assistant',
                 'text': 'Number 2, quarter to twelve in the morning. Any of those work?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         'or cancel an appointment? Any of those work?'},
                {'role': 'user', 'text': 'First one'},
                {'role': 'assistant',
                 'text': "So that's Saturday the 29th of August at half past nine in the morning — "
                         'could I take your first name and surname?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. So that's Saturday the 29th of August at half past "
                         'nine in the morning — could I take your first name and surname?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant', 'text': 'Could I take your first name and surname?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         'or cancel an appointment? Could I take your first name and surname?'},
                {'role': 'assistant',
                 'text': "I'm having trouble hearing you — you might be breaking up. Could I take "
                         'your first name and surname?'},
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
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': "So that's [NAME] [NAME], Saturday the 29th of August at half past nine "
                         'in the morning — shall I go ahead and book that in?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? So that's [NAME] [NAME], Saturday the 29th of "
                         'August at half past nine in the morning — shall I go ahead and book that '
                         'in?'}],
 'source': {'call_sid_slug': 'regression_e198b4ec',
            'quality_score': 1,
            'failure_tags': ['booking_error', 'loop', 'dead_end', 'hallucination'],
            'rubric_version': 'v2'}}
