from app.platform.content_types import ContentType, FieldSpec
from app.platform.plugins import Plugin
from plugins.glossary.__manifest__ import manifest


class GlossaryPlugin(Plugin):
    manifest = manifest

    def blueprint(self):
        from .controllers import bp
        return bp

    def content_types(self):
        # A structured Content Type contributed by a plugin: publish
        # dictionary-style definitions at /definitions alongside the
        # interactive glossary page.
        return [ContentType(
            slug='definition', singular='Definition', plural='Definitions',
            description='A glossary definition, publishable as content.',
            base='/definitions',
            fields=(
                FieldSpec(key='term', type='string', label='Term',
                          required=True),
                FieldSpec(key='pronunciation', type='string',
                          label='Pronunciation'),
            ),
            plugin='glossary',
        )]

    def on_install(self, org_id: int) -> None:
        """Seed a first term. Idempotent -- rows, never DDL."""
        from .models import GlossaryTerm
        if not GlossaryTerm.query.filter_by(org_id=org_id).first():
            from app.extensions import db
            db.session.add(GlossaryTerm(
                org_id=org_id, term='Supremely',
                definition='The platform this glossary runs on.'))

    def on_uninstall(self, org_id: int) -> None:
        """Disable only. Data survives so reinstalling restores it."""
