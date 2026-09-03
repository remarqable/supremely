"""Base model, tenancy mixin, and audit mixin."""

import re
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy.orm import declared_attr

from app.extensions import db
from app.models.types import BigIntFK, BigIntPK, TZDateTime
from app.platform.errors import ValidationError


def utcnow() -> datetime:
    """Timezone-aware UTC now. Never use datetime.utcnow() -- it is deprecated
    in Python 3.12+ and returns a naive datetime that claims to be UTC."""
    return datetime.now(UTC)


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
    created_at = db.Column(TZDateTime, nullable=False, default=utcnow)
    updated_at = db.Column(TZDateTime, nullable=False,
                           default=utcnow, onupdate=utcnow)

    def save(self):
        """Validate and persist. Commits immediately -- wrap multi-step
        operations in transaction() so they stay atomic."""
        if hasattr(self, 'validate'):
            self.validate()
        db.session.add(self)
        db.session.commit()
        return self

    def save_flag(self):
        """Persist without validating.

        For a change that touches only a flag on a row someone else wrote,
        where re-running validate() would judge content this save is not
        editing. Moderation is the case: a body too long to save is
        exactly the body that needs hiding.
        """
        db.session.add(self)
        db.session.commit()
        return self

    def delete(self):
        db.session.delete(self)
        db.session.commit()

    @classmethod
    def get_by_id(cls, id: int):
        return db.session.get(cls, id)


CONTROL_CHARS = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')


# How long a slug written from a title may be. Shorter than the column,
# which allows 200, because a derived slug is nobody's decision: a whole
# sentence of title makes an address that cannot be read out, pasted into a
# message or printed. A slug somebody types is their own business and keeps
# the column's limit.
DERIVED_SLUG_MAX = 60


def slugify(text: str, fallback: str = '',
            max_length: int = DERIVED_SLUG_MAX) -> str:
    """A URL-safe slug from human text.

    Accents are folded rather than dropped, so "Café notes" becomes
    "cafe-notes" instead of "caf-notes". Anything else that is not a letter
    or a digit becomes a hyphen, and runs collapse. Text with nothing usable
    in it (a title written entirely in a non-Latin script, say) returns the
    fallback, which is empty by default: better to ask an author for a slug
    than to invent one they cannot read.

    Long text is cut back to the last whole word rather than mid-word, so a
    long title ends at "...-make-a-url" instead of "...-make-a-ur".
    """
    import unicodedata
    folded = unicodedata.normalize('NFKD', text or '')
    ascii_only = folded.encode('ascii', 'ignore').decode('ascii')
    slug = re.sub(r'[^a-z0-9]+', '-', ascii_only.lower()).strip('-')
    if len(slug) > max_length:
        cut = slug[:max_length]
        # Back to the last hyphen, unless that would leave almost nothing
        # (one very long word), in which case the hard cut stands.
        boundary = cut.rfind('-')
        slug = cut[:boundary] if boundary > max_length // 2 else cut
    return slug.strip('-') or fallback


def reject_control_characters(value: str, label: str) -> None:
    """Refuse text that cannot travel in a header.

    Titles and organization names are interpolated into email subjects.
    The standard library refuses a newline there, so the whole message
    raises rather than one recipient failing, and the job burns its
    retries. Tabs are allowed; the rest are not.
    """
    if CONTROL_CHARS.search(value) or '\n' in value or '\r' in value:
        raise ValidationError(f'{label} cannot contain line breaks or '
                              'control characters')


LIKE_ESCAPE = '\\'


def like_contains(column, term: str):
    """A case-insensitive 'contains' that treats the term as literal text.

    Percent and underscore are wildcards in LIKE, so an unescaped search
    for '%' matched every row and scanned the whole table. The value was
    always bound as a parameter, so this is about the search meaning what
    the visitor typed, not about injection.
    """
    return column.ilike(f'%{escape_like(term)}%', escape=LIKE_ESCAPE)


def escape_like(term: str) -> str:
    """Neutralise LIKE's own wildcards inside a search term."""
    for char in (LIKE_ESCAPE, '%', '_'):
        term = term.replace(char, LIKE_ESCAPE + char)
    return term


def scoped_to_own_org(query, row):
    """Pin a uniqueness lookup to this row's organization.

    The session filter does it inside a request, but seeding, the CLI
    and background jobs run without one, where an unpinned lookup spans
    every tenant: a false collision, and a way to learn that another
    organization uses a name.
    """
    return query.filter_by(org_id=row.org_id) if row.org_id else query


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


class MarkdownBody:
    """Mixin: renders this row's `body` column as sanitized markdown."""

    @property
    def html(self) -> str:
        from app.platform.content import render_markdown
        return render_markdown(self.body)


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

    @property
    def author(self):
        return self.created_by

    def stamp_audit(self):
        """Set audit fields from the current user, if any."""
        from flask_login import current_user
        if current_user and getattr(current_user, 'is_authenticated', False):
            if self.created_by_id is None and self.id is None:
                self.created_by_id = current_user.id
            self.updated_by_id = current_user.id
        return self
