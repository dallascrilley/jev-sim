"""TypeSafe System One client (Choice over candidate IDs)."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import httpx

DEFAULT_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
CONFIG_KEY_PATH = Path("/home/box/.config/typesafe/api_key")


class TypeSafeError(RuntimeError):
    pass


def _load_dotenv() -> None:
    """Minimal .env loader; does not override existing env vars."""
    for env_path in (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"):
        if not env_path.is_file():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
        break


def resolve_api_key(*, allow_config_file: bool = True) -> str:
    """Return TYPESAFE_API_KEY from env (or optional config file). Never print it."""
    _load_dotenv()
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if key:
        return key
    if allow_config_file and CONFIG_KEY_PATH.is_file():
        # Prefer first non-empty line; file may contain comments.
        for line in CONFIG_KEY_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                # Also accept KEY=value form.
                if "=" in line and line.split("=", 1)[0].strip().upper() in {
                    "TYPESAFE_API_KEY",
                    "API_KEY",
                }:
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
                return line
    raise TypeSafeError(
        "Set TYPESAFE_API_KEY in the environment (see .env.example). "
        "Never commit the key."
    )


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    confidence: float | None
    probabilities: dict[str, float]
    model: str
    latency_ms: float
    usage: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class TypeSafeClient:
    api_key: str | None = None
    base_url: str = DEFAULT_URL
    model: str = DEFAULT_MODEL
    timeout: float = 30.0
    transport: httpx.BaseTransport | None = None

    def __post_init__(self) -> None:
        self.base_url = os.environ.get("TYPESAFE_BASE_URL", self.base_url)
        self.model = os.environ.get("TYPESAFE_MODEL", self.model)

    def _key(self) -> str:
        return self.api_key or resolve_api_key()

    def system_one(
        self,
        state: Any,
        questions: Mapping[str, Any],
        *,
        model: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "model": model or self.model,
            "state": state,
            "questions": dict(questions),
        }
        headers = {
            "Authorization": f"Bearer {self._key()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        t0 = time.perf_counter()
        with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
            resp = client.post(self.base_url, headers=headers, content=json.dumps(payload))
        latency_ms = (time.perf_counter() - t0) * 1000.0
        if resp.status_code >= 400:
            body = resp.text[:500]
            raise TypeSafeError(f"TypeSafe HTTP {resp.status_code}: {body}")
        data = resp.json()
        if not isinstance(data, dict):
            raise TypeSafeError("TypeSafe response is not a JSON object")
        data["_latency_ms"] = latency_ms
        return data

    def choose_action(
        self,
        *,
        goal: str,
        outline: str,
        criteria: Mapping[str, str],
        instructions: str | None = None,
    ) -> ChoiceAnswer:
        if len(criteria) < 2:
            raise TypeSafeError("Choice requires at least 2 options")
        if len(criteria) > 255:
            raise TypeSafeError("Choice supports at most 255 options")

        state = {
            "task": "mobile simulator computer-use",
            "goal": goal,
            "screen_outline": outline[:8000],
            "candidate_ids": list(criteria.keys()),
        }
        questions = {
            "action": {
                "type": "choice",
                "instructions": instructions
                or (
                    "Pick the single next action that best advances `goal` given "
                    "`screen_outline`. Prefer a concrete tap when one element "
                    "clearly matches. Use scroll-up/scroll-down when the target "
                    "is off-screen. Use wait for loading. Use done only when "
                    "the goal is already satisfied."
                ),
                "criteria": dict(criteria),
            }
        }
        raw = self.system_one(state, questions)
        answers = raw.get("answers") or {}
        action = answers.get("action") or {}
        choice = action.get("choice")
        if not choice:
            raise TypeSafeError(f"TypeSafe response missing answers.action.choice: {raw!r}")
        probs = action.get("probabilities") or {}
        conf = action.get("confidence")
        return ChoiceAnswer(
            choice=str(choice),
            confidence=float(conf) if conf is not None else None,
            probabilities={str(k): float(v) for k, v in probs.items()},
            model=str(raw.get("model") or self.model),
            latency_ms=float(raw.get("_latency_ms") or 0.0),
            usage=raw.get("usage") if isinstance(raw.get("usage"), dict) else None,
            raw=raw,
        )
