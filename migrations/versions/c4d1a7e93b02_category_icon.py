"""An icon on the category

Categories now carry an icon so a content card that has no picture of its
own still shows something recognisable: the archive draws the category's
icon and name on a tinted panel in place of the missing image.

The column stores a name from a fixed catalogue (Category.ICONS), never
markup — the drawing itself lives in the category_icon template macro, so
nothing from the database is ever rendered as SVG. Nullable, so existing
categories need no backfill and fall back to a generic icon until someone
picks one under Manage → Categories.

Revision ID: c4d1a7e93b02
Revises: a13451eb2b44
Create Date: 2026-09-08

"""
import sqlalchemy as sa
from alembic import op

revision = 'c4d1a7e93b02'
down_revision = 'a13451eb2b44'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('category', sa.Column('icon', sa.String(length=30),
                                        nullable=True))


def downgrade():
    op.drop_column('category', 'icon')
