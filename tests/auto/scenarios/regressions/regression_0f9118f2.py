"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_0f9118f2',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_0f9118f2 (score 2, tags ['booking_error', 'loop', "
         "'caller_frustration'])",
 'responses': ["yeah i emailed mark yesterday to say i'm cancelling my appointment tomorrow and "
               "i've just had a reminder about the appointment so i'm just checking the "
               'cancellation was read',
               'tomorrow',
               'yeah',
               "it was 11 o'clock tomorrow morning",
               'but i say i cancelled it yesterday i emailed him and cancelled it yesterday',
               'thank you',
               'sorry',
               'sorry',
               'hello',
               "yeah yeah i'm just saying that it's actually cancelled"],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Theorem Health's AI receptionist — to speak to Mark "
                         'directly press 1, otherwise how can I help you today?'},
                {'role': 'user',
                 'text': "yeah i emailed mark yesterday to say i'm cancelling my appointment "
                         "tomorrow and i've just had a reminder about the appointment so i'm just "
                         'checking the cancellation was read'},
                {'role': 'assistant',
                 'text': "I don't have access to Mark's emails, so I can't confirm whether it's "
                         'been read — but let me look up your appointment now and I can cancel it '
                         "from my end to make sure it's sorted. Was the appointment at our Awlstuh "
                         'or Redditch clinic?'},
                {'role': 'assistant', 'text': 'Alcester.'},
                {'role': 'user', 'text': 'tomorrow'},
                {'role': 'assistant', 'text': 'Let me just check that…'},
                {'role': 'assistant',
                 'text': "Yes, still here — we were just getting you sorted. I've got you on zero "
                         'seven five zero six, seven four nine, seven one seven — is that the '
                         'number the appointment was booked under?'},
                {'role': 'user', 'text': 'yeah'},
                {'role': 'user', 'text': "it was 11 o'clock tomorrow morning"},
                {'role': 'user',
                 'text': 'but i say i cancelled it yesterday i emailed him and cancelled it '
                         'yesterday'},
                {'role': 'assistant', 'text': 'Just getting that for you…'},
                {'role': 'assistant',
                 'text': 'Understood — let me get that cancelled from my end right now to make '
                         "sure it's confirmed in the system."},
                {'role': 'user', 'text': 'thank you'},
                {'role': 'assistant', 'text': 'Not to worry at all — take care!'},
                {'role': 'user', 'text': 'sorry'},
                {'role': 'assistant', 'text': 'Let me just check that…'},
                {'role': 'assistant', 'text': 'No rush at all — what can I help you with?'},
                {'role': 'user', 'text': 'sorry'},
                {'role': 'assistant', 'text': 'Take your time — no rush at all.'},
                {'role': 'user', 'text': 'hello'},
                {'role': 'assistant', 'text': 'Yes, still here — take your time.'},
                {'role': 'assistant', 'text': 'Sorry — go ahead.'},
                {'role': 'user', 'text': "yeah yeah i'm just saying that it's actually cancelled"},
                {'role': 'assistant', 'text': 'Got it — not to worry at all. Take care!'}],
 'source': {'call_sid_slug': 'regression_0f9118f2',
            'quality_score': 2,
            'failure_tags': ['booking_error', 'loop', 'caller_frustration'],
            'rubric_version': 'v2'}}
