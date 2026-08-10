"""
Google OAuth tokens must be keyed per clinic, and must never be silently
adopted from the shared legacy key.

Before this, every Google token lived under ONE Redis key, "google_tokens", for
the whole process — so one Redis meant one Google account. That survived only
because each clinic runs its own Render service. Two of the three clinics book
into Google Calendar (vital_edge is google_calendar_provisional, jv_v1 is
google_calendar), so the moment they share a Redis, whichever authorised last
owns the key and the other writes its bookings into the WRONG practice's
calendar. notifications/scheduler.py already talks about "another tenant sharing
this Redis", so the sharing is not hypothetical.

The migration is deliberately READ-ONLY:

  * resolve_tokens_key returns the namespaced key only if it ALREADY exists,
    else the legacy key — so an un-migrated clinic keeps working unchanged, and
    reads and writes can never land in different keys;
  * only the OAuth callback may create a namespaced key.

That second property is the one worth a test. Adoption is the tempting
"helpful" behaviour and it is precisely wrong: on a shared Redis it would bake
one practice's credentials into another practice's key — the same bug, now
invisible and durable rather than obvious.
"""

import ast
import inspect
import pathlib

import pytest

from app.tools import calendar_google as cg


# ── the key scheme ──────────────────────────────────────────────────────────


def test_namespaced_key_is_per_clinic():
    assert cg.tokens_key("vital_edge") == "google_tokens:vital_edge"
    assert cg.tokens_key("jv_v1") == "google_tokens:jv_v1"
    assert cg.tokens_key("vital_edge") != cg.tokens_key("jv_v1")


@pytest.mark.parametrize("cid", ["VITAL_EDGE", " vital_edge ", "Vital_Edge"])
def test_clinic_id_is_normalised(cid):
    """clinic_id reaches this from a session and from a query string. If case
    or whitespace produced a different key, a clinic would silently look
    unconnected and fall back to the legacy shared key."""
    assert cg.tokens_key(cid) == "google_tokens:vital_edge"


def test_no_clinic_falls_back_to_the_legacy_key():
    """A caller with genuinely no clinic must degrade to today's behaviour
    rather than inventing a key that reads empty."""
    assert cg.tokens_key("") == cg.GOOGLE_TOKENS_LEGACY_KEY
    assert cg.tokens_key(None) == cg.GOOGLE_TOKENS_LEGACY_KEY


# ── the resolver never adopts ───────────────────────────────────────────────


async def test_resolver_uses_the_namespaced_key_when_it_exists(monkeypatch):
    seen = {}

    async def fake_get(key):
        seen["key"] = key
        return {"refresh_token": "x"}

    monkeypatch.setattr(
        "app.storage.redis_store.redis_get_json", fake_get, raising=False
    )
    assert await cg.resolve_tokens_key("vital_edge") == "google_tokens:vital_edge"
    assert seen["key"] == "google_tokens:vital_edge"


async def test_resolver_falls_back_when_the_clinic_has_not_authorised(monkeypatch):
    async def fake_get(key):
        return None

    monkeypatch.setattr(
        "app.storage.redis_store.redis_get_json", fake_get, raising=False
    )
    assert await cg.resolve_tokens_key("vital_edge") == cg.GOOGLE_TOKENS_LEGACY_KEY


async def test_resolver_falls_back_when_redis_is_down(monkeypatch):
    """A Redis blip must not make a connected clinic look disconnected."""

    async def boom(key):
        raise RuntimeError("redis down")

    monkeypatch.setattr(
        "app.storage.redis_store.redis_get_json", boom, raising=False
    )
    assert await cg.resolve_tokens_key("vital_edge") == cg.GOOGLE_TOKENS_LEGACY_KEY


async def test_the_resolver_never_writes(monkeypatch):
    """
    The core anti-adoption property. Resolving must be a pure read — if it ever
    wrote, an un-migrated clinic would adopt whatever tokens the legacy key
    happens to hold, which on a shared Redis is another practice's.
    """
    async def fake_get(key):
        return None

    def refuse(*a, **k):
        raise AssertionError(
            "resolve_tokens_key wrote to Redis — it must never adopt the legacy "
            "key into a clinic-specific one"
        )

    monkeypatch.setattr(
        "app.storage.redis_store.redis_get_json", fake_get, raising=False
    )
    monkeypatch.setattr(
        "app.storage.redis_store.redis_set_json", refuse, raising=False
    )
    await cg.resolve_tokens_key("vital_edge")


# ── only the OAuth callback may create a namespaced key ─────────────────────


def _routes_src() -> str:
    return pathlib.Path(
        inspect.getfile(__import__("app.routes.google_calendar", fromlist=["x"]))
    ).read_text(encoding="utf-8")


def test_only_the_oauth_callback_writes_a_namespaced_key():
    """
    tokens_key() is the direct, un-resolved write. Exactly one function may use
    it — _save_new_grant — and exactly one caller may call that: the OAuth
    callback. Anything else doing so is adoption wearing a different hat.
    """
    tree = ast.parse(_routes_src())
    users = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name):
                if inner.func.id in ("tokens_key", "_save_new_grant"):
                    users.add(node.name)
    assert users == {"_save_new_grant", "google_callback"}, (
        f"unexpected writer of a namespaced token key: {sorted(users)} — only "
        f"_save_new_grant may call tokens_key, and only google_callback may "
        f"call _save_new_grant"
    )


def test_the_live_booking_path_has_no_global_token_key():
    """
    receptionist_tools used to carry `_TOKENS_KEY = "google_tokens"`. A bare
    module-level constant is what made every booking read one shared account,
    so it must not come back.
    """
    src = pathlib.Path("app/tools/receptionist_tools.py").read_text(
        encoding="utf-8"
    )
    assert '_TOKENS_KEY = "google_tokens"' not in src, (
        "the global token key is back in receptionist_tools — every clinic "
        "would read the same Google account again"
    )


def test_every_token_read_in_the_booking_path_passes_a_clinic():
    """
    Seven call sites, all inside tool executors that take `session`. A bare
    _get_tokens() would silently mean "the legacy shared key" and would be
    invisible in review.
    """
    src = pathlib.Path("app/tools/receptionist_tools.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    bare = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in ("_get_tokens", "_save_gcal_tokens"):
                # _get_tokens(clinic) is 1 arg; _save_gcal_tokens(tokens, clinic)
                # is 2. One fewer means the clinic was omitted.
                need = 1 if node.func.id == "_get_tokens" else 2
                if len(node.args) < need:
                    bare.append((node.func.id, node.lineno))
    assert not bare, (
        f"token access without a clinic_id at {bare} — these fall back to the "
        f"legacy shared key, which is the bug this change removes"
    )
