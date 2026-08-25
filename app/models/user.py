"""User model. Users are global to the installation; memberships scope them
to organizations. Auth is email + password (auth: password) -- see
blueprint/patterns/core/auth.md."""

import re
from typing import Optional

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db
from app.platform.errors import ValidationError
from .base import BaseModel


class User(BaseModel, UserMixin):
    __tablename__ = 'user'

    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    is_platform_admin = db.Column(db.Boolean, default=False, nullable=False)

    memberships = db.relationship('Membership', back_populates='user',
                                  cascade='all, delete-orphan', lazy='select')

    MIN_PASSWORD_LENGTH = 8

    def validate(self):
        self.email = self.email.strip().lower() if self.email else ''
        self.name = self.name.strip() if self.name else ''

        if not self.email:
            raise ValidationError('Email is required')
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', self.email):
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

    def set_password(self, password: str) -> None:
        if not password or len(password) < self.MIN_PASSWORD_LENGTH:
            raise ValidationError(
                f'Password must be at least {self.MIN_PASSWORD_LENGTH} characters')
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    def get_initials(self) -> str:
        return (self.name or self.email)[0].upper()

    def org_memberships(self):
        """Memberships in active organizations, launcher-ordered."""
        from .membership import Membership
        from .organization import Organization
        return (Membership.query.join(Organization)
                .filter(Membership.user_id == self.id,
                        Organization.is_active.is_(True))
                .order_by(Organization.name).all())

    @classmethod
    def get_by_email(cls, email: str) -> Optional['User']:
        email = email.strip().lower() if email else ''
        return cls.query.filter_by(email=email).first()

    @classmethod
    def create(cls, email: str, name: str, password: str,
               is_platform_admin: bool = False) -> 'User':
        user = cls(email=email, name=name, is_platform_admin=is_platform_admin)
        user.set_password(password)
        return user.save()
