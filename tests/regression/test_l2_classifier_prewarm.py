"""The L2 booking classifier's connection was never pre-warmed, so the first
booking confirmation after a deploy paid TLS setup inside a ~1s budget.

CA3a6cfb84 (2026-08-03, build 8e12aafe8b39), first call after the deploy:

    L2 classifier failed (TimeoutError()) — failing closed to a re-ask
    book_appointment BLOCKED — no clear caller yes (last_user_text='uh go for it')

The caller had to say "i said go for it" before the booking went through — an
extra turn on the most important question in the call. `_classifier_client()`
already builds the client once, and its docstring predicts this exact failure;
but it builds it LAZILY, so the cost stayed on call 1 after every cold start.

`app/main.py` step 1 pre-warms `app.flows.conversation._get_client()`. That is a
DIFFERENT AsyncAnthropic instance with its own httpx pool, and it did nothing
for this path. The test that matters most here is
`test_prewarm_uses_the_same_client_the_classifier_uses`.
"""
import asyncio

import pytest

from app.media_streams import llm_stream as ls


@pytest.fixture(autouse=True)
def _reset_client_cache():
    """The client is a module global; don't leak one test's fake into another."""
    before = ls._classifier_client_cached
    ls._classifier_client_cached = None
    yield
    ls._classifier_client_cached = before


class _FakeMessages:
    def __init__(self, fail=None, record=None):
        self._fail = fail
        self._record = record if record is not None else []

    async def create(self, **kwargs):
        self._record.append(kwargs)
        if self._fail:
            raise self._fail
        return object()


class _FakeClient:
    def __init__(self, fail=None):
        self.calls = []
        self.messages = _FakeMessages(fail=fail, record=self.calls)


# ── the property that makes this fix work at all ───────────────────────────


async def test_prewarm_uses_the_same_client_the_classifier_uses(monkeypatch):
    """Each AsyncAnthropic owns its own connection pool, so warming any other
    instance warms the wrong socket. This is precisely why main.py's existing
    Anthropic prewarm did not help."""
    fake = _FakeClient()
    monkeypatch.setattr(ls, "_classifier_client", lambda: fake)
    monkeypatch.setattr(ls, "BOOK_CLASSIFIER_ENABLED", True)
    monkeypatch.setattr(ls, "ANTHROPIC_API_KEY", "sk-test")

    await ls.prewarm_classifier()

    assert len(fake.calls) == 1, "prewarm must issue exactly one real request"


async def test_prewarm_issues_a_real_request_not_just_a_construction(monkeypatch):
    """Building the client is cheap; the expense is the first request's
    DNS+TCP+TLS+auth. A prewarm that only constructs the object leaves the whole
    cost exactly where it was."""
    fake = _FakeClient()
    monkeypatch.setattr(ls, "_classifier_client", lambda: fake)
    monkeypatch.setattr(ls, "BOOK_CLASSIFIER_ENABLED", True)
    monkeypatch.setattr(ls, "ANTHROPIC_API_KEY", "sk-test")

    elapsed = await ls.prewarm_classifier()

    assert fake.calls, "no request was made"
    assert elapsed > 0, "a successful prewarm reports its elapsed time"


async def test_prewarm_does_not_use_the_per_turn_timeout_budget(monkeypatch):
    """The per-turn budget is the thing too tight to absorb a cold connection —
    reusing it here would reproduce the bug at boot. At boot no caller is
    waiting, so the prewarm may take longer."""
    fake = _FakeClient()
    monkeypatch.setattr(ls, "_classifier_client", lambda: fake)
    monkeypatch.setattr(ls, "BOOK_CLASSIFIER_ENABLED", True)
    monkeypatch.setattr(ls, "ANTHROPIC_API_KEY", "sk-test")

    slow = ls._BOOK_CLASSIFIER_TIMEOUT_S + 0.35

    async def _slow_create(**kwargs):
        await asyncio.sleep(slow)
        return object()

    fake.messages.create = _slow_create

    elapsed = await ls.prewarm_classifier()
    assert elapsed >= slow, (
        "prewarm aborted at the per-turn budget — it must allow a real "
        "connection setup"
    )


# ── it must never be able to break startup ─────────────────────────────────


async def test_a_failing_prewarm_is_non_fatal(monkeypatch):
    """Boot must survive a dead or throttled API. Behaviour then is exactly
    what it is today: the first call pays setup and may fail closed."""
    fake = _FakeClient(fail=RuntimeError("api down"))
    monkeypatch.setattr(ls, "_classifier_client", lambda: fake)
    monkeypatch.setattr(ls, "BOOK_CLASSIFIER_ENABLED", True)
    monkeypatch.setattr(ls, "ANTHROPIC_API_KEY", "sk-test")

    assert await ls.prewarm_classifier() == 0.0


async def test_a_timing_out_prewarm_is_non_fatal(monkeypatch):
    """The failure mode that prompted this fix must not become a boot hang."""
    fake = _FakeClient(fail=asyncio.TimeoutError())
    monkeypatch.setattr(ls, "_classifier_client", lambda: fake)
    monkeypatch.setattr(ls, "BOOK_CLASSIFIER_ENABLED", True)
    monkeypatch.setattr(ls, "ANTHROPIC_API_KEY", "sk-test")

    assert await ls.prewarm_classifier() == 0.0


async def test_client_construction_failure_is_non_fatal(monkeypatch):
    """Even a client that cannot be built at all must not raise out of here."""
    def _boom():
        raise RuntimeError("no client")

    monkeypatch.setattr(ls, "_classifier_client", _boom)
    monkeypatch.setattr(ls, "BOOK_CLASSIFIER_ENABLED", True)
    monkeypatch.setattr(ls, "ANTHROPIC_API_KEY", "sk-test")

    assert await ls.prewarm_classifier() == 0.0


# ── it must respect the switches that already exist ────────────────────────


async def test_no_request_when_the_classifier_is_disabled(monkeypatch):
    """BOOK_CLASSIFIER_ENABLED=false is the no-redeploy kill switch. Warming a
    client nothing will use spends a token per boot for nothing."""
    fake = _FakeClient()
    monkeypatch.setattr(ls, "_classifier_client", lambda: fake)
    monkeypatch.setattr(ls, "BOOK_CLASSIFIER_ENABLED", False)
    monkeypatch.setattr(ls, "ANTHROPIC_API_KEY", "sk-test")

    assert await ls.prewarm_classifier() == 0.0
    assert fake.calls == []


async def test_no_request_without_an_api_key(monkeypatch):
    """A keyless environment (local, CI) must not attempt a live call."""
    fake = _FakeClient()
    monkeypatch.setattr(ls, "_classifier_client", lambda: fake)
    monkeypatch.setattr(ls, "BOOK_CLASSIFIER_ENABLED", True)
    monkeypatch.setattr(ls, "ANTHROPIC_API_KEY", "")

    assert await ls.prewarm_classifier() == 0.0
    assert fake.calls == []


# ── the request itself stays minimal ───────────────────────────────────────


async def test_prewarm_request_is_as_small_as_possible(monkeypatch):
    """This runs on every boot. It exists to open a socket, not to think."""
    fake = _FakeClient()
    monkeypatch.setattr(ls, "_classifier_client", lambda: fake)
    monkeypatch.setattr(ls, "BOOK_CLASSIFIER_ENABLED", True)
    monkeypatch.setattr(ls, "ANTHROPIC_API_KEY", "sk-test")

    await ls.prewarm_classifier()

    kwargs = fake.calls[0]
    assert kwargs["model"] == ls.HAIKU, "must warm the model the classifier uses"
    assert kwargs["max_tokens"] <= 5
    assert kwargs["temperature"] == 0
