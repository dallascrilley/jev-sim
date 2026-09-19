"""Observe → candidates → Jev Choice → execute core loop."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from jev_sim.candidates import ActionCandidate, build_candidates, choice_criteria, outline_text
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
    ui: Mapping[str, Any] | None = None,
    dry_run: bool = False,
    include_meta: bool = True,
) -> StepResult:
    """One observe → decide → (optional) execute cycle.

    If `ui` is provided, skip the live `sim-use ui` call (useful for dry-run
    and tests). Execution is skipped when `dry_run=True` or choice is `done`.
    """
    sim = sim or SimUseClient()
    jev = jev or TypeSafeClient()
    t_total = time.perf_counter()

    t0 = time.perf_counter()
    if ui is None:
        ui_data = sim.ui(no_raw=True)
    else:
        ui_data = dict(ui)
    observe_ms = (time.perf_counter() - t0) * 1000.0

    candidates = build_candidates(ui_data, include_meta=include_meta)
    if len(candidates) < 2:
        raise RuntimeError("need at least 2 candidates for a TypeSafe Choice")

    outline = outline_text(ui_data)
    criteria = choice_criteria(candidates)

    t1 = time.perf_counter()
    answer = jev.choose_action(goal=goal, outline=outline, criteria=criteria)
    decide_ms = (time.perf_counter() - t1) * 1000.0
    # Prefer measured HTTP latency when available.
    if answer.latency_ms:
        decide_ms = answer.latency_ms

    by_id = {c.id: c for c in candidates}
    chosen = by_id.get(answer.choice)
    if chosen is None:
        raise RuntimeError(
            f"Jev chose unknown id {answer.choice!r}; known={list(by_id)}"
        )

    done = chosen.id == "done"
    executed = False
    t2 = time.perf_counter()
    if not dry_run and not done:
        sim.execute(chosen.kind, chosen.selector)
        executed = True
    execute_ms = (time.perf_counter() - t2) * 1000.0
    total_ms = (time.perf_counter() - t_total) * 1000.0

    return StepResult(
        goal=goal,
        candidates=candidates,
        choice=chosen.id,
        confidence=answer.confidence,
        probabilities=answer.probabilities,
        model=answer.model,
        observe_ms=observe_ms,
        decide_ms=decide_ms,
        execute_ms=execute_ms,
        total_ms=total_ms,
        executed=executed,
        dry_run=dry_run,
        done=done,
        outline=outline,
        ui=ui_data,
        answer=answer,
    )


def run_loop(
    goal: str,
    *,
    max_steps: int = 20,
    sim: SimUseClient | None = None,
    jev: TypeSafeClient | None = None,
    dry_run: bool = False,
    on_step: Any = None,
) -> list[StepResult]:
    """Repeat run_step until done, max_steps, or interruption."""
    sim = sim or SimUseClient()
    jev = jev or TypeSafeClient()
    results: list[StepResult] = []
    for i in range(max_steps):
        result = run_step(goal, sim=sim, jev=jev, dry_run=dry_run)
        results.append(result)
        if on_step:
            on_step(i, result)
        if result.done:
            break
    return results
