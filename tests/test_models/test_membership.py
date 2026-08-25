import pytest

from app.models import Membership
from app.platform.errors import ValidationError
from tests.conftest import make_user


def test_add_is_idempotent(app, acme):
    user = make_user(email='m1@example.com')
    first = Membership.add(user.id, acme.id, role='member')
    second = Membership.add(user.id, acme.id, role='admin')
    assert first.id == second.id
    assert second.role == 'member'      # re-adding never mutates the role


def test_invalid_role_rejected(app, acme):
    user = make_user(email='m2@example.com')
    with pytest.raises(ValidationError):
        Membership.add(user.id, acme.id, role='superuser')


def test_last_owner_cannot_be_demoted(app, acme, user):
    membership = Membership.get(user.id, acme.id)
    with pytest.raises(ValidationError, match='at least one owner'):
        membership.change_role('member')


def test_last_owner_cannot_be_removed(app, acme, user):
    membership = Membership.get(user.id, acme.id)
    with pytest.raises(ValidationError, match='at least one owner'):
        membership.remove()


def test_owner_demotion_allowed_with_second_owner(app, acme, user):
    other = make_user(email='m3@example.com')
    Membership.add(other.id, acme.id, role='owner')
    membership = Membership.get(user.id, acme.id)
    membership.change_role('member')
    assert membership.role == 'member'


def test_different_roles_in_different_orgs(app, acme, globex, user):
    Membership.add(user.id, globex.id, role='member')
    assert Membership.get(user.id, acme.id).role == 'owner'
    assert Membership.get(user.id, globex.id).role == 'member'
