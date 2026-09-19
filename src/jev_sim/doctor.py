"""Environment diagnostics for jev-sim."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from jev_sim.sim_use import SimUseClient, which_sim_use
from jev_sim.typesafe import CONFIG_KEY_PATH, resolve_api_key, TypeSafeError


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def run_doctor(*, probe_devices: bool = True) -> list[Check]:
    checks: list[Check] = []

    bin_path = which_sim_use()
    if bin_path:
        checks.append(Check("sim-use", True, f"found at {bin_path}"))
        client = SimUseClient(bin=bin_path)
        try:
            ver = client.version()
            detail = (ver.stdout or ver.stderr).strip().splitlines()[:1]
            checks.append(
                Check(
                    "sim-use --version",
                    ver.ok,
                    detail[0] if detail else f"exit {ver.returncode}",
                )
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("sim-use --version", False, str(exc)))

        if probe_devices:
            try:
                devices = client.devices()
                checks.append(
                    Check(
                        "sim-use devices",
                        devices.ok,
                        (devices.stdout or devices.stderr).strip()[:200]
                        or f"exit {devices.returncode}",
                    )
                )
            except Exception as exc:  # noqa: BLE001
                checks.append(Check("sim-use devices", False, str(exc)))
    else:
        checks.append(
            Check(
                "sim-use",
                False,
                "not on PATH — brew tap lycorp-jp/tap && brew install lycorp-jp/tap/sim-use",
            )
        )

    # API key presence (never print the value).
    try:
        key = resolve_api_key(allow_config_file=True)
        source = "TYPESAFE_API_KEY env"
        if not os.environ.get("TYPESAFE_API_KEY", "").strip() and CONFIG_KEY_PATH.is_file():
            source = f"config file {CONFIG_KEY_PATH}"
        checks.append(Check("TYPESAFE_API_KEY", True, f"present ({source}, len={len(key)})"))
    except TypeSafeError as exc:
        checks.append(Check("TYPESAFE_API_KEY", False, str(exc)))

    httpx_ok = shutil.which("python3") is not None
    try:
        import httpx  # noqa: F401

        checks.append(Check("httpx", True, "importable"))
    except ImportError:
        checks.append(Check("httpx", False, "pip install httpx"))
        httpx_ok = False

    _ = httpx_ok
    return checks
