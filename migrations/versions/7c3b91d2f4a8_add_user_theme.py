"""add user theme preference

Revision ID: 7c3b91d2f4a8
Revises: 59a1a27ee997
Create Date: 2026-10-29 08:10:00.000000

Backfills every existing row with the default theme before making the column
NOT NULL — an ALTER that adds a non-nullable column to a populated table fails
otherwise.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '7c3b91d2f4a8'
down_revision: str | None = '59a1a27ee997'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

THEME_ENUM = sa.Enum(
    'ONYX_NEON',
    'VERCEL_MONO',
    'LINEAR_INDIGO',
    'SOLANA_CYBER',
    'NORDIC_MIST',
    name='theme',
    native_enum=False,
    length=32,
)


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'theme',
                THEME_ENUM,
                nullable=False,
                server_default='ONYX_NEON',
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('theme')
