from pathlib import Path

from scripts.generate_schema import SCHEMA_PATH, render


def test_committed_schema_matches_the_model():
    assert SCHEMA_PATH.read_text(encoding="utf-8") == render(), (
        "schemas/evaluation_result.json is stale — run scripts/generate_schema.py"
    )


def test_sdk_can_transform_the_model_into_a_structured_output_schema():
    from anthropic.lib._parse._transform import transform_schema
    from pydantic import TypeAdapter

    from hr_agents.models import EvaluationResult

    transformed = transform_schema(TypeAdapter(EvaluationResult).json_schema())
    assert transformed["additionalProperties"] is False
    assert "criteria" in transformed["required"]
