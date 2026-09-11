"""One-time password reset links.

A reset link is the strongest thing this application hands out: it buys
somebody's whole account, not entry to one organization. These are the rules
that keep it from being more than an organizer is owed.
"""

from datetime import timedelta

import pytest
from flask import g

from app.extensions import db
from app.models import Membership, PasswordReset, User
from app.models.base import utcnow
from app.models.password_reset import _hash_token, can_be_reset_by_org
from app.platform.errors import ValidationError
from tests.conftest import make_user


def _member_of(org, email='ada@example.com', role='member') -> User:
    user = make_user(email=email, name='Ada')
    Membership.add(user.id, org.id, role=role)
    return user


def _acting_as(org, owner) -> None:
    """Sign the organization's owner in inside a request context.

    Every link records who issued it, and one whose issuer no longer holds
    members.manage here is refused -- including a link with no issuer at
    all, which is what these tests were making when nobody was signed in.
    """
    from flask_login import login_user
    g.org = org
    login_user(owner)


def test_the_token_is_never_stored(app, acme, user):
    """The reason invitations hash theirs, only more so: this table must
    never be a list of ways into people's accounts."""
    with app.test_request_context():
        _acting_as(acme, user)
        user = _member_of(acme)
        reset, token = PasswordReset.issue(user, acme.id)

        assert token not in reset.token_hash
        assert reset.token_hash == _hash_token(token)
        rows = db.session.execute(db.text(
            'SELECT * FROM password_reset')).mappings().all()
        assert all(token not in str(value)
                   for row in rows for value in row.values())


def test_a_link_works_once(app, acme, user):
    with app.test_request_context():
        _acting_as(acme, user)
        user = _member_of(acme)
        _, token = PasswordReset.issue(user, acme.id)

        PasswordReset.find_valid(token).redeem('a-fresh-password-1')
        assert PasswordReset.find_valid(token) is None
        assert user.check_password('a-fresh-password-1')


def test_an_expired_link_is_no_link(app, acme, user):
    with app.test_request_context():
        _acting_as(acme, user)
        user = _member_of(acme)
        reset, token = PasswordReset.issue(user, acme.id)
        reset.expires_at = utcnow() - timedelta(minutes=1)
        reset.save()

        assert PasswordReset.find_valid(token) is None


def test_issuing_again_kills_the_first_link(app, acme, user):
    """Two live links is two chances for the wrong person to hold one, and
    an admin who reissues is usually reissuing because the first went
    astray."""
    with app.test_request_context():
        _acting_as(acme, user)
        user = _member_of(acme)
        _, first = PasswordReset.issue(user, acme.id)
        _, second = PasswordReset.issue(user, acme.id)

        assert PasswordReset.find_valid(first) is None
        assert PasswordReset.find_valid(second) is not None


def test_a_refused_password_leaves_the_link_usable(app, acme, user):
    """Somebody locked out who picks a password that is too short should
    get another go, not a spent link and a call to the organizer."""
    with app.test_request_context():
        _acting_as(acme, user)
        user = _member_of(acme)
        _, token = PasswordReset.issue(user, acme.id)

        with pytest.raises(ValidationError):
            PasswordReset.find_valid(token).redeem('short')
        db.session.rollback()
        assert PasswordReset.find_valid(token) is not None


def test_a_platform_admin_cannot_be_reset_by_an_organization(app, acme, user):
    """They run the whole installation, every other organization on it
    included. An organization's admin resetting that password would be
    taking the installation."""
    with app.test_request_context():
        _acting_as(acme, user)
        root = make_user(email='root@example.com', name='Root',
                         is_platform_admin=True)
        Membership.add(root.id, acme.id, role='member')

        assert can_be_reset_by_org(root, acme.id) is False
        with pytest.raises(ValidationError):
            PasswordReset.issue(root, acme.id)


def test_somebody_who_belongs_elsewhere_cannot_be_reset(app, acme, globex, user):
    """A plain member here may be the owner of the organization next door,
    and a reset issued here would be a way to take that one."""
    with app.test_request_context():
        _acting_as(acme, user)
        user = _member_of(acme)
        Membership.add(user.id, globex.id, role='owner')

        assert can_be_reset_by_org(user, acme.id) is False
        with pytest.raises(ValidationError):
            PasswordReset.issue(user, acme.id)


def test_belonging_elsewhere_kills_a_link_already_issued(app, acme, globex, user):
    """The rule is asked again at redemption. A day passes between issuing
    a link and using it, and a membership can arrive in that day."""
    with app.test_request_context():
        _acting_as(acme, user)
        user = _member_of(acme)
        _, token = PasswordReset.issue(user, acme.id)
        assert PasswordReset.find_valid(token) is not None

        Membership.add(user.id, globex.id, role='owner')
        assert PasswordReset.find_valid(token) is None


def test_a_link_from_one_organization_is_not_valid_at_another(app, acme, globex, user):
    """Same account either side, but the link was issued by one of them."""
    with app.test_request_context():
        _acting_as(acme, user)
        user = _member_of(acme)
        _, token = PasswordReset.issue(user, acme.id)

    with app.test_request_context():
        _acting_as(globex, user)
        assert PasswordReset.find_valid(token) is None


def test_redeeming_ends_every_session_the_account_had(app, acme, user):
    """The containment half: if the reason for the reset is that somebody
    else had got in, the reset has to put them out."""
    with app.test_request_context():
        _acting_as(acme, user)
        user = _member_of(acme)
        before = user.get_id()
        _, token = PasswordReset.issue(user, acme.id)

        PasswordReset.find_valid(token).redeem('a-fresh-password-1')
        assert user.get_id() != before


def test_an_empty_or_unknown_token_finds_nothing(app, acme, user):
    with app.test_request_context():
        _acting_as(acme, user)
        assert PasswordReset.find_valid('') is None
        assert PasswordReset.find_valid('not-a-real-token') is None


def test_a_deactivated_account_is_not_resettable(app, acme, user):
    """A reset link is a way in. Letting one be issued for an account
    somebody switched off would make this the way to switch it back on."""
    with app.test_request_context():
        _acting_as(acme, user)
        user = _member_of(acme)
        user.is_active = False
        user.save()

        assert can_be_reset_by_org(user, acme.id) is False


def test_the_rule_carries_no_wording(app, acme, user):
    """Models here raise plain English and never reach for the request;
    t() reads the language off it. The refusal a person reads is the
    controller's, so this answers yes or no and nothing else."""
    with app.test_request_context():
        _acting_as(acme, user)
        assert can_be_reset_by_org(None, acme.id) is False
        assert can_be_reset_by_org(_member_of(acme), acme.id) is True
