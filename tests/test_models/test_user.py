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
