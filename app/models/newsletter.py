"""Newsletter: subscribers and deliveries.

A Subscriber is distinct from Membership: just an email with a status, no
login required. Delivery state is tracked per recipient so sending is
idempotent and resumable (see blueprint/patterns/jobs.md).
"""

import re
import secrets

from app.extensions import db
from app.platform.errors import ValidationError

from .base import AuditMixin, BaseModel, OrgScoped, utcnow
from .types import BigIntFK, JSONColumn, TZDateTime

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


class Subscriber(OrgScoped, BaseModel):
    __tablename__ = 'subscriber'

    email = db.Column(db.String(255), nullable=False)
    # pending: awaiting double opt-in (only used when email is configured)
    status = db.Column(db.String(15), nullable=False, default='subscribed')
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    preferences = db.Column(JSONColumn, nullable=False, default=dict)
    confirmed_at = db.Column(TZDateTime, nullable=True)
    unsubscribed_at = db.Column(TZDateTime, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('org_id', 'email', name='uq_subscriber_org_email'),
    )

    STATUSES = ('pending', 'subscribed', 'unsubscribed')

    def validate(self):
        self.email = (self.email or '').strip().lower()
        if not EMAIL_RE.match(self.email):
            raise ValidationError('Invalid email address')
        # The column is 255; SQLite stores a longer one anyway and
        # PostgreSQL raises, so the check has to live here.
        if len(self.email) > 255:
            raise ValidationError('Email too long (max 255 chars)')
        if self.status not in self.STATUSES:
            raise ValidationError('Invalid status')

    @classmethod
    def subscribe(cls, email: str, org_id: int,
                  require_confirmation: bool) -> 'Subscriber':
        """Subscribe (or re-subscribe) an email. Deduplicates per org."""
        email = (email or '').strip().lower()
        # Pinned to the organization being subscribed to. The session
        # filter does this inside a request, and outside one this returned
        # another tenant's row, re-subscribed it, and handed it back.
        existing = cls.query.filter_by(email=email, org_id=org_id).first()
        if existing:
            if existing.status != 'subscribed':
                existing.status = ('pending' if require_confirmation
                                   else 'subscribed')
                existing.unsubscribed_at = None
                existing.save()
            return existing
        subscriber = cls(
            email=email, org_id=org_id,
            status='pending' if require_confirmation else 'subscribed',
            token=secrets.token_urlsafe(32),
        )
        return subscriber.save()

    @classmethod
    def by_token(cls, token: str):
        return cls.query.filter_by(token=token).first()

    def confirm(self):
        if self.status == 'pending':
            self.status = 'subscribed'
            self.confirmed_at = utcnow()
            self.save()
        return self

    def unsubscribe(self):
        self.status = 'unsubscribed'
        self.unsubscribed_at = utcnow()
        return self.save()

    @classmethod
    def audience(cls, org_id: int):
        """Everyone a send to this organization would reach.

        The organization is required rather than assumed. Inside a request
        the session filter would supply it, but this is the method whose
        whole failure mode is gathering every tenant list into one send, so
        it should not be possible to ask the question without saying whose
        audience is meant.
        """
        return cls.query.filter_by(status='subscribed', org_id=org_id)


class Delivery(OrgScoped, AuditMixin, BaseModel):
    """One email send of one published Content item to the audience."""
    __tablename__ = 'newsletter_delivery'

    content_id = db.Column(BigIntFK, db.ForeignKey('content.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    status = db.Column(db.String(10), nullable=False, default='pending')
    # pending -> sending -> done | failed
    recipients_total = db.Column(db.Integer, nullable=False, default=0)
    sent_count = db.Column(db.Integer, nullable=False, default=0)
    failed_count = db.Column(db.Integer, nullable=False, default=0)
    finished_at = db.Column(TZDateTime, nullable=True)

    content = db.relationship('Content', lazy='select')

    @classmethod
    def create_for_content(cls, content) -> 'Delivery':
        """Snapshot the audience and create per-recipient rows upfront, so
        the send job is idempotent: it only mails rows not yet marked."""
        subscribers = Subscriber.audience(content.org_id).all()
        delivery = cls(content_id=content.id, org_id=content.org_id,
                       recipients_total=len(subscribers))
        delivery.stamp_audit()
        delivery.save()
        for subscriber in subscribers:
            db.session.add(DeliveryRecipient(
                delivery_id=delivery.id, subscriber_id=subscriber.id,
                org_id=content.org_id))
        db.session.commit()
        return delivery


class DeliveryRecipient(OrgScoped, BaseModel):
    __tablename__ = 'newsletter_delivery_recipient'

    delivery_id = db.Column(BigIntFK,
                            db.ForeignKey('newsletter_delivery.id',
                                          ondelete='CASCADE'),
                            nullable=False, index=True)
    subscriber_id = db.Column(BigIntFK,
                              db.ForeignKey('subscriber.id', ondelete='CASCADE'),
                              nullable=False)
    sent_at = db.Column(TZDateTime, nullable=True)
    error = db.Column(db.String(500), nullable=True)

    subscriber = db.relationship('Subscriber', lazy='select')

    __table_args__ = (
        db.UniqueConstraint('delivery_id', 'subscriber_id',
                            name='uq_delivery_recipient'),
    )
