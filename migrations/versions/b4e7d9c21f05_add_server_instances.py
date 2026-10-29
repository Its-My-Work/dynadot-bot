"""add server instances (vps/vpn)

Revision ID: b4e7d9c21f05
Revises: 7c3b91d2f4a8
Create Date: 2026-10-29 21:55:00.000000

The Reality private key is deliberately absent: it only ever travels to the VM
inside cloud-init, so a dump of this table cannot be used to impersonate a
customer's server.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b4e7d9c21f05'
down_revision: str | None = '7c3b91d2f4a8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VPS_PROVIDER_ENUM = sa.Enum(
    'TIMEWEB',
    'AEZA',
    'PQ_HOSTING',
    'MOCK',
    name='vpsprovider',
    native_enum=False,
    length=32,
)

SERVER_STATUS_ENUM = sa.Enum(
    'PENDING',
    'INSTALLING',
    'ACTIVE',
    'SUSPENDED',
    'DELETED',
    'FAILED',
    name='serverstatus',
    native_enum=False,
    length=32,
)


def upgrade() -> None:
    op.create_table(
        'server_instances',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('provider', VPS_PROVIDER_ENUM, nullable=False),
        sa.Column('external_id', sa.String(length=64), nullable=True),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('location_code', sa.String(length=16), nullable=True),
        sa.Column('location_name', sa.String(length=64), nullable=True),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('status', SERVER_STATUS_ENUM, nullable=False),
        sa.Column('preset_name', sa.String(length=64), nullable=True),
        sa.Column('cpu', sa.Integer(), nullable=True),
        sa.Column('ram_mb', sa.Integer(), nullable=True),
        sa.Column('disk_gb', sa.Integer(), nullable=True),
        sa.Column('price_rub', sa.Numeric(12, 2), nullable=False),
        sa.Column('vless_uuid', sa.String(length=64), nullable=True),
        sa.Column('vless_public_key', sa.String(length=128), nullable=True),
        sa.Column('vless_short_id', sa.String(length=32), nullable=True),
        sa.Column('vless_sni', sa.String(length=253), nullable=True),
        sa.Column('vless_link', sa.Text(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('CURRENT_TIMESTAMP'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('CURRENT_TIMESTAMP'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
            name=op.f('fk_server_instances_user_id_users'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_server_instances')),
    )
    op.create_index(
        op.f('ix_server_instances_user_id'), 'server_instances', ['user_id']
    )
    op.create_index(
        op.f('ix_server_instances_provider'), 'server_instances', ['provider']
    )
    op.create_index(
        op.f('ix_server_instances_external_id'), 'server_instances', ['external_id']
    )
    op.create_index(
        op.f('ix_server_instances_ip_address'), 'server_instances', ['ip_address']
    )
    op.create_index(
        op.f('ix_server_instances_status'), 'server_instances', ['status']
    )
    op.create_index(
        op.f('ix_server_instances_expires_at'), 'server_instances', ['expires_at']
    )
    # The server list screen filters by (user, status) on every open.
    op.create_index(
        'ix_server_instances_user_status',
        'server_instances',
        ['user_id', 'status'],
    )


def downgrade() -> None:
    op.drop_index('ix_server_instances_user_status', table_name='server_instances')
    op.drop_index(
        op.f('ix_server_instances_expires_at'), table_name='server_instances'
    )
    op.drop_index(op.f('ix_server_instances_status'), table_name='server_instances')
    op.drop_index(
        op.f('ix_server_instances_ip_address'), table_name='server_instances'
    )
    op.drop_index(
        op.f('ix_server_instances_external_id'), table_name='server_instances'
    )
    op.drop_index(op.f('ix_server_instances_provider'), table_name='server_instances')
    op.drop_index(op.f('ix_server_instances_user_id'), table_name='server_instances')
    op.drop_table('server_instances')
