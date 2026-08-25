"""Glossary tables, prefixed with slug AND major: glossary_v1_*."""

from app.extensions import db
from app.models.base import AuditMixin, BaseModel, OrgScoped
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
        existing = GlossaryTerm.query.filter_by(term=self.term).first()
        if existing and existing.id != self.id:
            raise ValidationError('That term is already defined')
