from __future__ import annotations

import json

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
        criteria={key: key for key in mock_choice_response["answers"]["action"]["probabilities"]},
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


@respx.mock
def test_reject_probability_id_mismatch(mock_choice_response):
    malformed = json.loads(json.dumps(mock_choice_response))
    malformed["answers"]["action"]["probabilities"].pop("@5")
    respx.post(DEFAULT_URL).mock(return_value=httpx.Response(200, json=malformed))
    with pytest.raises(TypeSafeError, match="IDs do not match"):
        TypeSafeClient(api_key="k").choose_action(
            goal="g", outline="o", criteria={"@9": "nine", "@5": "five", "done": "done"}
        )


@respx.mock
def test_reject_probability_mass_error(mock_choice_response):
    malformed = json.loads(json.dumps(mock_choice_response))
    malformed["answers"]["action"]["probabilities"] = {
        "@5": 0.24,
        "@7": 0.24,
        "@9": 0.24,
        "@10": 0.24,
        "@11": 0.24,
        "@43": 0,
        "done": 0,
        "scroll-up": 0,
        "scroll-down": 0,
        "wait": 0,
    }
    respx.post(DEFAULT_URL).mock(return_value=httpx.Response(200, json=malformed))
    criteria = {key: key for key in malformed["answers"]["action"]["probabilities"]}
    with pytest.raises(TypeSafeError, match="do not sum"):
        TypeSafeClient(api_key="k").choose_action(goal="g", outline="o", criteria=criteria)


@respx.mock
def test_reject_selected_unknown_candidate(mock_choice_response):
    malformed = json.loads(json.dumps(mock_choice_response))
    malformed["answers"]["action"]["choice"] = "@404"
    respx.post(DEFAULT_URL).mock(return_value=httpx.Response(200, json=malformed))
    with pytest.raises(TypeSafeError, match="not a candidate"):
        TypeSafeClient(api_key="k").choose_action(
            goal="g", outline="o", criteria={"@9": "nine", "done": "done"}
        )


@respx.mock
def test_reject_truncated_response(mock_choice_response):
    truncated = dict(mock_choice_response, truncated=True)
    respx.post(DEFAULT_URL).mock(return_value=httpx.Response(200, json=truncated))
    with pytest.raises(TypeSafeError, match="truncated"):
        TypeSafeClient(api_key="k").choose_action(
            goal="g", outline="o", criteria={"@9": "nine", "done": "done"}
        )


@respx.mock
def test_refuses_wrong_or_missing_model_identity():
    for payload in [{}, {"model": "other"}]:
        respx.post("https://api.typesafe.ai/v1/systemone").mock(
            return_value=httpx.Response(
                200,
                json={
                    **payload,
                    "answers": {
                        "action": {"choice": "done", "probabilities": {"@1": 0.0, "done": 1.0}}
                    },
                },
            )
        )
        with pytest.raises(TypeSafeError, match="model mismatch"):
            TypeSafeClient(api_key="test", model="jev-1.13.0").choose_action(
                goal="g", outline="o", criteria={"@1": "tap", "done": "done"}
            )
