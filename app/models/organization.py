"""Organization: the tenant. Represents the website/community being operated."""

import re
from typing import TYPE_CHECKING, ClassVar

from app.extensions import db
from app.platform.errors import ValidationError

from .base import BaseModel, reject_control_characters, transaction
from .types import JSONColumn, TZDateTime

if TYPE_CHECKING:                       # circular at runtime, fine for hints
    from app.platform.content_types import ContentType


class Organization(BaseModel):
    __tablename__ = 'organization'

    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(63), unique=True, nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    theme = db.Column(db.String(50), nullable=False, default='origin')
    brand_primary = db.Column(db.String(7), nullable=True)      # #RRGGBB
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    archived_at = db.Column(TZDateTime, nullable=True)
    settings = db.Column(JSONColumn, nullable=False, default=dict)

    memberships = db.relationship('Membership', back_populates='organization',
                                  cascade='all, delete-orphan', lazy='select')

    RESERVED_SLUGS: ClassVar[set[str]] = {
        'www', 'api', 'admin', 'app', 'static', 'mail', 'smtp', 'status',
        'setup', 'auth', 'login', 'logout', 'launcher', 'health', 'files',
        'themes', 'assets', 'blog', 'docs', 'help', 'support',
    }

    def validate(self):
        self.name = (self.name or '').strip()
        self.slug = (self.slug or '').strip().lower()

        if not self.name:
            raise ValidationError('Organization name is required')
        if len(self.name) > 100:
            raise ValidationError('Organization name too long (max 100 chars)')
        reject_control_characters(self.name, 'Organization name')
        if not re.fullmatch(r'[a-z0-9]([a-z0-9-]{1,61}[a-z0-9])?', self.slug):
            raise ValidationError('Slug must be 3-63 chars: a-z, 0-9 and hyphens')
        if self.slug in self.RESERVED_SLUGS:
            raise ValidationError('That slug is reserved')
        # Unvalidated tenant input inside a <style> block is CSS injection;
        # Jinja's HTML autoescaping does not protect inside <style>.
        if self.brand_primary and not re.fullmatch(r'#[0-9a-fA-F]{6}',
                                                   self.brand_primary):
            raise ValidationError('Brand colour must be #RRGGBB')

        existing = Organization.query.filter_by(slug=self.slug).first()
        if existing and existing.id != self.id:
            raise ValidationError('That slug is already taken')

    @classmethod
    def get_by_slug(cls, slug: str):
        return cls.query.filter_by(slug=(slug or '').strip().lower()).first()

    @classmethod
    def provision(cls, name: str, slug: str, owner,
                  seed_defaults: bool = True,
                  vertical: str | None = None) -> 'Organization':
        """Create an organization with its owner membership and starter
        content (homepage, About, navigation, first post, General space),
        atomically."""
        from .membership import Membership
        org = cls(name=name, slug=slug)
        org.validate()
        with transaction():
            db.session.add(org)
            db.session.flush()                       # need org.id
            db.session.add(Membership(user_id=owner.id, org_id=org.id, role='owner'))
            if seed_defaults:
                from app.platform.defaults import seed_default_content
                seed_default_content(db.session, org, owner_id=owner.id,
                                     vertical=vertical)
        return org

    def suspend(self):
        self.is_active = False
        return self.save()

    def reactivate(self):
        self.is_active = True
        self.archived_at = None
        return self.save()

    def archive(self):
        from .base import utcnow
        self.is_active = False
        self.archived_at = utcnow()
        return self.save()

    def member_count(self) -> int:
        from .membership import Membership
        return Membership.query.filter_by(org_id=self.id).count()

    def setting(self, key: str, default=None):
        return (self.settings or {}).get(key, default)

    def teases_gated_content(self) -> bool:
        """Tease-don't-hide policy (Manage → Settings → Privacy): when on
        (the default), members-only items appear in public lists as locked
        titles and direct hits land on the gate page. When off, gated
        content is invisible to non-members — hidden from lists, and direct
        URLs behave as before the gate existed (login redirect / 404)."""
        return bool(self.setting('gated_teasers', True))

    # One map, not a setting per question. Before this there was a map for
    # who may read a section, and a plan to add one for whether a type is
    # published at all and another for whether it appears on the public
    # site: three keys on the same slug, each read somewhere different.
    TYPE_SETTINGS_KEY = 'content_types'

    def type_settings(self, slug: str) -> dict:
        """What this organization has said about one type. {} means nothing."""
        stored = self.setting(self.TYPE_SETTINGS_KEY) or {}
        return stored.get(slug) or {}

    def set_type_settings(self, slug: str, **values) -> None:
        """Record decisions about one type. None means "no opinion", which
        is not the same as False: it is what makes a setting inheritable."""
        store = dict(self.setting(self.TYPE_SETTINGS_KEY) or {})
        entry = dict(store.get(slug) or {})
        entry.update(values)
        store[slug] = {key: value for key, value in entry.items()
                       if value is not None}
        self.update_settings(**{self.TYPE_SETTINGS_KEY: store})

    def type_enabled(self, content_type: 'ContentType') -> bool:
        """Does this organization publish this kind of thing?

        Page and article are not a choice: without them there is nothing to
        publish at all. Everything else answers from what was chosen, and
        falls back to the type's own default when nothing was.
        """
        if content_type.essential:
            return True
        chosen = self.type_settings(content_type.slug).get('enabled')
        if chosen is not None:
            return bool(chosen)
        # A plugin's types arrive already chosen: installing the plugin is
        # the act of asking for them, and a second switch to find would be
        # a puzzle rather than a control.
        return True if content_type.plugin else content_type.enabled_by_default

    def type_presentation(self, content_type: 'ContentType') -> str:
        """Where this type renders: through the theme, or in the shell.

        The type declares what it is for, and an organization can disagree:
        a roster is site furniture for most communities and a members-only
        directory for some. Absent means the type's own answer, so this is
        an override rather than a copy made on the day it was saved.
        """
        chosen = self.type_settings(content_type.slug).get('presentation')
        return (chosen if chosen in ('site', 'community')
                else content_type.presentation)

    def type_visibility(self, slug: str) -> str:
        """Who may read this whole section. Absent means public, and items
        then decide for themselves."""
        from app.platform.authz import VISIBILITY_LEVELS
        chosen = self.type_settings(slug).get('visibility')
        return chosen if chosen in VISIBILITY_LEVELS else VISIBILITY_LEVELS[0]

    def type_teases(self, slug: str) -> bool:
        """Does a gated item of this type show as a locked title, or vanish?

        Who may read and how a refusal looks are two questions, deliberately
        kept apart: one is about access and the other about presentation,
        and folding them into a single three-valued setting leaves nowhere
        for membership tiers to add values later. None here inherits the
        organization's own answer.
        """
        chosen = self.type_settings(slug).get('tease')
        return self.teases_gated_content() if chosen is None else bool(chosen)

    def analytics_config(self) -> dict:
        """The org's tracker config (Manage → Settings → Analytics):
        {'provider': ..., <provider fields>}, or {} when analytics is off.
        Written only through clean_analytics_settings, so consumers
        (analytics_head, the CSP hook) can trust the values."""
        value = self.setting('analytics') or {}
        return value if isinstance(value, dict) and value.get('provider') else {}

    def update_settings(self, **updates) -> 'Organization':
        self.settings = {**(self.settings or {}), **updates}
        return self.save()

    @property
    def site_name(self) -> str:
        """What the public site calls this organization.

        Usually the same as `name`, and defaults to it. They differ when the
        community and the product it belongs to are named separately -- a
        community called "Acme Community" whose website is just "Acme". It
        lives on the organization rather than in a theme's copy, because it
        is the organization's identity: it must not disappear when someone
        tries a different theme.
        """
        return (self.setting('site_name') or '').strip() or self.name

    def _site_image(self, key: str):
        """An image this organization shows on its public pages.

        Public only, and re-checked here rather than trusted from settings:
        a file can be switched to members-only long after it was chosen, and
        a private file behind a public page is a broken image to every
        visitor, not a private one. A query, so the tenant filter runs.
        """
        from .upload import Upload
        upload_id = self.setting(key)
        if not upload_id:
            return None
        return Upload.query.filter_by(id=upload_id,
                                      visibility='public').first()

    def logo(self):
        return self._site_image('logo_upload_id')

    def favicon(self):
        return self._site_image('favicon_upload_id')

    def hero_image(self):
        """The organization's main picture, for a theme that wants one.

        Owned by the organization the way the logo and favicon are, so it
        survives a theme change: a photo of the business is not copy written
        for one theme's layout. Themes read it and are free to ignore it.
        """
        return self._site_image('hero_upload_id')
