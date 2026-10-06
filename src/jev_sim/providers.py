"""Pinned Jev, Rizzo, and Tev1 Choice providers."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from jev_sim.typesafe import TypeSafeClient, TypeSafeError, resolve_api_key

RIZZO_BASE = "http://127.0.0.1:18094"
RIZZO_MODEL = "rizzo-flow-4b-q8_0"
RIZZO_FINGERPRINT = "5310c906db493b1a2414d9ae25b9fd7f0d15fd28e5b593810ce17f4d87bf7ae8"
RIZZO_FILES = {
    "rizzo-flow-4b-q8.gguf": "dbec3c89d33984772324e65a8ed56b48e958e301b856cb870a7c1b385d01691a"
}
TEV1_BASE = "http://127.0.0.1:11434"
TEV1_MODEL = "tev1:4b-q4_K_M"
TEV1_DIGEST = "07be32e6e5e3dcc6b2deae7c68b89321e99daeedb08521e92771cc155473cbd9"


class RizzoClient(TypeSafeClient):
    """Call the already-running pinned Rizzo service without starting it."""

    def __init__(self, *, timeout: float = 30.0, transport: httpx.BaseTransport | None = None):
        super().__init__(
            base_url=f"{RIZZO_BASE}/v1/systemone",
            model=RIZZO_MODEL,
            timeout=timeout,
            transport=transport,
            auth_required=False,
            backend="rizzo",
        )

    def _preflight(self) -> None:
        try:
            with httpx.Client(timeout=2.0, transport=self.transport) as client:
                health = client.get(f"{RIZZO_BASE}/health")
                models = client.get(f"{RIZZO_BASE}/v1/models")
            health.raise_for_status()
            models.raise_for_status()
            data = models.json()
            state = health.json()
            if not isinstance(state, dict) or state.get("status") != "ready":
                raise TypeSafeError("Rizzo is not ready")
            model = state.get("model", {})
            if (
                not isinstance(model, dict)
                or model.get("fingerprint") != RIZZO_FINGERPRINT
                or model.get("source_files") != RIZZO_FILES
            ):
                raise TypeSafeError("Rizzo pinned runtime fingerprint mismatch")
        except (httpx.HTTPError, ValueError) as exc:
            raise TypeSafeError(f"Rizzo readiness check failed: {exc}") from exc
        rows = data.get("models", []) if isinstance(data, dict) else []
        if not isinstance(rows, list) or not any(
            isinstance(row, dict) and row.get("name") == RIZZO_MODEL for row in rows
        ):
            raise TypeSafeError(f"Rizzo model identity mismatch: expected {RIZZO_MODEL}")


class Tev1Client(TypeSafeClient):
    """Call pinned Tev1 through the existing Ollama System One endpoint."""

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        lock_path: Path | None = None,
    ):
        self.lock_path = lock_path
        self.model_digest: str | None = None
        super().__init__(
            base_url=f"{TEV1_BASE}/v1/systemone",
            model=TEV1_MODEL,
            timeout=timeout,
            transport=transport,
            auth_required=False,
            backend="tev1",
        )

    def _preflight(self) -> None:
        try:
            expected = TEV1_DIGEST
            if self.lock_path is not None:
                lock = json.loads(self.lock_path.read_text(encoding="utf-8"))
                expected = lock[TEV1_MODEL]["digest"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise TypeSafeError(f"Tev1 digest pin is unavailable: {exc}") from exc
        try:
            with httpx.Client(timeout=2.0, transport=self.transport) as client:
                response = client.get(f"{TEV1_BASE}/api/tags")
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TypeSafeError(f"Tev1 model check failed: {exc}") from exc
        rows = data.get("models", []) if isinstance(data, dict) else []
        actual = (
            next(
                (
                    row.get("digest")
                    for row in rows
                    if isinstance(row, dict) and row.get("name") == TEV1_MODEL
                ),
                None,
            )
            if isinstance(rows, list)
            else None
        )
        if actual != expected:
            raise TypeSafeError(f"Tev1 digest mismatch for {TEV1_MODEL}")
        self.model_digest = str(expected)


def create_backend(name: str) -> TypeSafeClient:
    """Create one explicitly selected backend; never substitute another."""
    if name == "jev":
        return TypeSafeClient(
            api_key=resolve_api_key(),
            model="jev-1.13.0",
            backend="jev",
            allow_model_env_override=False,
        )
    if name == "rizzo":
        return RizzoClient()
    if name == "tev1":
        return Tev1Client()
    raise ValueError(f"unknown backend {name!r}")
