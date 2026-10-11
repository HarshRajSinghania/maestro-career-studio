"""chat_messages.parts / provider / model

Neutral transcript parts per message (types.dump_parts v1 JSON) plus the provider and model
that wrote an assistant row. Existing rows are backfilled losslessly from content / tool_calls /
tool_call_id; the legacy columns stay. `legacy_parts_doc` is an inline copy of
app.services.transcript.from_legacy + types.dump_parts (a revision never imports app code,
SYSTEM.md §12); tests/test_transcript.py keeps the two equal.

Revision ID: cf0804d00ae2
Revises: 559244b575bc
"""
import json
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "cf0804d00ae2"
down_revision: Union[str, Sequence[str], None] = "559244b575bc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_V = 1  # types.TRANSCRIPT_VERSION at the time of writing; frozen here on purpose


def _arguments(raw: Any):
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "")
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def legacy_parts_doc(role, content, tool_calls, tool_call_id) -> dict:
    if role == "tool":
        return {"v": _V, "parts": [{"type": "tool_result", "tool_call_id": tool_call_id or "",
                                     "content": content or "", "is_error": False}]}
    parts: list = []
    if content:
        parts.append({"type": "text", "text": content})
    for call in tool_calls or []:
        fn = call.get("function") or {}
        parts.append({"type": "tool_call", "id": call.get("id") or "", "name": fn.get("name") or "",
                      "arguments": _arguments(fn.get("arguments")), "meta": None})
    return {"v": _V, "parts": parts}


def upgrade() -> None:
    with op.batch_alter_table("chat_messages", schema=None) as batch_op:
        batch_op.add_column(sa.Column("parts", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("provider", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("model", sa.Text(), nullable=True))

    chat_messages = sa.table(
        "chat_messages",
        sa.column("id", sa.Uuid()),
        sa.column("role", sa.Text()),
        sa.column("content", sa.Text()),
        sa.column("tool_calls", sa.JSON()),
        sa.column("tool_call_id", sa.Text()),
        sa.column("parts", sa.JSON()),
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(chat_messages.c.id, chat_messages.c.role, chat_messages.c.content,
                  chat_messages.c.tool_calls, chat_messages.c.tool_call_id)
    ).all()
    for row in rows:
        bind.execute(
            chat_messages.update()
            .where(chat_messages.c.id == row.id)
            .values(parts=legacy_parts_doc(row.role, row.content, row.tool_calls, row.tool_call_id))
        )


def downgrade() -> None:
    with op.batch_alter_table("chat_messages", schema=None) as batch_op:
        batch_op.drop_column("model")
        batch_op.drop_column("provider")
        batch_op.drop_column("parts")
