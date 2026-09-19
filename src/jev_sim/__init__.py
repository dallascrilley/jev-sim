"""jev-sim — fast mobile-simulator computer-use via sim-use + TypeSafe Jev."""

__version__ = "0.1.0"

from jev_sim.candidates import ActionCandidate, build_candidates
from jev_sim.loop import StepResult, run_step

__all__ = [
    "__version__",
    "ActionCandidate",
    "build_candidates",
    "StepResult",
    "run_step",
]
