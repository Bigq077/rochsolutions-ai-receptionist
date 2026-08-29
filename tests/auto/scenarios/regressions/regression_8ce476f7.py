"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_8ce476f7',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_8ce476f7 (score 1, tags ['loop', 'dead_end', 'booking_error', "
         "'missed_escalation', 'caller_frustration'])",
 'responses': ["I've been having really bad headaches",
               'Yes',
               'No',
               'Next week',
               'Next week First',
               'Yes',
               'Yes'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Joint Venture Physiotherapy's AI receptionist — how "
                         'can I help you today?'},
                {'role': 'user', 'text': "I've been having really bad headaches"},
                {'role': 'assistant',
                 'text': "I'm sorry to hear that — that sounds really rough. Can I ask a bit more "
                         'about where the headaches are coming from — are they starting at the '
                         'back of your neck and creeping over one side of your head, or more of a '
                         'general throbbing?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': 'That pattern — starting at the neck and spreading over the head — can '
                         "often have the neck as the driver, and it's something Marcus is "
                         'well-placed to assess and treat.'},
                {'role': 'user', 'text': 'No'},
                {'role': 'assistant',
                 'text': 'No problem — could you tell me a bit more about where the headaches are, '
                         'or what they feel like?'},
                {'role': 'user', 'text': 'Next week'},
                {'role': 'assistant',
                 'text': "Right — so you're thinking next week for an appointment. Would you like "
                         "to book in with Marcus so he can take a proper look at what's going on?"},
                {'role': 'user', 'text': 'Next week First'},
                {'role': 'assistant', 'text': 'Let me see —'},
                {'role': 'assistant',
                 'text': "Here's what we've got coming up — Number 1, Monday 24th August — half "
                         'past four in the afternoon.'},
                {'role': 'assistant',
                 'text': 'Number 2, Tuesday 25th August — half past six in the evening. Either of '
                         'those suit you?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': 'Which one works for you — Monday the 24th at half past four, or Tuesday '
                         'the 25th at half past six?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         'or cancel an appointment? Which one works for you — Monday the 24th at '
                         'half past four, or Tuesday the 25th at half past six?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Which one works for you — Monday the 24th at half "
                         'past four, or Tuesday the 25th at half past six?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': 'Which one would you like — Monday the 24th at half past four, or Tuesday '
                         'the 25th at half past six?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         'or cancel an appointment? Which one would you like — Monday the 24th at '
                         'half past four, or Tuesday the 25th at half past six?'}],
 'source': {'call_sid_slug': 'regression_8ce476f7',
            'quality_score': 1,
            'failure_tags': ['loop',
                             'dead_end',
                             'booking_error',
                             'missed_escalation',
                             'caller_frustration'],
            'rubric_version': 'v2'}}
