"""Alternative text on uploads, and the site name on the organization

Two changes from the theme contract, both about something a theme used to
own that belongs to the organization instead.

`upload.alt` is what a screen reader says in place of a picture, stored with
the file so one description follows the image everywhere it is used.
Nullable, so existing uploads need no backfill and describe themselves when
someone gets to them.

The site name used to be a field the Supremely theme declared, so an
organization whose public site is named differently from its community (a
community called "Acme Community" whose website is just "Acme") stored that
name in theme copy, where switching theme would lose it. It moves to
settings['site_name'] on the organization, and the old key is dropped. That
half edits one JSON column rather than the schema, and reverses cleanly.

Revision ID: a13451eb2b44
Revises: 3eaa1fc1cd33
Create Date: 2026-09-02

"""
import json

import sqlalchemy as sa
from alembic import op

revision = 'a13451eb2b44'
down_revision = '3eaa1fc1cd33'
branch_labels = None
depends_on = None

# The only theme that ever declared the field.
LEGACY_THEME = 'supremely'


def _rows(bind):
    """(id, settings dict) for every organization, JSON already parsed.

    The column is JSON on SQLite and JSONB on PostgreSQL, so the driver
    hands back either a string or a dict depending on the engine.
    """
    for org_id, raw in bind.execute(
            sa.text('SELECT id, settings FROM organization')):
        if isinstance(raw, str):
            raw = json.loads(raw or '{}')
        yield org_id, (raw or {})


def _write(bind, org_id, settings):
    statement = sa.text(
        'UPDATE organization SET settings = :settings WHERE id = :id')
    if bind.dialect.name == 'postgresql':
        statement = sa.text(
            'UPDATE organization SET settings = CAST(:settings AS JSONB) '
            'WHERE id = :id')
    bind.execute(statement, {'settings': json.dumps(settings), 'id': org_id})


def upgrade():
    with op.batch_alter_table('upload', schema=None) as batch_op:
        batch_op.add_column(sa.Column('alt', sa.String(length=200),
                                      nullable=True))

    bind = op.get_bind()
    for org_id, settings in list(_rows(bind)):
        store = settings.get('theme_content')
        if not isinstance(store, dict):
            continue
        theme_copy = store.get(LEGACY_THEME)
        if not isinstance(theme_copy, dict):
            continue
        name = (theme_copy.pop('brand_name', '') or '').strip()
        if not name:
            continue
        # An explicit site name already set by hand wins over the old one.
        settings.setdefault('site_name', name)
        _write(bind, org_id, settings)


def downgrade():
    bind = op.get_bind()
    for org_id, settings in list(_rows(bind)):
        name = (settings.pop('site_name', '') or '').strip()
        if not name:
            continue
        store = settings.setdefault('theme_content', {})
        if isinstance(store, dict):
            store.setdefault(LEGACY_THEME, {})['brand_name'] = name
        _write(bind, org_id, settings)

    with op.batch_alter_table('upload', schema=None) as batch_op:
        batch_op.drop_column('alt')
