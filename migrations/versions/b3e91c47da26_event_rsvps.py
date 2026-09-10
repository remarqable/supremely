"""Who is coming to an event

There was no way to say you were coming to something, and no way to see
who else was. One row per person per event, and the row's existence is the
answer: there is no status column, because there is one thing to say.

The row points straight at the content it is about rather than carrying a
type and an id the way a reaction does. A reaction answers about two
different things and cannot have a foreign key; an RSVP is only ever about
an event, so the column says so and the database keeps it honest. Deleting
an event takes its guest list with it instead of leaving rows behind that
point at nothing.

Revision ID: b3e91c47da26
Revises: f7c2a91d4e08
Create Date: 2026-09-10

"""
import sqlalchemy as sa
from alembic import op

revision = 'b3e91c47da26'
down_revision = 'f7c2a91d4e08'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'rsvp',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
                  autoincrement=True, nullable=False),
        sa.Column('org_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
                  nullable=False),
        sa.Column('user_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
                  nullable=False),
        sa.Column('content_id',
                  sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
                  nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organization.id'],
                                name=op.f('fk_rsvp_org_id_organization'),
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'],
                                name=op.f('fk_rsvp_user_id_user'),
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['content_id'], ['content.id'],
                                name=op.f('fk_rsvp_content_id_content'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_rsvp')),
        sa.UniqueConstraint('user_id', 'content_id',
                            name='uq_rsvp_user_content'),
    )
    op.create_index(op.f('ix_rsvp_org_id'), 'rsvp', ['org_id'], unique=False)
    op.create_index(op.f('ix_rsvp_user_id'), 'rsvp', ['user_id'], unique=False)
    op.create_index(op.f('ix_rsvp_content_id'), 'rsvp', ['content_id'],
                    unique=False)


def downgrade():
    op.drop_index(op.f('ix_rsvp_content_id'), table_name='rsvp')
    op.drop_index(op.f('ix_rsvp_user_id'), table_name='rsvp')
    op.drop_index(op.f('ix_rsvp_org_id'), table_name='rsvp')
    op.drop_table('rsvp')
