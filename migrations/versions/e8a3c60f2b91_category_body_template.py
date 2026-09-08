"""A starting skeleton on the category

A category can now carry a body template: the headings an item in that
category always has, so a review or an interview starts from its usual shape
rather than an empty box.

It is offered, never imposed. The editor fills an empty body with it when the
author picks a single category, and from that moment the text belongs to the
item like anything else typed there. Editing the template later changes what
the next new item starts from and touches nothing already written.

Nullable, so categories without one behave exactly as before.

Revision ID: e8a3c60f2b91
Revises: d5f2b8c14a77
Create Date: 2026-09-08

"""
import sqlalchemy as sa
from alembic import op

revision = 'e8a3c60f2b91'
down_revision = 'd5f2b8c14a77'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('category', sa.Column('body_template', sa.Text(),
                                        nullable=True))


def downgrade():
    op.drop_column('category', 'body_template')
