"""Thin subprocess wrapper around the sim-use CLI (mockable)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

Runner = Callable[[Sequence[str], float | None], "CmdResult"]


@dataclass(frozen=True)
class CmdResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class SimUseError(RuntimeError):
    def __init__(self, message: str, result: CmdResult | None = None):
        super().__init__(message)
        self.result = result


def default_bin() -> str:
    return os.environ.get("SIM_USE_BIN", "sim-use")


def which_sim_use(bin_name: str | None = None) -> str | None:
    return shutil.which(bin_name or default_bin())


def _subprocess_runner(argv: Sequence[str], timeout: float | None) -> CmdResult:
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SimUseError(f"sim-use binary not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise SimUseError(f"sim-use timed out after {timeout}s: {' '.join(argv)}") from exc
    return CmdResult(
        argv=tuple(argv),
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )


@dataclass
class SimUseClient:
    """Call sim-use via subprocess. Inject `runner` in tests."""

    bin: str = "sim-use"
    device: str | None = None
    timeout: float = 60.0
    runner: Runner = _subprocess_runner

    def __post_init__(self) -> None:
        if self.bin == "sim-use":
            self.bin = default_bin()
        if self.device is None:
            self.device = os.environ.get("JEV_SIM_DEVICE") or None

    def _argv(self, *parts: str, json_out: bool = False) -> list[str]:
        argv = [self.bin, *parts]
        if json_out:
            argv.append("--json")
        if self.device:
            argv.extend(["--device", self.device])
        return argv

    def run(self, *parts: str, json_out: bool = False) -> CmdResult:
        argv = self._argv(*parts, json_out=json_out)
        return self.runner(argv, self.timeout)

    def version(self) -> CmdResult:
        return self.runner([self.bin, "--version"], self.timeout)

    def devices(self) -> CmdResult:
        return self.run("devices", json_out=True)

    def ui(self, *, no_raw: bool = True) -> dict[str, Any]:
        parts = ["ui"]
        if no_raw:
            parts.append("--no-raw")
        result = self.run(*parts, json_out=True)
        if not result.ok:
            raise SimUseError(
                f"sim-use ui failed (exit {result.returncode}): {result.stderr or result.stdout}",
                result,
            )
        return _parse_json(result.stdout, result)

    def tap(self, selector: str) -> CmdResult:
        result = self.run("tap", selector)
        if not result.ok:
            raise SimUseError(
                f"sim-use tap {selector!r} failed: {result.stderr or result.stdout}",
                result,
            )
        return result

    def gesture(self, name: str) -> CmdResult:
        result = self.run("gesture", name)
        if not result.ok:
            raise SimUseError(
                f"sim-use gesture {name!r} failed: {result.stderr or result.stdout}",
                result,
            )
        return result

    def execute(self, kind: str, selector: str) -> CmdResult | None:
        """Execute a chosen action. Returns None for meta no-ops (done/wait)."""
        if kind == "tap":
            return self.tap(selector)
        if kind == "gesture":
            # Map our ids to sim-use gesture names.
            name = selector  # scroll-up / scroll-down
            return self.gesture(name)
        if selector == "wait":
            import time

            time.sleep(0.5)
            return None
        if selector == "done":
            return None
        raise SimUseError(f"unknown action kind={kind!r} selector={selector!r}")


def _parse_json(text: str, result: CmdResult) -> dict[str, Any]:
    text = text.strip()
    if not text:
        raise SimUseError("sim-use returned empty stdout", result)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SimUseError(f"sim-use stdout is not JSON: {exc}", result) from exc
    if not isinstance(data, Mapping):
        raise SimUseError("sim-use JSON root must be an object", result)
    return dict(data)
