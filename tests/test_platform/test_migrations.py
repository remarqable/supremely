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
