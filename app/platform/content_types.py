"""Content Types: the universal publishing architecture.

Everything an organization publishes is a Content row with a `type`. Content
Types are defined in code -- by Supremely core or by plugins -- and validated
at registration. Each type declares its own labels and public URL base, so a
recipe site shows "Recipes" at /recipes while a blog shows "Blog" at /blog;
there is no generic "posts" surface. The Post subsystem never changes when a
vertical is added -- you register one ContentType.

("Content" here is deliberately distinct from a discussion Post; see
app/models/discussion.py.)
"""

import re
from dataclasses import dataclass

from app.platform.errors import ValidationError

FIELD_TYPES = ('string', 'text', 'url', 'number', 'boolean', 'date')

_SLUG_RE = re.compile(r'[a-z][a-z0-9_]{0,49}')
_BASE_RE = re.compile(r'/[a-z0-9]([a-z0-9-]{0,48})?')


@dataclass(frozen=True)
class FieldSpec:
    key: str
    type: str = 'string'
    label: str = ''
    required: bool = False
    help: str = ''

    def clean(self, raw):
        """Validate and coerce one submitted value. Returns the stored value."""
        value = (raw or '').strip() if isinstance(raw, str) else raw

        if value in (None, '', False) and self.type != 'boolean':
            if self.required:
                raise ValidationError(f'{self.label or self.key} is required')
            return None

        if self.type in ('string', 'text'):
            return str(value)
        if self.type == 'url':
            if not str(value).startswith(('http://', 'https://')):
                raise ValidationError(
                    f'{self.label or self.key} must be an http(s) URL')
            return str(value)
        if self.type == 'number':
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise ValidationError(f'{self.label or self.key} must be a number')
            return int(number) if number == int(number) else number
        if self.type == 'boolean':
            if isinstance(value, bool):
                return value
            return str(value).lower() in ('true', '1', 'yes', 'on')
        if self.type == 'date':
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(value)):
                raise ValidationError(
                    f'{self.label or self.key} must be YYYY-MM-DD')
            return str(value)
        raise ValidationError(f'Unknown field type: {self.type}')


@dataclass(frozen=True)
class ContentType:
    slug: str
    singular: str                   # "Recipe"
    plural: str                     # "Recipes"
    description: str = ''
    fields: tuple = ()
    # WordPress-style template hierarchy: single.html / single-{type}.html,
    # archive.html / archive-{type}.html.
    template: str = 'single'
    list_template: str = 'archive'
    plugin: str | None = None       # owning plugin slug, if any
    # Feed types: standalone dated entries with an archive at `base`
    # (e.g. /blog, /recipes). The `page` type sets has_archive=False and is
    # served at /<slug> like a standalone page.
    has_archive: bool = True
    base: str = ''                  # public URL base for feed types, e.g. /blog
    show_in_nav: bool = False       # seed a nav entry for this type

    @property
    def is_page(self) -> bool:
        return not self.has_archive

    def validate_definition(self):
        if not _SLUG_RE.fullmatch(self.slug):
            raise ValueError(f'Invalid content type slug: {self.slug!r}')
        if not (self.singular and self.plural):
            raise ValueError('Content type needs singular and plural labels')
        if self.has_archive and not _BASE_RE.fullmatch(self.base or ''):
            raise ValueError(
                f'Feed content type {self.slug} needs a URL base like /blog')
        seen = set()
        for spec in self.fields:
            if not isinstance(spec, FieldSpec):
                raise ValueError('fields must be FieldSpec instances')
            if spec.type not in FIELD_TYPES:
                raise ValueError(f'Unknown field type: {spec.type}')
            if not _SLUG_RE.fullmatch(spec.key):
                raise ValueError(f'Invalid field key: {spec.key!r}')
            if spec.key in seen:
                raise ValueError(f'Duplicate field key: {spec.key}')
            seen.add(spec.key)

    def clean_fields(self, data: dict) -> dict:
        cleaned = {}
        for spec in self.fields:
            value = spec.clean(data.get(spec.key))
            if value is not None:
                cleaned[spec.key] = value
        return cleaned


CONTENT_TYPES: dict[str, ContentType] = {}


def register_content_type(content_type: ContentType) -> ContentType:
    content_type.validate_definition()
    if content_type.slug in CONTENT_TYPES:
        raise ValueError(f'Content type already registered: {content_type.slug}')
    # Base collisions would make routing ambiguous.
    if content_type.base:
        for other in CONTENT_TYPES.values():
            if other.base and other.base == content_type.base:
                raise ValueError(
                    f'Content type base {content_type.base} already used by '
                    f'{other.slug}')
    CONTENT_TYPES[content_type.slug] = content_type
    return content_type


def get_content_type(slug: str) -> ContentType:
    return CONTENT_TYPES.get(slug) or CONTENT_TYPES['article']


def feed_types() -> list[ContentType]:
    return [ct for ct in CONTENT_TYPES.values() if ct.has_archive]


def type_for_base(base: str) -> ContentType | None:
    for ct in CONTENT_TYPES.values():
        if ct.has_archive and ct.base == base:
            return ct
    return None


def register_core_types() -> None:
    """Core content types. `page` is the standalone type (Home, About);
    `article` is the blog; `event` is a vertical hint; `link` is the reference
    structured type."""
    if 'page' in CONTENT_TYPES:
        return
    register_content_type(ContentType(
        slug='page', singular='Page', plural='Pages',
        description='A standalone page (Home, About, Contact).',
        has_archive=False, base='', template='page',
    ))
    register_content_type(ContentType(
        slug='article', singular='Article', plural='Blog',
        description='The standard blog post.',
        base='/blog', show_in_nav=True,
    ))
    register_content_type(ContentType(
        slug='event', singular='Event', plural='Events',
        description='A vertical example: dated events with a location.',
        base='/events', show_in_nav=True,
        fields=(
            FieldSpec(key='starts_on', type='date', label='Date', required=True),
            FieldSpec(key='location', type='string', label='Location'),
        ),
    ))
    register_content_type(ContentType(
        slug='link', singular='Link', plural='Links',
        description='Reference structured type: share a link with commentary.',
        base='/links', template='single-link',
        fields=(
            FieldSpec(key='url', type='url', label='Link URL', required=True),
            FieldSpec(key='source', type='string', label='Source name'),
        ),
    ))
