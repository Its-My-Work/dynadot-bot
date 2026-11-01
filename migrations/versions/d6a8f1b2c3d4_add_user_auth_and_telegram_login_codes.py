"""add user auth fields and telegram login codes

Revision ID: d6a8f1b2c3d4
Revises: c5f8e1a32b07
Create Date: 2026-11-01 17:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd6a8f1b2c3d4'
down_revision: str | None = 'c5f8e1a32b07'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add auth fields to users table
    op.add_column('users', sa.Column('email', sa.String(255), nullable=True))
    op.create_index('ix_users_email', 'users', ['email'], unique=True)

    op.add_column('users', sa.Column('password_hash', sa.String(255), nullable=True))
    op.add_column(
        'users',
        sa.Column('has_password', sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.add_column('users', sa.Column('telegram_username', sa.String(64), nullable=True))
    op.create_index('ix_users_telegram_username', 'users', ['telegram_username'])

    op.add_column('users', sa.Column('telegram_link_code', sa.String(32), nullable=True))
    op.create_index('ix_users_telegram_link_code', 'users', ['telegram_link_code'])

    op.add_column(
        'users',
        sa.Column('telegram_link_expires', sa.DateTime(timezone=True), nullable=True),
    )

    # Make telegram_id nullable for email-first users
    op.alter_column('users', 'telegram_id', existing_type=sa.BigInteger(), nullable=True)

    # 2. Create telegram_login_codes table
    op.create_table(
        'telegram_login_codes',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('telegram_id', sa.BigInteger(), nullable=False, index=True),
        sa.Column('code_hash', sa.String(64), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('telegram_login_codes')

    op.drop_index('ix_users_telegram_link_code', table_name='users')
    op.drop_column('users', 'telegram_link_expires')
    op.drop_column('users', 'telegram_link_code')

    op.drop_index('ix_users_telegram_username', table_name='users')
    op.drop_column('users', 'telegram_username')

    op.drop_column('users', 'has_password')
    op.drop_column('users', 'password_hash')

    op.drop_index('ix_users_email', table_name='users')
    op.drop_column('users', 'email')
