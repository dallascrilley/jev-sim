"""Observe, choose from bounded candidates, verify, and optionally execute."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping

from jev_sim.candidates import (
    ActionCandidate,
    build_candidates,
    choice_criteria,
    outline_text,
    semantic_target_id,
)
from jev_sim.sim_use import SimUseClient
from jev_sim.typesafe import ChoiceAnswer, TypeSafeClient


@dataclass
class StepResult:
    goal: str
    candidates: list[ActionCandidate]
    choice: str
    confidence: float | None
    probabilities: dict[str, float]
    model: str
    observe_ms: float
    decide_ms: float
    execute_ms: float
    total_ms: float
    executed: bool
    dry_run: bool
    done: bool
    backend: str = "jev"
    model_digest: str | None = None
    probability_source: str = "backend-reported"
    provider_calls: list[dict[str, Any]] = field(default_factory=list)
    outline: str = ""
    ui: dict[str, Any] = field(default_factory=dict, repr=False)
    answer: ChoiceAnswer | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("answer", None)
        d["candidates"] = [c.to_dict() for c in self.candidates]
        return d

    def summary_line(self) -> str:
        conf = f"{self.confidence:.3f}" if self.confidence is not None else "?"
        mode = "dry-run" if self.dry_run else ("done" if self.done else "exec")
        return (
            f"choice={self.choice} conf={conf} "
            f"observe={self.observe_ms:.0f}ms decide={self.decide_ms:.0f}ms "
            f"exec={self.execute_ms:.0f}ms total={self.total_ms:.0f}ms [{mode}]"
        )


def run_step(
    goal: str,
    *,
    sim: SimUseClient | None = None,
    jev: TypeSafeClient | None = None,
    provider: TypeSafeClient | None = None,
    ui: Mapping[str, Any] | None = None,
    dry_run: bool = False,
    include_meta: bool = True,
    trace_callback: Callable[[dict[str, Any]], None] | None = None,
) -> StepResult:
    """Run one observation and bounded choice, then verify before execution."""
    sim = sim or SimUseClient()
    decision = provider or jev or TypeSafeClient()
    t_total = time.perf_counter()
    observe_ms = decide_ms = execute_ms = 0.0
    ui_data: dict[str, Any] = {}
    candidates: list[ActionCandidate] = []
    answer: ChoiceAnswer | None = None
    chosen: ActionCandidate | None = None
    error: BaseException | None = None
    executed = False
    done = False
    result: StepResult | None = None
    try:
        t0 = time.perf_counter()
        ui_data = sim.ui(no_raw=True) if ui is None else dict(ui)
        observe_ms = (time.perf_counter() - t0) * 1000.0

        candidates = build_candidates(ui_data, include_meta=include_meta)
        if not candidates:
            raise RuntimeError("need at least 1 candidate for a Choice")

        outline = outline_text(ui_data)
        criteria = choice_criteria(candidates)
        t1 = time.perf_counter()
        answer = decision.choose_action(goal=goal, outline=outline, criteria=criteria)
        decide_ms = (time.perf_counter() - t1) * 1000.0
        if answer.latency_ms:
            decide_ms = answer.latency_ms

        by_id = {candidate.id: candidate for candidate in candidates}
        chosen = by_id.get(answer.choice)
        if chosen is None:
            raise RuntimeError(f"Provider chose unknown ID {answer.choice!r}; known={list(by_id)}")

        done = chosen.id == "done"
        t2 = time.perf_counter()
        if not dry_run and not done:
            fresh_ui = sim.ui(no_raw=True)
            fresh_ids = {
                candidate.id for candidate in build_candidates(fresh_ui, include_meta=include_meta)
            }
            if chosen.id not in fresh_ids:
                raise RuntimeError(f"Selected action {chosen.id!r} is stale")
            if chosen.kind == "tap":
                before_id = semantic_target_id(ui_data, chosen.id)
                after_id = semantic_target_id(fresh_ui, chosen.id)
                if before_id is None or after_id is None or before_id != after_id:
                    raise RuntimeError(f"Stale alias {chosen.id!r}: semantic target changed")
            sim.execute(chosen.kind, chosen.selector)
            executed = True
        execute_ms = (time.perf_counter() - t2) * 1000.0
        result = StepResult(
            goal=goal,
            candidates=candidates,
            choice=chosen.id,
            confidence=answer.confidence,
            probabilities=answer.probabilities,
            model=answer.model,
            observe_ms=observe_ms,
            decide_ms=decide_ms,
            execute_ms=execute_ms,
            total_ms=(time.perf_counter() - t_total) * 1000.0,
            executed=executed,
            dry_run=dry_run,
            done=done,
            backend=getattr(decision, "backend", "jev"),
            model_digest=getattr(decision, "model_digest", None),
            probability_source=answer.probability_source,
            provider_calls=answer.calls,
            outline=outline,
            ui=ui_data,
            answer=answer,
        )
        return result
    except BaseException as exc:
        error = exc
        raise
    finally:
        total_ms = (time.perf_counter() - t_total) * 1000.0
        if trace_callback:
            trace_callback(
                {
                    "event": "step",
                    "goal": goal,
                    "backend": getattr(decision, "backend", "jev"),
                    "model": answer.model if answer else getattr(decision, "model", None),
                    "model_digest": getattr(decision, "model_digest", None),
                    "candidate_ids": [candidate.id for candidate in candidates],
                    "selected_id": chosen.id if chosen else None,
                    "confidence": answer.confidence if answer else None,
                    "probabilities": answer.probabilities if answer else None,
                    "probability_source": answer.probability_source if answer else None,
                    "provider_calls": answer.calls
                    if answer
                    else getattr(decision, "last_calls", []),
                    "timings_ms": {
                        "observe": observe_ms,
                        "decide": decide_ms,
                        "execute": execute_ms,
                        "total": total_ms,
                    },
                    "dry_run": dry_run,
                    "executed": executed,
                    "outcome": (
                        "error"
                        if error
                        else "done"
                        if done
                        else "executed"
                        if executed
                        else "decision"
                    ),
                    "error": (
                        {"type": type(error).__name__, "message": str(error)[:500]}
                        if error
                        else None
                    ),
                }
            )


def run_loop(
    goal: str,
    *,
    max_steps: int = 20,
    sim: SimUseClient | None = None,
    jev: TypeSafeClient | None = None,
    provider: TypeSafeClient | None = None,
    dry_run: bool = False,
    on_step: Any = None,
    trace_callback: Callable[[dict[str, Any]], None] | None = None,
) -> list[StepResult]:
    """Repeat verified steps until done or max_steps."""
    if max_steps < 1:
        raise ValueError("max_steps must be at least 1")
    sim = sim or SimUseClient()
    decision = provider or jev or TypeSafeClient()
    results: list[StepResult] = []
    for i in range(max_steps):
        result = run_step(
            goal,
            sim=sim,
            provider=decision,
            dry_run=dry_run,
            trace_callback=trace_callback,
        )
        results.append(result)
        if on_step:
            on_step(i, result)
        if result.done:
            break
    return results
