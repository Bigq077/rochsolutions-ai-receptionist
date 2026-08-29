"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_7ccf6e6b',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_7ccf6e6b (score 1, tags ['missed_escalation', 'loop', "
         "'dead_end', 'booking_error', 'hallucination'])",
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
                 'text': "I'm sorry to hear that — that sounds really rough. Can I ask, do the "
                         'headaches tend to start at the back of your neck and creep over one side '
                         'of your head, or are they more of a general throbbing all over?'},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': 'That pattern — starting at the neck and spreading over the head — can '
                         "often have the neck as the driver, and it's something Marcus can assess "
                         'and treat directly. Would you like to book an assessment so he can take '
                         'a proper look?'},
                {'role': 'user', 'text': 'No'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that — could you say that again?"},
                {'role': 'user', 'text': 'Next week'},
                {'role': 'assistant', 'text': "Right — so you'd like to come in next week."},
                {'role': 'user', 'text': 'Next week First'},
                {'role': 'assistant',
                 'text': "What's the appointment for — is it just the headaches, or is there "
                         'anything else going on?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? What's the appointment for — is it just the "
                         'headaches, or is there anything else going on?'},
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
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Either of those suit you?"},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant', 'text': "Right, let's see —"},
                {'role': 'assistant',
                 'text': "Here's what we've got coming up — Number 1, Monday 24th August — half "
                         'past four in the afternoon.'},
                {'role': 'assistant',
                 'text': 'Number 2, Tuesday 25th August — half past six in the evening. Either of '
                         'those suit you?'}],
 'source': {'call_sid_slug': 'regression_7ccf6e6b',
            'quality_score': 1,
            'failure_tags': ['missed_escalation',
                             'loop',
                             'dead_end',
                             'booking_error',
                             'hallucination'],
            'rubric_version': 'v2'}}
