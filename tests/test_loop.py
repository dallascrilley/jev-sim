from __future__ import annotations

import json
from typing import Sequence

import httpx
import respx

from jev_sim.loop import run_step
from jev_sim.sim_use import CmdResult, SimUseClient
from jev_sim.typesafe import TypeSafeClient, DEFAULT_URL


def _fake_runner(ui_payload: dict):
    calls: list[tuple[str, ...]] = []

    def runner(argv: Sequence[str], timeout: float | None) -> CmdResult:
        calls.append(tuple(argv))
        if "ui" in argv:
            return CmdResult(tuple(argv), 0, json.dumps(ui_payload), "")
        if "tap" in argv or "gesture" in argv:
            return CmdResult(tuple(argv), 0, "ok", "")
        if "--version" in argv:
            return CmdResult(tuple(argv), 0, "sim-use 0.0.0-test", "")
        return CmdResult(tuple(argv), 0, "{}", "")

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


@respx.mock
def test_run_step_dry_run_with_fixture(ui_settings, mock_choice_response):
    respx.post(DEFAULT_URL).mock(return_value=httpx.Response(200, json=mock_choice_response))
    jev = TypeSafeClient(api_key="test-key")
    result = run_step(
        "Open General",
        ui=ui_settings,
        jev=jev,
        dry_run=True,
    )
    assert result.choice == "@9"
    assert result.dry_run is True
    assert result.executed is False
    assert result.confidence == 0.91
    assert result.decide_ms >= 0
    assert any(c.id == "@9" for c in result.candidates)


@respx.mock
def test_run_step_executes_tap(ui_settings, mock_choice_response):
    respx.post(DEFAULT_URL).mock(return_value=httpx.Response(200, json=mock_choice_response))
    runner = _fake_runner(ui_settings)
    sim = SimUseClient(runner=runner, device="TEST-UDID")
    jev = TypeSafeClient(api_key="test-key")
    result = run_step("Open General", sim=sim, jev=jev, dry_run=False)
    assert result.executed is True
    assert result.choice == "@9"
    tap_calls = [c for c in runner.calls if "tap" in c]  # type: ignore[attr-defined]
    assert tap_calls
    assert "@9" in tap_calls[0]
    assert "--device" in tap_calls[0]
    assert "TEST-UDID" in tap_calls[0]


@respx.mock
def test_run_step_observes_via_sim_use(ui_settings, mock_choice_response):
    respx.post(DEFAULT_URL).mock(return_value=httpx.Response(200, json=mock_choice_response))
    runner = _fake_runner(ui_settings)
    sim = SimUseClient(runner=runner)
    jev = TypeSafeClient(api_key="test-key")
    result = run_step("Open General", sim=sim, jev=jev, dry_run=True)
    ui_calls = [c for c in runner.calls if "ui" in c]  # type: ignore[attr-defined]
    assert ui_calls
    assert "--json" in ui_calls[0]
    assert "--no-raw" in ui_calls[0]
    assert result.observe_ms >= 0
