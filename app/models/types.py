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
