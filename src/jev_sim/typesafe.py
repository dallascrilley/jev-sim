"""TypeSafe System One client (Choice over candidate IDs)."""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import httpx

DEFAULT_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-1.13.0"
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
        "Set TYPESAFE_API_KEY in the environment (see .env.example). Never commit the key."
    )


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    confidence: float | None
    probabilities: dict[str, float]
    model: str
    latency_ms: float
    usage: dict[str, Any] | None = None
    probability_source: str = "backend-reported"
    calls: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class TypeSafeClient:
    api_key: str | None = None
    base_url: str = DEFAULT_URL
    model: str = DEFAULT_MODEL
    timeout: float = 30.0
    transport: httpx.BaseTransport | None = None
    auth_required: bool = True
    backend: str = "jev"
    allow_model_env_override: bool = True
    last_calls: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.base_url == DEFAULT_URL:
            self.base_url = os.environ.get("TYPESAFE_BASE_URL", self.base_url)
        if self.allow_model_env_override and self.model == DEFAULT_MODEL:
            self.model = os.environ.get("TYPESAFE_MODEL", self.model)

    def _key(self) -> str:
        return self.api_key or resolve_api_key()

    def _preflight(self) -> None:
        """Override this method for pinned local model backends."""

    def system_one(
        self,
        state: Any,
        questions: Mapping[str, Any],
        *,
        model: str | None = None,
    ) -> dict[str, Any]:
        self._preflight()
        payload = {
            "model": model or self.model,
            "state": state,
            "questions": dict(questions),
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.auth_required:
            headers["Authorization"] = f"Bearer {self._key()}"
        t0 = time.perf_counter()
        with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
            resp = client.post(self.base_url, headers=headers, content=json.dumps(payload))
        latency_ms = (time.perf_counter() - t0) * 1000.0
        if resp.status_code >= 400:
            raise TypeSafeError(f"{self.backend} HTTP {resp.status_code}; response body omitted")
        data = resp.json()
        if not isinstance(data, dict):
            raise TypeSafeError("TypeSafe response is not a JSON object")
        usage = data.get("usage")
        if data.get("truncated") or (
            isinstance(usage, dict)
            and (
                usage.get("truncated")
                or usage.get("truncated_questions")
                or usage.get("state_tokens_dropped", 0)
            )
        ):
            raise TypeSafeError("Backend truncated decision evidence")
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
        self.last_calls = []
        if not criteria:
            raise TypeSafeError("Choice requires at least 1 option")
        if len(criteria) == 1 and self.backend != "jev":
            selected = next(iter(criteria))
            return ChoiceAnswer(
                choice=selected,
                confidence=1.0,
                probabilities={selected: 1.0},
                model=self.model,
                latency_ms=0.0,
                probability_source="deterministic singleton; no provider call",
                calls=[],
                raw={"singleton": True},
            )
        if len(criteria) < 2:
            raise TypeSafeError("Choice requires at least 2 options")
        if self.backend == "jev" and len(criteria) > 255:
            raise TypeSafeError("Choice supports at most 255 options")
        try:
            if self.backend != "jev" and len(criteria) > 26:
                return self._choose_hierarchical(
                    goal=goal, outline=outline, criteria=criteria, instructions=instructions
                )
            return self._choose_direct(
                goal=goal, outline=outline, criteria=criteria, instructions=instructions
            )
        except Exception as exc:
            if self.last_calls and self.last_calls[-1].get("outcome") == "pending":
                started = self.last_calls[-1].get("started_at_monotonic", time.monotonic())
                self.last_calls[-1].update(
                    {
                        "outcome": "error",
                        "latency_ms": (time.monotonic() - started) * 1000.0,
                        "error": {"type": type(exc).__name__, "message": str(exc)[:300]},
                    }
                )
            raise

    def _choose_direct(
        self,
        *,
        goal: str,
        outline: str,
        criteria: Mapping[str, str],
        instructions: str | None = None,
    ) -> ChoiceAnswer:
        max_options = 255 if self.backend == "jev" else 26
        if not 2 <= len(criteria) <= max_options:
            raise TypeSafeError(f"{self.backend} Choice supports 2 to {max_options} options")
        started = time.perf_counter()
        if len(outline) > 8000:
            raise TypeSafeError("Screen outline exceeds the 8000-character input limit")
        call_record: dict[str, Any] = {
            "backend": self.backend,
            "model": self.model,
            "candidate_ids": list(criteria),
            "started_at_monotonic": time.monotonic(),
            "outcome": "pending",
        }
        self.last_calls.append(call_record)

        state = {
            "task": "mobile simulator computer-use",
            "goal": goal,
            "screen_outline": outline,
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
        try:
            raw = self.system_one(state, questions)
        except Exception as exc:
            call_record.update(
                {
                    "outcome": "error",
                    "latency_ms": (time.perf_counter() - started) * 1000.0,
                    "error": {"type": type(exc).__name__, "message": str(exc)[:300]},
                }
            )
            raise
        response_model = raw.get("model")
        if response_model != self.model:
            raise TypeSafeError(
                f"Backend model mismatch: expected {self.model}, received {response_model}"
            )
        answers = raw.get("answers")
        if not isinstance(answers, dict) or set(answers) != {"action"}:
            raise TypeSafeError("TypeSafe response answers must be an object")
        action = answers.get("action")
        if not isinstance(action, dict):
            raise TypeSafeError("TypeSafe response answers.action must be an object")
        choice = action.get("choice")
        if not choice:
            raise TypeSafeError(f"TypeSafe response missing answers.action.choice: {raw!r}")
        choice = str(choice)
        if choice not in criteria:
            raise TypeSafeError(f"Selected ID {choice!r} is not a candidate")
        probs = action.get("probabilities")
        if not isinstance(probs, dict) or set(probs) != set(criteria):
            raise TypeSafeError("Choice probability IDs do not match candidate IDs")
        if any(type(value) not in (int, float) for value in probs.values()):
            raise TypeSafeError("Choice probabilities must be numeric")
        probabilities = {str(k): float(v) for k, v in probs.items()}
        if any(not math.isfinite(v) or not 0 <= v <= 1 for v in probabilities.values()):
            raise TypeSafeError("Choice probabilities must be finite values from 0 to 1")
        if abs(sum(probabilities.values()) - 1.0) > 0.02:
            raise TypeSafeError("Choice probabilities do not sum to one")
        conf = action.get("confidence")
        if conf is not None and type(conf) not in (int, float):
            raise TypeSafeError("Choice confidence must be numeric")
        confidence = float(conf) if conf is not None else None
        if confidence is not None and (not math.isfinite(confidence) or not 0 <= confidence <= 1):
            raise TypeSafeError("Choice confidence must be a finite value from 0 to 1")
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else None
        call_latency_ms = (time.perf_counter() - started) * 1000.0
        call = {
            "backend": self.backend,
            "model": response_model,
            "candidate_ids": list(criteria),
            "selected_id": choice,
            "probabilities": probabilities,
            "latency_ms": call_latency_ms,
            "usage": usage,
            "outcome": "selected",
        }
        call_record.update(call)
        return ChoiceAnswer(
            choice=choice,
            confidence=confidence,
            probabilities=probabilities,
            model=response_model,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            usage=usage,
            probability_source=(
                "backend-reported, not task-calibrated"
                if self.backend != "jev"
                else "backend-reported"
            ),
            calls=[call_record],
            raw=raw,
        )

    def _choose_hierarchical(
        self,
        *,
        goal: str,
        outline: str,
        criteria: Mapping[str, str],
        instructions: str | None,
    ) -> ChoiceAnswer:
        """Reduce large local choices in deterministic groups of at most 26."""
        started = time.perf_counter()
        original_ids = list(criteria)
        clusters: list[tuple[str, dict[str, float]]] = [
            (candidate_id, {candidate_id: 1.0}) for candidate_id in original_ids
        ]
        calls: list[dict[str, Any]] = []

        while len(clusters) > 26:
            next_clusters: list[tuple[str, dict[str, float]]] = []
            for offset in range(0, len(clusters), 26):
                group = clusters[offset : offset + 26]
                if len(group) == 1:
                    next_clusters.append(group[0])
                    continue
                group_criteria = {candidate_id: criteria[candidate_id] for candidate_id, _ in group}
                answer = self._choose_direct(
                    goal=goal,
                    outline=outline,
                    criteria=group_criteria,
                    instructions=instructions,
                )
                calls.extend(answer.calls)
                distribution: dict[str, float] = {}
                group_mass = sum(answer.probabilities.values())
                for candidate_id, child_distribution in group:
                    weight = answer.probabilities[candidate_id] / group_mass
                    for leaf_id, probability in child_distribution.items():
                        distribution[leaf_id] = (
                            distribution.get(leaf_id, 0.0) + weight * probability
                        )
                next_clusters.append((answer.choice, distribution))
            clusters = next_clusters

        final_criteria = {candidate_id: criteria[candidate_id] for candidate_id, _ in clusters}
        final = self._choose_direct(
            goal=goal,
            outline=outline,
            criteria=final_criteria,
            instructions=instructions,
        )
        calls.extend(final.calls)
        full_distribution: dict[str, float] = {}
        final_mass = sum(final.probabilities.values())
        for candidate_id, child_distribution in clusters:
            weight = final.probabilities[candidate_id] / final_mass
            for leaf_id, probability in child_distribution.items():
                full_distribution[leaf_id] = (
                    full_distribution.get(leaf_id, 0.0) + weight * probability
                )
        if set(full_distribution) != set(original_ids):
            raise TypeSafeError("Hierarchical Choice lost one or more original candidate IDs")
        if abs(sum(full_distribution.values()) - 1.0) > 0.02:
            raise TypeSafeError("Hierarchical Choice probabilities do not sum to one")

        usage_totals: dict[str, int | float] = {}
        for call in calls:
            usage = call.get("usage")
            if isinstance(usage, dict):
                for key, value in usage.items():
                    if type(value) in (int, float):
                        usage_totals[key] = usage_totals.get(key, 0) + value
        return ChoiceAnswer(
            choice=final.choice,
            confidence=full_distribution[final.choice],
            probabilities=full_distribution,
            model=final.model,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            usage=usage_totals or None,
            probability_source="hierarchical product of backend distributions; derived and uncalibrated",
            calls=calls,
            raw={"hierarchical": True},
        )
