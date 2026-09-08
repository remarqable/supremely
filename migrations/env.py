import logging
from logging.config import fileConfig

from alembic import context
from flask import current_app

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
fileConfig(config.config_file_name)
logger = logging.getLogger('alembic.env')


def get_engine():
    try:
        # this works with Flask-SQLAlchemy<3 and Alchemical
        return current_app.extensions['migrate'].db.get_engine()
    except (TypeError, AttributeError):
        # this works with Flask-SQLAlchemy>=3
        return current_app.extensions['migrate'].db.engine


def get_engine_url():
    try:
        return get_engine().url.render_as_string(hide_password=False).replace(
            '%', '%%')
    except AttributeError:
        return str(get_engine().url).replace('%', '%%')


# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
config.set_main_option('sqlalchemy.url', get_engine_url())
target_db = current_app.extensions['migrate'].db

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def get_metadata():
    if hasattr(target_db, 'metadatas'):
        return target_db.metadatas[None]
    return target_db.metadata


def run_migrations_offline():
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=get_metadata(), literal_binds=True
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    # this callback is used to prevent an auto-migration from being generated
    # when there are no changes to the schema
    # reference: http://alembic.zzzcomputing.com/en/latest/cookbook.html
    def process_revision_directives(context, revision, directives):
        if getattr(config.cmd_opts, 'autogenerate', False):
            script = directives[0]
            if script.upgrade_ops.is_empty():
                directives[:] = []
                logger.info('No changes in schema detected.')

    def render_item(type_, obj, autogen_context):
        """Render custom column types as plain SQLAlchemy in migrations.

        TZDateTime is a behavioral wrapper over DateTime(timezone=True) --
        the schema is identical, and migrations should not import app code.
        Postgres JSONB variants also need sa.Text qualified.
        """
        if type_ == 'type':
            type_name = type(obj).__name__
            if type_name == 'TZDateTime':
                return 'sa.DateTime(timezone=True)'
        return False

    conf_args = current_app.extensions['migrate'].configure_args
    if conf_args.get("process_revision_directives") is None:
        conf_args["process_revision_directives"] = process_revision_directives
    conf_args.setdefault("render_item", render_item)

    connectable = get_engine()

    with connectable.connect() as connection:
        # SQLite cannot alter a column in place, so Alembic's batch mode
        # rebuilds the whole table: create the new shape, copy the rows, DROP
        # the old table, rename. With foreign keys enforced -- which the
        # application turns on for every connection -- that DROP fires ON
        # DELETE CASCADE on everything pointing at the table, so migrating
        # `content` silently deletes every row of content_category,
        # navigation_item and newsletter_delivery while `content` itself
        # survives intact. PRAGMA foreign_key_check then reports clean and
        # nothing tells the operator.
        #
        # Issued straight to the driver, not through this connection: a
        # statement run through SQLAlchemy starts a transaction, the pragma
        # is ignored inside one, and Alembic's own transaction then never
        # commits the version row -- leaving a database with the new schema
        # that believes it was never migrated. Other engines rebuild nothing
        # and are unaffected.
        sqlite = connection.dialect.name == 'sqlite'
        driver = connection.connection.driver_connection if sqlite else None
        if sqlite:
            driver.execute('PRAGMA foreign_keys=OFF')

        try:
            context.configure(
                connection=connection,
                target_metadata=get_metadata(),
                **conf_args
            )

            with context.begin_transaction():
                context.run_migrations()

            if sqlite:
                # A migration that really does strand a reference should be
                # loud rather than silent, now that nothing enforces it live.
                dangling = driver.execute(
                    'PRAGMA foreign_key_check').fetchall()
                if dangling:
                    raise RuntimeError(
                        'Migration left rows pointing at rows that are not '
                        f'there: {dangling[:10]}')
        finally:
            # In a finally, because this connection goes back to the pool
            # afterwards and the connect event does not fire again on
            # checkout. A migration that raised would otherwise hand the
            # application a connection with foreign keys off for the rest of
            # the process, making every ondelete='CASCADE' a silent no-op --
            # which is the failure this whole block exists to prevent.
            if sqlite:
                driver.execute('PRAGMA foreign_keys=ON')


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
