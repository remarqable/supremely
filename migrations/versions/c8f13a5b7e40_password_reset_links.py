"""One-time password reset links

Email is optional, so a member who forgot their password on an installation
with no SMTP had no way back in short of the operator running a command on
the server. An owner or admin can now hand them a link instead.

Only the hash of the token is stored, like an invitation: the table must
never be a list of credentials. The row outlives the link it stood for,
because who issued a reset and when is worth keeping after the link is
spent.

`stamp` holds a digest of the password the link was issued against, so a
link cannot outlive the password it was made to replace: setting a new one
by any route leaves every outstanding link no longer matching, and dead.

Revision ID: c8f13a5b7e40
Revises: b3e91c47da26
Create Date: 2026-09-11

"""
import sqlalchemy as sa
from alembic import op

revision = 'c8f13a5b7e40'
down_revision = 'b3e91c47da26'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'password_reset',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
                  autoincrement=True, nullable=False),
        sa.Column('org_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
                  nullable=False),
        sa.Column('user_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
                  nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        # The digest of the password this link was issued to replace, so
        # changing that password by any route spends the link.
        sa.Column('stamp', sa.String(length=64), nullable=False),
        sa.Column('created_by_id',
                  sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
                  nullable=True),
        sa.Column('updated_by_id',
                  sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
                  nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organization.id'],
                                name=op.f('fk_password_reset_org_id_organization'),
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'],
                                name=op.f('fk_password_reset_user_id_user'),
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_id'], ['user.id'],
                                name=op.f('fk_password_reset_created_by_id_user'),
                                ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['updated_by_id'], ['user.id'],
                                name=op.f('fk_password_reset_updated_by_id_user'),
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_password_reset')),
    )
    op.create_index(op.f('ix_password_reset_org_id'), 'password_reset',
                    ['org_id'], unique=False)
    op.create_index(op.f('ix_password_reset_user_id'), 'password_reset',
                    ['user_id'], unique=False)
    # Unique as well as indexed: the token is looked up by this hash on
    # every redemption, and two rows sharing one would mean two links that
    # are the same link.
    op.create_index(op.f('ix_password_reset_token_hash'), 'password_reset',
                    ['token_hash'], unique=True)


def downgrade():
    op.drop_index(op.f('ix_password_reset_token_hash'), table_name='password_reset')
    op.drop_index(op.f('ix_password_reset_user_id'), table_name='password_reset')
    op.drop_index(op.f('ix_password_reset_org_id'), table_name='password_reset')
    op.drop_table('password_reset')
