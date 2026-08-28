"""An avatar is served only where the person it belongs to is a member.

The route had no check at all. User is not organization scoped, so the
tenant filter never touched the lookup, and any host would serve any
picture. That leaked the faces of a private community to any visitor, and
answering 200 or 404 per id listed every account on the installation.

It stays reachable without signing in, because author avatars appear on
pages the public can read.
"""

import io

import pytest
from flask import g
from PIL import Image

from app.extensions import db
from app.models import Membership, User
from tests.conftest import login_as

ACME = 'http://acme.example.test'
GLOBEX = 'http://globex.example.test'


def _give_avatar(user):
    """Written through the session the request will use.

    The app fixture yields inside an app context, so opening another one
    here would write through a second session and leave the first holding
    a stale copy of the row.
    """
    from app.platform.storage import storage
    out = io.BytesIO()
    Image.new('RGB', (16, 16), 'white').save(out, 'WEBP')
    out.seek(0)
    user.avatar_key = f'avatars/{user.id}/probe.webp'
    storage().save(user.avatar_key, out)
    db.session.commit()
    return user.id


@pytest.fixture
def globex_member(app, globex):
    with app.test_request_context(base_url=GLOBEX):
        g.org = globex
        person = User.create(email='gx@example.test', password='x' * 12,
                             name='Globex Person')
        Membership.add(person.id, globex.id, role='member')
        db.session.commit()
        return person


def test_a_visitor_sees_an_avatar_on_the_host_where_that_person_is_a_member(
        app, client, acme, user):
    """Author avatars appear on public pages, so this must not need login."""
    uid = _give_avatar(user)
    got = client.get(f'/avatars/{uid}', base_url=ACME)
    assert got.status_code == 200
    assert got.headers['Cache-Control'] == 'private, max-age=86400'


def test_another_tenant_cannot_serve_that_avatar(app, client, acme, globex,
                                                 user):
    uid = _give_avatar(user)
    assert client.get(f'/avatars/{uid}', base_url=GLOBEX).status_code == 404


def test_a_globex_member_is_not_served_from_acme(app, client, acme, globex,
                                                 globex_member):
    uid = _give_avatar(globex_member)
    assert client.get(f'/avatars/{uid}', base_url=GLOBEX).status_code == 200
    assert client.get(f'/avatars/{uid}', base_url=ACME).status_code == 404


def test_your_own_avatar_follows_you_where_no_organization_resolves(
        app, client, acme, globex, user):
    """The top bar renders it on /admin and /launcher, which bypass tenancy.
    Gating the route on an organization would break the picture there.

    Globex is requested so that two organizations exist. With only one, the
    bare domain resolves to it and this passes through the membership
    branch instead, testing nothing.
    """
    uid = _give_avatar(user)
    login_as(client, user)
    got = client.get(f'/avatars/{uid}', base_url='http://example.test/launcher')
    assert got.status_code == 200


def test_a_suspended_member_is_no_longer_served(app, client, acme, user):
    uid = _give_avatar(user)
    assert client.get(f'/avatars/{uid}', base_url=ACME).status_code == 200
    Membership.query.filter_by(user_id=uid).first().is_active = False
    db.session.commit()
    assert client.get(f'/avatars/{uid}', base_url=ACME).status_code == 404


def test_an_unknown_id_is_still_a_plain_404(client, acme):
    assert client.get('/avatars/999999', base_url=ACME).status_code == 404
