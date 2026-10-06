"""Shared, process-owned lock for full simulator loops.

This module and ``mobile-jev/scripts/mobile-agent/device-lock.mjs`` MUST stay
protocol-compatible.

Layout under the lock root (``AGENT_DEVICE_LOCK_ROOT`` or ``LOCK_ROOT``)::

    <sha256(identity)[:24]>.lock          lock directory containing owner.json
    <sha256(identity)[:24]>.lock.guard    acquisition guard directory

``owner.json`` is exactly ``{"pid": int > 0, "deviceId": identity, "runId": str}``.
A lock directory only appears at its final path with ``owner.json`` already inside
it (staging directory + atomic rename), so a valid lock is never half built.

Acquisition, including stale reclaim, is serialized by the guard:

1. ``mkdir <lock>.guard``. If it exists, fail closed. No wait, no retry.
2. While holding the guard, inspect the lock. A live owner is rejected. A dead owner
   is renamed to a tombstone and deleted. A missing or invalid owner record is
   rejected and never reclaimed automatically.
3. Build the new lock in ``<lock>.staging-<runId>`` and rename it to ``<lock>``.
4. Remove the guard.

Renaming a stale lock alone is not safe: two contenders can observe the same dead
owner, and the slower one then renames away the lock the faster one just created.
Every mutation of acquisition or stale reclaim therefore holds the guard.

Release is owner specific and needs no guard. A live owner is never reclaimed, so no
acquirer can replace the lock between release's read and its rename.

GUARD CRASH CLEANUP (manual, intentionally never automatic): the guard is held for
milliseconds. If a process is killed inside that window, ``<lock>.guard`` is left
behind and every acquirer fails closed with "acquisition guard is held or abandoned".
An operator must first confirm that no jev or mobile-jev process is acquiring (the
guard's ``guard.json`` records the pid), then run ``rm -r <root>/<hash>.lock.guard``.
Leftover ``.staging-*``, ``.stale-*`` and ``.release-*`` directories are inert garbage
and may be deleted at any time while nothing is acquiring.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid


LOCK_ROOT = Path.home() / ".local/state/agent-device-android/locks"


def lock_root() -> Path:
    override = os.environ.get("AGENT_DEVICE_LOCK_ROOT", "").strip()
    return Path(override) if override else LOCK_ROOT


def device_identity(device: str | None = None) -> str:
    return os.environ.get("AGENT_DEVICE_IDENTITY", "").strip() or device or "device"


def lock_path(identity: str) -> Path:
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return lock_root() / f"{key}.lock"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _valid_owner(owner: object, identity: str) -> bool:
    return (
        isinstance(owner, dict)
        and set(owner) == {"pid", "deviceId", "runId"}
        and type(owner["pid"]) is int
        and owner["pid"] > 0
        and owner["deviceId"] == identity
        and isinstance(owner["runId"], str)
        and owner["runId"] != ""
    )


def _run_id_at(directory: Path) -> object:
    try:
        owner = json.loads((directory / "owner.json").read_text(encoding="utf-8"))
        return owner.get("runId") if isinstance(owner, dict) else None
    except (OSError, ValueError):
        return None


class DeviceLock:
    def __init__(self, identity: str):
        self.identity = identity
        self.path = lock_path(identity)
        self.guard = self.path.with_name(f"{self.path.name}.guard")
        self.run_id = str(uuid.uuid4())
        self.owner = {"pid": os.getpid(), "deviceId": identity, "runId": self.run_id}
        self.acquired = False

    def acquire(self) -> "DeviceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.mkdir(self.guard, 0o700)
        except FileExistsError as exc:
            raise RuntimeError(
                f"Device lock acquisition guard is held or abandoned: {self.guard}. "
                "If no jev process is acquiring, remove it manually (rm -r)."
            ) from exc
        failure: BaseException | None = None
        staging = self.path.with_name(f"{self.path.name}.staging-{self.run_id}")
        try:
            try:
                (self.guard / "guard.json").write_text(
                    json.dumps({"pid": os.getpid(), "runId": self.run_id}), encoding="utf-8"
                )
            except OSError:
                pass  # Diagnostic only. The guard never depends on this file.
            if os.path.lexists(self.path):
                self._reclaim_dead_owner()
            os.mkdir(staging, 0o700)
            descriptor = os.open(
                staging / "owner.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(self.owner))
            if os.path.lexists(self.path):
                raise RuntimeError(f"Device lock appeared during acquisition: {self.path}")
            os.rename(staging, self.path)
            self.acquired = True
        except BaseException as exc:  # noqa: BLE001 - re-raised after guard cleanup
            failure = exc
            shutil.rmtree(staging, ignore_errors=True)
        try:
            shutil.rmtree(self.guard)
        except OSError as exc:
            if failure is None:
                self.release()
                failure = exc
        if failure is not None:
            raise failure
        return self

    def _reclaim_dead_owner(self) -> None:
        """Remove a dead owner's lock. Caller MUST hold the acquisition guard."""
        owner_path = self.path / "owner.json"
        try:
            owner = json.loads(owner_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise RuntimeError(
                f"Device lock has no owner record; refusing recovery: {self.path}"
            ) from exc
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Device lock owner record is unreadable: {owner_path}") from exc
        if not _valid_owner(owner, self.identity):
            raise RuntimeError(f"Device lock owner record is invalid: {owner_path}")
        if _pid_alive(owner["pid"]):
            raise RuntimeError(
                f"Device loop is already active (pid={owner['pid']}, runId={owner['runId']})"
            )
        stale = self.path.with_name(f"{self.path.name}.stale-{self.run_id}")
        os.rename(self.path, stale)
        shutil.rmtree(stale, ignore_errors=True)

    def release(self) -> None:
        """Owner-specific and tolerant: a missing or foreign lock is left alone."""
        if not self.acquired:
            return
        self.acquired = False
        if _run_id_at(self.path) != self.run_id:
            return
        tombstone = self.path.with_name(f"{self.path.name}.release-{self.run_id}")
        try:
            os.rename(self.path, tombstone)
        except FileNotFoundError:
            return
        if _run_id_at(tombstone) != self.run_id:
            # Replaced between read and rename: put it back if the path is free.
            try:
                os.rename(tombstone, self.path)
            except OSError:
                pass
            return
        shutil.rmtree(tombstone, ignore_errors=True)

    def __enter__(self) -> "DeviceLock":
        return self.acquire()

    def __exit__(self, *_: object) -> None:
        self.release()
