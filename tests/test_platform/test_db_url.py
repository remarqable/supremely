"""PostgreSQL URL normalisation.

Managed providers hand out `postgres://` or `postgresql://`. SQLAlchemy maps
both to psycopg2, which this project does not install -- an unnormalised URL
fails at boot with ModuleNotFoundError rather than anything diagnostic.
"""

import pytest

from app.config import _normalise_db_url

PSYCOPG3 = 'postgresql+psycopg://'


@pytest.mark.parametrize('url', [
    'postgresql://u:p@host:25060/db',
    'postgres://u:p@host:5432/db',
])
def test_provider_urls_are_pointed_at_psycopg3(url):
    assert _normalise_db_url(url).startswith(PSYCOPG3)


def test_query_string_is_preserved():
    """DigitalOcean requires sslmode=require; losing it breaks the connection."""
    out = _normalise_db_url('postgresql://u:p@host:25060/db?sslmode=require')
    assert out == f'{PSYCOPG3}u:p@host:25060/db?sslmode=require'


def test_credentials_are_preserved():
    out = _normalise_db_url('postgres://user:s3cr3t@host/db')
    assert out == f'{PSYCOPG3}user:s3cr3t@host/db'


def test_explicit_driver_is_left_alone():
    for url in (f'{PSYCOPG3}u@h/db', 'postgresql+psycopg2://u@h/db'):
        assert _normalise_db_url(url) == url


def test_sqlite_is_untouched():
    assert _normalise_db_url('sqlite:////data/app.db') == 'sqlite:////data/app.db'


def test_normalised_url_still_reads_as_postgres():
    """IS_POSTGRES keys off the prefix; normalisation must not break it."""
    assert _normalise_db_url('postgres://u@h/db').startswith('postgresql')
