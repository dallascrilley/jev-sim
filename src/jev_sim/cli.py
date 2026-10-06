"""CLI entrypoint for steps, loops, dry runs, and diagnostics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from jev_sim import __version__
from jev_sim.candidates import build_candidates, choice_criteria, outline_text
from jev_sim.device_lock import DeviceLock, device_identity
from jev_sim.doctor import run_doctor
from jev_sim.loop import run_loop, run_step
from jev_sim.providers import create_backend
from jev_sim.sim_use import SimUseClient
from jev_sim.trace import TraceWriter
from jev_sim.typesafe import TypeSafeError


def _load_ui_file(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise click.ClickException("UI fixture must be a JSON object")
    return data


def _print_step(result: Any, *, verbose: bool = False) -> None:
    click.echo(result.summary_line())
    if verbose:
        click.echo(json.dumps(result.to_dict(), indent=2)[:4000])


def _create_backend_with_trace(name: str, sink: TraceWriter) -> Any:
    try:
        return create_backend(name)
    except Exception as exc:  # noqa: BLE001
        model = {"jev": "jev-1.13.0", "rizzo": "rizzo-flow-4b-q8_0", "tev1": "tev1:4b-q4_K_M"}.get(
            name
        )
        sink.write(
            {
                "event": "backend_error",
                "backend": name,
                "model": model,
                "timings_ms": {"total": 0.0},
                "outcome": "error",
                "error": {"type": type(exc).__name__, "message": str(exc)[:500]},
            }
        )
        raise


@click.group()
@click.version_option(__version__, prog_name="jev-sim")
def main() -> None:
    """Fast mobile-simulator computer-use: sim-use + TypeSafe Jev."""


@main.command("doctor")
@click.option("--no-devices", is_flag=True, help="Skip sim-use devices probe")
def doctor_cmd(no_devices: bool) -> None:
    """Check sim-use binary, devices, and TYPESAFE_API_KEY presence."""
    checks = run_doctor(probe_devices=not no_devices)
    failed = 0
    for c in checks:
        mark = "ok" if c.ok else "FAIL"
        click.echo(f"[{mark}] {c.name}: {c.detail}")
        if not c.ok:
            failed += 1
    raise SystemExit(1 if failed else 0)


@main.command("step")
@click.argument("goal")
@click.option("--device", default=None, help="sim-use --device UDID")
@click.option(
    "--backend", type=click.Choice(["jev", "rizzo", "tev1"]), default="jev", show_default=True
)
@click.option(
    "--trace",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Append JSONL decision trace",
)
@click.option("--dry-run", is_flag=True, help="Decide but do not execute")
@click.option(
    "--ui-file",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Use a saved sim-use ui --json instead of a live observe",
)
@click.option("--verbose", "-v", is_flag=True)
def step_cmd(
    goal: str,
    device: str | None,
    backend: str,
    trace: Path | None,
    dry_run: bool,
    ui_file: Path | None,
    verbose: bool,
) -> None:
    """Observe once, ask Jev, optionally execute."""
    sim = SimUseClient(device=device)
    ui = _load_ui_file(ui_file) if ui_file else None
    # Fixture-driven steps never execute against a device.
    decide_only = dry_run or ui is not None
    try:
        with TraceWriter(trace) as sink:
            provider = _create_backend_with_trace(backend, sink)
            with DeviceLock(device_identity(sim.device)):
                result = run_step(
                    goal,
                    sim=sim,
                    provider=provider,
                    ui=ui,
                    dry_run=decide_only,
                    trace_callback=sink.write,
                )
    except TypeSafeError as exc:
        raise click.ClickException(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(str(exc)) from exc
    _print_step(result, verbose=verbose)


@main.command("loop")
@click.argument("goal")
@click.option("--device", default=None, help="sim-use --device UDID")
@click.option(
    "--backend", type=click.Choice(["jev", "rizzo", "tev1"]), default="jev", show_default=True
)
@click.option(
    "--trace",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Append JSONL decision trace",
)
@click.option("--max-steps", default=20, show_default=True, type=int)
@click.option("--dry-run", is_flag=True, help="Decide but do not execute")
@click.option("--verbose", "-v", is_flag=True)
def loop_cmd(
    goal: str,
    device: str | None,
    backend: str,
    trace: Path | None,
    max_steps: int,
    dry_run: bool,
    verbose: bool,
) -> None:
    """Repeat observe → decide → act until done or max-steps."""
    sim = SimUseClient(device=device)

    def on_step(i: int, result: Any) -> None:
        click.echo(f"[{i + 1}/{max_steps}] {result.summary_line()}")
        if verbose:
            click.echo(f"  outline head: {result.outline[:120]!r}")

    try:
        with TraceWriter(trace) as sink:
            provider = _create_backend_with_trace(backend, sink)
            identity = device_identity(sim.device)
            try:
                with DeviceLock(identity):
                    results = run_loop(
                        goal,
                        max_steps=max_steps,
                        sim=sim,
                        provider=provider,
                        dry_run=dry_run,
                        on_step=on_step,
                        trace_callback=sink.write,
                    )
            except Exception as exc:  # noqa: BLE001
                sink.write(
                    {
                        "event": "loop_error",
                        "backend": backend,
                        "model": provider.model,
                        "model_digest": getattr(provider, "model_digest", None),
                        "timings_ms": {"total": 0.0},
                        "outcome": "error",
                        "error": {"type": type(exc).__name__, "message": str(exc)[:500]},
                    }
                )
                raise
    except TypeSafeError as exc:
        raise click.ClickException(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(str(exc)) from exc
    if not results:
        raise click.ClickException("no steps ran")
    if results[-1].done:
        click.echo("stopped: done")
    else:
        click.echo(f"stopped: max-steps={max_steps}")


@main.command("dry-run")
@click.argument("goal")
@click.option(
    "--ui-file",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to sim-use ui --json fixture",
)
@click.option("--verbose", "-v", is_flag=True)
@click.option(
    "--backend", type=click.Choice(["jev", "rizzo", "tev1"]), default="jev", show_default=True
)
@click.option(
    "--trace",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Append JSONL decision trace",
)
@click.option(
    "--list-only",
    is_flag=True,
    help="Only list candidates; skip TypeSafe call",
)
def dry_run_cmd(
    goal: str,
    ui_file: Path,
    verbose: bool,
    backend: str,
    trace: Path | None,
    list_only: bool,
) -> None:
    """Parse a UI fixture, build candidates, optionally call Jev (no execute)."""
    ui = _load_ui_file(ui_file)
    candidates = build_candidates(ui)
    click.echo(f"candidates ({len(candidates)}):")
    for c in candidates:
        click.echo(f"  {c.id:16} {c.kind:8} {c.label}")
    if list_only:
        return
    try:
        with TraceWriter(trace) as sink:
            provider = _create_backend_with_trace(backend, sink)
            result = run_step(
                goal, ui=ui, provider=provider, dry_run=True, trace_callback=sink.write
            )
    except TypeSafeError as exc:
        raise click.ClickException(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(str(exc)) from exc
    _print_step(result, verbose=verbose)
    if verbose:
        click.echo("--- criteria ---")
        click.echo(json.dumps(choice_criteria(candidates), indent=2)[:2000])
        click.echo("--- outline ---")
        click.echo(outline_text(ui)[:1500])


if __name__ == "__main__":
    raise SystemExit(main(standalone_mode=True) or 0)
