"""Column types that compile correctly on both SQLite and PostgreSQL.

See blueprint/patterns/core/portability.md.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# BIGINT primary keys do NOT autoincrement on SQLite: only INTEGER PRIMARY KEY
# is a rowid alias. Without this variant every INSERT fails with
# "NOT NULL constraint failed: <table>.id".
BigIntPK = sa.BigInteger().with_variant(sa.Integer, 'sqlite')

# Foreign keys must match the referenced column's type on both engines.
BigIntFK = sa.BigInteger().with_variant(sa.Integer, 'sqlite')

# JSONB on Postgres (indexable, binary), plain JSON on SQLite.
JSONColumn = sa.JSON().with_variant(postgresql.JSONB, 'postgresql')


class TZDateTime(sa.TypeDecorator):
    """UTC-aware datetimes on both engines.

    SQLite has no timezone type: values written aware come back naive, and
    comparing them against utcnow() raises TypeError. Store naive UTC on
    SQLite and re-attach UTC on read; PostgreSQL passes through untouched.
    """
    impl = sa.DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        import datetime as dt
        if value is None:
            return None
        if value.tzinfo is not None and dialect.name == 'sqlite':
            return value.astimezone(dt.UTC).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        import datetime as dt
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=dt.UTC)
        return value
