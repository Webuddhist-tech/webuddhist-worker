"""Create rec_membership_changes table

Revision ID: 003_rec_membership_changes
Revises: 002_recommendation_tables
Create Date: 2026-10-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003_rec_membership_changes"
down_revision: Union[str, None] = "002_recommendation_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rec_membership_changes",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("item_type", sa.String(length=32), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("present", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "item_type", "item_id"),
    )
    op.create_index(
        "idx_rec_membership_changes_changed_at",
        "rec_membership_changes",
        ["item_type", "changed_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_rec_membership_changes_changed_at", table_name="rec_membership_changes")
    op.drop_table("rec_membership_changes")
