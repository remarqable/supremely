"""Link a discussion thread to the item it discusses

A published article, video or resource can now have one discussion thread,
so the item can show "Discuss - N replies" and the thread can say what it is
about. Readers who arrive from search could not previously see that a
conversation existed at all; it lived on a separate URL with nothing pointing
at it.

The thread stays an ordinary discussion post: it lives in a group and is
moderated there like any other. This column only records the subject, which
keeps published content and conversation as separate things that reference
each other rather than merging them.

Unique, so an item has one canonical thread and a second person joins it
instead of opening a rival. Nullable, and repeated nulls are allowed under a
unique constraint on both SQLite and PostgreSQL, which is what lets every
unrelated post leave it empty. ON DELETE SET NULL: deleting an article
orphans its thread rather than destroying a conversation people took part in.

Revision ID: d5f2b8c14a77
Revises: c4d1a7e93b02
Create Date: 2026-09-08

"""
import sqlalchemy as sa
from alembic import op

revision = 'd5f2b8c14a77'
down_revision = 'c4d1a7e93b02'
branch_labels = None
depends_on = None


def upgrade():
    # Batch mode: SQLite cannot add a constraint to an existing table, so
    # Alembic rebuilds it. A no-op wrapper on PostgreSQL.
    with op.batch_alter_table('discussion_post') as batch:
        # Same variant the models use (app/models/types.py, BigIntFK): a
        # foreign key must match the column it references on both engines.
        batch.add_column(sa.Column(
            'content_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
            nullable=True))
        batch.create_unique_constraint('uq_discussion_post_content',
                                       ['content_id'])
        batch.create_foreign_key('fk_discussion_post_content', 'content',
                                 ['content_id'], ['id'], ondelete='SET NULL')


def downgrade():
    with op.batch_alter_table('discussion_post') as batch:
        batch.drop_constraint('fk_discussion_post_content',
                              type_='foreignkey')
        batch.drop_constraint('uq_discussion_post_content', type_='unique')
        batch.drop_column('content_id')
