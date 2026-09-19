from __future__ import annotations

from click.testing import CliRunner

from jev_sim.cli import main
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
