"""Make the live tier fail for the right reason, or not run at all.

The root `.env` has no ANTHROPIC_API_KEY - the deployed services get it from
Render - but `tests/auto/.env` does. Without this, every live test ran with no
credentials and the model call raised "Could not resolve authentication
method", which the engine's broad except turned into "Sorry, I had a bit of a
blip there".

That is worse than a plain failure: the xfail-marked defect test PASSED AS
XFAILED on an auth error, so an unrelated environment problem was silently
reported as evidence of the defect it was written to pin. An xfail that any
error can satisfy proves nothing.

So: load the key if it is findable, and hard-SKIP if it is not. A live test
must never run un-authenticated.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

_AUTO_ENV = Path(__file__).resolve().parents[1] / "auto" / ".env"


def _load_anthropic_key_if_missing() -> None:
    if os.getenv("ANTHROPIC_API_KEY"):
        return
    if not _AUTO_ENV.is_file():
        return
    for line in _AUTO_ENV.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line.startswith("ANTHROPIC_API_KEY="):
            continue
        value = line.split("=", 1)[1].strip().strip('"').strip("'")
        if value:
            os.environ["ANTHROPIC_API_KEY"] = value
        return


_HARNESS_DIR = Path(__file__).resolve().parent


def pytest_collection_modifyitems(config, items):
    """Skip only THIS directory's live tests when the key cannot be found.

    pytest calls this hook in a subdirectory conftest with the WHOLE session's
    item list, not just the items below it. An unscoped loop here would reach
    across the entire suite and, worse, the obvious predicate ("has a skipif
    marker") matches hundreds of unrelated tests - so running any harness test
    with HARNESS_LIVE_LLM=1 would silently skip a slice of the suite and move
    the red baseline that all this repo's work is judged against.

    Hence the explicit path check.
    """
    if os.getenv("HARNESS_LIVE_LLM") != "1":
        return

    _load_anthropic_key_if_missing()
    if os.getenv("ANTHROPIC_API_KEY"):
        return

    skip = pytest.mark.skip(
        reason=(
            "HARNESS_LIVE_LLM=1 but no ANTHROPIC_API_KEY (looked in the "
            f"environment and {_AUTO_ENV}). Refusing to run un-authenticated: "
            "the engine swallows the auth error and the conversation looks "
            "like an engine fault instead of a missing key."
        )
    )
    for item in items:
        try:
            in_harness = _HARNESS_DIR in Path(str(item.fspath)).resolve().parents
        except (OSError, ValueError):
            continue
        if in_harness:
            item.add_marker(skip)
