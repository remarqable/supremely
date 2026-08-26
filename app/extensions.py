"""Flask extensions and database setup."""

import sqlalchemy as sa
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event

# Named constraints are required for SQLite migrations. Set this before the
# first migration or Alembic will want to rename every constraint you have.
NAMING_CONVENTION = {
    'ix': 'ix_%(table_name)s_%(column_0_name)s',
    'uq': 'uq_%(table_name)s_%(column_0_name)s',
    'ck': 'ck_%(table_name)s_%(constraint_name)s',
    'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
    'pk': 'pk_%(table_name)s',
}

db = SQLAlchemy(metadata=sa.MetaData(naming_convention=NAMING_CONVENTION))
# render_as_batch: required for ALTER support on SQLite. compare_type: detect
# column type changes. See blueprint/patterns/core/portability.md.
migrate = Migrate(render_as_batch=True, compare_type=True)
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message_category = 'warning'


@login_manager.user_loader
def load_user(user_id):
    from .models.user import User
    return db.session.get(User, int(user_id))


def init_sqlite_pragmas(app):
    """SQLite enforces no foreign keys by default -- every ondelete='CASCADE'
    silently does nothing without this. No-op on PostgreSQL.
    See blueprint/patterns/core/portability.md."""
    if not app.config['IS_SQLITE']:
        return

    @event.listens_for(db.engine, 'connect')
    def _pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute('PRAGMA foreign_keys=ON')
        cur.execute('PRAGMA journal_mode=WAL')
        cur.execute('PRAGMA synchronous=NORMAL')
        cur.execute('PRAGMA busy_timeout=5000')
        cur.close()
