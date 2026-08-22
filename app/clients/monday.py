"""Monday.com automation (box "5") via the GraphQL API.

Column ids differ per board, so the mapping is configuration
(`MONDAY_COLUMN_MAP`), never hardcoded. Anything unmapped is skipped rather
than guessed at, because writing to the wrong column silently corrupts a board.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

import httpx

from app.config import Settings, get_settings
from app.models import Recommendation

log = logging.getLogger(__name__)

API_URL = "https://api.monday.com/v2"
API_VERSION = "2024-10"

# Recommendation -> (group setting name, human label)
GROUP_FOR: dict[Recommendation, str] = {
    Recommendation.proceed: "monday_group_proceed",
    Recommendation.review: "monday_group_review",
    Recommendation.do_not_proceed: "monday_group_rejected",
}


class MondaySink(Protocol):
    async def upsert_candidate(self, **kwargs) -> str | None: ...
    async def aclose(self) -> None: ...


class MondayClient:
    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self.settings = settings or get_settings()
        if not (self.settings.monday_api_key and self.settings.monday_board_id):
            raise RuntimeError("MONDAY_API_KEY and MONDAY_BOARD_ID are required")
        self._column_types: dict[str, str] | None = None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={
                "Authorization": self.settings.monday_api_key,
                "Content-Type": "application/json",
                "API-Version": API_VERSION,
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _gql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._client.post(
            API_URL, json={"query": query, "variables": variables or {}}
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(f"monday.com GraphQL error: {payload['errors']}")
        return payload.get("data", {})

    # ------------------------------------------------------------------ schema
    async def column_types(self) -> dict[str, str]:
        """`{column_id: type}` for the board, fetched once and cached.

        Needed because the same logical field can point at differently typed
        columns on different boards — a score may be a `numbers` column on one
        board and a `text` column on another, and monday rejects (or silently
        drops) a value in the wrong shape.
        """
        if self._column_types is None:
            data = await self._gql(
                """
                query ($board: ID!) {
                  boards(ids: [$board]) { columns { id type } }
                }
                """,
                {"board": str(self.settings.monday_board_id)},
            )
            boards = data.get("boards") or []
            columns = (boards[0].get("columns") if boards else []) or []
            self._column_types = {c["id"]: c["type"] for c in columns}
            log.info("monday board %s columns: %s", self.settings.monday_board_id, self._column_types)
        return self._column_types

    @staticmethod
    def coerce(column_type: str, value: Any) -> Any:
        """Shape a value for a monday column type. Returns `None` to skip."""
        if value is None:
            return None
        if column_type in ("text", "long-text", "long_text"):
            # A status label or a number in a text column is still readable.
            if isinstance(value, dict):
                value = value.get("label") or value.get("text") or value.get("url")
            return str(value)
        if column_type in ("numbers", "numeric"):
            if isinstance(value, dict):
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
        if column_type in ("status", "color", "dropdown"):
            if isinstance(value, dict):
                return value
            return {"label": str(value)}
        if column_type in ("email", "phone", "link", "date", "location"):
            return value if isinstance(value, dict) else None
        if column_type == "name":
            return str(value)
        # Unrecognised type (file, people, board-relation, formula, ...). Writing
        # a guess would corrupt the column, so skip and say so.
        log.warning("monday column type %r is not writable by this app — skipping", column_type)
        return None

    # ------------------------------------------------------------------ lookups
    async def find_item(self, *, email: str) -> str | None:
        """Locate an existing candidate row by email so we update instead of
        creating duplicates. Requires an `email` column mapped."""
        column_id = self.settings.monday_column_map.get("email")
        if not column_id:
            return None
        data = await self._gql(
            """
            query ($board: ID!, $column: String!, $value: String!) {
              items_page_by_column_values(
                board_id: $board, limit: 1,
                columns: [{column_id: $column, column_values: [$value]}]
              ) { items { id } }
            }
            """,
            {"board": str(self.settings.monday_board_id), "column": column_id, "value": email},
        )
        items = (data.get("items_page_by_column_values") or {}).get("items") or []
        return items[0]["id"] if items else None

    # -------------------------------------------------------------------- write
    def build_column_values(
        self, column_types: dict[str, str] | None = None, **fields: Any
    ) -> dict[str, Any]:
        """Translate logical fields into this board's column ids and value shapes.

        `column_types` comes from `column_types()`. Without it the canonical
        shape for each field is used, which is right for a board whose columns
        are the expected types.
        """
        cmap = self.settings.monday_column_map
        out: dict[str, Any] = {}

        def put(logical: str, value: Any) -> None:
            column_id = cmap.get(logical)
            if not column_id or value is None:
                return
            if column_types is not None:
                column_type = column_types.get(column_id)
                if column_type is None:
                    log.warning(
                        "MONDAY_COLUMN_MAP maps %r to column %r, which does not exist on "
                        "board %s — skipping",
                        logical,
                        column_id,
                        self.settings.monday_board_id,
                    )
                    return
                value = self.coerce(column_type, value)
                if value is None:
                    return
            out[column_id] = value

        put("email", {"email": fields.get("email"), "text": fields.get("email")}
            if fields.get("email") else None)
        put("phone", {"phone": fields.get("phone"), "countryShortName": "PH"}
            if fields.get("phone") else None)
        put("status", {"label": fields["recommendation"]} if fields.get("recommendation") else None)
        put("score", fields.get("overall_score"))
        put("role", fields.get("role"))
        put("interview_date", {"date": fields["interview_date"]}
            if fields.get("interview_date") else None)
        put("summary", fields.get("summary"))
        put("transcript", {"url": fields["transcript_url"], "text": "View transcript"}
            if fields.get("transcript_url") else None)
        put("recording", {"url": fields["recording_url"], "text": "Recording"}
            if fields.get("recording_url") else None)
        for key in ("communication", "experience", "availability"):
            put(key, fields.get(f"score_{key}"))
        return out

    async def upsert_candidate(
        self,
        *,
        name: str,
        recommendation: Recommendation,
        note: str | None = None,
        **fields: Any,
    ) -> str | None:
        group_id = getattr(self.settings, GROUP_FOR[recommendation], None)
        try:
            types = await self.column_types()
        except Exception:
            log.exception("could not read the monday board schema — using canonical shapes")
            types = None
        column_values = self.build_column_values(
            column_types=types, recommendation=recommendation.value, **fields
        )

        item_id = await self.find_item(email=fields.get("email", "")) if fields.get("email") else None

        if item_id:
            await self._gql(
                """
                mutation ($board: ID!, $item: ID!, $values: JSON!) {
                  change_multiple_column_values(board_id: $board, item_id: $item,
                                                column_values: $values) { id }
                }
                """,
                {
                    "board": str(self.settings.monday_board_id),
                    "item": item_id,
                    "values": json.dumps(column_values),
                },
            )
            if group_id:
                await self._gql(
                    """
                    mutation ($item: ID!, $group: String!) {
                      move_item_to_group(item_id: $item, group_id: $group) { id }
                    }
                    """,
                    {"item": item_id, "group": group_id},
                )
        else:
            data = await self._gql(
                """
                mutation ($board: ID!, $group: String, $name: String!, $values: JSON!) {
                  create_item(board_id: $board, group_id: $group, item_name: $name,
                              column_values: $values, create_labels_if_missing: true) { id }
                }
                """,
                {
                    "board": str(self.settings.monday_board_id),
                    "group": group_id,
                    "name": name,
                    "values": json.dumps(column_values),
                },
            )
            item_id = (data.get("create_item") or {}).get("id")

        if item_id and note:
            await self._gql(
                """
                mutation ($item: ID!, $body: String!) {
                  create_update(item_id: $item, body: $body) { id }
                }
                """,
                {"item": item_id, "body": note},
            )
        log.info("monday item %s updated (%s)", item_id, recommendation.value)
        return item_id


class NullMondaySink:
    async def upsert_candidate(self, **kwargs: Any) -> str | None:
        log.info(
            "[no monday] would upsert %s -> %s",
            kwargs.get("name"),
            getattr(kwargs.get("recommendation"), "value", kwargs.get("recommendation")),
        )
        return None

    async def aclose(self) -> None:
        return None


def build_monday_sink(settings: Settings | None = None) -> MondaySink:
    settings = settings or get_settings()
    try:
        return MondayClient(settings)
    except RuntimeError as exc:
        log.warning("%s — skipping monday.com updates", exc)
        return NullMondaySink()
