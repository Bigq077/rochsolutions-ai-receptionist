"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_efd6e600',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_efd6e600 (score 1, tags ['dead_end'])",
 'responses': ['Do you accept BUPA insurance?', 'No thanks'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Joint Venture Physiotherapy's AI receptionist — how "
                         'can I help you today?'},
                {'role': 'user', 'text': 'Do you accept BUPA insurance?'},
                {'role': 'assistant',
                 'text': "One moment — I'll get that logged for Jonathan now."},
                {'role': 'user', 'text': 'No thanks'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that — could you say that again?"}],
 'source': {'call_sid_slug': 'regression_efd6e600',
            'quality_score': 1,
            'failure_tags': ['dead_end'],
            'rubric_version': 'v2'}}
