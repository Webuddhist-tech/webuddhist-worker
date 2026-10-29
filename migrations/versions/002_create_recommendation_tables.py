"""Create recommendation signal tables

Revision ID: 002_recommendation_tables
Revises: 001_upcoming_reminders
Create Date: 2026-10-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002_recommendation_tables"
down_revision: Union[str, None] = "001_upcoming_reminders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rec_memberships",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("item_type", sa.String(length=32), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "item_type", "item_id"),
    )
    op.create_index("idx_rec_memberships_item", "rec_memberships", ["item_type", "item_id"])
    op.create_index(
        "idx_rec_memberships_joined_at", "rec_memberships", ["item_type", "joined_at"]
    )

    op.create_table(
        "rec_items",
        sa.Column("item_type", sa.String(length=32), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=True),
        sa.Column("tradition_id", sa.Uuid(), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("tag_ids", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("item_type", "item_id"),
    )

    op.create_table(
        "rec_exposures",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("item_type", sa.String(length=32), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("impressions", sa.Integer(), nullable=False),
        sa.Column("clicks", sa.Integer(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "item_type", "item_id"),
    )

    op.create_table(
        "rec_dismissals",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("item_type", sa.String(length=32), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "item_type", "item_id"),
    )

    op.create_table(
        "rec_processed_events",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        "idx_rec_processed_events_processed_at", "rec_processed_events", ["processed_at"]
    )


def downgrade() -> None:
    op.drop_index("idx_rec_processed_events_processed_at", table_name="rec_processed_events")
    op.drop_table("rec_processed_events")
    op.drop_table("rec_dismissals")
    op.drop_table("rec_exposures")
    op.drop_table("rec_items")
    op.drop_index("idx_rec_memberships_joined_at", table_name="rec_memberships")
    op.drop_index("idx_rec_memberships_item", table_name="rec_memberships")
    op.drop_table("rec_memberships")
