"""Guessing is bounded per account, not only per address.

The shipped budget is a hundred, the ceiling NIST sets on consecutive
failed attempts for one account. These tests lower it, because what they
check is that the budget exists, follows the account rather than the
caller, and clears on success.

The rate limit keys on the caller's address, which bounds one attacker and
does nothing about one account being guessed at from many. Nothing counted
failures against the account itself, so a spraying attack simply rotated
addresses. Nothing was logged either, so it left no trace.
"""

import pytest

from app.middleware.ratelimit import _rate_limits
from app.models import User
from app.platform.errors import ValidationError

PASSWORD = 'correct-horse-9'
ACME = 'http://acme.example.test'


BUDGET = 6


@pytest.fixture
def throttling(app, monkeypatch):
    """The suite disables rate limiting; this commit's guard is the point.

    The budget is lowered here because each attempt pays a real password
    hash, and the shipped number would spend ten seconds a test proving
    something none of these assert on.
    """
    import app.controllers.auth as auth_module
    monkeypatch.setattr(auth_module, 'LOGIN_FAILURES', BUDGET)
    app.config['RATELIMIT_ENABLED'] = True
    _rate_limits.clear()
    yield
    _rate_limits.clear()
    app.config['RATELIMIT_ENABLED'] = False


@pytest.fixture
def account(app, acme):
    return User.create(email='real@example.test', password=PASSWORD, name='R')


_next_address = iter(range(1, 250))


def _attempt(client, password, email='real@example.test'):
    """Each attempt comes from a fresh address.

    The per address limit is tighter than the per account one, so from a
    single caller it fires first and the account budget is never reached.
    Rotating is what a spraying attack does, and what this guard is for.
    """
    client.environ_base['REMOTE_ADDR'] = f'10.0.{next(_next_address)}.1'
    return client.post('/auth/login', data={'email': email, 'password': password},
                       base_url=ACME)


def test_guessing_one_account_runs_out_of_attempts(client, account, throttling):
    for _ in range(BUDGET):
        assert _attempt(client, 'wrong').status_code == 401
    # The budget is spent, so even the real password is refused now.
    assert _attempt(client, PASSWORD).status_code == 401


def test_the_budget_follows_the_account_not_the_address(client, account,
                                                        throttling):
    """Changing address is the whole point of a spraying attack, so the
    count must not reset when the caller does."""
    for _ in range(BUDGET):
        assert _attempt(client, 'wrong').status_code == 401
    assert _attempt(client, PASSWORD).status_code == 401


def test_an_unknown_address_is_counted_too(client, account, throttling):
    """Counting only real accounts would say which addresses are real."""
    for _ in range(BUDGET):
        assert _attempt(client, 'wrong', 'nobody@example.test').status_code == 401
    assert 'failure:nobody@example.test' in _rate_limits


def test_signing_in_clears_the_count(client, account, throttling):
    for _ in range(BUDGET - 1):
        _attempt(client, 'wrong')
    assert _attempt(client, PASSWORD).status_code == 302
    assert 'failure:real@example.test' not in _rate_limits


def test_a_few_typos_cost_nothing(client, account, throttling):
    for _ in range(3):
        assert _attempt(client, 'oops').status_code == 401
    assert _attempt(client, PASSWORD).status_code == 302


def test_a_commonly_guessed_password_is_refused(app):
    for candidate in ('password', 'Password1', 'QWERTY123', 'iloveyou'):
        with pytest.raises(ValidationError, match='commonly used'):
            User.create(email=f'{candidate}@example.test', name='X',
                        password=candidate)


def test_an_ordinary_password_is_still_accepted(app):
    User.create(email='fine@example.test', name='X',
                password='a-perfectly-fine-one')



def test_the_development_seed_may_use_an_obvious_password(app):
    """The seed exists to give someone a login they can type from memory."""
    User.create(email='seeded@example.test', name='X', password='password',
                allow_common=True)


def test_that_exemption_is_ignored_on_a_real_installation(app):
    """So passing it can never weaken a live site, and there is nothing to
    be gained by reaching for it."""
    app.config['APP_ENV'] = 'production'
    try:
        with pytest.raises(ValidationError, match='commonly used'):
            User.create(email='nope@example.test', name='X',
                        password='password', allow_common=True)
    finally:
        app.config['APP_ENV'] = 'test'


def test_a_huge_address_cannot_fill_the_counter_store(client, throttling):
    """The counter is keyed on whatever was typed, and a key outlives the
    request. The entry cap counts entries, not their size."""
    from app.middleware.ratelimit import _rate_limits

    _attempt(client, 'wrong', 'a' * 100_000 + '@example.test')
    assert _rate_limits, 'the attempt was not counted at all'
    assert max(len(key) for key in _rate_limits) < 1000


def test_the_wizard_refuses_an_obvious_password_where_it_is_typed(tmp_path):
    """Left to the model, the refusal arrived two steps later as an error
    page with no way back to the field.

    Needs an install that has not run yet, since the wizard is closed once
    it has.
    """
    from app import create_app
    from app.config import TestConfig
    from app.extensions import db as _db

    class FreshConfig(TestConfig):
        SETUP_COMPLETE = False
        DATA_DIR = str(tmp_path)

    fresh = create_app(FreshConfig)
    with fresh.app_context():
        _db.create_all()
        client = fresh.test_client()
        client.post('/setup/environment', data={
            'name': 'My Community', 'base_url': 'http://example.test',
            'timezone': 'UTC', 'language': 'en'})

        refused = client.post('/setup/admin',
                              data={'password': 'password',
                                    'confirm_password': 'password'},
                              follow_redirects=True)
        assert b'commonly used' in refused.data

        accepted = client.post('/setup/admin',
                               data={'password': 'super-secret-1',
                                     'confirm_password': 'super-secret-1'},
                               follow_redirects=True)
        assert b'commonly used' not in accepted.data
        _db.session.remove()
        _db.drop_all()
