"""Content rendering: Markdown to sanitized HTML, then directives.

Authors of published content are org admins, but their output is served to
members and the public: sanitize on render so content can never carry script.

One renderer serves two trust levels -- editorial Content and member-written
discussion posts (app/models/base.py, MarkdownBody) -- so the allowlist below
is the strict one either surface can live with. That is why a video is a
directive rather than an `iframe` tag: see the video embed section.

Four directives, each a line or paragraph of its own:

    :::video https://www.youtube.com/watch?v=...   a player
    :::image 42 left                               a picture from the library
    :::embed episode/why-we-build                  one published item
    :::feed episode limit=3                        a type's latest items

Video and image are available in any body. A video resolves to a frame built
here from an id parsed here and reaches no further; an image is one lookup by
primary key against the organization's own uploads, and who may fetch the
file is settled by the route that serves it rather than by anything decided
here. Embed and feed resolve only where somebody with content.write was
publishing, and not in discussion posts and replies, which share this
renderer and are written by any member: each one runs a query, an
authorization check and a template render on the server, and a forum post is
not the place to hand that to everyone. Opt in with
`MarkdownBody.resolves_directives`.

All four are spliced in *after* sanitizing, never before, and the order is
the whole security argument. Sanitize what the author wrote, then add what we
wrote. The other way round strips our markup and trusts theirs.

They get there by two routes, and both preserve that order. A video or an
image is stashed behind an unguessable per-render token before the Markdown
runs and restored afterwards, so nothing an author can type is mistaken for
ours. An embed or a feed is matched in the cleaned document, where Markdown
has left it as the plain text it always was.
"""

import re
import secrets
import threading
from collections.abc import Callable
from html import escape
from typing import TYPE_CHECKING

import markdown as md
import nh3
from flask import has_request_context

from app.platform.logger import get_logger

if TYPE_CHECKING:
    from app.models.upload import Upload
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


# --- inline images ---------------------------------------------------------
#
# An author writes a line of its own:
#
#     :::image 42
#     :::image 42 left
#
# The number is the id of a file in the organization's own media library, so
# this reuses the upload pipeline whole -- magic-byte sniffing, EXIF
# stripping, the WEBP variants -- rather than letting a body name a file by
# path. Everything an author controls here is one integer and one word out of
# a fixed list; the markup around them is written below and spliced in after
# the cleaner, like the video frame and for the same reason.
#
# Plain Markdown (`![alt](/files/42/medium)`) still works and always has.
# What it cannot say is where the picture sits, and a picture in an article
# that can only ever be a full-width block between two paragraphs is half a
# feature. That is what the placement word is for.
#
# The description is read off the Upload rather than written into the body:
# Upload.alt exists so that one description follows a picture everywhere it
# is used, and a body that carried its own would be a second answer free to
# disagree with the media library's.

PLACEMENTS = ('center', 'left', 'right', 'wide')

# How many pictures one body may show. Generous -- a photo essay is a real
# thing to publish -- but not unbounded: a discussion body may be 64 KB of
# whatever a member types (Post.BODY_MAX), which is room for some thousands
# of directives, and every one of them is a figure in the page every reader
# then downloads. Past the cap they render as nothing, the same as any other
# directive that cannot be honoured.
_MAX_IMAGES = 50

# Which stored variant each placement reads. A floated picture occupies a
# third of a column of text, so the 800px medium is already more than any
# screen can use for it; a centred or full-width one gets the 1600px full.
_PLACEMENT_VARIANT = {'center': 'full', 'wide': 'full',
                      'left': 'medium', 'right': 'medium'}

# The id is bounded rather than left as \d+: it is about to become an
# integer, and a body somebody typed should not be able to hand SQLAlchemy a
# thousand-digit number.
_IMAGE_DIRECTIVE_RE = re.compile(
    r'^[ \t]*:::image[ \t]+(\d{1,18})(?:[ \t]+([a-z]+))?[ \t]*$',
    re.MULTILINE)


def _readable_images(upload_ids: set[int]) -> dict[int, 'Upload']:
    """{id: Upload} for the ones this body may actually show.

    One query for the whole body rather than one per directive. Bodies are
    rendered on every page view and a discussion body is written by any
    member, so a query per picture would let one saved post cost thousands
    of them -- the same reasoning the directive cap below is written down
    for, and the reason the lookup is batched here instead of inside the
    substitution.

    Fails closed with no tenant in force. The tenant filter does not apply
    when there is no organization to apply it for (platform/tenant.py), so
    without this an id typed into a body would reach any organization's
    file from a context that had not resolved one. Every neighbour fails
    closed the same way: can_view answers False with no visitor, and
    serve_upload carries @org_required for exactly this.

    A file the reader could not fetch is left out, so the body agrees with
    what /files/<id> will actually serve. Otherwise a members-only picture
    in a public article showed every visitor a broken box with the
    admin's description of it underneath, and gave anyone who could write a
    body a way to ask which ids exist.
    """
    from app.models import Upload
    from app.platform.authz import is_member_or_platform_admin
    from app.platform.tenant import current_org_id

    if not upload_ids or current_org_id() is None:
        return {}
    inside = has_request_context() and is_member_or_platform_admin()
    rows = Upload.query.filter(Upload.id.in_(upload_ids)).all()
    readable = {upload.id: upload for upload in rows
                if upload.is_image and (upload.visibility == 'public' or inside)}
    if len(readable) < len(upload_ids):
        # Debug, like a directive that could not be honoured: this runs on
        # every page view and a body outliving one of its pictures is not
        # an operational event. Worth a line all the same -- the newsletter
        # worker has no reader to be a member, so a members-only picture
        # leaves an emailed body with a hole in it and nothing else says so.
        log.debug('body_images_withheld', wanted=len(upload_ids),
                  shown=len(readable), in_request=has_request_context())
    return readable


def _image_html(upload: 'Upload', placement: str,
                absolute_url: Callable[[str], str] | None) -> str:
    """The figure for one library image."""
    variant = _PLACEMENT_VARIANT[placement] if upload.has_variants else 'original'
    src = upload.url(variant)
    if absolute_url:
        src = absolute_url(src)
    alt = escape(upload.alt or '', quote=True)
    # The inline style is for mail clients, which drop the stylesheet and
    # would otherwise lay a 1600px picture out at 1600px inside a 600px
    # message. On the web it says what the stylesheet already says. The
    # placement is a class and stays one: an inline float would win against
    # the stylesheet on the web and take the responsive behaviour with it,
    # for a property mail clients honour unevenly anyway. An emailed body
    # centres every picture.
    return (f'<figure class="body-image body-image--{placement}">'
            f'<img src="{escape(src, quote=True)}" alt="{alt}" loading="lazy" '
            'style="max-width:100%;height:auto"></figure>')


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


DIRECTIVE_MODES = ('resolve', 'drop', 'ignore')


def render_markdown(text: str, *, embed_videos: bool = True,
                    directives: str = 'resolve', images: bool = True,
                    absolute_url: Callable[[str], str] | None = None) -> str:
    """This body as sanitized HTML.

    `embed_videos=False` renders a :::video directive as a link rather than
    a frame, for a surface that cannot show one. Email is the case: every
    mail client drops an iframe, so an embedded video would be a blank space
    in a newsletter rather than a video.

    `images=False` removes a :::image directive instead of resolving it,
    for a caller about to strip the markup anyway. A summary is the case:
    it renders the body and then takes every tag out of it, and a picture
    has no text to contribute, so resolving one costs a query to produce
    nothing -- once per card, down a listing of thirty. Removed rather than
    left alone, for the reason a dropped :::embed is: a card summarising a
    post must not read ":::image 42 left The words.".

    `absolute_url` is a path-to-address function (emails.absolute_url_for),
    for the same surface and the same reason: a message carries no origin to
    resolve `/files/42/full` against, so an inline picture in a newsletter
    needs the whole address or it arrives broken. Left out on the web, where
    a path is what the page wants.

    What happens to a :::embed or a :::feed depends on what kind of body
    this is, and the three answers are genuinely different things rather
    than degrees of one:

    'resolve' -- honour them. A published body.

    'drop' -- remove them, honour none. What an embedded item renders and
    what a listing summarises. A card inside a card must not show the words
    ":::embed episode/x", and neither should a one-line archive summary.
    This is also the recursion guard: an embed inside an embedded item is
    never looked at, so a body that embeds itself terminates rather than
    descending. One level, matching blocks.

    'ignore' -- leave the text exactly as written, because directives are
    not a feature of this kind of body at all. A discussion post is somebody
    talking, and ":::embed" in a sentence is a sentence. Dropping it there
    would delete a member's own words on the grounds that they resemble
    syntax they were never offered.
    """
    if not text:
        return ''
    # A browser submits a textarea with CRLF line endings, so every body
    # saved through the editor carries them. A directive is matched a line
    # at a time and `$` in a multiline pattern stops before \n, not before
    # \r, which left the carriage return sitting between the URL and the end
    # of the line and no directive matching anything an author had actually
    # typed. Normalize once here rather than teaching each pattern about it.
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    # Videos and images are taken out before the Markdown and put back
    # after the cleaner, so what goes through the cleaner is only ever what
    # the author wrote. Embeds and feeds are matched after it, in the
    # cleaned document.
    videos: list[str] = []
    figures: list[str] = []
    text = (_stash_videos(text, videos) if embed_videos
            else _link_videos(text))
    text = (_stash_images(text, figures, absolute_url) if images
            else _drop_images(text))
    html = md.markdown(text, extensions=['extra', 'sane_lists'])
    html = nh3.clean(html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRIBUTES,
                     link_rel='noopener noreferrer')
    html = _restore_blocks(html, videos, 'video')
    html = _restore_blocks(html, figures, 'image')

    if directives == 'ignore' or ':::' not in html:
        return html
    # Two more reasons a directive is dropped rather than honoured, both
    # meaning the same thing as an explicit 'drop': we are already inside an
    # embed and one level is the cap, or this is a job, where there is
    # nobody to answer can_view for and so no safe answer.
    nested = getattr(_resolving, 'active', False)
    resolver = _Resolver(active=(directives == 'resolve' and not nested
                                 and has_request_context()))
    _resolving.active = True
    try:
        return _PARAGRAPH_RE.sub(resolver, html)
    finally:
        # Restored rather than cleared, so a nested call cannot end the
        # guard for the render still running around it.
        _resolving.active = nested


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


def _placement_of(match: 're.Match[str]') -> str | None:
    """The placement a directive asked for, or None if it asked for a word
    we do not know -- in which case the line was never a directive and stays
    the text it is, the same answer an unrecognised video link gets."""
    placement = match.group(2) or 'center'
    return placement if placement in PLACEMENTS else None


def _wanted_ids(text: str) -> set[int]:
    """The first _MAX_IMAGES distinct files this body asks for.

    Cut here, before the query, not while substituting. A body names as
    many files as somebody cares to type -- a discussion body is 64 KB of
    whatever a member writes, which is room for some thousands of
    directives -- and every distinct id would otherwise become a bound
    parameter in one enormous IN clause. SQLite built before 3.32 caps
    those at 999 and raises rather than truncating, which on a self-hosted
    installation is one saved post that returns 500 for good.

    Distinct, so a body that shows the same photograph a hundred times
    asks for one file and pays for one parameter.
    """
    wanted: set[int] = set()
    for match in _IMAGE_DIRECTIVE_RE.finditer(text):
        if _placement_of(match) is None:
            continue
        wanted.add(int(match.group(1)))
        if len(wanted) >= _MAX_IMAGES:
            break
    return wanted


def _drop_images(text: str) -> str:
    """Take the directives out, resolve none. See render_markdown's
    `images` argument. A line naming a placement we do not know is not a
    directive and is left exactly as typed, the same as on the way in."""
    return _IMAGE_DIRECTIVE_RE.sub(
        lambda match: match.group(0) if _placement_of(match) is None else '',
        text)


def _stash_images(text: str, placeholders: list[str],
                  absolute_url: Callable[[str], str] | None) -> str:
    """As _stash_videos, for :::image. Its own token and its own list, so
    the two markers can never be read for one another.

    Two passes: read every directive first so the files behind them are
    fetched in one query, then substitute. See _readable_images.
    """
    uploads = _readable_images(_wanted_ids(text))
    token = secrets.token_hex(8)

    def swap(match: 're.Match[str]') -> str:
        placement = _placement_of(match)
        if placement is None:
            return match.group(0)
        upload = uploads.get(int(match.group(1)))
        if upload is None or len(placeholders) >= _MAX_IMAGES:
            # Deleted, not ours, not readable, not a picture, or past the
            # cap. Nothing rather than a broken box or the raw directive: a
            # body outlives the media library and must not end up showing a
            # torn page where a photograph used to be.
            return ''
        placeholders.append(_image_html(upload, placement, absolute_url))
        return f'\n\nsupremelyimage{token}{len(placeholders) - 1}\n\n'

    swapped = _IMAGE_DIRECTIVE_RE.sub(swap, text)
    if placeholders:
        placeholders.append(token)      # the tail entry is the token itself
    return swapped


def _restore_blocks(html: str, placeholders: list[str], word: str) -> str:
    """Put the markup we built back where its marker sits.

    Highest index first, because the markers end in a number and a shorter
    number is a prefix of a longer one: replacing marker 1 while marker 10
    is still standing would put the second picture where the eleventh
    belongs and leave its trailing 0 sitting in the prose. Any index that
    another index is a prefix of has more digits, so it is the larger
    number, so going down settles every pair. Ten of anything used to be
    more than a body had.
    """
    if not placeholders:
        return html
    *blocks, token = placeholders
    for index in range(len(blocks) - 1, -1, -1):
        marker, block = f'supremely{word}{token}{index}', blocks[index]
        # Markdown wraps a lone marker in a paragraph. Replace the paragraph
        # too, or the block lands inside a <p> it is not allowed to sit in.
        html = html.replace(f'<p>{marker}</p>', block).replace(marker, block)
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


def _active_type(name: str) -> 'ContentType | None':
    """The type a directive is naming, if this organization publishes it.

    Accepts either the type's slug or the segment its archive lives at, so
    both of these find the same thing:

        :::embed resource/setup-guide
        :::embed resources/setup-guide

    The second is what an author will write, because it is what the address
    bar shows them: the item is at /resources/setup-guide. Not one type in
    the library has a URL base equal to its slug -- article publishes at
    /blog, episode at /podcast, team_member at /team -- so accepting only
    the slug means the obvious spelling silently renders nothing, on every
    type, forever.

    The slug wins where a name could be both, so adding a type can never
    change what an existing body points at.

    None for a type this organization does not publish, so a directive is
    not a way in through a door the archive closed.
    """
    from app.platform.content_types import active_types
    active = active_types()
    if name in active:
        return active[name]
    return next((content_type for content_type in active.values()
                 if content_type.has_archive
                 and content_type.base.strip('/') == name), None)
