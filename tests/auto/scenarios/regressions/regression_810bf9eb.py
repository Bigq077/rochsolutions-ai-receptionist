"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_810bf9eb',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_810bf9eb (score 2, tags ['wrong_info', 'caller_frustration', "
         "'loop'])",
 'responses': ["yeah i'd like to move my appointment please",
               'um yes it was',
               "uh yes that's the right one",
               'um anytime next week',
               'uh the second day suits me could you give me all the slots you have on that day',
               'yeah quarter past 8 works',
               'um when are you moving it to could you repeat that',
               'yeah go for it',
               'could you repeat all the days you have please',
               'monday please',
               'yeah 6 in the evening works',
               'uh yeah go for it'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Joint Venture Physiotherapy's AI receptionist — how "
                         'can I help you today?'},
                {'role': 'user', 'text': "yeah i'd like to move my appointment please"},
                {'role': 'assistant', 'text': 'One moment…'},
                {'role': 'assistant',
                 'text': "Let's get that moved for you. I've got you on oh seven five oh two, two "
                         'one one, two oh seven — is that the number the appointment was booked '
                         'under?'},
                {'role': 'assistant', 'text': 'Right with you…'},
                {'role': 'user', 'text': 'um yes it was'},
                {'role': 'assistant', 'text': 'One moment while I find that for you…'},
                {'role': 'assistant',
                 'text': 'I can see an appointment on Tuesday the 1st of September at five in the '
                         'afternoon — is that the right one?'},
                {'role': 'user', 'text': "uh yes that's the right one"},
                {'role': 'assistant',
                 'text': "Do you have a preference for when you'd like to reschedule to?"},
                {'role': 'user', 'text': 'um anytime next week'},
                {'role': 'assistant', 'text': 'Let me bring that up for you…'},
                {'role': 'assistant', 'text': 'Let me pull that up now…'},
                {'role': 'assistant',
                 'text': "Here's what we've got coming up — Number 1, Monday 7th September — half "
                         'past four in the afternoon.'},
                {'role': 'assistant',
                 'text': 'Number 2, Tuesday 8th September — five in the evening. Either of those '
                         'suit you?'},
                {'role': 'assistant', 'text': 'Still with you — which of those would you like?'},
                {'role': 'user',
                 'text': 'uh the second day suits me could you give me all the slots you have on '
                         'that day'},
                {'role': 'assistant',
                 'text': 'On Monday 7th September I also have quarter past five in the evening, '
                         'six in the evening, or quarter past eight in the evening. Any of those '
                         'work?'},
                {'role': 'user', 'text': 'yeah quarter past 8 works'},
                {'role': 'assistant', 'text': 'Just getting that for you…'},
                {'role': 'assistant',
                 'text': "Just to confirm — I'm moving your appointment to Tuesday the 8th of "
                         'September at five in the evening. Shall I go ahead and move it for you?'},
                {'role': 'user', 'text': 'um when are you moving it to could you repeat that'},
                {'role': 'assistant', 'text': 'Let me just check that…'},
                {'role': 'assistant',
                 'text': "Just to confirm — I'm moving your appointment to Tuesday the 8th of "
                         'September at five in the evening. Shall I go ahead and move it for you?'},
                {'role': 'user', 'text': 'yeah go for it'},
                {'role': 'assistant', 'text': 'Just moving that for you now…'},
                {'role': 'assistant',
                 'text': 'Let me do that now. Actually, I already have everything I need. Let me '
                         'go ahead and action that.'},
                {'role': 'assistant', 'text': 'Let me see what we have available…'},
                {'role': 'assistant',
                 'text': 'I apologize — I need to correct that. You said quarter past eight, but '
                         'looking at the slots available on Tuesday 8th September, the latest time '
                         'is five in the evening. Let me confirm: did you mean five in the '
                         'evening, or would you prefer one of the other times on that day?'},
                {'role': 'user', 'text': 'could you repeat all the days you have please'},
                {'role': 'assistant', 'text': 'One moment…'},
                {'role': 'assistant',
                 'text': "I've got Monday the 7th of September, or Tuesday the 8th of September — "
                         'which of those would you like?'},
                {'role': 'user', 'text': 'monday please'},
                {'role': 'assistant', 'text': 'Right with you…'},
                {'role': 'assistant',
                 'text': "Let me check what's available on Monday the 7th of September."},
                {'role': 'assistant',
                 'text': 'Monday the 7th of September — Number 1, half past four in the '
                         'afternoon.'},
                {'role': 'assistant', 'text': 'Number 2, quarter past five in the evening.'},
                {'role': 'assistant', 'text': 'Number 3, six in the evening. Any of those work?'},
                {'role': 'user', 'text': 'yeah 6 in the evening works'},
                {'role': 'assistant', 'text': 'Right with you…'},
                {'role': 'assistant',
                 'text': "Just to confirm — I'm moving your appointment to Monday the 7th of "
                         'September at six in the evening. Shall I go ahead and move it for you?'},
                {'role': 'user', 'text': 'uh yeah go for it'},
                {'role': 'assistant', 'text': 'Just moving that for you now…'},
                {'role': 'assistant',
                 'text': "That's you rescheduled — you're now in for Monday the 7th of September "
                         "at six in the evening. Confirmation text on its way. We'll see you then "
                         '— take care.'}],
 'source': {'call_sid_slug': 'regression_810bf9eb',
            'quality_score': 2,
            'failure_tags': ['wrong_info', 'caller_frustration', 'loop'],
            'rubric_version': 'v2'}}
