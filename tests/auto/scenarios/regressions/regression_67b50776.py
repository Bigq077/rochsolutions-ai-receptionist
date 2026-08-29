"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_67b50776',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_67b50776 (score 1, tags ['loop', 'dead_end', "
         "'missed_escalation', 'booking_error'])",
 'responses': ["I'm just not feeling right",
               'Yes',
               'No',
               'Any morning',
               'One',
               'Yes',
               'Bob Jones',
               'Yes'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Joint Venture Physiotherapy's AI receptionist — how "
                         'can I help you today?'},
                {'role': 'user', 'text': "I'm just not feeling right"},
                {'role': 'assistant', 'text': "I'm sorry to hear that — what's been going on?"},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': "I'm sorry to hear you're not feeling yourself. Could you tell me a bit "
                         "more about what's been bothering you — is it a pain somewhere, or "
                         "something else that's been troubling you?"},
                {'role': 'user', 'text': 'No'},
                {'role': 'assistant',
                 'text': "Not to worry — could you tell me a little more about what's been going "
                         'on? Is it something physical, like a pain or stiffness somewhere, or '
                         'more of a general feeling of being unwell?'},
                {'role': 'user', 'text': 'Any morning'},
                {'role': 'assistant',
                 'text': 'Right — so mornings are when you tend to feel it most. Can you tell me a '
                         "bit more about what you're experiencing — is it pain or stiffness in a "
                         'particular area, or something else?'},
                {'role': 'user', 'text': 'One'},
                {'role': 'assistant', 'text': "Take your time — what is it you're feeling?"},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? Take your time — what is it you're feeling?"},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': 'Not to worry — could you tell me what the appointment would be for, so I '
                         'can point you in the right direction?'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Not to worry — could you tell me what the appointment "
                         'would be for, so I can point you in the right direction?'},
                {'role': 'user', 'text': 'Bob Jones'},
                {'role': 'assistant',
                 'text': 'Thanks for that — it sounds like you might be wanting to book an '
                         "appointment, Bob. What's the appointment for?"},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? What's the appointment for?"},
                {'role': 'user', 'text': 'Yes'},
                {'role': 'assistant',
                 'text': "Right — and what is it you'd like to see Marcus about, Bob? Is it pain "
                         "or stiffness somewhere, or something else that's been bothering you?"},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that. Are you calling to book, reschedule, "
                         "or cancel an appointment? — if you'd like to call back when you're "
                         "ready, we're here. Is it pain or stiffness somewhere, or something else "
                         "that's been bothering you?"}],
 'source': {'call_sid_slug': 'regression_67b50776',
            'quality_score': 1,
            'failure_tags': ['loop', 'dead_end', 'missed_escalation', 'booking_error'],
            'rubric_version': 'v2'}}
