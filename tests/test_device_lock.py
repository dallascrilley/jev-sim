from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from jev_sim.device_lock import DeviceLock, lock_path

SRC = str(Path(__file__).resolve().parents[1] / "src")
NODE_MODULE = (
    Path(__file__).resolve().parents[2] / "mobile-jev/scripts/mobile-agent/device-lock.mjs"
)


@pytest.fixture(autouse=True)
def pinned_lock_env(tmp_path, monkeypatch):
    """Pin the lock root and identity so the developer's shell cannot redirect them."""
    root = tmp_path / "locks"
    monkeypatch.setenv("AGENT_DEVICE_LOCK_ROOT", str(root))
    monkeypatch.delenv("AGENT_DEVICE_IDENTITY", raising=False)
    return root


def _name(identity: str) -> str:
    return hashlib.sha256(identity.encode()).hexdigest()[:24] + ".lock"


def _dead_pid() -> int:
    child = subprocess.Popen([sys.executable, "-c", ""])
    child.wait()
    return child.pid


def _write_stale(identity: str, pid: int) -> Path:
    path = lock_path(identity)
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    (path / "owner.json").write_text(
        json.dumps({"pid": pid, "deviceId": identity, "runId": "stale"})
    )
    return path


def test_lock_uses_shared_hashed_filename(pinned_lock_env):
    identity = "R52T50B4F1D"
    lock = DeviceLock(identity)
    with lock:
        owner = json.loads((pinned_lock_env / _name(identity) / "owner.json").read_text())
        assert set(owner) == {"pid", "deviceId", "runId"}
        assert owner["deviceId"] == identity
        assert owner["pid"] == os.getpid()
    assert not (pinned_lock_env / _name(identity)).exists()


def test_lock_root_honors_environment_like_node(pinned_lock_env):
    assert lock_path("device").parent == pinned_lock_env


def test_lock_refuses_live_owner(pinned_lock_env):
    first = DeviceLock("device").acquire()
    try:
        with pytest.raises(RuntimeError, match="already active"):
            DeviceLock("device").acquire()
        assert not (pinned_lock_env / (_name("device") + ".guard")).exists()
    finally:
        first.release()


def test_lock_reclaims_dead_owner_without_residue(pinned_lock_env):
    path = _write_stale("device", _dead_pid())
    with DeviceLock("device"):
        owner = json.loads((path / "owner.json").read_text())
        assert owner["pid"] == os.getpid()
        assert [p.name for p in pinned_lock_env.iterdir()] == [_name("device")]
    assert list(pinned_lock_env.iterdir()) == []


def test_guard_fails_closed_and_is_never_reclaimed(pinned_lock_env):
    guard = pinned_lock_env / (_name("device") + ".guard")
    guard.mkdir(parents=True)
    stale = _write_stale("device", _dead_pid())
    with pytest.raises(RuntimeError, match="guard is held or abandoned"):
        DeviceLock("device").acquire()
    assert guard.exists()
    assert json.loads((stale / "owner.json").read_text())["runId"] == "stale"
    shutil.rmtree(guard)
    with DeviceLock("device"):
        assert json.loads((stale / "owner.json").read_text())["pid"] == os.getpid()


@pytest.mark.parametrize(
    "content",
    [
        None,
        "{not json",
        json.dumps({"pid": 0, "deviceId": "device", "runId": "x"}),
        json.dumps({"pid": True, "deviceId": "device", "runId": "x"}),
        json.dumps({"pid": 1, "deviceId": "other", "runId": "x"}),
        json.dumps({"pid": 1, "deviceId": "device", "runId": "x", "extra": 1}),
    ],
)
def test_invalid_owner_record_fails_closed(pinned_lock_env, content):
    path = lock_path("device")
    path.mkdir(parents=True)
    if content is not None:
        (path / "owner.json").write_text(content)
    with pytest.raises(RuntimeError):
        DeviceLock("device").acquire()
    assert path.exists()
    assert not (pinned_lock_env / (_name("device") + ".guard")).exists()


def test_release_is_owner_specific_and_tolerates_missing_lock(pinned_lock_env):
    lock = DeviceLock("device").acquire()
    shutil.rmtree(lock_path("device"))
    lock.release()  # Missing lock: no error.
    lock.release()
    first = DeviceLock("device").acquire()
    foreign = json.dumps({"pid": os.getpid(), "deviceId": "device", "runId": "someone-else"})
    (lock_path("device") / "owner.json").write_text(foreign)
    first.release()
    assert (lock_path("device") / "owner.json").read_text() == foreign
    assert [p.name for p in pinned_lock_env.iterdir()] == [_name("device")]


CONTENDER = """
import os, sys, time
sys.path.insert(0, os.environ["JEV_SIM_SRC"])
from jev_sim.device_lock import DeviceLock
while time.time() * 1000 < float(os.environ["START_AT"]):
    pass
try:
    lock = DeviceLock("device").acquire()
    print("ACQUIRED", flush=True)
    time.sleep(0.5)
    lock.release()
except Exception as error:
    print("REJECTED " + str(error), flush=True)
"""

NODE_CONTENDER = """
import { acquireDeviceLock } from %s;
while (Date.now() < Number(process.env.START_AT));
try {
  const release = await acquireDeviceLock('device');
  console.log('ACQUIRED');
  await new Promise((resolve) => setTimeout(resolve, 500));
  await release();
} catch (error) {
  console.log('REJECTED ' + error.message);
}
""" % json.dumps(NODE_MODULE.as_uri())


def _race(kinds: list[str]) -> list[str]:
    _write_stale("device", _dead_pid())
    env = {**os.environ, "JEV_SIM_SRC": SRC, "START_AT": str((time.time() + 0.8) * 1000)}
    env["START_AT"] = str(int((time.time() + 0.8) * 1000))
    commands = {
        "python": [sys.executable, "-c", CONTENDER],
        "node": ["node", "--input-type=module", "-e", NODE_CONTENDER],
    }
    processes = [
        subprocess.Popen(
            commands[kind], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        for kind in kinds
    ]
    lines = []
    for process in processes:
        out, err = process.communicate(timeout=30)
        assert process.returncode == 0, err
        lines.append(out.strip())
    return lines


def _assert_single_winner(lines: list[str]) -> None:
    assert lines.count("ACQUIRED") == 1, lines
    for line in lines:
        assert line == "ACQUIRED" or line.startswith("REJECTED "), line


def test_simultaneous_stale_lock_recovery_has_one_acquirer():
    for _ in range(5):
        _assert_single_winner(_race(["python"] * 6))


@pytest.mark.skipif(
    shutil.which("node") is None or not NODE_MODULE.exists(), reason="node lock unavailable"
)
def test_python_and_node_share_one_stale_lock_recovery():
    for _ in range(4):
        _assert_single_winner(_race(["python", "node"] * 3))
