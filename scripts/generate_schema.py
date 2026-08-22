#!/usr/bin/env python3
"""Regenerate schemas/evaluation_result.json from hr_agents/models.py.

The Pydantic model is the source of truth; the JSON file exists so the contract
is readable without running Python. `tests/test_schema.py` fails if they drift.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from hr_agents.models import EvaluationResult

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "evaluation_result.json"


def build() -> dict:
    schema = TypeAdapter(EvaluationResult).json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["description"] = (
        "Structured output the model returns for a single video submission. "
        "Generated from hr_agents/models.py by scripts/generate_schema.py — do not edit by hand. "
        "The model scores criteria; hr_agents/scoring.py computes the weighted total and "
        "applies the pass threshold."
    )
    return schema


def render() -> str:
    return json.dumps(build(), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    SCHEMA_PATH.write_text(render(), encoding="utf-8")
    print(f"wrote {SCHEMA_PATH}")
