"""Make every activation token permanent until explicitly revoked."""

from alembic import op
import sqlalchemy as sa
from datetime import datetime, timedelta, timezone


revision = "0013_permanent_tokens"
down_revision = "0012_token_groups_study"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tokens = sa.table(
        "activation_tokens",
        sa.column("expires_at", sa.DateTime(timezone=True)),
        sa.column("status", sa.String(length=20)),
    )
    op.execute(tokens.update().values(expires_at=None))
    op.execute(
        tokens.update()
        .where(tokens.c.status == "expired")
        .values(status="available")
    )
    refresh_tokens = sa.table(
        "refresh_tokens",
        sa.column("expires_at", sa.DateTime(timezone=True)),
        sa.column("revoked_at", sa.DateTime(timezone=True)),
    )
    extended_until = datetime.now(timezone.utc) + timedelta(days=90)
    op.execute(
        refresh_tokens.update()
        .where(
            refresh_tokens.c.revoked_at.is_(None),
            refresh_tokens.c.expires_at < extended_until,
        )
        .values(expires_at=extended_until)
    )


def downgrade() -> None:
    # Expiration dates cannot be reconstructed safely. A downgrade retains
    # permanent tokens instead of inventing deadlines.
    pass
