"""Phase 4: membership experience. Completion test: an organization operates
as a private or mixed public/member website with NO email infrastructure."""

import io
import re

from flask import g

from app.extensions import db
from app.models import Content, Invitation, Membership, User
from app.platform.mailer import is_email_configured
from tests.conftest import login_as, make_png, make_user

ACME = 'http://acme.example.test'


def extract_invite_url(html: bytes) -> str:
    match = re.search(rb'value="(http[^"]*/invite/[^"]+)"', html)
    assert match, 'invite URL not shown'
    return match.group(1).decode()


# --- Invitations without email ---------------------------------------------------

def test_invitation_full_lifecycle_without_email(app, client, acme, globex, user):
    assert not is_email_configured()

    login_as(client, user)      # acme owner
    response = client.post('/manage/invitations', base_url=ACME,
                           data={'role': 'member'}, follow_redirects=True)
    invite_url = extract_invite_url(response.data)
    token = invite_url.rsplit('/', 1)[1]

    # Anonymous stranger opens the link and signs up -- atomic with acceptance
    stranger = app.test_client()
    page = stranger.get(f'/invite/{token}', base_url=ACME)
    assert page.status_code == 200
    assert b'Acme' in page.data

    joined = stranger.post(f'/invite/{token}/signup', base_url=ACME, data={
        'name': 'Newcomer', 'email': 'new@example.com',
        'password': 'newcomer-secret-1',
    })
    assert joined.status_code == 302

    newcomer = User.get_by_email('new@example.com')
    assert newcomer is not None
    membership = Membership.get(newcomer.id, acme.id)
    assert membership is not None and membership.role == 'member'

    # Single use: the same token is now dead
    assert stranger.get(f'/invite/{token}', base_url=ACME).status_code == 404


def test_invitation_accept_logged_in(app, client, acme, globex, user):
    login_as(client, user)
    response = client.post('/manage/invitations', base_url=ACME,
                           data={'role': 'admin'}, follow_redirects=True)
    token = extract_invite_url(response.data).rsplit('/', 1)[1]

    other = make_user(email='existing@example.com')
    other_client = app.test_client()
    login_as(other_client, other)
    other_client.post(f'/invite/{token}/accept', base_url=ACME)
    assert Membership.get(other.id, acme.id).role == 'admin'


def test_expired_invitation_rejected(app, client, acme, globex, user):
    from datetime import timedelta

    from app.models.base import utcnow
    with app.test_request_context(base_url=ACME):
        g.org = acme
        invitation, token = Invitation.create(acme.id)
        invitation.expires_at = utcnow() - timedelta(days=1)
        db.session.commit()
    assert client.get(f'/invite/{token}', base_url=ACME).status_code == 404


def test_invitation_isolated_to_org(app, client, acme, globex, user):
    login_as(client, user)
    response = client.post('/manage/invitations', base_url=ACME,
                           data={'role': 'member'}, follow_redirects=True)
    token = extract_invite_url(response.data).rsplit('/', 1)[1]
    # Same token on another org's host: unreachable
    assert client.get(f'/invite/{token}',
                      base_url='http://globex.example.test').status_code == 404


def test_member_cannot_manage_members(app, client, acme, globex, user):
    member = make_user(email='m@example.com')
    Membership.add(member.id, acme.id, role='member')
    login_as(client, member)
    assert client.get('/manage/members', base_url=ACME).status_code == 403
    assert client.post('/manage/invitations', base_url=ACME,
                       data={'role': 'member'}).status_code == 403


# --- Suspension --------------------------------------------------------------------

def test_suspended_member_loses_access(app, client, acme, globex, user):
    with app.test_request_context(base_url=ACME):
        g.org = acme
        page = Content(type='page', title='Inside', slug='inside',
                       org_id=acme.id, visibility='members', fields={}, tags=[])
        page.save()
        page.publish()

    member = make_user(email='m2@example.com')
    membership = Membership.add(member.id, acme.id, role='member')
    login_as(client, member)
    assert client.get('/inside', base_url=ACME).status_code == 200

    membership.suspend()
    assert client.get('/inside', base_url=ACME).status_code == 404
    assert client.get('/dashboard', base_url=ACME).status_code == 404

    membership.unsuspend()
    assert client.get('/inside', base_url=ACME).status_code == 200


def test_last_owner_cannot_be_suspended(app, acme, user):
    membership = Membership.get(user.id, acme.id)
    import pytest

    from app.platform.errors import ValidationError
    with pytest.raises(ValidationError, match='at least one owner'):
        membership.suspend()


# --- Ownership transfer --------------------------------------------------------------

def test_transfer_ownership(app, client, acme, globex, user):
    member = make_user(email='next-owner@example.com')
    target = Membership.add(member.id, acme.id, role='member')
    login_as(client, user)
    response = client.post(f'/manage/members/{target.id}/transfer',
                           base_url=ACME)
    assert response.status_code == 302
    assert Membership.get(member.id, acme.id).role == 'owner'
    assert Membership.get(user.id, acme.id).role == 'admin'


def test_admin_cannot_transfer(app, client, acme, globex, user):
    admin = make_user(email='adm@example.com')
    Membership.add(admin.id, acme.id, role='admin')
    member = make_user(email='m3@example.com')
    target = Membership.add(member.id, acme.id, role='member')
    login_as(client, admin)
    assert client.post(f'/manage/members/{target.id}/transfer',
                       base_url=ACME).status_code == 403


# --- Profiles & directory -------------------------------------------------------------

def test_profile_update_with_avatar(app, client, acme, globex, user):
    login_as(client, user)
    response = client.post('/profile', base_url=ACME, data={
        'name': 'Renamed', 'bio': 'I build things.',
        'avatar': (io.BytesIO(make_png(300, 300)), 'me.png'),
    }, content_type='multipart/form-data')
    assert response.status_code == 302

    refreshed = db.session.get(User, user.id)
    assert refreshed.name == 'Renamed'
    assert refreshed.bio == 'I build things.'
    assert refreshed.avatar_key

    avatar = client.get(f'/avatars/{user.id}', base_url=ACME)
    assert avatar.status_code == 200
    assert avatar.mimetype == 'image/webp'


def test_avatar_rejects_non_image(client, acme, globex, user):
    login_as(client, user)
    response = client.post('/profile', base_url=ACME, data={
        'name': 'X', 'avatar': (io.BytesIO(b'plain text'), 'x.png'),
    }, content_type='multipart/form-data')
    assert b'must be a PNG' in response.data


def test_directory_on_by_default_and_can_be_disabled(client, acme, globex, user):
    login_as(client, user)
    assert client.get('/members', base_url=ACME).status_code == 200
    acme.update_settings(member_directory=False)
    assert client.get('/members', base_url=ACME).status_code == 404


def test_directory_members_only(app, client, acme, globex, user):
    acme.update_settings(member_directory=True)
    # Anonymous: redirected to login
    anon = app.test_client()
    response = anon.get('/members', base_url=ACME)
    assert response.status_code == 302

    login_as(client, user)
    listing = client.get('/members', base_url=ACME)
    assert listing.status_code == 200
    assert user.name.encode() in listing.data
