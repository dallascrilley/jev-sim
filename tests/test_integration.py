"""Optional live integration tests — skipped by default (`-m 'not integration'`)."""

from __future__ import annotations

import os

import pytest

from jev_sim.sim_use import which_sim_use
from jev_sim.typesafe import TypeSafeClient, resolve_api_key, TypeSafeError

pytestmark = pytest.mark.integration


def test_live_typesafe_choice_smoke():
    try:
        key = resolve_api_key()
    except TypeSafeError:
        pytest.skip("no TYPESAFE_API_KEY")
    client = TypeSafeClient(api_key=key)
    answer = client.choose_action(
        goal="Smoke test: pick done",
        outline='App: Fake\n  @1 Button "Nope"',
        criteria={
            "@1": "tap: Nope — ignore",
            "done": "meta: goal already satisfied for this smoke test",
            "wait": "meta: wait",
        },
    )
    assert answer.choice in {"@1", "done", "wait"}
    assert answer.confidence is not None
    # Do not print the key; latency is fine to show via assert message.
    assert answer.latency_ms < 30_000


def test_live_sim_use_version():
    if not which_sim_use():
        pytest.skip("sim-use not installed")
    from jev_sim.sim_use import SimUseClient

    result = SimUseClient().version()
    assert result.ok
    assert result.stdout or result.stderr
