import pytest

from app.models import Membership, Organization
from app.platform.errors import ValidationError


def test_provision_creates_owner_membership(app, user):
    org = Organization.provision(name='Acme', slug='acme', owner=user)
    membership = Membership.get(user.id, org.id)
    assert membership is not None
    assert membership.role == 'owner'


def test_slug_validation(app, user):
    for bad in ('', 'ab', 'has space', '-leading', 'trailing-', 'a' * 64):
        with pytest.raises(ValidationError):
            Organization(name='X', slug=bad).save()


def test_reserved_slug_rejected(app):
    with pytest.raises(ValidationError, match='reserved'):
        Organization(name='X', slug='admin').save()


def test_duplicate_slug_rejected(app, acme):
    with pytest.raises(ValidationError, match='taken'):
        Organization(name='Other', slug='acme').save()


def test_suspend_and_reactivate(app, acme):
    acme.suspend()
    assert not acme.is_active
    acme.reactivate()
    assert acme.is_active


def test_archive(app, acme):
    acme.archive()
    assert not acme.is_active
    assert acme.archived_at is not None
