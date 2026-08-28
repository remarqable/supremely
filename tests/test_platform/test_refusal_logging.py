"""Every refusal the server makes leaves a record.

Nothing recorded them before. A permission denial, a rejected form token, a
spent rate limit and an attempt to write across organizations were all
silent, which meant probing left no trace and the most significant event in
a multi-tenant system was indistinguishable in a log from a null reference.
"""

import pytest
import structlog
from flask import g

from app.extensions import db
from app.models import Content, Membership, User
from app.platform.errors import TenantViolation
from tests.conftest import login_as

ACME = 'http://acme.example.test'


@pytest.fixture
def refusals():
    """Capture what the logger was asked to record."""
    captured = []

    def sink(logger, method, event_dict):
        captured.append(event_dict)
        raise structlog.DropEvent

    original = structlog.get_config()['processors']
    structlog.configure(processors=[*original[:-1], sink])
    yield captured
    structlog.configure(processors=original)


def _events(captured):
    return [entry.get('event') for entry in captured]


def test_a_permission_denial_is_recorded(app, client, acme, refusals):
    plain = User.create(email='plain@example.test', password='x' * 12,
                        name='Plain')
    Membership.add(plain.id, acme.id, role='member')
    db.session.commit()

    login_as(client, plain)
    assert client.get('/manage/settings', base_url=ACME).status_code == 403

    denial = [e for e in refusals if e.get('event') == 'permission_denied']
    assert denial, _events(refusals)
    assert denial[0]['actor_id'] == plain.id
    assert denial[0]['path'] == '/manage/settings'
    assert denial[0]['permission']


def test_reaching_the_console_without_the_privilege_is_recorded(
        app, client, acme, user, refusals):
    """The visitor is told the page does not exist. The log says otherwise."""
    login_as(client, user)
    assert client.get('/admin/', base_url='http://example.test').status_code == 404
    assert 'platform_admin_denied' in _events(refusals)


def test_a_rejected_form_token_is_recorded(app, client, acme, refusals):
    app.config['CSRF_ENABLED'] = True
    try:
        posted = client.post('/auth/login',
                             data={'email': 'a@b.co', 'password': 'x'},
                             base_url=ACME)
    finally:
        app.config['CSRF_ENABLED'] = False
    assert posted.status_code == 403
    assert 'csrf_rejected' in _events(refusals)


def test_writing_across_organizations_is_recorded_and_typed(
        app, acme, globex, refusals):
    """A dedicated type, so this cannot be mistaken for an ordinary bug."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        theirs = Content.query.filter_by(org_id=globex.id).first()
        assert theirs is None, 'the read filter should already hide it'

        with pytest.raises(TenantViolation):
            db.session.add(Content(type='post', title='t', slug='cross',
                                   status='draft', org_id=globex.id))
            db.session.flush()
        db.session.rollback()

    assert 'tenant_write_refused' in _events(refusals)


def test_a_refusal_never_carries_a_credential(app, client, acme, refusals):
    app.config['CSRF_ENABLED'] = True
    try:
        client.post('/auth/login',
                    data={'email': 'a@b.co', 'password': 'hunter2-secret'},
                    base_url=ACME)
    finally:
        app.config['CSRF_ENABLED'] = False

    assert refusals, 'nothing was recorded, so this proves nothing'
    for entry in refusals:
        rendered = repr(entry)
        assert 'hunter2-secret' not in rendered
        assert 'password' not in rendered


def test_a_spent_rate_limit_is_recorded(app, client, acme, refusals):
    app.config['RATELIMIT_ENABLED'] = True
    try:
        for _ in range(12):
            client.post('/auth/login', data={'email': 'a@b.co', 'password': 'x'},
                        base_url=ACME)
    finally:
        app.config['RATELIMIT_ENABLED'] = False
    assert 'rate_limited' in _events(refusals)


def test_a_field_name_the_helper_already_uses_does_not_raise(app):
    """The helper exists so a refusal is recorded. A caller passing a name
    it stamps itself must not turn that into an exception."""
    from app.platform.logger import log_refusal

    with app.test_request_context('/somewhere'):
        log_refusal('probe', path='/elsewhere', ip='1.2.3.4', actor_id=99)
