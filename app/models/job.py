"""Background job queue, DB-backed. See blueprint/patterns/jobs.md.

Deliberately NOT OrgScoped: the worker has no request context and crosses
tenants by design. org_id is plain data here.
"""

from app.extensions import db
from .base import BaseModel, utcnow
from .types import BigIntFK, JSONColumn


class Job(BaseModel):
    __tablename__ = 'job'

    name = db.Column(db.String(100), nullable=False, index=True)
    payload = db.Column(JSONColumn, nullable=False, default=dict)
    org_id = db.Column(BigIntFK,
                       db.ForeignKey('organization.id', ondelete='CASCADE'),
                       nullable=True, index=True)

    # pending -> running -> done | failed (terminal after max attempts)
    status = db.Column(db.String(10), nullable=False, default='pending', index=True)
    run_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    max_attempts = db.Column(db.Integer, nullable=False, default=3)
    last_error = db.Column(db.Text, nullable=True)
    locked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)

    __table_args__ = (
        db.Index('ix_job_claim', 'status', 'run_at'),
    )
