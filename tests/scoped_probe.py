"""A minimal OrgScoped model so tenant isolation is testable in phases that
predate real scoped business models. Harmless outside tests: only test
imports register it."""

from app.extensions import db
from app.models.base import BaseModel, OrgScoped


class ScopedProbe(OrgScoped, BaseModel):
    __tablename__ = 'scoped_probe'

    name = db.Column(db.String(100), nullable=False)
