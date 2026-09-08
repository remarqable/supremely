"""Content rendering: Markdown to sanitized HTML, then directives.

Authors of published content are org admins, but their output is served to
members and the public: sanitize on render so content can never carry script.

One renderer serves two trust levels -- editorial Content and member-written
discussion posts (app/models/base.py, MarkdownBody) -- so the allowlist below
is the strict one either surface can live with. That is why a video is a
directive rather than an `iframe` tag: see the video embed section.

Three directives, each a line or paragraph of its own:

    :::video https://www.youtube.com/watch?v=...   a player
    :::embed episode/why-we-build                  one published item
    :::feed episode limit=3                        a type's latest items

Video is available in any body, because it resolves to a frame built here
from an id parsed here and reaches no further. Embed and feed resolve only
where somebody with content.write was publishing, and not in discussion
posts and replies, which share this renderer and are written by any member:
each one runs a query, an authorization check and a template render on the
server, and a forum post is not the place to hand that to everyone. Opt in
with `MarkdownBody.resolves_directives`.

All three are spliced in *after* sanitizing, never before, and the order is
the whole security argument. Sanitize what the author wrote, then add what we
wrote. The other way round strips our markup and trusts theirs.

They get there by two routes, and both preserve that order. A video is
stashed behind an unguessable per-render token before the Markdown runs and
restored afterwards, so nothing an author can type is mistaken for ours. An
embed or a feed is matched in the cleaned document, where Markdown has left
it as the plain text it always was.
"""

import re
import secrets
import threading
from html import escape
from typing import TYPE_CHECKING

import markdown as md
import nh3

from app.platform.logger import get_logger

if TYPE_CHECKING:
    from app.platform.content_types import ContentType

log = get_logger()


_ALLOWED_TAGS = {
    'a', 'abbr', 'blockquote', 'br', 'code', 'del', 'div', 'em', 'figure',
    'figcaption', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'img', 'li',
    'ol', 'p', 'pre', 'span', 'strong', 'sub', 'sup', 'table', 'tbody',
    'td', 'th', 'thead', 'tr', 'ul',
}

_ALLOWED_ATTRIBUTES = {
    'a': {'href', 'title'},
    'img': {'src', 'alt', 'title', 'width', 'height', 'loading'},
    'td': {'align'}, 'th': {'align'},
    'div': {'class'}, 'span': {'class'}, 'code': {'class'}, 'pre': {'class'},
}


# --- video embeds ----------------------------------------------------------
#
# An author writes a line of its own:
#
#     :::video https://www.youtube.com/watch?v=QMH4rPEJ5BI
#
# `iframe` is deliberately NOT in the allowlist above. Allowing it would let
# anyone who can write a body -- including a member writing a discussion
# post -- frame any page in the organization's own origin. Instead the URL is
# parsed down to a bare video id here and the iframe is built from a fixed
# template, so the only thing an author controls is which video plays. The
# frame is spliced in AFTER nh3.clean: sanitize what the author wrote, then
# add what we wrote. Doing it the other way round would strip our markup and
# trust theirs.
#
# The browser also has to allow the frame: see the frame-src hosts in the
# Content-Security-Policy (app/__init__.py). Both halves are required, and
# VIDEO_FRAME_HOSTS below is what keeps them saying the same thing.

VIDEO_FRAME_HOSTS = ('https://www.youtube-nocookie.com', 'https://player.vimeo.com')

_VIDEO_DIRECTIVE_RE = re.compile(r'^[ \t]*:::video[ \t]+(\S+)[ \t]*$', re.MULTILINE)

# Ids are substituted into a URL, so they are matched strictly rather than
# trimmed: anything that is not a plain id is left alone as ordinary text.
_YOUTUBE_RE = re.compile(
    r'^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|embed/|live/|shorts/)'
    r'|youtu\.be/)([A-Za-z0-9_-]{11})(?:[?&#].*)?$')
_VIMEO_RE = re.compile(
    r'^https?://(?:www\.)?vimeo\.com/(?:video/)?(\d{6,12})(?:[?&#].*)?$')


def _embed_src(url: str) -> str | None:
    """The player URL for a supported video link, or None for anything else."""
    match = _YOUTUBE_RE.match(url)
    if match:
        return f'https://www.youtube-nocookie.com/embed/{match.group(1)}'
    match = _VIMEO_RE.match(url)
    if match:
        return f'https://player.vimeo.com/video/{match.group(1)}'
    return None


def _embed_html(src: str) -> str:
    """The frame itself. `src` is built from a matched id, never author text."""
    return (
        '<div class="video-embed">'
        f'<iframe src="{escape(src, quote=True)}" title="Video" loading="lazy" '
        'allow="accelerometer; autoplay; clipboard-write; encrypted-media; '
        'gyroscope; picture-in-picture" allowfullscreen></iframe>'
        '</div>'
    )



def video_link(url: str) -> str | None:
    """The watch page for a supported video link, or None.

    For surfaces that cannot show a frame at all -- newsletter email, where
    every client drops an iframe -- so they can offer the link instead of a
    blank space.
    """
    return url if _embed_src(url) else None


# Match the paragraph, then read what is inside it in Python.
# Whether this thread is already resolving directives. One level is the cap,
# and it is enforced here rather than left to callers: the embed partial is
# theme-overridable, so a theme rendering the target's `html` instead of its
# `html_flat` would otherwise follow a body that references itself until the
# worker died.
_resolving = threading.local()

_PARAGRAPH_RE = re.compile(r'<p>([^<]*)</p>')

# How many directives one body may resolve. Each one is a query and a
# template render, so an unbounded count turns one saved body into a page
# that costs thousands of both. Past the cap they render as nothing, the
# same as any other directive that cannot be honoured.
_MAX_DIRECTIVES = 10

# What a type slug and a content slug are allowed to look like. Narrow on
# purpose: these arrive from a body somebody typed.
_TARGET_RE = re.compile(r'(?P<type>[a-z][a-z0-9_]{0,49})'
                        r'/(?P<slug>[a-z0-9][a-z0-9-]{0,199})\Z')
_FEED_RE = re.compile(r'(?P<type>[a-z][a-z0-9_]{0,49})'
                      r'(?:\s+limit=(?P<limit>\d{1,3}))?\Z')




def render_markdown(text: str, *, embed_videos: bool = True,
                    resolve_directives: bool = True) -> str:
    """This body as sanitized HTML, with its directives honoured.

    `embed_videos=False` renders a :::video directive as a link rather than a
    frame, for a surface that cannot show one. Email is the case: every mail
    client drops an iframe, so an embedded video would be a blank space in a
    newsletter rather than a video.

    `resolve_directives=False` renders the body with :::embed and :::feed
    left as the plain text they are. That is what an embedded item renders,
    and it is the whole recursion guard: an embed inside an embedded item is
    never looked at, so a body that embeds itself terminates rather than
    descending. One level, matching blocks.
    """
    if not text:
        return ''
    # Videos are taken out before the Markdown and put back after the
    # cleaner, so what goes through the cleaner is only ever what the author
    # wrote. Embeds and feeds are matched after it, in the cleaned document.
    placeholders: list[str] = []
    text = (_stash_videos(text, placeholders) if embed_videos
            else _link_videos(text))
    html = md.markdown(text, extensions=['extra', 'sane_lists'])
    html = nh3.clean(html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRIBUTES,
                     link_rel='noopener noreferrer')
    html = _restore_videos(html, placeholders)

    if not resolve_directives or ':::' not in html:
        return html
    if getattr(_resolving, 'active', False):
        # Already inside an embed. Whatever this body says, it is being read
        # as part of something else and goes no deeper.
        return html
    from flask import has_request_context
    resolver = _Resolver(active=has_request_context())
    _resolving.active = True
    try:
        return _PARAGRAPH_RE.sub(resolver, html)
    finally:
        _resolving.active = False


def _link_videos(text: str) -> str:
    """Turn each directive into an ordinary Markdown link."""
    def swap(match):
        url = match.group(1)
        if _embed_src(url) is None:
            return match.group(0)
        return f'[{url}]({url})'

    return _VIDEO_DIRECTIVE_RE.sub(swap, text)


def _stash_videos(text: str, placeholders: list[str]) -> str:
    """Swap each directive for an unguessable marker before rendering.

    The marker carries a per-render random token so an author cannot type one
    by hand and have their own text treated as ours.
    """
    token = secrets.token_hex(8)

    def swap(match):
        src = _embed_src(match.group(1))
        if src is None:
            return match.group(0)       # not a video we know: leave the text
        placeholders.append(_embed_html(src))
        return f'\n\nsupremelyvideo{token}{len(placeholders) - 1}\n\n'

    swapped = _VIDEO_DIRECTIVE_RE.sub(swap, text)
    if placeholders:
        placeholders.append(token)      # the tail entry is the token itself
    return swapped


def _restore_videos(html: str, placeholders: list[str]) -> str:
    if not placeholders:
        return html
    *embeds, token = placeholders
    for index, embed in enumerate(embeds):
        marker = f'supremelyvideo{token}{index}'
        # Markdown wraps a lone marker in a paragraph. Replace the paragraph
        # too, or the frame lands inside a <p> it is not allowed to sit in.
        html = html.replace(f'<p>{marker}</p>', embed).replace(marker, embed)
    return html


class _Resolver:
    """Replaces one paragraph at a time, counting as it goes.

    A class rather than a closure so the cap is state the substitution owns
    rather than something the module remembers between calls.
    """

    def __init__(self, active: bool) -> None:
        self.active = active
        self.used = 0

    def __call__(self, match: 're.Match[str]') -> str:
        whole = match.group(0)
        text = match.group(1).strip()
        if not text.startswith(':::'):
            return whole
        name, _, args = text[3:].partition(' ')
        if name not in ('embed', 'feed'):
            return whole
        args = args.strip()
        parsed = (_TARGET_RE.match(args) if name == 'embed'
                  else _FEED_RE.match(args))
        if parsed is None or not self.active or self.used >= _MAX_DIRECTIVES:
            # A directive that cannot be honoured leaves nothing behind,
            # whatever stopped it: a typo in the target, no reader to answer
            # for, or a body that has already had its ten. Never the raw
            # text, which is machinery showing through in a published page,
            # and never an error message in the middle of somebody's
            # article. An author writing *about* directives puts them in
            # backticks like any other code, and then they are code.
            return ''
        self.used += 1
        try:
            return (_render_embed(parsed) if name == 'embed'
                    else _render_feed(parsed))
        except Exception as exc:
            from werkzeug.exceptions import HTTPException

            from app.platform.errors import TenantViolation
            if isinstance(exc, HTTPException | TenantViolation):
                # An abort inside a partial, or the tenant filter refusing a
                # read. Both are meant to stop the request, and swallowing
                # either would turn a loud failure into a quiet wrong page.
                raise
            # Otherwise a body must still render. Logged at debug: a typo in
            # an article is not an operational event, and this runs on every
            # page view.
            log.debug('directive_failed', directive=name, args=args)
            return ''


def _render_embed(target: 're.Match[str]') -> str:
    from flask import render_template

    from app.models import Content
    from app.platform.authz import can_view
    from app.platform.theming import embed_template
    content_type = _active_type(target.group('type'))
    if content_type is None:
        return ''
    item = Content.published_by_slug(content_type.slug, target.group('slug'))
    if item is None or not Content.section_readable_by_current_visitor(
            content_type.slug):
        return ''
    locked = not can_view(item)
    if locked:
        # Gated, so the same tease-or-hide answer the rest of the product
        # gives: a locked title where this organization teases, and no sign
        # the item exists where it does not. Never the body, either way.
        from flask import g
        org = getattr(g, 'org', None)
        if org is None or not org.type_teases(content_type.slug):
            return ''
    return render_template(embed_template(item), item=item,
                           content_type=content_type, locked=locked)


def _render_feed(wanted: 're.Match[str]') -> str:
    from flask import render_template

    from app.platform.theming import site_feed_template
    content_type = _active_type(wanted.group('type'))
    if content_type is None:
        return ''
    limit = int(wanted.group('limit')) if wanted.group('limit') else None
    # The same partial the front page window draws, so a section an author
    # places in a body and one a theme places on the front page are the same
    # thing rendered the same way. One data verb, two callers.
    return render_template(site_feed_template(content_type),
                           content_type=content_type, limit=limit)


def _active_type(slug: str) -> 'ContentType | None':
    """The type, if this organization publishes it. None otherwise, so a
    directive naming a type that was turned off renders nothing rather than
    publishing through a door the archive closed."""
    from app.platform.content_types import active_types
    return active_types().get(slug)
