"""Rename default theme rows to origin

Revision ID: 1c7e4d3868a2
Revises: d6b85b1ec76a
Create Date: 2026-08-25 09:35:19.757315

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1c7e4d3868a2'
down_revision = 'd6b85b1ec76a'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE organization SET theme = 'origin' WHERE theme = 'default'")


def downgrade():
    op.execute("UPDATE organization SET theme = 'default' WHERE theme = 'origin'")
