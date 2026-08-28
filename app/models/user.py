"""User model. Users are global to the installation; memberships scope them
to organizations. Auth is email + password (auth: password) -- see
blueprint/patterns/core/auth.md."""

import hashlib
import hmac
import re
import secrets
from typing import Optional

from flask import current_app
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.platform.errors import ValidationError

from .base import BaseModel
from .common_passwords import COMMON_PASSWORDS


def _is_production() -> bool:
    """True on a real installation. Read here rather than trusted from the
    caller, so an exemption meant for development cannot be carried into
    one by a later caller passing the wrong argument."""
    return current_app.config.get('APP_ENV') == 'production'


_ABSENT_USER_HASH = None


def _absent_user_hash() -> str:
    """A throwaway digest, built once and kept for the life of the process.

    Not a constant in the source: it would then be identical everywhere the
    project is installed, and a digest anyone can reproduce is one anyone
    can time against.

    Built on first use rather than at import, which keeps a hash off the
    start of every command and test run. The cost is that the first miss in
    a process is twice the usual, once, for any address.
    """
    global _ABSENT_USER_HASH
    if _ABSENT_USER_HASH is None:
        _ABSENT_USER_HASH = generate_password_hash(secrets.token_urlsafe(32))
    return _ABSENT_USER_HASH


def verify_credentials(user: Optional['User'], password: str) -> bool:
    """Whether this password signs this user in, at a fixed cost.

    Stopping at `user is None` let the clock answer the question the
    wording carefully refuses to. A missing address returned after one
    indexed lookup, a real one after a full hash comparison, and the two
    differ by more than a hundredfold. Same message, same status, same
    length, and the timing gave it away regardless.

    So the comparison always runs, against a throwaway digest when there is
    no account, and being suspended is judged after it rather than before.
    """
    if user is None:
        check_password_hash(_absent_user_hash(), password)
        return False
    matched = user.check_password(password)
    return matched and user.is_active


class User(BaseModel, UserMixin):
    __tablename__ = 'user'

    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    bio = db.Column(db.Text, nullable=True)
    avatar_key = db.Column(db.String(255), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    is_platform_admin = db.Column(db.Boolean, default=False, nullable=False)

    memberships = db.relationship('Membership', back_populates='user',
                                  cascade='all, delete-orphan', lazy='select')

    MIN_PASSWORD_LENGTH = 8

    EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

    # The one account the first-boot wizard creates, so an installation is
    # usable before any SMTP exists. It is the single value allowed to sit in
    # the identity column without being an address; everyone else must be
    # reachable, because invitations, newsletters and notifications are all
    # delivered by email.
    #
    # Keyed on the VALUE, not on is_platform_admin: tying it to the privilege
    # would let any admin be created with a bare username, and would make this
    # very account fail validation the moment it is demoted.
    INSTALL_ADMIN_USERNAME = 'admin'

    def validate(self):
        self.email = self.email.strip().lower() if self.email else ''
        self.name = self.name.strip() if self.name else ''

        if not self.email:
            raise ValidationError('Email is required')
        if not (self.email == self.INSTALL_ADMIN_USERNAME
                or self.EMAIL_RE.match(self.email)):
            raise ValidationError('Invalid email format')
        if len(self.email) > 255:
            raise ValidationError('Email too long')
        if not self.name:
            raise ValidationError('Name is required')
        if len(self.name) > 100:
            raise ValidationError('Name too long (max 100 chars)')

        # Friendly duplicate check; the unique constraint is the real guarantee.
        existing = User.query.filter_by(email=self.email).first()
        if existing and existing.id != self.id:
            raise ValidationError('Email already registered')

    @property
    def is_emailable(self) -> bool:
        """Whether this account has an address anything can be sent to.

        False for the installation administrator, whose identity is a
        username. Senders must check this rather than assume the identity
        column holds a deliverable address.
        """
        return bool(self.email) and bool(self.EMAIL_RE.match(self.email))

    def set_password(self, password: str, allow_common: bool = False) -> None:
        """Set the password, refusing the ones guessed first.

        allow_common exists for the development seed, whose whole purpose
        is a login someone can type from memory. It is ignored when APP_ENV
        says production, so there is nothing to gain by reaching for it.

        That is the same value the seed itself checks, so the two are one
        gate written twice rather than two independent ones. An install
        that leaves APP_ENV unset has a debug console and cleartext session
        cookies before it has a weak seed password, so this is not the
        thing that would go wrong first.
        """
        if not password or len(password) < self.MIN_PASSWORD_LENGTH:
            raise ValidationError(
                f'Password must be at least {self.MIN_PASSWORD_LENGTH} characters')
        # A length rule alone still admits the handful of strings a spraying
        # attack tries first, and those are exactly what it is aimed at.
        if password.lower() in COMMON_PASSWORDS and not (
                allow_common and not _is_production()):
            raise ValidationError(
                'That password is one of the most commonly used. '
                'Please choose another.')
        self.password_hash = generate_password_hash(password)

    def session_auth_stamp(self) -> str:
        """Digest of the credentials a signed-in session depends on.

        Flask-Login's remember cookie is `id|hmac(SECRET_KEY, id)` with no
        server-side record, so a stolen copy stays valid forever: logging
        out or changing the password does not revoke it. Carrying this
        stamp in the session id means any change to the password or the
        active flag invalidates every session and remember cookie already
        issued, without a new column to migrate. Same shape as Django's
        get_session_auth_hash().

        Derived from the password alone. is_active is deliberately not in
        the material: UserMixin.is_authenticated already returns it, so
        deactivation locks out on the next request either way -- and a
        boolean is not monotonic, so including it would let a later
        reactivation regenerate the same stamp and hand back a cookie
        issued before the account was suspended. Containment is deactivate
        plus a password reset; only the reset revokes what is already out.
        """
        material = str(self.password_hash).encode()
        secret = current_app.config['SECRET_KEY']
        if isinstance(secret, str):
            secret = secret.encode()
        return hmac.new(secret, material, hashlib.sha256).hexdigest()[:32]

    def get_id(self) -> str:
        """Overrides UserMixin's bare str(id). See session_auth_stamp.

        UserMixin.__eq__ compares get_id(), so User equality now costs an
        HMAC and needs an app context. Compare .id instead.
        """
        return f'{self.id}:{self.session_auth_stamp()}'

    def check_password(self, password: str) -> bool:
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    def get_initials(self) -> str:
        return (self.name or self.email)[0].upper()

    def org_memberships(self):
        """Active memberships in active organizations, launcher-ordered."""
        from .membership import Membership
        from .organization import Organization
        return (Membership.query.join(Organization)
                .filter(Membership.user_id == self.id,
                        Membership.is_active.is_(True),
                        Organization.is_active.is_(True))
                .order_by(Organization.name).all())

    @property
    def avatar_url(self):
        return f'/avatars/{self.id}' if self.avatar_key else None

    @classmethod
    def get_by_email(cls, email: str) -> Optional['User']:
        email = email.strip().lower() if email else ''
        return cls.query.filter_by(email=email).first()

    @classmethod
    def create(cls, email: str, name: str, password: str,
               is_platform_admin: bool = False,
               allow_common: bool = False) -> 'User':
        user = cls(email=email, name=name, is_platform_admin=is_platform_admin)
        user.set_password(password, allow_common=allow_common)
        return user.save()
