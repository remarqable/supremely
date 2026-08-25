import pytest

from app.models import User
from app.platform.errors import ValidationError
from tests.conftest import PASSWORD, make_user


def test_create_user(app):
    user = make_user(email='new@example.com')
    assert user.id is not None
    assert user.email == 'new@example.com'
    assert user.check_password(PASSWORD)
    assert not user.is_platform_admin


def test_email_normalized(app):
    user = make_user(email='  UPPER@Example.COM  ')
    assert user.email == 'upper@example.com'


def test_invalid_email_rejected(app):
    with pytest.raises(ValidationError):
        make_user(email='not-an-email')


def test_duplicate_email_rejected(app, user):
    with pytest.raises(ValidationError, match='already registered'):
        make_user(email=user.email)


def test_password_never_stored_plaintext(app, user):
    assert PASSWORD not in (user.password_hash or '')


def test_short_password_rejected(app):
    with pytest.raises(ValidationError, match='at least 8'):
        User.create(email='a@b.co', name='A', password='short')


def test_wrong_password_fails(app, user):
    assert not user.check_password('wrong-password')


def test_get_by_email(app, user):
    assert User.get_by_email('USER@example.com  ').id == user.id
    assert User.get_by_email('nobody@example.com') is None


def test_the_seeded_admin_username_is_allowed(app):
    """The wizard creates "admin", which is not an email address."""
    admin = User.create(email='admin', name='Admin', password=PASSWORD,
                        is_platform_admin=True)
    assert admin.id is not None
    assert User.get_by_email('admin') is admin


def test_no_other_bare_username_is_allowed_even_for_an_admin(app):
    """The exception is one identity, not a privilege. Keying it on
    is_platform_admin would let any admin be created without an address."""
    with pytest.raises(ValidationError):
        User.create(email='bob', name='Bob', password=PASSWORD,
                    is_platform_admin=True)


def test_the_seeded_admin_stays_valid_when_demoted(app):
    """Keying the rule on the privilege flag made this account impossible to
    demote: validate() would reject its own username on save."""
    admin = User.create(email='admin', name='Admin', password=PASSWORD,
                        is_platform_admin=True)
    admin.is_platform_admin = False
    admin.save()
    assert User.get_by_email('admin').is_platform_admin is False


def test_ordinary_user_may_not_use_a_bare_username(app):
    """Everyone else must be reachable: invitations, newsletters and
    notifications are all delivered by email."""
    with pytest.raises(ValidationError):
        User.create(email='bob', name='Bob', password=PASSWORD)


def test_admin_username_is_normalised_to_lowercase(app):
    admin = User.create(email='  ADMIN  ', name='Admin', password=PASSWORD,
                        is_platform_admin=True)
    assert admin.email == 'admin'
    assert User.get_by_email('Admin') is admin


def test_platform_admin_may_still_use_an_email(app):
    admin = User.create(email='root@example.com', name='Root',
                        password=PASSWORD, is_platform_admin=True)
    assert admin.email == 'root@example.com'


def test_the_seeded_admin_is_not_emailable(app):
    """Senders must not hand this identity to smtplib."""
    admin = User.create(email='admin', name='Admin', password=PASSWORD,
                        is_platform_admin=True)
    assert admin.is_emailable is False


def test_ordinary_users_are_emailable(app):
    assert make_user(email='real@example.com').is_emailable is True
