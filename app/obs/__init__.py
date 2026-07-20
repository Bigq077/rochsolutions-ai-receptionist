"""
app.obs — Call observability subsystem (Susie_Call_Observability_Spec_for_Jules.md).

Phase 1 (this package): durable capture of every completed call to a Postgres
`calls` table, plus an offline replay harness. Everything here is additive and
gated behind config.OBS_CAPTURE_ENABLED (default OFF) — importing this package
has no effect on the live call path.

Later phases extend the same package: 2 (alerts), 3 (LLM-as-judge), 4 (failure→
regression), 5 (dashboard).
"""
