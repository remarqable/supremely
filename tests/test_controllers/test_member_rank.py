"""A member manager cannot act on someone who outranks them.

members.manage belongs to admin as well as owner, and the member routes only
ever asked what the caller was allowed to hand out, never what the target
already held. An admin could therefore demote, suspend or remove a founding
owner. The keep-an-owner rule did not stop it, because it only protects the
last owner, not a particular one.
"""

import pytest
from flask import g

from app.extensions import db
from app.models import Membership, User
from tests.conftest import login_as

ACME = 'http://acme.example.test'


@pytest.fixture
def cast(app, acme, user):
    """The founding owner, a second owner, and an admin who attacks them."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        second = User.create(email='owner2@example.test', password='x' * 12,
                             name='Second Owner')
        Membership.add(second.id, acme.id, role='owner')
        attacker = User.create(email='admin@example.test', password='x' * 12,
                               name='Admin')
        Membership.add(attacker.id, acme.id, role='admin')
        db.session.commit()
        founder = Membership.query.filter_by(org_id=acme.id,
                                             user_id=user.id).first()
        return {'founder_membership': founder.id, 'founder': user.id,
                'attacker': attacker, 'second_owner': second.id}


def _as_attacker(client, cast):
    login_as(client, cast['attacker'])


def _role_of(app, membership_id):
    with app.app_context():
        return db.session.get(Membership, membership_id).role


def test_an_admin_cannot_demote_an_owner(app, client, cast):
    _as_attacker(client, cast)
    got = client.post(f"/manage/members/{cast['founder_membership']}/role",
                      data={'role': 'member'}, base_url=ACME,
                      follow_redirects=True)
    assert got.status_code == 200
    assert _role_of(app, cast['founder_membership']) == 'owner'


def test_an_admin_cannot_suspend_an_owner(app, client, cast):
    _as_attacker(client, cast)
    client.post(f"/manage/members/{cast['founder_membership']}/suspend",
                base_url=ACME, follow_redirects=True)
    with app.app_context():
        assert db.session.get(Membership, cast['founder_membership']).is_active


def test_an_admin_cannot_remove_an_owner(app, client, cast):
    _as_attacker(client, cast)
    client.post(f"/manage/members/{cast['founder_membership']}/remove",
                base_url=ACME, follow_redirects=True)
    with app.app_context():
        assert db.session.get(Membership, cast['founder_membership']) is not None


def test_an_admin_can_still_manage_a_member(app, client, acme, cast):
    """The guard must not cost an admin the job they are there to do."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        ordinary = User.create(email='plain@example.test', password='x' * 12,
                               name='Plain')
        Membership.add(ordinary.id, acme.id, role='member')
        db.session.commit()
        target = Membership.query.filter_by(org_id=acme.id,
                                            user_id=ordinary.id).first().id

    _as_attacker(client, cast)
    client.post(f'/manage/members/{target}/suspend', base_url=ACME,
                follow_redirects=True)
    with app.app_context():
        assert not db.session.get(Membership, target).is_active

    client.post(f'/manage/members/{target}/unsuspend', base_url=ACME,
                follow_redirects=True)
    with app.app_context():
        assert db.session.get(Membership, target).is_active


def test_an_admin_can_still_manage_another_admin(app, client, acme, cast):
    """Equal rank is not more, so peers stay manageable."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        peer = User.create(email='peer@example.test', password='x' * 12,
                           name='Peer')
        Membership.add(peer.id, acme.id, role='admin')
        db.session.commit()
        target = Membership.query.filter_by(org_id=acme.id,
                                            user_id=peer.id).first().id

    _as_attacker(client, cast)
    client.post(f'/manage/members/{target}/role', data={'role': 'member'},
                base_url=ACME, follow_redirects=True)
    assert _role_of(app, target) == 'member'


def test_an_owner_can_still_manage_another_owner(app, client, cast, user):
    """Ownership handover has to keep working."""
    login_as(client, user)
    client.post(f"/manage/members/{cast['second_owner']}/role",
                data={'role': 'admin'}, base_url=ACME, follow_redirects=True)
    with app.app_context():
        second = Membership.query.filter_by(
            user_id=cast['second_owner']).first()
        assert second.role == 'admin'
