"""Background job queue, DB-backed. See blueprint/patterns/jobs.md.

Deliberately NOT OrgScoped: the worker has no request context and crosses
tenants by design. org_id is plain data here.
"""

from app.extensions import db

from .base import BaseModel, utcnow
from .types import BigIntFK, JSONColumn, TZDateTime


class Job(BaseModel):
    __tablename__ = 'job'

    name = db.Column(db.String(100), nullable=False, index=True)
    payload = db.Column(JSONColumn, nullable=False, default=dict)
    org_id = db.Column(BigIntFK,
                       db.ForeignKey('organization.id', ondelete='CASCADE'),
                       nullable=True, index=True)

    # pending -> running -> done | failed (terminal after max attempts)
    status = db.Column(db.String(10), nullable=False, default='pending', index=True)
    run_at = db.Column(TZDateTime, nullable=False, default=utcnow)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    max_attempts = db.Column(db.Integer, nullable=False, default=3)
    last_error = db.Column(db.Text, nullable=True)
    locked_at = db.Column(TZDateTime, nullable=True)
    finished_at = db.Column(TZDateTime, nullable=True)

    # Named only by the admin list. Left lazy so the worker's per-job
    # session.get() does not pay for a join it never reads.
    organization = db.relationship('Organization', lazy='select')

    __table_args__ = (
        db.Index('ix_job_claim', 'status', 'run_at'),
    )

    @classmethod
    def counts_by_status(cls) -> dict:
        """How many jobs sit in each state, for the admin surfaces."""
        import sqlalchemy as sa
        return dict(db.session.execute(
            sa.select(cls.status, sa.func.count()).group_by(cls.status)).all())

    @classmethod
    def failed(cls, limit: int = 100):
        """Terminally failed jobs, most recent first. Not org scoped: the
        installation operator diagnoses across every tenant."""
        from sqlalchemy.orm import joinedload
        return (cls.query.filter_by(status='failed')
                .options(joinedload(cls.organization))
                .order_by(cls.finished_at.desc().nullslast(), cls.id.desc())
                .limit(limit).all())

    def retry(self) -> 'Job':
        """Put a failed job back in the queue with a clean slate.

        Attempts reset, so it gets its full allowance again, and the previous
        error is cleared rather than left on a row that is pending: a fresh
        failure records a fresh reason.
        """
        if self.status != 'failed':
            from app.platform.errors import ValidationError
            raise ValidationError('Only a failed job can be retried')
        self.status = 'pending'
        self.attempts = 0
        self.last_error = None
        self.finished_at = None
        self.locked_at = None
        self.run_at = utcnow()
        return self.save()
