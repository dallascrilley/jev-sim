from __future__ import annotations

import json

import httpx
import pytest
import respx

from jev_sim.providers import (
    RIZZO_BASE,
    RIZZO_MODEL,
    RIZZO_FINGERPRINT,
    RIZZO_FILES,
    TEV1_BASE,
    TEV1_MODEL,
    RizzoClient,
    Tev1Client,
)
from jev_sim.typesafe import TypeSafeError


@respx.mock
def test_rizzo_checks_served_model_identity():
    respx.get(f"{RIZZO_BASE}/health").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ready",
                "model": {"fingerprint": RIZZO_FINGERPRINT, "source_files": RIZZO_FILES},
            },
        )
    )
    respx.get(f"{RIZZO_BASE}/v1/models").mock(
        return_value=httpx.Response(200, json={"models": [{"name": RIZZO_MODEL}]})
    )
    respx.post(f"{RIZZO_BASE}/v1/systemone").mock(
        return_value=httpx.Response(
            200,
            json={
                "model": RIZZO_MODEL,
                "answers": {
                    "action": {
                        "choice": "done",
                        "confidence": 1.0,
                        "probabilities": {"@1": 0.0, "done": 1.0},
                    }
                },
            },
        )
    )
    answer = RizzoClient().choose_action(
        goal="g", outline="o", criteria={"@1": "tap", "done": "done"}
    )
    assert answer.choice == "done"


@respx.mock
def test_tev1_checks_digest_before_choice(tmp_path):
    pin = "a" * 64
    lock = tmp_path / "models.lock.json"
    lock.write_text(json.dumps({TEV1_MODEL: {"digest": pin}}))
    respx.get(f"{TEV1_BASE}/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": TEV1_MODEL, "digest": pin}]})
    )
    route = respx.post(f"{TEV1_BASE}/v1/systemone").mock(
        return_value=httpx.Response(
            200,
            json={
                "model": TEV1_MODEL,
                "answers": {
                    "action": {
                        "choice": "done",
                        "confidence": 1.0,
                        "probabilities": {"@1": 0.0, "done": 1.0},
                    }
                },
            },
        )
    )
    client = Tev1Client(lock_path=lock)
    answer = client.choose_action(goal="g", outline="o", criteria={"@1": "tap", "done": "done"})
    assert route.called
    assert client.model_digest == pin
    assert answer.choice == "done"


@respx.mock
def test_tev1_refuses_digest_mismatch(tmp_path):
    lock = tmp_path / "models.lock.json"
    lock.write_text(json.dumps({TEV1_MODEL: {"digest": "a" * 64}}))
    respx.get(f"{TEV1_BASE}/api/tags").mock(
        return_value=httpx.Response(
            200, json={"models": [{"name": TEV1_MODEL, "digest": "b" * 64}]}
        )
    )
    with pytest.raises(TypeSafeError, match="digest mismatch"):
        Tev1Client(lock_path=lock).choose_action(
            goal="g", outline="o", criteria={"@1": "tap", "done": "done"}
        )


@respx.mock
def test_rizzo_hierarchical_choice_keeps_every_candidate():
    respx.get(f"{RIZZO_BASE}/health").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ready",
                "model": {"fingerprint": RIZZO_FINGERPRINT, "source_files": RIZZO_FILES},
            },
        )
    )
    respx.get(f"{RIZZO_BASE}/v1/models").mock(
        return_value=httpx.Response(200, json={"models": [{"name": RIZZO_MODEL}]})
    )
    requests = []

    def choose(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        ids = list(body["questions"]["action"]["criteria"])
        requests.append(ids)
        if len(requests) == 1:
            probs = {candidate: (0.6 if candidate == "@0" else 0.4 / 25) for candidate in ids}
        else:
            probs = {candidate: (0.75 if candidate == "@0" else 0.25) for candidate in ids}
        return httpx.Response(
            200,
            json={
                "model": RIZZO_MODEL,
                "answers": {
                    "action": {
                        "choice": ids[0],
                        "confidence": probs[ids[0]],
                        "probabilities": probs,
                    }
                },
            },
        )

    respx.post(f"{RIZZO_BASE}/v1/systemone").mock(side_effect=choose)
    criteria = {f"@{i}": f"tap: target {i}" for i in range(27)}
    answer = RizzoClient().choose_action(goal="pick", outline="screen", criteria=criteria)

    assert len(requests) == 2
    assert all(2 <= len(candidate_ids) <= 26 for candidate_ids in requests)
    assert set(requests[0]) == set(list(criteria)[:26])
    assert set(requests[1]) == {"@0", "@26"}
    assert set(answer.probabilities) == set(criteria)
    assert sum(answer.probabilities.values()) == pytest.approx(1.0)
    assert answer.probabilities["@26"] == pytest.approx(0.25)
    assert answer.probabilities["@0"] == pytest.approx(0.75 * 0.6)
    assert answer.choice == "@0"
    assert "derived and uncalibrated" in answer.probability_source
    assert [call["candidate_ids"] for call in answer.calls] == requests


def test_rizzo_singleton_choice_skips_provider_call():
    answer = RizzoClient().choose_action(
        goal="only action", outline="screen", criteria={"@7": "tap: sole target"}
    )
    assert answer.choice == "@7"
    assert answer.confidence == 1.0
    assert answer.probabilities == {"@7": 1.0}
    assert answer.calls == []
    assert "no provider call" in answer.probability_source


def test_jev_cli_backend_keeps_pinned_model(monkeypatch):
    import jev_sim.providers as providers

    monkeypatch.setenv("TYPESAFE_MODEL", "jev-latest")
    monkeypatch.setattr(providers, "resolve_api_key", lambda: "test-key")
    client = providers.create_backend("jev")
    assert client.model == "jev-1.13.0"


@respx.mock
def test_rizzo_rejects_wrong_pinned_runtime():
    respx.get(f"{RIZZO_BASE}/health").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ready",
                "model": {"fingerprint": "changed", "source_files": RIZZO_FILES},
            },
        )
    )
    respx.get(f"{RIZZO_BASE}/v1/models").mock(
        return_value=httpx.Response(200, json={"models": [{"name": RIZZO_MODEL}]})
    )
    route = respx.post(f"{RIZZO_BASE}/v1/systemone").mock(return_value=httpx.Response(200))
    with pytest.raises(TypeSafeError, match="fingerprint mismatch"):
        RizzoClient().choose_action(goal="g", outline="o", criteria={"@1": "tap", "done": "done"})
    assert not route.called
