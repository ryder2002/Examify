"""Add a canonical manifest hash for idempotent desktop sync."""

import hashlib
import json

from alembic import op
import sqlalchemy as sa


revision = "0016_desktop_manifest_hash"
down_revision = "0015_attempt_indexes"
branch_labels = None
depends_on = None


def _hash(value: object) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            pass
    return hashlib.sha256(
        json.dumps(
            value or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("desktop_syncs")}
    if "manifest_hash" not in columns:
        op.add_column("desktop_syncs", sa.Column("manifest_hash", sa.String(64), nullable=True))
    rows = bind.execute(sa.text("SELECT id, manifest FROM desktop_syncs WHERE manifest_hash IS NULL"))
    for row in rows:
        bind.execute(
            sa.text("UPDATE desktop_syncs SET manifest_hash=:hash WHERE id=:id"),
            {"hash": _hash(row.manifest), "id": row.id},
        )
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("desktop_syncs")}
    if "ix_desktop_syncs_manifest_hash" not in indexes:
        op.create_index("ix_desktop_syncs_manifest_hash", "desktop_syncs", ["manifest_hash"])


def downgrade() -> None:
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("desktop_syncs")}
    if "ix_desktop_syncs_manifest_hash" in indexes:
        op.drop_index("ix_desktop_syncs_manifest_hash", table_name="desktop_syncs")
    op.drop_column("desktop_syncs", "manifest_hash")
