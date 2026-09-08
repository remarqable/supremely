"""One map for what an organization does with each content type, and content
that lives inside other content

Settings about a content type were keyed by slug in their own map
(settings['section_visibility']), and the work that follows wanted two more
of those: one for whether a type is published at all, another for whether it
appears on the public site. Three maps keyed by the same slug, each read
somewhere different, is how the three come to disagree.

They collapse into settings['content_types'], one entry per slug holding
everything decided about that type.

Two things this migration has to get right.

Grandfathering. Library types are off for an organization that has not asked
for one, which is the point: a woodworking club should not be handed a
podcast archive. But an organization that has been publishing episodes for a
year has asked, by publishing them. Any type with rows here is turned on, so
nobody loses a section they were using.

The old key goes. Leaving it as a fallback would mean two places to read the
same answer from. The downgrade rebuilds it, and leaves the new map in place
rather than deleting it: the code this reverts to ignores keys it does not
know, and throwing away which sections an organization had turned off would
republish them the moment anybody rolled forward again.

The same revision also gives content a parent. A block inside an article -- a
recipe card, a lesson in a course -- is an ordinary content row with
content.parent_id set, ordered by content.position, and it has no address of
its own, so content.slug becomes nullable. One registry, one field system,
one renderer: an inline block is not a second kind of thing.

That gives content two address spaces rather than one, so the single unique
rule over (org_id, type, slug) becomes two partial ones: standalone rows stay
unique across the organization, and blocks are unique inside the item they
belong to. Without the split, two courses could not each hold a lesson called
"one", and folding parent_id into one constraint instead would let two
articles share an address, because nulls never compare equal.

Revision ID: c41d7a9e3b52
Revises: e8a3c60f2b91
Create Date: 2026-09-04

"""
import json

import sqlalchemy as sa
from alembic import op

revision = 'c41d7a9e3b52'
down_revision = 'e8a3c60f2b91'
branch_labels = None
depends_on = None

SETTINGS_KEY = 'content_types'
LEGACY_KEY = 'section_visibility'

# Kept as literals rather than imported from the application: a migration
# describes the database as it was on the day it ran, and importing today's
# code would make it describe whatever the code says next year instead.
ESSENTIAL = ('page', 'article')


def _rows(bind):
    """(id, settings dict) per organization, JSON already parsed.

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


def _types_in_use(bind):
    """{org_id: {type slug}} for every organization, in one query.

    Drafts count. Somebody part way through writing their first episode has
    asked for a podcast just as much as somebody who published one.

    One grouped query rather than one per organization: an installation with
    a few thousand tenants should not pay a round trip each to answer the
    same question.
    """
    in_use = {}
    for org_id, slug in bind.execute(
            sa.text('SELECT org_id, type FROM content GROUP BY org_id, type')):
        in_use.setdefault(org_id, set()).add(slug)
    return in_use


def upgrade():
    # Batch mode because making slug nullable is an ALTER SQLite cannot do
    # in place: Alembic rebuilds the table and copies the rows.
    with op.batch_alter_table('content', schema=None) as batch:
        batch.add_column(sa.Column(
            'parent_id',
            sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
            nullable=True))
        batch.add_column(sa.Column('position', sa.Integer(), nullable=True))
        batch.alter_column('slug', existing_type=sa.String(length=200),
                           nullable=True)
        # Deleting a course deletes its lessons. Named, because an unnamed
        # constraint cannot be dropped again on a downgrade.
        batch.create_foreign_key('fk_content_parent_id', 'content',
                                 ['parent_id'], ['id'], ondelete='CASCADE')
        batch.drop_constraint('uq_content_org_type_slug', type_='unique')
    # Every render of a parent asks for its children in order, and every
    # listing asks for rows that have no parent.
    op.create_index('ix_content_parent_position', 'content',
                    ['org_id', 'parent_id', 'position'])
    # The old single rule, now applying only where it was ever right.
    op.create_index('uq_content_org_type_slug', 'content',
                    ['org_id', 'type', 'slug'], unique=True,
                    sqlite_where=sa.text('parent_id IS NULL'),
                    postgresql_where=sa.text('parent_id IS NULL'))
    op.create_index('uq_content_parent_type_slug', 'content',
                    ['org_id', 'parent_id', 'type', 'slug'], unique=True,
                    sqlite_where=sa.text('parent_id IS NOT NULL'),
                    postgresql_where=sa.text('parent_id IS NOT NULL'))

    bind = op.get_bind()
    in_use = _types_in_use(bind)
    # Read every row before writing any: updating a table while a select on
    # it is still being consumed on the same connection is undefined on
    # SQLite, and this writes to the table it is reading.
    for org_id, settings in list(_rows(bind)):
        legacy = settings.get(LEGACY_KEY) or {}
        entries = dict(settings.get(SETTINGS_KEY) or {})

        for slug in in_use.get(org_id, set()) | set(legacy):
            entry = dict(entries.get(slug) or {})
            if slug not in ESSENTIAL:
                entry['enabled'] = True
            if legacy.get(slug) == 'members':
                entry['visibility'] = 'members'
            if entry:
                entries[slug] = entry

        if entries:
            settings[SETTINGS_KEY] = entries
        settings.pop(LEGACY_KEY, None)
        _write(bind, org_id, settings)


def downgrade():
    """Put the old key back, and keep the new one.

    The old code reads section_visibility and ignores anything else, so
    leaving content_types in place costs it nothing. Deleting it would
    throw away which sections an organization had turned off, and rolling
    forward again would republish every one of them.
    """
    # Children have no address, so there is nowhere to put them: a schema
    # without parent_id cannot hold them at all. Deleting them is the only
    # honest downgrade, and it happens before slug goes back to NOT NULL,
    # which would otherwise fail on their empty slugs.
    op.drop_index('ix_content_parent_position', table_name='content')
    op.drop_index('uq_content_parent_type_slug', table_name='content')
    op.drop_index('uq_content_org_type_slug', table_name='content')
    op.execute('DELETE FROM content WHERE parent_id IS NOT NULL')
    with op.batch_alter_table('content', schema=None) as batch:
        batch.create_unique_constraint('uq_content_org_type_slug',
                                       ['org_id', 'type', 'slug'])
        batch.drop_constraint('fk_content_parent_id', type_='foreignkey')
        batch.drop_column('position')
        batch.drop_column('parent_id')
        batch.alter_column('slug', existing_type=sa.String(length=200),
                           nullable=False)

    bind = op.get_bind()
    for org_id, settings in list(_rows(bind)):
        entries = settings.get(SETTINGS_KEY) or {}
        legacy = {slug: 'members' for slug, entry in entries.items()
                  if (entry or {}).get('visibility') == 'members'}
        if legacy:
            settings[LEGACY_KEY] = legacy
        # No else: a key already there was not written by this migration,
        # and a downgrade that deletes what it did not create is a downgrade
        # that loses data.
        _write(bind, org_id, settings)
