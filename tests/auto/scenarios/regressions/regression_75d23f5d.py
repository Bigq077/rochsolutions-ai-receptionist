"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_75d23f5d',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_75d23f5d (score 1, tags ['dead_end', 'missed_escalation', "
         "'caller_frustration'])",
 'responses': ['um are you a real person', 'um can i speak to somebody in the reception please'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Joint Venture Physiotherapy's AI receptionist — how "
                         'can I help you today?'},
                {'role': 'user', 'text': 'um are you a real person'},
                {'role': 'assistant', 'text': 'Right —'},
                {'role': 'assistant',
                 'text': "No — I'm Susie, Joint Venture Physiotherapy's AI receptionist."},
                {'role': 'assistant',
                 'text': 'I can get you booked in or answer questions about the clinic, and I can '
                         "put you through to Marcus if you'd rather speak to a person."},
                {'role': 'user', 'text': 'um can i speak to somebody in the reception please'},
                {'role': 'assistant', 'text': 'So —'},
                {'role': 'assistant',
                 'text': "You've got reception — I'm Susie, the AI receptionist. What can I help "
                         "you with? Or I can put you through to Marcus if you'd prefer."}],
 'source': {'call_sid_slug': 'regression_75d23f5d',
            'quality_score': 1,
            'failure_tags': ['dead_end', 'missed_escalation', 'caller_frustration'],
            'rubric_version': 'v2'}}
