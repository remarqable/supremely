"""The migration path, which nothing else in the suite touches.

Every other test builds its schema from the models (`db.create_all`), and so
does dev via `flask dev sync-db`. Only production and CI run `flask db
upgrade`, and only on first boot, so a drifted migration is invisible
everywhere a developer looks until a deploy.
"""

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect, text

from app import create_app
from app.config import TestConfig
from app.extensions import db

MIGRATIONS_DIR = Path(__file__).parents[2] / 'migrations'

# ScopedProbe is a test-only model conftest imports for its side effect, so it
# is in db.metadata for the whole run but deliberately not in the migrations.
TEST_ONLY_TABLES = {'scoped_probe'}


def _include_object(obj, name, type_, reflected, compare_to):
    if type_ == 'table':
        return name not in TEST_ONLY_TABLES
    table = getattr(obj, 'table', None)
    return table is None or table.name not in TEST_ONLY_TABLES


def _wipe():
    # drop_all only knows the metadata's tables, so alembic_version survives
    # it and would make the next upgrade() a no-op.
    db.drop_all()
    db.session.execute(text('DROP TABLE IF EXISTS alembic_version'))
    db.session.commit()


@pytest.fixture
def migrated_app(tmp_path):
    """Schema built by `flask db upgrade`, the way an installation builds it."""
    class Cfg(TestConfig):
        DATA_DIR = str(tmp_path)

    app = create_app(Cfg)
    with app.app_context():
        _wipe()
        upgrade()
        yield app
        db.session.remove()
        _wipe()


def test_the_migrations_build_the_schema_the_models_describe(migrated_app):
    # Runs on whichever engine the suite runs on, so CI checks both. That is
    # the point: a hand-written migration goes wrong where the engines differ.
    with db.engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={'include_object': _include_object,
                              'compare_type': True,
                              'compare_server_default': True})
        diff = compare_metadata(context, db.metadata)

    assert diff == [], ('Migrations no longer match the models. Alembic wants:\n'
                        + '\n'.join(f'  {entry}' for entry in diff))


def test_downgrade_removes_everything_upgrade_created(migrated_app):
    # Production rollback is restore-from-backup, not a downgrade, so this
    # promises operators nothing. It keeps the generated downgrade() honest.
    assert 'organization' in inspect(db.engine).get_table_names()

    downgrade(revision='base')

    left = set(inspect(db.engine).get_table_names()) - {'alembic_version'}
    assert left == set(), f'downgrade left tables behind: {sorted(left)}'


def test_there_is_a_single_migration_head():
    # Two migrations authored in parallel give two heads, and `flask db
    # upgrade` then fails on a real installation rather than picking one.
    config = AlembicConfig()
    config.set_main_option('script_location', str(MIGRATIONS_DIR))
    heads = ScriptDirectory.from_config(config).get_heads()

    assert len(heads) == 1, f'expected one migration head, found: {heads}'


def test_sync_db_adds_a_column_the_table_is_missing(app, runner):
    """`flask dev sync-db` must keep a live dev database usable after a new
    model column: create_all skips existing tables, so the command adds the
    column itself, with the model's default applied to existing rows."""
    import sqlalchemy as sa

    from app.extensions import db

    db.session.execute(sa.text('ALTER TABLE content DROP COLUMN presentation'))
    db.session.commit()

    result = runner.invoke(args=['dev', 'sync-db'])
    assert result.exit_code == 0, result.output
    assert 'Added column content.presentation' in result.output

    columns = {c['name'] for c in sa.inspect(db.engine).get_columns('content')}
    assert 'presentation' in columns


def test_an_organization_keeps_the_sections_it_was_already_using(migrated_app):
    """Grandfathering, which is the part of this migration that matters.

    Library types are off for an organization that has not asked for one.
    An organization that has been publishing episodes for a year has asked,
    by publishing them, and must not lose the section on upgrade. Written
    against the database rather than the models, because that is what a real
    upgrade has in front of it.
    """
    import json

    import sqlalchemy as sa
    from alembic.command import downgrade as alembic_downgrade
    from alembic.command import upgrade as alembic_upgrade
    from flask_migrate import Migrate  # noqa: F401

    from app.extensions import db as _db

    bind = _db.engine
    with bind.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO organization (id, name, slug, is_active, theme, "
            "settings, created_at, updated_at) VALUES "
            "(9001, 'Old', 'old', true, 'origin', :settings, :now, :now)"),
            {'settings': json.dumps({'section_visibility':
                                     {'article': 'members'}}),
             'now': '2026-01-01 00:00:00'})
        for row_id, slug, kind in ((9101, 'ep-one', 'episode'),
                                   (9102, 'a-post', 'article')):
            connection.execute(sa.text(
                "INSERT INTO content (id, org_id, type, title, slug, body, "
                "fields, tags, status, visibility, presentation, created_at, "
                "updated_at) VALUES (:id, 9001, :kind, 'T', :slug, '', "
                "'{}', '[]', 'published', 'public', 'site', :now, :now)"),
                {'id': row_id, 'slug': slug, 'kind': kind,
                 'now': '2026-01-01 00:00:00'})

    from alembic.config import Config as AlembicConfig
    config = AlembicConfig('migrations/alembic.ini')
    config.set_main_option('script_location', 'migrations')

    def settings_now():
        with bind.connect() as connection:
            raw = connection.execute(sa.text(
                'SELECT settings FROM organization WHERE id = 9001')).scalar()
        return json.loads(raw) if isinstance(raw, str) else (raw or {})

    alembic_downgrade(config, 'a13451eb2b44')
    alembic_upgrade(config, 'head')

    settings = settings_now()
    types = settings.get('content_types') or {}
    # The podcast it was using survives the upgrade.
    assert types.get('episode', {}).get('enabled') is True
    # The lock it had set moves across rather than being lost.
    assert types.get('article', {}).get('visibility') == 'members'
    # Article is essential, so it is never written as a choice to make.
    assert 'enabled' not in types.get('article', {})
    # A type it never used stays off.
    assert 'recipe' not in types
    # And the key this replaces is gone, so there is one place to look.
    assert 'section_visibility' not in settings


def test_the_downgrade_does_not_republish_what_was_turned_off(migrated_app):
    """A round trip must not resurrect a section somebody switched off.

    The downgrade rebuilds the key the old code reads and leaves the new
    map alone, because the old code ignores what it does not know. Deleting
    it would throw away every "off" and rolling forward would republish the
    lot.
    """
    import json

    import sqlalchemy as sa
    from alembic.command import downgrade as alembic_downgrade
    from alembic.command import upgrade as alembic_upgrade
    from alembic.config import Config as AlembicConfig

    from app.extensions import db as _db

    bind = _db.engine
    decided = {'episode': {'enabled': False, 'tease': False},
               'article': {'visibility': 'members'}}
    with bind.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO organization (id, name, slug, is_active, theme, "
            "settings, created_at, updated_at) VALUES "
            "(9002, 'Decided', 'decided', true, 'origin', :settings, "
            ":now, :now)"),
            {'settings': json.dumps({'content_types': decided}),
             'now': '2026-01-01 00:00:00'})

    config = AlembicConfig('migrations/alembic.ini')
    config.set_main_option('script_location', 'migrations')
    alembic_downgrade(config, 'a13451eb2b44')
    alembic_upgrade(config, 'head')

    with bind.connect() as connection:
        raw = connection.execute(sa.text(
            'SELECT settings FROM organization WHERE id = 9002')).scalar()
    settings = json.loads(raw) if isinstance(raw, str) else (raw or {})
    types = settings.get('content_types') or {}
    assert types.get('episode', {}).get('enabled') is False   # still off
    assert types.get('episode', {}).get('tease') is False     # still kept
    assert types.get('article', {}).get('visibility') == 'members'


def test_a_migration_does_not_delete_rows_that_point_at_what_it_alters(tmp_path):
    """The failure mode of SQLite batch migrations, in a test.

    SQLite cannot alter a column in place, so Alembic rebuilds the table:
    create, copy, DROP the old one, rename. With foreign keys enforced, the
    DROP fires ON DELETE CASCADE on everything referencing it, so migrating
    `content` deletes every row of content_category, navigation_item and
    newsletter_delivery, leaves `content` itself intact, and passes
    PRAGMA foreign_key_check afterwards. Nothing anywhere reports it.

    Seeded before the revision that rebuilds the table and counted after.
    Every other test builds its schema from the models, so this is the only
    place the real upgrade path is exercised with data in it.
    """
    class Cfg(TestConfig):
        DATA_DIR = str(tmp_path)

    app = create_app(Cfg)
    with app.app_context():
        _wipe()
        upgrade(revision='a13451eb2b44')       # the revision before the rebuild
        db.session.execute(text(
            "INSERT INTO organization (id, name, slug, theme, is_active,"
            " settings, created_at, updated_at)"
            " VALUES (1, 'T', 't', 'origin', true, '{}',"
            " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
        db.session.execute(text(
            "INSERT INTO content (id, org_id, type, title, slug, body, fields,"
            " tags, status, visibility, presentation, created_at, updated_at)"
            " VALUES (1, 1, 'article', 'H', 'h', 'x', '{}', '[]', 'published',"
            " 'public', 'site', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
        db.session.execute(text(
            "INSERT INTO category (id, org_id, name, slug, created_at,"
            " updated_at) VALUES (1, 1, 'C', 'c', CURRENT_TIMESTAMP,"
            " CURRENT_TIMESTAMP)"))
        db.session.execute(text(
            'INSERT INTO content_category (content_id, category_id)'
            ' VALUES (1, 1)'))
        db.session.execute(text(
            "INSERT INTO navigation_item (id, org_id, menu, label, url,"
            " content_id, position, created_at, updated_at)"
            " VALUES (1, 1, 'primary', 'H', '/h', 1, 0, CURRENT_TIMESTAMP,"
            " CURRENT_TIMESTAMP)"))
        db.session.commit()
        db.session.remove()

        upgrade()

        counted = {
            table: db.session.execute(
                text(f'SELECT count(*) FROM {table}')).scalar()
            for table in ('content', 'content_category', 'navigation_item')}
        assert counted == {'content': 1, 'content_category': 1,
                           'navigation_item': 1}, counted
        # ...and the reference still resolves, rather than surviving as a
        # row pointing at nothing.
        assert db.session.execute(text(
            'SELECT content_id FROM navigation_item WHERE id = 1')).scalar() == 1
        db.session.remove()
        _wipe()


def test_a_failed_migration_leaves_foreign_keys_enforced(tmp_path, monkeypatch):
    """The pragma that protects the batch rebuild must not outlive it.

    Migrations run with SQLite foreign keys off, because Alembic's batch mode
    rebuilds the table and the DROP would cascade. The connection then goes
    back into the pool, and the connect event does not fire again on
    checkout, so a migration that raised part-way would hand the application
    a connection with enforcement off for the rest of the process -- turning
    every ondelete='CASCADE' into a silent no-op.
    """
    class Cfg(TestConfig):
        DATA_DIR = str(tmp_path)

    app = create_app(Cfg)
    with app.app_context():
        _wipe()
        if db.engine.dialect.name != 'sqlite':
            pytest.skip('the pragma only applies to SQLite')

        import alembic.runtime.migration as runtime

        def explode(self, *args, **kwargs):
            raise RuntimeError('migration blew up')

        monkeypatch.setattr(runtime.MigrationContext, 'run_migrations', explode)
        # Flask-Migrate catches the error and exits, so the exception that
        # escapes here is SystemExit rather than the one raised inside.
        with pytest.raises(SystemExit):
            upgrade()
        monkeypatch.undo()

        enforced = db.session.execute(
            text('PRAGMA foreign_keys')).scalar()
        db.session.remove()
        assert enforced == 1, (
            'the migration connection went back to the pool with foreign '
            'keys off')
        _wipe()
