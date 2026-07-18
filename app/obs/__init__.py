"""
app.obs — Call observability subsystem.

Ported from Theorem (main) to give the Joint Venture deployment the same operator
failure-alerting as Theorem: a completed call that matches a failure condition
(spec §5.2) sends an immediate SMS to the operator.

This slice contains the alerting layer only (alerts.py). It is additive and
gated behind config.OBS_ALERTS_ENABLED (default OFF) — importing this package
has no effect on the live call path, and nothing is sent until the flag is set.
The Phase 1 durable-capture / Phase 3 LLM-judge layers from main are not part of
this slice (they require a Postgres store and are analytics, not alerting).
"""
