"""Rate limiting. The suite disables it globally (TestConfig), so these tests
build their own application with it switched on."""

import time

import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.middleware import ratelimit
from tests.conftest import PASSWORD, login_as, make_user


@pytest.fixture
def app(tmp_path):
    class Cfg(TestConfig):
        DATA_DIR = str(tmp_path)
        RATELIMIT_ENABLED = True

    application = create_app(Cfg)
    with application.app_context():
        db.create_all()
        ratelimit._rate_limits.clear()
        yield application
        db.session.remove()
        db.drop_all()
        ratelimit._rate_limits.clear()


def test_reading_the_login_page_does_not_use_up_the_login_budget(app):
    """A GET and a POST share one view here, so counting GET meant that
    reloading the form locked everyone out of signing in."""
    client = app.test_client()
    user = make_user()

    for _ in range(25):
        assert client.get('/auth/login').status_code == 200

    response = client.post('/auth/login',
                           data={'email': user.email, 'password': PASSWORD})
    assert response.status_code == 302


def test_repeated_failed_logins_are_still_limited(app):
    client = app.test_client()
    make_user()

    codes = [client.post('/auth/login',
                         data={'email': 'user@example.com', 'password': 'wrong'}
                         ).status_code for _ in range(14)]
    assert 429 in codes


def test_changing_a_password_is_limited(app):
    """It checks the current password, so without a limit it is an oracle."""
    client = app.test_client()
    login_as(client, make_user())

    codes = [client.post('/auth/password',
                         data={'current_password': 'wrong',
                               'new_password': 'a-good-password-1',
                               'confirm_password': 'a-good-password-1'}
                         ).status_code for _ in range(14)]
    assert 429 in codes
    assert client.get('/auth/password').status_code == 200   # form still opens


def test_a_get_only_route_can_opt_in(app):
    """/tls-check answers Caddy on every certificate decision, so it is
    expensive to serve and does ask to be counted."""
    client = app.test_client()
    codes = [client.get('/tls-check?domain=nope.example.test').status_code
             for _ in range(305)]
    assert 429 in codes


def test_stale_entries_are_swept(app):
    """Unauthenticated routes are keyed by client IP, which an attacker varies
    freely, so nothing ever removing an entry is a slow memory leak."""
    ratelimit._rate_limits.clear()
    stale = time.time() - 999
    for index in range(ratelimit._SWEEP_AT + 5):
        ratelimit._rate_limits[f'key-{index}'] = (1, stale, 60)

    with app.test_request_context('/'):
        ratelimit._check_limit('a-fresh-key', 10, 60)

    assert len(ratelimit._rate_limits) == 1


def test_a_live_entry_keeps_its_count_through_a_sweep(app):
    ratelimit._rate_limits.clear()
    now = time.time()
    for index in range(ratelimit._SWEEP_AT + 5):
        ratelimit._rate_limits[f'key-{index}'] = (1, now - 999, 60)
    ratelimit._rate_limits['still-counting'] = (3, now, 60)

    with app.test_request_context('/'):
        ratelimit._check_limit('a-fresh-key', 10, 60)

    # Resetting a live counter would hand back the budget it had spent.
    assert ratelimit._rate_limits['still-counting'][0] == 3


def test_head_counts_against_a_get_only_route(app):
    """Flask answers HEAD with the GET view, so a route that opts into GET is
    unlimited over HEAD unless HEAD counts too. /tls-check runs a database
    lookup per call, which is exactly what the limit is protecting."""
    client = app.test_client()

    codes = [client.head('/tls-check?domain=nope.example.test').status_code
             for _ in range(305)]
    assert 429 in codes


def test_the_sweep_does_not_run_on_every_request(app):
    """It walks the whole dict, so running it per request would let anyone
    holding the dict above the threshold tax everybody else's requests."""
    ratelimit._rate_limits.clear()
    ratelimit._last_sweep = 0.0
    now = time.time()
    for index in range(ratelimit._SWEEP_AT + 5):
        ratelimit._rate_limits[f'key-{index}'] = (1, now - 999, 60)

    with app.test_request_context('/'):
        ratelimit._check_limit('first', 10, 60)      # sweeps
        swept_at = ratelimit._last_sweep
        for index in range(ratelimit._SWEEP_AT + 5):
            ratelimit._rate_limits[f'again-{index}'] = (1, now - 999, 60)
        ratelimit._check_limit('second', 10, 60)     # must not sweep again

    assert ratelimit._last_sweep == swept_at
    assert len(ratelimit._rate_limits) > ratelimit._SWEEP_AT


def test_live_entries_are_capped(app):
    """Expiry alone cannot bound a flood of entries that are all still inside
    their window."""
    ratelimit._rate_limits.clear()
    ratelimit._last_sweep = 0.0
    now = time.time()
    for index in range(ratelimit._MAX_ENTRIES + 5_000):
        ratelimit._rate_limits[f'key-{index}'] = (1, now, 300)

    with app.test_request_context('/'):
        ratelimit._check_limit('a-fresh-key', 10, 60)

    assert len(ratelimit._rate_limits) <= ratelimit._MAX_ENTRIES + 1


def test_a_proxy_without_trusted_proxies_is_reported(app):
    """Every client shares one bucket in that configuration, so one visitor
    can lock out the installation. Reported on a counted request, which is
    the only time the shared bucket matters."""
    ratelimit._warned_about_proxy = False
    client = app.test_client()

    client.post('/auth/login', data={'email': 'nobody@example.com',
                                     'password': 'wrong'},
                headers={'X-Forwarded-For': '203.0.113.9'})

    assert ratelimit._warned_about_proxy is True


def test_no_proxy_warning_when_trusted_proxies_is_set(app):
    ratelimit._warned_about_proxy = False
    app.config['TRUSTED_PROXIES'] = 1
    client = app.test_client()

    client.post('/auth/login', data={'email': 'nobody@example.com',
                                     'password': 'wrong'},
                headers={'X-Forwarded-For': '203.0.113.9'})

    assert ratelimit._warned_about_proxy is False
