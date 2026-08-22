from __future__ import annotations

import pytest
import yaml

from app.interview.knowledge import load_knowledge


def test_default_role_resolves(kb):
    ids = [q.id for q in kb.questions_for("default")]
    assert "intro" in ids
    assert ids[0] == "intro"


def test_unknown_role_falls_back_to_default(kb):
    assert [q.id for q in kb.questions_for("nonexistent-role")] == [
        q.id for q in kb.questions_for("default")
    ]


def test_inherits_and_extra_questions_insert_after_anchor(kb):
    ids = [q.id for q in kb.questions_for("virtual_assistant")]
    assert ids.index("va_written_english") == ids.index("intro") + 1
    assert ids.index("va_admin_tools") == ids.index("tools") + 1


def test_weights_are_normalised(kb):
    assert pytest.approx(sum(kb.normalised_weights.values()), abs=1e-9) == 1.0


def test_script_falls_back_through_inherits(kb):
    opening, closing = kb.script_for("virtual_assistant")
    assert "{first_name}" in opening and closing


def test_question_with_unknown_category_is_rejected(tmp_path):
    src = load_knowledge()
    questions = {"roles": {"default": {"questions": [
        {"id": "q1", "category": "does_not_exist", "text": "hi", "required": True}
    ]}}}
    (tmp_path / "questions.yaml").write_text(yaml.safe_dump(questions))
    (tmp_path / "rubric.yaml").write_text(yaml.safe_dump(src.rubric_raw))
    (tmp_path / "policies.yaml").write_text(yaml.safe_dump(src.policies_raw))
    with pytest.raises(ValueError, match=r"not in rubric\.yaml"):
        load_knowledge(tmp_path)


def test_rule_referencing_unknown_fact_is_rejected(tmp_path):
    src = load_knowledge()
    policies = dict(src.policies_raw)
    policies["disqualification_rules"] = [
        {"id": "bogus", "fact": "not_a_fact", "op": "is_true", "message": "x"}
    ]
    policies["early_exit_rules"] = []
    (tmp_path / "questions.yaml").write_text(yaml.safe_dump(src.questions_raw))
    (tmp_path / "rubric.yaml").write_text(yaml.safe_dump(src.rubric_raw))
    (tmp_path / "policies.yaml").write_text(yaml.safe_dump(policies))
    with pytest.raises(ValueError, match="unknown fact"):
        load_knowledge(tmp_path)


def test_missing_file_is_reported_clearly(tmp_path):
    with pytest.raises(FileNotFoundError, match=r"questions\.yaml"):
        load_knowledge(tmp_path)
