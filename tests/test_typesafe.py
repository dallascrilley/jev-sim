from __future__ import annotations

import httpx
import pytest
import respx

from jev_sim.typesafe import TypeSafeClient, TypeSafeError, DEFAULT_URL


@respx.mock
def test_choose_action_parses_choice(mock_choice_response):
    route = respx.post(DEFAULT_URL).mock(
        return_value=httpx.Response(200, json=mock_choice_response)
    )
    client = TypeSafeClient(api_key="test-key-not-real")
    answer = client.choose_action(
        goal="Open General settings",
        outline='App: Settings\n  @9 Button "General"',
        criteria={
            "@9": "tap: General",
            "done": "meta: done",
            "wait": "meta: wait",
        },
    )
    assert route.called
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer test-key-not-real"
    body = req.content
    assert b"jev-latest" in body or b"jev" in body
    assert answer.choice == "@9"
    assert answer.confidence == pytest.approx(0.91)
    assert answer.probabilities["@9"] == pytest.approx(0.88)
    assert answer.model == "jev-1.13.0"
    assert answer.latency_ms >= 0


@respx.mock
def test_http_error_raises():
    respx.post(DEFAULT_URL).mock(return_value=httpx.Response(401, text="nope"))
    client = TypeSafeClient(api_key="bad")
    with pytest.raises(TypeSafeError, match="401"):
        client.choose_action(
            goal="x",
            outline="y",
            criteria={"a": "A", "b": "B"},
        )


def test_criteria_too_small():
    client = TypeSafeClient(api_key="k")
    with pytest.raises(TypeSafeError, match="at least 2"):
        client.choose_action(goal="g", outline="o", criteria={"only": "one"})
