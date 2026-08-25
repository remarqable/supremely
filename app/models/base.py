"""Base model, tenancy mixin, and audit mixin."""

from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy.orm import declared_attr

from app.extensions import db
from app.models.types import BigIntPK, BigIntFK


def utcnow() -> datetime:
    """Timezone-aware UTC now. Never use datetime.utcnow() -- it is deprecated
    in Python 3.12+ and returns a naive datetime that claims to be UTC."""
    return datetime.now(timezone.utc)


@contextmanager
def transaction():
    """Transaction context manager with auto-rollback."""
    try:
        yield db.session
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


class BaseModel(db.Model):
    """Abstract base model with timestamps."""

    __abstract__ = True

    id = db.Column(BigIntPK, primary_key=True, autoincrement=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False,
                           default=utcnow, onupdate=utcnow)

    def save(self):
        """Validate and persist. Commits immediately -- wrap multi-step
        operations in transaction() so they stay atomic."""
        if hasattr(self, 'validate'):
            self.validate()
        db.session.add(self)
        db.session.commit()
        return self

    def delete(self):
        db.session.delete(self)
        db.session.commit()

    @classmethod
    def get_by_id(cls, id: int):
        return db.session.get(cls, id)


class OrgScoped:
    """Mixin: rows belong to exactly one organization.

    Models using this are AUTOMATICALLY filtered by the current tenant.
    See app/platform/tenant.py.
    """

    @declared_attr
    def org_id(cls):
        return db.Column(BigIntFK,
                         db.ForeignKey('organization.id', ondelete='CASCADE'),
                         nullable=False, index=True)


class AuditMixin:
    """Track who created and last updated a record (audit_logging: true)."""

    @declared_attr
    def created_by_id(cls):
        return db.Column(BigIntFK, db.ForeignKey('user.id', ondelete='SET NULL'),
                         nullable=True)

    @declared_attr
    def updated_by_id(cls):
        return db.Column(BigIntFK, db.ForeignKey('user.id', ondelete='SET NULL'),
                         nullable=True)

    @declared_attr
    def created_by(cls):
        return db.relationship('User', foreign_keys=[cls.created_by_id], lazy='select')

    @declared_attr
    def updated_by(cls):
        return db.relationship('User', foreign_keys=[cls.updated_by_id], lazy='select')

    def stamp_audit(self):
        """Set audit fields from the current user, if any."""
        from flask_login import current_user
        if current_user and getattr(current_user, 'is_authenticated', False):
            if self.created_by_id is None and self.id is None:
                self.created_by_id = current_user.id
            self.updated_by_id = current_user.id
        return self
