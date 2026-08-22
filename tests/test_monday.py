from __future__ import annotations

import json

import httpx

from app.clients.monday import MondayClient
from app.config import Settings
from app.models import Recommendation


def build(**overrides) -> tuple[MondayClient, list[dict]]:
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.append(body)
        query = body["query"]
        if "items_page_by_column_values" in query:
            return httpx.Response(200, json={"data": {"items_page_by_column_values": {"items": []}}})
        if "create_item" in query:
            return httpx.Response(200, json={"data": {"create_item": {"id": "item-1"}}})
        return httpx.Response(200, json={"data": {}})

    settings = Settings(
        monday_api_key="fake-token",
        monday_board_id="123456",
        monday_group_proceed="group_proceed",
        monday_group_rejected="group_rejected",
        **overrides,
    )
    return MondayClient(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))), captured


def test_unmapped_columns_are_skipped_not_guessed():
    """Writing to a wrong column id silently corrupts a board, so anything the
    operator has not mapped must be dropped."""
    client, _ = build(monday_column_map={"status": "status4"})
    values = client.build_column_values(
        recommendation="PROCEED", overall_score=82, email="a@b.com", summary="hi"
    )
    assert values == {"status4": {"label": "PROCEED"}}


def test_mapped_columns_use_monday_value_shapes():
    client, _ = build(
        monday_column_map={
            "status": "status4",
            "score": "numbers1",
            "email": "email5",
            "phone": "phone2",
            "transcript": "link8",
            "interview_date": "date0",
            "communication": "numbers_comm",
        }
    )
    values = client.build_column_values(
        recommendation="REVIEW",
        overall_score=61.5,
        email="ana@example.com",
        phone="+639170000000",
        transcript_url="https://example.com/t",
        interview_date="2026-09-01",
        score_communication=7,
    )
    assert values["status4"] == {"label": "REVIEW"}
    assert values["numbers1"] == 61.5
    assert values["email5"] == {"email": "ana@example.com", "text": "ana@example.com"}
    assert values["phone2"]["phone"] == "+639170000000"
    assert values["link8"] == {"url": "https://example.com/t", "text": "View transcript"}
    assert values["date0"] == {"date": "2026-09-01"}
    assert values["numbers_comm"] == 7


async def test_create_item_targets_the_recommendation_group():
    client, captured = build(monday_column_map={"status": "status4"})
    item_id = await client.upsert_candidate(
        name="Ana Santos", recommendation=Recommendation.proceed, note="scored 82"
    )
    assert item_id == "item-1"
    create = next(c for c in captured if "create_item" in c["query"])
    assert create["variables"]["group"] == "group_proceed"
    assert create["variables"]["name"] == "Ana Santos"
    assert any("create_update" in c["query"] for c in captured)


async def test_rejected_candidates_go_to_the_rejected_group():
    client, captured = build(monday_column_map={"status": "status4"})
    await client.upsert_candidate(name="X", recommendation=Recommendation.do_not_proceed)
    create = next(c for c in captured if "create_item" in c["query"])
    assert create["variables"]["group"] == "group_rejected"


async def test_existing_item_is_updated_and_moved():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if "items_page_by_column_values" in body["query"]:
            return httpx.Response(
                200, json={"data": {"items_page_by_column_values": {"items": [{"id": "existing-9"}]}}}
            )
        return httpx.Response(200, json={"data": {}})

    calls: list[dict] = []
    settings = Settings(
        monday_api_key="t",
        monday_board_id="1",
        monday_group_review="group_review",
        monday_column_map={"email": "email5", "status": "status4"},
    )
    client = MondayClient(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    item_id = await client.upsert_candidate(
        name="Ana", recommendation=Recommendation.review, email="ana@example.com"
    )
    assert item_id == "existing-9"
    assert any("change_multiple_column_values" in c["query"] for c in calls)
    move = next(c for c in calls if "move_item_to_group" in c["query"])
    assert move["variables"]["group"] == "group_review"
    assert not any("create_item" in c["query"] for c in calls)


# ---------------------------------------------------------------------------
# Type coercion. The same logical field points at differently typed columns on
# different boards — these cases are taken from a real board where Score is a
# `text` column, not `numbers`.
# ---------------------------------------------------------------------------
def test_score_into_a_text_column_is_stringified():
    client, _ = build(monday_column_map={"score": "text8"})
    values = client.build_column_values(column_types={"text8": "text"}, overall_score=82.5)
    assert values == {"text8": "82.5"}


def test_status_label_into_a_text_column_is_flattened():
    client, _ = build(monday_column_map={"status": "text9"})
    values = client.build_column_values(column_types={"text9": "text"}, recommendation="PROCEED")
    assert values == {"text9": "PROCEED"}


def test_score_into_a_numbers_column_stays_numeric():
    client, _ = build(monday_column_map={"score": "numbers1"})
    values = client.build_column_values(column_types={"numbers1": "numbers"}, overall_score=82.5)
    assert values == {"numbers1": 82.5}


def test_unwritable_column_type_is_skipped_not_corrupted():
    """A file or people column cannot take a URL or a label; guessing would
    corrupt the column, so it must be dropped."""
    client, _ = build(monday_column_map={"transcript": "files", "status": "person"})
    values = client.build_column_values(
        column_types={"files": "file", "person": "people"},
        transcript_url="https://example.com/t",
        recommendation="PROCEED",
    )
    assert values == {}


def test_column_id_absent_from_the_board_is_skipped():
    client, _ = build(monday_column_map={"score": "typo_column"})
    values = client.build_column_values(column_types={"text8": "text"}, overall_score=82.5)
    assert values == {}


def test_long_text_summary_is_stringified():
    client, _ = build(monday_column_map={"summary": "long_text1"})
    values = client.build_column_values(
        column_types={"long_text1": "long-text"}, summary="Strong candidate."
    )
    assert values == {"long_text1": "Strong candidate."}


async def test_board_schema_is_fetched_once_and_reused():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["query"])
        if "columns { id type }" in body["query"]:
            return httpx.Response(
                200,
                json={"data": {"boards": [{"columns": [
                    {"id": "text8", "type": "text"},
                    {"id": "dup__of_progress", "type": "status"},
                ]}]}},
            )
        if "create_item" in body["query"]:
            return httpx.Response(200, json={"data": {"create_item": {"id": "item-1"}}})
        return httpx.Response(200, json={"data": {}})

    settings = Settings(
        monday_api_key="t",
        monday_board_id="4277249290",
        monday_group_proceed="new_group49772",
        monday_column_map={"score": "text8", "status": "dup__of_progress"},
    )
    client = MondayClient(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    await client.upsert_candidate(name="A", recommendation=Recommendation.proceed, overall_score=80)
    await client.upsert_candidate(name="B", recommendation=Recommendation.proceed, overall_score=70)

    schema_calls = [q for q in calls if "columns { id type }" in q]
    assert len(schema_calls) == 1  # cached across writes

    assert client._column_types == {"text8": "text", "dup__of_progress": "status"}


async def test_a_schema_failure_falls_back_to_canonical_shapes():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "columns { id type }" in body["query"]:
            return httpx.Response(500, text="monday is down")
        if "create_item" in body["query"]:
            return httpx.Response(200, json={"data": {"create_item": {"id": "item-1"}}})
        return httpx.Response(200, json={"data": {}})

    settings = Settings(
        monday_api_key="t", monday_board_id="1", monday_column_map={"status": "status"}
    )
    client = MondayClient(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    # The write still lands rather than the whole publish failing.
    assert await client.upsert_candidate(name="A", recommendation=Recommendation.review) == "item-1"
