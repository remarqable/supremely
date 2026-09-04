"""Content Types: the universal publishing architecture.

Everything an organization publishes is a Content row with a `type`. Content
Types are defined in code -- by Supremely core or by plugins -- and validated
at registration. Each type declares its own labels and public URL base, so a
recipe site shows "Recipes" at /recipes while a blog shows "Blog" at /blog;
there is no generic "posts" surface — the word "post" belongs to
discussions (app/models/discussion.py). The Content subsystem never changes
when a vertical is added -- you register one ContentType.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from app.platform.errors import ValidationError

FIELD_TYPES = ('string', 'text', 'url', 'number', 'boolean', 'date',
               'select', 'image', 'file', 'datetime', 'list')

# A repeating field holds rows, not a spreadsheet. Fifty is past what anyone
# types into a recipe and well short of what would make the editor unusable
# or the JSON column unwieldy.
LIST_ROW_CAP = 50

# Sidebar sections of the community surface. Feed types declare where they
# live; empty groups are hidden.
NAV_GROUPS = ('community', 'meet', 'learn')

_SLUG_RE = re.compile(r'[a-z][a-z0-9_]{0,49}')
_BASE_RE = re.compile(r'/[a-z0-9]([a-z0-9-]{0,48})?')


def _is_filled(value: object) -> bool:
    """Did somebody put something in this box?

    An unticked checkbox and an empty string are both nothing, which is what
    makes a row of them a row to drop rather than a row to complain about.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None and value != []


@dataclass(frozen=True)
class FieldSpec:
    key: str
    type: str = 'string'
    label: str = ''
    required: bool = False
    help: str = ''
    # Does this field belong on a listing card as well as the item's own
    # page? A single renders every field; an archive wants a date and a
    # location, not a full ingredients list. Declared by the type rather
    # than decided by each template, so a theme cannot disagree with the
    # next one about what an event card says.
    in_summary: bool = False
    # For 'select': the offered options as (stored key, label) pairs. The key
    # is what lands in the JSON, so a label can be reworded without
    # rewriting every row that chose it.
    choices: tuple[tuple[str, str], ...] = ()
    # For 'list': the fields one row holds. One level only. A row of rows
    # would need an editor that nests, and the value of a repeating field is
    # that it stays a table somebody can read.
    of: tuple['FieldSpec', ...] = ()

    def clean(self, raw):
        """Validate and coerce one submitted value. Returns the stored value."""
        value = (raw or '').strip() if isinstance(raw, str) else raw

        if self.type == 'list':
            return self._clean_rows(value)

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
            except (TypeError, ValueError) as exc:
                raise ValidationError(
                    f'{self.label or self.key} must be a number') from exc
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
        if self.type == 'datetime':
            return self._clean_datetime(value)
        if self.type == 'select':
            if str(value) not in {key for key, _label in self.choices}:
                raise ValidationError(
                    f'{self.label or self.key}: {value} is not one of the '
                    f'choices')
            return str(value)
        if self.type in ('image', 'file'):
            return self._clean_upload(value)
        raise ValidationError(f'Unknown field type: {self.type}')

    def _clean_datetime(self, value: object) -> str:
        """An instant, stored as ISO 8601 in UTC.

        A browser's datetime-local sends no zone, so a bare value is read as
        UTC. That is a real limitation and the honest one: the alternative
        is guessing at a zone the form never asked for.
        """
        from datetime import UTC, datetime
        try:
            moment = datetime.fromisoformat(str(value))
        except ValueError as exc:
            raise ValidationError(
                f'{self.label or self.key} must be a date and time') from exc
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment.astimezone(UTC).isoformat()

    def _clean_upload(self, value: object) -> int:
        """An upload id, resolved rather than trusted.

        Read back through a query so the tenant filter answers, exactly as
        _own_upload_id does in the controller: an id belonging to another
        organization is not storable by typing it into a form.

        Inside a request, which is where the editor runs. The filter has
        nothing to scope by outside one, so a seed or a command line can
        still write any id -- the same latitude every other query has there,
        and the reason those callers are trusted.
        """
        from app.models import Upload
        text = str(value).strip()
        # Length before int(): CPython refuses to parse an integer literal
        # past 4300 digits, so isdigit() alone lets a long enough string
        # through to raise where nothing is catching.
        if not text.isdigit() or len(text) > 19:
            raise ValidationError(
                f'{self.label or self.key} must be a file from your library')
        if Upload.query.filter_by(id=int(text)).first() is None:
            raise ValidationError(
                f'{self.label or self.key}: that file is not in your library')
        return int(text)

    def _clean_rows(self, raw: object) -> list | None:
        """Rows for a repeating field.

        Empty rows are dropped rather than refused: an editor that offers a
        blank row to type into will always post the one nobody filled in.
        """
        if raw in (None, '', [], ()):
            if self.required:
                raise ValidationError(f'{self.label or self.key} is required')
            return None
        if not isinstance(raw, (list, tuple)):
            raise ValidationError(f'{self.label or self.key} must be a list')
        if len(raw) > LIST_ROW_CAP:
            raise ValidationError(
                f'{self.label or self.key}: at most {LIST_ROW_CAP} rows')
        rows = []
        for row in raw:
            if not isinstance(row, dict):
                raise ValidationError(
                    f'{self.label or self.key} rows must be groups of fields')
            # Emptiness is decided before the row is cleaned, not after. The
            # other way round, a blank row fails its own required sub-field,
            # so an editor offering a row to type into refuses every save
            # until somebody fills in the one nobody wanted.
            if not any(_is_filled(row.get(sub.key)) for sub in self.of):
                continue
            cleaned = {}
            for sub in self.of:
                cleaned_value = sub.clean(row.get(sub.key))
                if cleaned_value is not None:
                    cleaned[sub.key] = cleaned_value
            if cleaned:
                rows.append(cleaned)
        if not rows:
            if self.required:
                raise ValidationError(f'{self.label or self.key} is required')
            return None
        return rows


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
    group: str = 'community'        # community-sidebar section (NAV_GROUPS)
    # Where this type's public archive and singles present: 'community'
    # renders inside the app-owned shell, 'site' through the theme (the
    # marketing look) — same vocabulary as Content.presentation for pages.
    # Declared here and passed through the render_site seam; never a
    # membership test at a callsite.
    # A 'site' type is also left out of the community sidebar and the
    # community home feed: its pages render themed, so sending a member
    # there from inside the shell would drop them onto the public site
    # mid-browse (see in_community_nav).
    presentation: str = 'community'
    # The field a listing card leads with, in place of the author avatar.
    # An event card shows a date chip; that used to be a slug test in
    # community/archive.html, which is the callsite membership test the
    # architecture forbids. Naming the field here lets any type lead with
    # one, and lets a theme change what leading looks like.
    lead_field: str = ''
    # Which icon the community sidebar draws for this type: the name of a
    # partial in app/views/icons/. Empty falls back to the document icon,
    # so a plugin's type is never iconless and no template has to know
    # which types exist.
    icon: str = ''

    @property
    def is_page(self) -> bool:
        return not self.has_archive

    @property
    def in_community_nav(self) -> bool:
        """Does this type belong in the community sidebar?

        A type that presents as site furniture does not. Its archive renders
        through the theme, so a row in the community sidebar would take a
        member out of the shell they were browsing and into the public site,
        which is a jarring thing for a sidebar to do. Team is the current
        example: a roster is something a visitor reads on the website, not
        something a member navigates to from inside the community.
        """
        return self.has_archive and self.presentation == 'community'

    def validate_definition(self):
        if not _SLUG_RE.fullmatch(self.slug):
            raise ValueError(f'Invalid content type slug: {self.slug!r}')
        if not (self.singular and self.plural):
            raise ValueError('Content type needs singular and plural labels')
        if self.has_archive and not _BASE_RE.fullmatch(self.base or ''):
            raise ValueError(
                f'Feed content type {self.slug} needs a URL base like /blog')
        if self.group not in NAV_GROUPS:
            raise ValueError(f'Unknown nav group: {self.group!r}')
        if self.presentation not in ('community', 'site'):
            raise ValueError(f'Unknown presentation: {self.presentation!r}')
        if self.icon and not _SLUG_RE.fullmatch(self.icon):
            raise ValueError(f'Invalid icon name: {self.icon!r}')
        keys = {spec.key for spec in self.fields}
        if self.lead_field and self.lead_field not in keys:
            raise ValueError(
                f'lead_field {self.lead_field!r} is not a field of '
                f'{self.slug}')
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
            _validate_field(self.slug, spec)

    @property
    def field_keys(self) -> set:
        """The keys this type declares today.

        Anything stored outside this set was written when the type declared
        more than it does now, and is kept rather than dropped
        (Content.set_structured_fields).
        """
        return {spec.key for spec in self.fields}

    def clean_fields(self, data: dict) -> dict:
        cleaned = {}
        for spec in self.fields:
            value = spec.clean(data.get(spec.key))
            if value is not None:
                cleaned[spec.key] = value
        return cleaned


def _validate_field(type_slug: str, spec: FieldSpec,
                    nested: bool = False) -> None:
    """What a field type needs declared with it, checked at registration.

    A select with no choices and a list with no row fields are both editors
    that render nothing, and both are the kind of mistake that only shows up
    when somebody opens the form.
    """
    where = f'{type_slug}.{spec.key}'
    if spec.type == 'select' and not spec.choices:
        raise ValueError(f'{where}: a select needs choices')
    if spec.choices:
        for choice in spec.choices:
            if not (isinstance(choice, (tuple, list)) and len(choice) == 2):
                raise ValueError(
                    f'{where}: choices are (key, label) pairs')
    if spec.type == 'list':
        if nested:
            # One level. A row of rows needs an editor that nests, and the
            # value of a repeating field is that it stays a table.
            raise ValueError(f'{where}: a list cannot contain a list')
        if not spec.of:
            raise ValueError(f'{where}: a list needs `of` sub-fields')
        sub_seen = set()
        for sub in spec.of:
            if not isinstance(sub, FieldSpec):
                raise ValueError(f'{where}: `of` takes FieldSpec instances')
            if sub.type not in FIELD_TYPES:
                raise ValueError(f'{where}: unknown field type: {sub.type}')
            if not _SLUG_RE.fullmatch(sub.key):
                raise ValueError(f'{where}: invalid sub-field key: {sub.key!r}')
            if sub.key in sub_seen:
                raise ValueError(f'{where}: duplicate sub-field: {sub.key}')
            sub_seen.add(sub.key)
            _validate_field(where, sub, nested=True)
    elif spec.of:
        raise ValueError(f'{where}: only a list takes `of`')


_ROW_NAME_RE = re.compile(r'field_(?P<key>[a-z][a-z0-9_]*)__'
                          r'(?P<index>\d{1,4})__(?P<sub>[a-z][a-z0-9_]*)$')


def submitted_fields(content_type: ContentType,
                     form: 'Mapping[str, str]') -> dict:
    """The type's fields as the editor posted them, ready for clean_fields.

    Scalars arrive one per name. A row of a repeating field names its own
    index (field_photos__0__caption), so a value belongs to a row rather
    than to a position in a list.

    That is not decoration. Zipping parallel lists by position looks
    simpler, and it is wrong the moment one column is shorter than another:
    an unticked checkbox posts nothing at all, so every value after it slides
    onto the row above and the author's data is quietly rearranged.

    Indexes only order the rows; gaps and reordering are both fine, and the
    stored list is renumbered from zero.
    """
    data = {}
    rows: dict[str, dict[int, dict]] = {}
    for name in form:
        match = _ROW_NAME_RE.fullmatch(name)
        if match:
            key, index = match['key'], int(match['index'])
            rows.setdefault(key, {}).setdefault(index, {})
            rows[key][index][match['sub']] = form.get(name)

    for spec in content_type.fields:
        if spec.type != 'list':
            data[spec.key] = form.get(f'field_{spec.key}')
            continue
        by_index = rows.get(spec.key, {})
        # Sliced one past the cap so an over-long post is still refused with
        # a message, without building every row somebody chose to send.
        data[spec.key] = [by_index[index]
                          for index in sorted(by_index)][:LIST_ROW_CAP + 1]
    return data


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


def type_is_active(content_type: ContentType) -> bool:
    """Active for the current tenant. Registration is global (boot-time, no
    restarts); visibility is per-request: a plugin's types surface only where
    that plugin is enabled for the org. Core/library types are active
    everywhere until per-org type enablement exists."""
    if content_type.plugin is None:
        return True
    from flask import has_request_context
    if not has_request_context():
        # CLI/jobs/workers: there is no tenant, so the question has no
        # answer. Fail closed -- a caller that legitimately needs every
        # type outside a request should read CONTENT_TYPES directly.
        return False
    from app.platform.plugins import installed_version
    return installed_version(content_type.plugin) is not None


def active_types() -> dict[str, ContentType]:
    return {slug: ct for slug, ct in CONTENT_TYPES.items()
            if type_is_active(ct)}


def feed_types() -> list[ContentType]:
    return [ct for ct in CONTENT_TYPES.values()
            if ct.has_archive and type_is_active(ct)]


def community_types(group: str | None = None) -> list[ContentType]:
    """Types the community surface lists, optionally within one nav group.

    The community's own answer to feed_types(): everything a member can be
    sent to from inside the shell, which excludes anything that presents on
    the site (ContentType.in_community_nav).
    """
    return [ct for ct in CONTENT_TYPES.values()
            if ct.in_community_nav and type_is_active(ct)
            and (group is None or ct.group == group)]


def type_for_base(base: str) -> ContentType | None:
    for ct in CONTENT_TYPES.values():
        if ct.has_archive and ct.base == base and type_is_active(ct):
            return ct
    return None


def register_core_types() -> None:
    """Core content types. `page` is the standalone type (Home, About);
    `article` is the blog; `event` is the structured-fields example. Further
    types come from the library (content_library) and plugins."""
    if 'page' in CONTENT_TYPES:
        return
    register_content_type(ContentType(
        slug='page', singular='Page', plural='Pages',
        description='A standalone page (Home, About, Contact).',
        has_archive=False, base='', template='page',
    ))
    register_content_type(ContentType(
        slug='article', singular='Article', plural='Articles',
        description='The standard blog post.',
        base='/blog', show_in_nav=True, icon='article',
    ))
    register_content_type(ContentType(
        slug='event', singular='Event', plural='Events',
        description='A vertical example: dated events with a location.',
        base='/events', show_in_nav=True, group='meet',
        lead_field='starts_on', icon='calendar',
        fields=(
            FieldSpec(key='starts_on', type='date', label='Date',
                      required=True, in_summary=True),
            FieldSpec(key='location', type='string', label='Location',
                      in_summary=True),
        ),
    ))
