"""Rendering the fields a content type declares.

Types have always been able to declare typed fields, and the editor has
always collected them, but nothing put them on a screen: a video's URL was
stored and never shown. What did reach a reader got there through slug tests
in templates (`content_type.slug == 'event'`), which is the callsite
membership test the architecture forbids everywhere else.

One renderer, three surfaces. A field is drawn by a partial chosen from its
type and key, so a theme overrides how every URL looks by dropping in one
file, and a new field type is a new partial rather than an edit to every
template that might show it.

Partials are trusted template output, not sanitized Markdown. That is what
lets a video field emit an iframe when `_ALLOWED_TAGS` in
app/platform/content.py never can. It is also why nothing here may put an
author's value into markup unescaped: `video_embed` below parses an id out
of a URL rather than passing the URL through.
"""

import re
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlparse

from flask import has_request_context
from markupsafe import Markup

from app.platform.logger import get_logger

if TYPE_CHECKING:                       # circular at runtime, fine for hints
    from app.models import Content
    from app.platform.content_types import FieldSpec

log = get_logger()

# Where a field can be drawn. 'web' is the item's own page, 'summary' a
# listing card, 'email' a newsletter. Email is its own family rather than a
# fallback: no stylesheet survives an email client, so those partials carry
# inline styles and cannot be the web ones.
# Each surface is its own family and never borrows another's: an email
# carries inline styles no stylesheet can reach, and a listing chip is not a
# page row. Families end in _default.html, so a partial exists only where a
# field type has something of its own to say -- 'string', 'text' and
# 'number' had none, and were the same four lines written out three times.
#
# 'lead' has no default on purpose. A type naming a lead field whose type
# nothing draws should lead with nothing, and the caller falls back to what
# the card normally shows.
_ROOTS = {
    'web': ('fields/',),
    'summary': ('fields/summary/',),
    'email': ('fields/email/',),
    'lead': ('fields/lead/',),
}
_DEFAULTING = ('web', 'summary', 'email')

# The only origins a field may frame or play from. Read by the CSP builder
# in app/__init__.py, so the policy that permits an embed and the code that
# writes one are the same list.
# The hosts a video player may be framed from live in the body renderer, as
# VIDEO_FRAME_HOSTS: a :::video directive and a video field build the same
# frame for the same hosts, and a second copy here would be a second thing
# to forget when one of them changes. video_embed below imports it where it
# is used, and the content security policy is built from the same list.

# A field value is author input. Anything that is not http(s) is refused at
# the point it would become an href or a src, not only where it was typed:
# Content.set_structured_fields keeps values stored under keys the type did
# not declare at the time, seeds build rows directly, and neither passes
# FieldSpec.clean. Escaping quotes a javascript: URL, it does not disarm it.
SAFE_URL_SCHEMES = ('http', 'https')


def safe_url(value: object) -> str:
    """The value if it is a link a browser should follow, otherwise ''."""
    try:
        scheme = urlparse(str(value or '')).scheme.lower()
    except ValueError:
        return ''
    return str(value) if scheme in SAFE_URL_SCHEMES else ''


YOUTUBE_HOSTS = ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be')
VIMEO_HOSTS = ('vimeo.com', 'www.vimeo.com', 'player.vimeo.com')
YOUTUBE_PATH_RE = re.compile(r'/(?:embed/|v/|shorts/)([A-Za-z0-9_-]{11})')
YOUTUBE_ID_RE = re.compile(r'[A-Za-z0-9_-]{11}')
VIMEO_PATH_RE = re.compile(r'/(?:video/)?(\d{6,12})')


def video_embed(url: object) -> str | None:
    """An embed URL for a host we know, or None for anything else.

    Matched on the parsed host, never on the text of the URL. Searching the
    whole string for "youtu.be/" embeds a video for
    https://evil.test/#youtu.be/<id>, which is somebody else's page wearing
    a known host's name.

    The id is then parsed out and a fresh URL built from it, rather than the
    author's URL being handed to an iframe: a field value is author input,
    an iframe src is a document loaded beside this page, and the two should
    not meet.
    """
    if not safe_url(url):
        return None
    parts = urlparse(str(url))
    host = parts.hostname or ''
    if host in YOUTUBE_HOSTS:
        if host == 'youtu.be':
            candidate = parts.path.lstrip('/')
            if YOUTUBE_ID_RE.fullmatch(candidate):
                return f'https://www.youtube-nocookie.com/embed/{candidate}'
            return None
        match = YOUTUBE_PATH_RE.fullmatch(parts.path)
        if match:
            return f'https://www.youtube-nocookie.com/embed/{match.group(1)}'
        for key, value in parse_qsl(parts.query):
            if key == 'v' and YOUTUBE_ID_RE.fullmatch(value):
                return f'https://www.youtube-nocookie.com/embed/{value}'
        return None
    if host in VIMEO_HOSTS:
        match = VIMEO_PATH_RE.fullmatch(parts.path)
        if match:
            return f'https://player.vimeo.com/video/{match.group(1)}'
    return None


def _candidates(field_type: str, key: str, surface: str) -> list[str]:
    """Partial names for one field, most specific first.

    Key before type, and a theme before origin before core, so a theme's
    `fields/url.html` beats core's `url-audio_url.html` -- the override the
    contract promises. Defaults are last of all, after every typed partial
    anywhere, so shipping one adds a fallback rather than removing the
    embeds.
    """
    from app.platform.devices import device_candidates
    from app.platform.theming import current_theme
    roots = _ROOTS[surface]
    prefixes = ['']
    if surface != 'email':
        # current_theme guards its own request access, so this resolves the
        # organization's theme in a job too rather than quietly using Origin.
        prefixes = [f'themes/{current_theme()}/', 'themes/origin/', '']
    names = []
    for root in roots:
        for prefix in prefixes:
            names.append(f'{prefix}{root}{field_type}-{key}.html')
            names.append(f'{prefix}{root}{field_type}.html')
    # Defaults come after every typed partial at every level, not after each
    # level's own. A theme shipping only _default.html means "when nothing
    # of mine matched", not "replace the video player with a line of text".
    if surface in _DEFAULTING:
        for root in roots:
            for prefix in prefixes:
                names.append(f'{prefix}{root}_default.html')
    names = list(dict.fromkeys(names))
    if surface == 'email':
        return names            # an email has no device to resolve for
    # Every other template seam offers a mobile sibling ahead of each
    # candidate (CLAUDE.md, Mobile). This is the fourth seam and would have
    # been the only one where a theme's fields/mobile/url.html was ignored.
    return device_candidates(names)


def _resolved(field_type: str, key: str, surface: str):
    """The partial that wins, memoized for the request.

    Most of these names do not exist -- themes/origin/fields/ ships nothing,
    and few themes override anything -- so an archive of twenty cards spent
    a hundred failing loader lookups redrawing the same two fields. Jinja
    caches templates it finds, not the ones it does not.
    """
    from flask import current_app, g
    from jinja2 import TemplateNotFound
    cache_key = (field_type, key, surface)
    cache = g.setdefault('_field_partials', {}) if has_request_context() else {}
    if cache_key in cache:
        return cache[cache_key]
    found = None
    for name in _candidates(field_type, key, surface):
        try:
            found = current_app.jinja_env.get_template(name)
            break
        except TemplateNotFound:
            continue
    cache[cache_key] = found
    return found


def render_field(spec: 'FieldSpec', value: object, surface: str = 'web',
                 *, content: 'Content | None' = None) -> Markup:
    """One field, or nothing at all when no partial claims it.

    A missing partial is not an error: a theme may declare a field type this
    installation has no renderer for, and half a page is worse than a page
    with one thing missing from it.

    Rendered through the Jinja environment rather than flask.render_template,
    which is not a detail. render_template runs the application's context
    processors, and those read the request -- so a newsletter, which is a job
    with no request, could not render a field at all.

    The context is small on purpose: the value, how to say it, and the row it
    came from. `content` is the whole Content row, because a partial has real
    uses for it (a video's title on its iframe), which does mean a partial
    could reach further than it should. Nothing stops that here; what stops
    it is that access is decided before rendering (authz.can_view) and the
    caller withholds a locked row's fields, so a partial reaching for
    `content.visibility` would be answering a question already answered.
    """
    from app.platform.i18n import t
    if surface not in _ROOTS:
        raise ValueError(f'Unknown field surface: {surface!r}')
    context = {'spec': spec, 'value': value, 'content': content,
               'label': spec.label or spec.key,
               '_': t, 't': t, 'video_embed': video_embed,
               'safe_url': safe_url}
    template = _resolved(spec.type, spec.key, surface)
    if template is not None:
        try:
            return Markup(template.render(context))
        except Exception:
            # A stored value can be the wrong shape for its declared type:
            # types change, seeds write rows directly, and JSON has no
            # schema. Losing one field beats losing the page, and inside a
            # newsletter it beats marking every recipient failed.
            log.exception('field_render_failed', field=spec.key,
                          type=spec.type, surface=surface)
            return Markup('')
    log.warning('field_partial_missing', field=spec.key, type=spec.type,
                surface=surface)
    return Markup('')


def _has_value(value: object) -> bool:
    """Booleans are values; empty strings and empty lists are not."""
    if isinstance(value, bool):
        return value
    return value not in (None, '', [], {})


def render_fields(content: 'Content', surface: str = 'web') -> Markup:
    """Every declared field of `content` that has a value.

    Declaration order, not storage order: the type decides what comes first,
    so two items of the same type read the same way.
    """
    content_type = content.content_type
    if content_type is None:
        return Markup('')
    stored = content.fields or {}
    parts = []
    for spec in content_type.fields:
        # The lead field is already the card's leading block; saying it
        # twice on one card is the obvious way to get this wrong.
        if surface == 'summary' and (not spec.in_summary
                                     or spec.key == content_type.lead_field):
            continue
        value = stored.get(spec.key)
        if not _has_value(value):
            continue
        parts.append(render_field(spec, value, surface, content=content))
    return Markup('').join(parts)


def render_fields_text(content: 'Content') -> str:
    """The same fields as plain text, for the text half of an email.

    Written here rather than as partials because a text email has no markup
    to override: there is nothing for a theme to say about "Location: Berlin".
    Kept in this module so a type's fields still have one place that knows
    how to render them.
    """
    from app.platform.i18n import t
    content_type = content.content_type
    if content_type is None:
        return ''
    stored = content.fields or {}
    lines = []
    for spec in content_type.fields:
        value = stored.get(spec.key)
        if not _has_value(value):
            continue
        if isinstance(value, bool):
            value = t('common.yes') if value else t('common.no')
        lines.append(f'{spec.label or spec.key}: {value}')
    return ('\n'.join(lines) + '\n') if lines else ''


def render_lead_field(content: 'Content') -> Markup:
    """The field a listing card leads with, or nothing.

    Empty when the type names no lead field, when the value is missing, or
    when no partial draws that field type. The caller falls back to whatever
    a card normally shows, so a type without one is not a special case.
    """
    content_type = content.content_type
    if content_type is None or not content_type.lead_field:
        return Markup('')
    spec = next((f for f in content_type.fields
                 if f.key == content_type.lead_field), None)
    if spec is None:
        return Markup('')
    value = (content.fields or {}).get(spec.key)
    if not _has_value(value):
        return Markup('')
    return render_field(spec, value, 'lead', content=content)
