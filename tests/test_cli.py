from __future__ import annotations

from click.testing import CliRunner
import json

from jev_sim.cli import main
from jev_sim.typesafe import ChoiceAnswer
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "ui_settings.json"


def test_cli_dry_run_list_only():
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["dry-run", "Open General", "--ui-file", str(FIXTURE), "--list-only"],
    )
    assert result.exit_code == 0, result.output
    assert "@9" in result.output
    assert "done" in result.output


def test_cli_doctor_no_devices():
    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--no-devices"])
    # May fail if sim-use missing or key missing — still should print checks.
    assert "sim-use" in result.output
    assert "TYPESAFE_API_KEY" in result.output


def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_cli_dry_run_backend_and_jsonl_trace(tmp_path, monkeypatch):
    class Provider:
        backend = "tev1"
        model = "tev1:4b-q4_K_M"
        model_digest = "d" * 64

        def choose_action(self, *, goal, outline, criteria):
            probabilities = {candidate: float(candidate == "done") for candidate in criteria}
            return ChoiceAnswer(
                choice="done",
                confidence=1.0,
                probabilities=probabilities,
                model=self.model,
                latency_ms=1.0,
                calls=[
                    {"backend": self.backend, "model": self.model, "candidate_ids": list(criteria)}
                ],
            )

    monkeypatch.setattr("jev_sim.cli.create_backend", lambda name: Provider())
    trace = tmp_path / "steps.jsonl"
    result = CliRunner().invoke(
        main,
        [
            "dry-run",
            "Open General",
            "--backend",
            "tev1",
            "--ui-file",
            str(FIXTURE),
            "--trace",
            str(trace),
        ],
    )
    assert result.exit_code == 0, result.output
    record = json.loads(trace.read_text().strip())
    assert record["backend"] == "tev1"
    assert record["model_digest"] == "d" * 64
    assert record["selected_id"] == "done"
    assert record["outcome"] == "done"
    assert record["timings_ms"]["total"] >= 0
    assert record["probability_source"] == "backend-reported"
    assert len(record["provider_calls"]) == 1


def test_cli_loop_accepts_equals_max_steps_and_backend(tmp_path, monkeypatch):
    fixture_data = json.loads(FIXTURE.read_text())

    class FakeSim:
        device = "fake-device"

        def ui(self, *, no_raw=True):
            return fixture_data

    class FakeLock:
        def __init__(self, identity):
            assert identity == "fake-device"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    class Provider:
        backend = "rizzo"
        model = "rizzo-flow-4b-q8_0"
        model_digest = None

        def choose_action(self, *, goal, outline, criteria):
            probabilities = {candidate: float(candidate == "done") for candidate in criteria}
            return ChoiceAnswer(
                "done",
                1.0,
                probabilities,
                self.model,
                1.0,
                calls=[
                    {"backend": self.backend, "model": self.model, "candidate_ids": list(criteria)}
                ],
            )

    monkeypatch.setattr("jev_sim.cli.SimUseClient", lambda device=None: FakeSim())
    monkeypatch.setattr("jev_sim.cli.DeviceLock", FakeLock)
    monkeypatch.setattr("jev_sim.cli.create_backend", lambda name: Provider())
    trace = tmp_path / "loop.jsonl"
    result = CliRunner().invoke(
        main,
        ["loop", "Finish", "--backend=rizzo", "--max-steps=30", "--trace", str(trace)],
    )
    assert result.exit_code == 0, result.output
    assert "[1/30]" in result.output
    record = json.loads(trace.read_text().strip())
    assert record["model"] == "rizzo-flow-4b-q8_0"
    assert record["provider_calls"][0]["candidate_ids"]
