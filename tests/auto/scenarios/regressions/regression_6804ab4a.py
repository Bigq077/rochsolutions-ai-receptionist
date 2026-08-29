"""Auto-generated regression scenario (app/obs/to_scenario.py).
PII-redacted from a real judged-bad call. Do not add real names/numbers.
Refine `expected` when you fix the underlying failure.
"""

SCENARIO = {'id': 'regression_6804ab4a',
 'phase': 'Regression — mined from real calls',
 'name': "Regression from regression_6804ab4a (score 1, tags ['dead_end', 'hallucination'])",
 'responses': ['Do you take AXA health insurance?', 'No thank you'],
 'expected': {'no_technical_error': True},
 'transcript': [{'role': 'assistant',
                 'text': "Hi there, I'm Susie, Joint Venture Physiotherapy's AI receptionist — how "
                         'can I help you today?'},
                {'role': 'user', 'text': 'Do you take AXA health insurance?'},
                {'role': 'assistant',
                 'text': "One moment — I'll get that logged for Jonathan now."},
                {'role': 'user', 'text': 'No thank you'},
                {'role': 'assistant',
                 'text': "Sorry, I didn't quite catch that — could you say that again?"}],
 'source': {'call_sid_slug': 'regression_6804ab4a',
            'quality_score': 1,
            'failure_tags': ['dead_end', 'hallucination'],
            'rubric_version': 'v2'}}
