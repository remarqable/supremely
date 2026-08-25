"""Which plugin, at which major version, each tenant runs.

Deliberately NOT OrgScoped: the automatic filter reads g.org, and this table
is consulted while establishing what the tenant may access. Keep the filter
separate from the thing that gates it. See blueprint/patterns/plugins.md.
"""

from app.extensions import db
from .base import BaseModel, utcnow
from .types import BigIntFK, JSONColumn, TZDateTime


class OrgPlugin(BaseModel):
    __tablename__ = 'org_plugin'

    org_id = db.Column(BigIntFK,
                       db.ForeignKey('organization.id', ondelete='CASCADE'),
                       nullable=False, index=True)
    plugin_slug = db.Column(db.String(50), nullable=False)
    version = db.Column(db.String(10), nullable=False)      # major only, e.g. '1'
    is_enabled = db.Column(db.Boolean, nullable=False, default=True)
    settings = db.Column(JSONColumn, nullable=False, default=dict)
    installed_at = db.Column(TZDateTime, nullable=False, default=utcnow)
    upgraded_at = db.Column(TZDateTime, nullable=True)

    __table_args__ = (
        # One major per tenant per plugin, enforced by the database.
        db.UniqueConstraint('org_id', 'plugin_slug', name='uq_org_plugin'),
    )
