"""Content rendering: Markdown to sanitized HTML.

Authors are org admins, but their output is served to members and the public:
sanitize on render so content can never carry script.

One renderer serves two trust levels -- editorial Content and member-written
discussion posts (app/models/base.py, MarkdownBody) -- so the allowlist below
is the strict one either surface can live with. That is why a video is a
directive rather than an `iframe` tag: see the video embed section.
"""

import re
import secrets
from html import escape

import markdown as md
import nh3

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

_DIRECTIVE_RE = re.compile(r'^[ \t]*:::video[ \t]+(\S+)[ \t]*$', re.MULTILINE)

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


def render_markdown(text: str, embed_videos: bool = True) -> str:
    """Body Markdown as sanitized HTML.

    `embed_videos=False` renders a :::video directive as a link rather than a
    frame, for surfaces that cannot show one. Email is the case: every mail
    client drops an iframe, so an embedded video would be a blank space in a
    newsletter rather than a video.
    """
    if not text:
        return ''
    placeholders: list[str] = []
    text = (_stash_videos(text, placeholders) if embed_videos
            else _link_videos(text))
    html = md.markdown(text, extensions=['extra', 'sane_lists'])
    html = nh3.clean(html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRIBUTES,
                     link_rel='noopener noreferrer')
    return _restore_videos(html, placeholders)


def _link_videos(text: str) -> str:
    """Turn each directive into an ordinary Markdown link."""
    def swap(match):
        url = match.group(1)
        if _embed_src(url) is None:
            return match.group(0)
        return f'[{url}]({url})'

    return _DIRECTIVE_RE.sub(swap, text)


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

    swapped = _DIRECTIVE_RE.sub(swap, text)
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
