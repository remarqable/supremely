"""Structured Post Types: the universal publishing architecture.

Post Types are defined in code -- by Supremely core or by plugins -- and
validated at startup/registration. The standard Article is simply the default
Post Type. A developer defines a new vertical by registering a PostType; the
Post subsystem itself never changes. Field storage is schema-flexible (typed
values in JSON) so admin-defined types remain possible post-MVP.
"""

import re
from dataclasses import dataclass, field

from app.platform.errors import ValidationError

FIELD_TYPES = ('string', 'text', 'url', 'number', 'boolean', 'date')

_SLUG_RE = re.compile(r'[a-z][a-z0-9_]{0,49}')


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
class PostType:
    slug: str
    name: str
    description: str = ''
    fields: tuple = ()
    # WordPress-style hierarchy names: single.html / single-{type}.html
    template: str = 'single'        # single template name (theme-resolvable)
    list_template: str = 'archive'  # listing/card template name
    plugin: str | None = None       # owning plugin slug, if any

    def validate_definition(self):
        if not _SLUG_RE.fullmatch(self.slug):
            raise ValueError(f'Invalid post type slug: {self.slug!r}')
        if not self.name:
            raise ValueError('Post type needs a name')
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
        """Validate submitted structured data against this type's fields."""
        cleaned = {}
        for spec in self.fields:
            value = spec.clean(data.get(spec.key))
            if value is not None:
                cleaned[spec.key] = value
        return cleaned


POST_TYPES: dict[str, PostType] = {}


def register_post_type(post_type: PostType) -> PostType:
    post_type.validate_definition()
    if post_type.slug in POST_TYPES:
        raise ValueError(f'Post type already registered: {post_type.slug}')
    POST_TYPES[post_type.slug] = post_type
    return post_type


def get_post_type(slug: str) -> PostType:
    return POST_TYPES.get(slug) or POST_TYPES['article']


def register_core_types() -> None:
    """Core types. Article is the default; Link is the reference structured
    type proving the architecture (spec Phase 3)."""
    if 'article' in POST_TYPES:
        return
    register_post_type(PostType(
        slug='article', name='Article',
        description='The standard post.',
    ))
    register_post_type(PostType(
        slug='link', name='Link',
        description='Reference structured type: share a link with commentary.',
        fields=(
            FieldSpec(key='url', type='url', label='Link URL', required=True),
            FieldSpec(key='source', type='string', label='Source name'),
        ),
        template='single-link',
    ))
