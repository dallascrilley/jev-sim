from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def ui_settings() -> dict:
    return json.loads((FIXTURES / "ui_settings.json").read_text(encoding="utf-8"))


@pytest.fixture
def mock_choice_response() -> dict:
    return {
        "model": "jev-1.13.0",
        "answers": {
            "action": {
                "type": "choice",
                "choice": "@9",
                "confidence": 0.91,
                "probabilities": {
                    "@5": 0.02,
                    "@7": 0.03,
                    "@9": 0.88,
                    "@10": 0.02,
                    "@11": 0.01,
                    "@43": 0.01,
                    "done": 0.01,
                    "scroll-up": 0.01,
                    "scroll-down": 0.005,
                    "wait": 0.005,
                },
            }
        },
        "usage": {"input_tokens": 120, "output_tokens": 40},
    }
