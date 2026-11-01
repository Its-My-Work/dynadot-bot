"""add domain_redirects and domain_watchlist tables

Revision ID: c5f8e1a32b07
Revises: b4e7d9c21f05
Create Date: 2026-11-01 04:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c5f8e1a32b07'
down_revision: str | None = 'b4e7d9c21f05'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'domain_redirects',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column(
            'domain_id',
            sa.String(36),
            sa.ForeignKey('domains.id', ondelete='CASCADE'),
            nullable=False,
            index=True,
        ),
        sa.Column('domain_name', sa.String(253), nullable=False, index=True),
        sa.Column('target_url', sa.String(2048), nullable=False),
        sa.Column('http_status', sa.Integer(), nullable=False, server_default='302'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.UniqueConstraint('domain_name', name='uq_domain_redirects_name'),
    )

    op.create_table(
        'domain_watchlist',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('telegram_id', sa.BigInteger(), nullable=False, index=True),
        sa.Column('domain', sa.String(253), nullable=False, index=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.UniqueConstraint('telegram_id', 'domain', name='uq_watchlist_user_domain'),
    )


def downgrade() -> None:
    op.drop_table('domain_watchlist')
    op.drop_table('domain_redirects')
