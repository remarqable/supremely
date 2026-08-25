"""Discussions: topic->post, reply->comment

Revision ID: aa529acf8379
Revises: aefb3d93b791
Create Date: 2026-08-25 10:49:57.768522

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'aa529acf8379'
down_revision = 'aefb3d93b791'
branch_labels = None
depends_on = None


def upgrade():
    # Rename the topic table -> post, and its reply_count column.
    op.rename_table('discussion_topic', 'discussion_post')
    with op.batch_alter_table('discussion_post') as batch:
        batch.alter_column('reply_count', new_column_name='comment_count')

    # Rename the reply table -> comment, and topic_id -> post_id.
    op.rename_table('discussion_reply', 'discussion_comment')
    with op.batch_alter_table('discussion_comment') as batch:
        batch.alter_column('topic_id', new_column_name='post_id')

    # Follow table: topic_id -> post_id.
    with op.batch_alter_table('discussion_follow') as batch:
        batch.alter_column('topic_id', new_column_name='post_id')

    # Reaction / flag target-type values.
    op.execute("UPDATE discussion_reaction SET target_type='post' WHERE target_type='topic'")
    op.execute("UPDATE discussion_reaction SET target_type='comment' WHERE target_type='reply'")
    op.execute("UPDATE discussion_flag SET target_type='post' WHERE target_type='topic'")
    op.execute("UPDATE discussion_flag SET target_type='comment' WHERE target_type='reply'")


def downgrade():
    op.execute("UPDATE discussion_flag SET target_type='reply' WHERE target_type='comment'")
    op.execute("UPDATE discussion_flag SET target_type='topic' WHERE target_type='post'")
    op.execute("UPDATE discussion_reaction SET target_type='reply' WHERE target_type='comment'")
    op.execute("UPDATE discussion_reaction SET target_type='topic' WHERE target_type='post'")
    with op.batch_alter_table('discussion_follow') as batch:
        batch.alter_column('post_id', new_column_name='topic_id')
    with op.batch_alter_table('discussion_comment') as batch:
        batch.alter_column('post_id', new_column_name='topic_id')
    op.rename_table('discussion_comment', 'discussion_reply')
    with op.batch_alter_table('discussion_reply') as batch:
        batch.alter_column('comment_count', new_column_name='reply_count')
    op.rename_table('discussion_post', 'discussion_topic')
