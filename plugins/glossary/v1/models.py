"""Glossary tables, prefixed with slug AND major: glossary_v1_*."""

from app.extensions import db
from app.models.base import AuditMixin, BaseModel, OrgScoped, scoped_to_own_org
from app.platform.errors import ValidationError


class GlossaryTerm(OrgScoped, AuditMixin, BaseModel):
    __tablename__ = 'glossary_v1_term'

    term = db.Column(db.String(100), nullable=False)
    definition = db.Column(db.Text, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('org_id', 'term', name='uq_glossary_v1_term_org'),
    )

    def validate(self):
        self.term = (self.term or '').strip()
        if not self.term:
            raise ValidationError('Term is required')
        if not (self.definition or '').strip():
            raise ValidationError('Definition is required')
        # Pinned to this row's organization: outside a request the filter
        # does not run, and an unpinned lookup reports a clash with a term
        # another community happens to define.
        existing = scoped_to_own_org(
            GlossaryTerm.query.filter_by(term=self.term), self).first()
        if existing and existing.id != self.id:
            raise ValidationError('That term is already defined')
