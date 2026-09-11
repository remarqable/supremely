"""Syndication and discovery: RSS, Atom, and the sitemap.

A public community should be followable and findable. These are the two
protocols that do it: a feed reader subscribes to an address, and a crawler
reads a list of addresses.

App-owned, never themed. A feed is a protocol rather than a page, so there
is nothing here for a theme to restyle and no seam offered to it; what a
theme does get is the `<link rel="alternate">` tags in its head, through
partials/_head_links.html.

Visibility is the whole of the care needed here. A feed is read by machines
and cached by them, so it is the easiest place in a publishing system to
leak something gated without anybody noticing. Two rules, both enforced
below rather than left to a template:

Feeds list exactly what an archive lists, by going through the same
Content.visible_query -- so an organization that teases gated content sees
locked titles in its feed and one that does not sees nothing at all. An
item somebody may not read contributes its title and its address and no
body, the same as the locked card on the archive page.

The sitemap lists only what is public, whoever asks for it. It exists for
crawlers, a crawler is anonymous, and building it from the asker's own view
would put members-only addresses into a file whose whole purpose is to be
fetched and cached by strangers.
"""

import re
from collections.abc import Callable
from typing import TYPE_CHECKING
from urllib.parse import quote

from flask import Blueprint, Response, abort, g, render_template
from flask.typing import ResponseReturnValue

from app.models import Content
from app.platform.authz import is_member_or_platform_admin, org_required
from app.platform.content_types import feed_types, type_for_base
from app.platform.tenant import org_url

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.platform.content_types import ContentType

bp = Blueprint('feeds', __name__)

# How many items one syndication document carries. A reader wants the
# recent past, not the archive: everything ever published would make a
# megabyte of XML that every subscriber refetches on a schedule.
#
# Named for this file rather than FEED_LIMIT, which Content already has
# and which means something else: the ceiling on what a theme's front-page
# grid may ask for.
ENTRY_LIMIT = 50

# How long a shared cache may keep one of these. Only ever set where the
# document does not depend on the reader -- see _xml.
CACHE_MAX_AGE = 900


@bp.after_request
def _no_cookie_on_a_cacheable_response(response: Response) -> Response:
    """Do not hand a shared cache a session to keep.

    Rendering a template runs the CSRF context processor, which puts a
    token in the session, which makes Flask set a cookie. On a response
    also marked `public` that is a real problem: a cache may store the
    Set-Cookie and hand that one anonymous session, and its CSRF token, to
    everybody who fetches the feed afterwards.

    Nothing here needs a session. An anonymous fetch whose session holds
    nothing but that token is dropped before Flask saves it, so no cookie
    goes out. A signed-in reader keeps theirs untouched -- their feed is
    marked private anyway.
    """
    from flask import session
    if '_user_id' not in session and set(session) <= {'_csrf_token'}:
        session.clear()
        # clear() marks it modified, which is what makes Flask write the
        # cookie. Saying otherwise is how the cookie is not written.
        session.modified = False
    return response


def _xml(body: str, content_type: str) -> Response:
    """One XML response, with the caching a viewer-dependent document can
    have.

    A feed fetched by a member can carry titles a visitor would not be
    shown, so it must never land in a shared cache. Anonymous responses
    contain what any visitor could already read off the archive, so those
    are worth caching: a feed reader polls.
    """
    response = Response(body, mimetype=content_type)
    if is_member_or_platform_admin():
        response.headers['Cache-Control'] = 'private, no-store'
    else:
        response.headers['Cache-Control'] = f'public, max-age={CACHE_MAX_AGE}'
    return response


# XML 1.0 forbids the C0 control characters outright, tab, newline and
# carriage return excepted -- there is no escape for them, so one in a
# title takes the whole document down for every subscriber rather than
# spoiling one entry. Titles are already refused them on the way in, but a
# body, a category name and the organization's own description are not, and
# all three reach a feed. Stripped here, where everything a feed renders is
# assembled, rather than trusted to each template to remember.
_ILLEGAL_XML = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def _text(value: str | None) -> str:
    return _ILLEGAL_XML.sub('', value) if value else ''


def _absolute(org: 'Organization') -> Callable[[str], str]:
    """A path-to-address function for one organization, worked out once.

    org_url counts this installation's active organizations on every call
    to decide whether the site sits on the bare domain or a subdomain. A
    feed builds one address per item, so calling it per entry turned fifty
    items into fifty extra queries.
    """
    origin = org_url(org, '/').rstrip('/')
    return lambda path: origin + quote(path)


def _entry(content: Content, absolute: Callable[[str], str]) -> dict:
    """One item, flattened to what both formats need.

    `summary` is None for anything the reader may not open. A gated item is
    teased in a feed exactly as it is teased in a list: the title says
    there is something here, and the body stays behind the gate. Without
    this the feed would be the way round it.
    """
    readable = content.visible_to_current_visitor()
    return {
        'title': _text(content.title),
        'url': absolute(content.permalink),
        'published_at': content.published_at or content.created_at,
        # Never None: RFC 4287 makes atom:updated mandatory on an entry,
        # and updated_at is a non-null column, so the fallbacks are belt
        # and braces rather than a real case.
        'updated_at': (content.updated_at or content.published_at
                       or content.created_at),
        'summary': _text(content.excerpt_or_summary(400)) if readable else None,
        'author': _text(content.author.name) if content.author else None,
        'categories': [_text(category.name) for category in content.categories],
    }


def _feed_context(content_type: 'ContentType | None' = None) -> dict:
    absolute = _absolute(g.org)
    site_name = _text(g.org.site_name)
    title = (f'{site_name} — {_text(content_type.plural)}' if content_type
             else site_name)
    base = content_type.base if content_type else ''
    items = (Content.syndication_query(content_type.slug if content_type
                                       else None)
             .limit(ENTRY_LIMIT).all())
    entries = [_entry(item, absolute) for item in items]
    return {
        'feed_title': title,
        'feed_description': _text(g.org.description) or title,
        'site_url': absolute('/'),
        'rss_url': absolute(f'{base}/feed'),
        'atom_url': absolute(f'{base}/feed.atom'),
        'entries': entries,
        # RFC 4287 makes atom:updated mandatory on the feed, so a feed with
        # nothing in it still needs one. The organization's own timestamp
        # rather than the clock: a date that moved on every request would
        # tell every reader polling an empty feed that it had changed.
        'updated_at': max((entry['updated_at'] for entry in entries
                           if entry['updated_at']),
                          default=g.org.updated_at or g.org.created_at),
        # RFC 4287 asks for an author on the feed or on every entry, and
        # plenty of content has no named author -- seeded, imported, or
        # written by somebody since deleted. One here answers for all of
        # them, and an entry that names its own still overrides it.
        'feed_author': site_name,
    }


def _readable_type_or_404(seg: str) -> 'ContentType':
    """The type this segment names, if it has a feed to give.

    404 rather than a gate for a locked section: a gate is a page, and
    nothing that reads XML can be shown one. It leaks nothing either, since
    a locked section shows a visitor the same refusal whether it holds a
    thousand items or none.
    """
    content_type = type_for_base('/' + seg)
    if content_type is None:
        abort(404)
    if not Content.section_readable_by_current_visitor(content_type.slug):
        abort(404)
    return content_type


@bp.route('/feed')
@org_required
def site_rss() -> ResponseReturnValue:
    """Everything this organization publishes, newest first."""
    return _xml(render_template('feeds/rss.xml', **_feed_context()),
                'application/rss+xml')


@bp.route('/feed.atom')
@org_required
def site_atom() -> ResponseReturnValue:
    return _xml(render_template('feeds/atom.xml', **_feed_context()),
                'application/atom+xml')


@bp.route('/<seg>/feed')
@org_required
def type_rss(seg: str) -> ResponseReturnValue:
    """One type's archive as a feed, e.g. /blog/feed.

    More static parts than the /<seg>/<slug> rule it sits beside, so the
    URL map matches this first. An item cannot take the slug either --
    RESERVED_ITEM_SLUGS refuses it -- because an item that lost would be
    unreachable rather than merely shadowed.
    """
    content_type = _readable_type_or_404(seg)
    return _xml(render_template('feeds/rss.xml', **_feed_context(content_type)),
                'application/rss+xml')


@bp.route('/<seg>/feed.atom')
@org_required
def type_atom(seg: str) -> ResponseReturnValue:
    content_type = _readable_type_or_404(seg)
    return _xml(render_template('feeds/atom.xml', **_feed_context(content_type)),
                'application/atom+xml')


# --- Sitemap ------------------------------------------------------------------

# A sitemap may hold 50,000 URLs before it has to be split into an index.
# Nothing here is near that, and the cap is what keeps a runaway
# installation from finding out the hard way.
SITEMAP_LIMIT = 5000


def _sitemap_urls() -> list[dict]:
    """Every public address on this site, for a crawler.

    Public whoever asks. Category archives used to come from
    Category.for_type, which is built on visible_query and so answers for
    whoever fetched the file: a member's copy listed a category whose only
    items were gated, and the response carried a public cache header, so a
    shared cache could hand that list to a stranger. They are read off the
    public items instead, which cannot name a category that has no public
    item in it.

    Pages are here as well as feed types. A page is a public address --
    /about, /contact -- and a sitemap that omitted them was not the thing
    it says it is.

    The cap is on the document and is checked as the list grows, not
    applied per type and sliced at the end: a slice makes the file the
    right length while the work behind it is still types-times-limit rows,
    and it silently drops whichever types sorted last.
    """
    absolute = _absolute(g.org)
    urls: list[dict] = [{'loc': absolute('/'), 'lastmod': None}]
    seen_categories: set[str] = set()

    def room() -> int:
        return max(0, SITEMAP_LIMIT - len(urls))

    # Which sections are public is the model's answer, asked once here and
    # again inside public_query for the items. A section gated to members
    # is not a public address: its archive would meet a crawler with a
    # gate page.
    public = set(Content.public_archive_types())
    for content_type in feed_types():
        if content_type.slug not in public or not room():
            continue
        urls.append({'loc': absolute(content_type.base), 'lastmod': None})
        for item in Content.public_query(content_type.slug).limit(room()).all():
            if not room():
                break
            urls.append({'loc': absolute(item.permalink),
                         'lastmod': item.updated_at or item.published_at})
            for category in item.categories:
                key = f'{content_type.base}/category/{category.slug}'
                if key not in seen_categories and room():
                    seen_categories.add(key)
                    urls.append({'loc': absolute(key), 'lastmod': None})
    for page in Content.public_pages(limit=room()):
        if not room():
            break
        urls.append({'loc': absolute(page.permalink),
                     'lastmod': page.updated_at or page.published_at})
    return urls


@bp.route('/sitemap.xml')
@org_required
def sitemap() -> ResponseReturnValue:
    """The sitemap. Cacheable by anybody, because there is no version of it
    that depends on the reader."""
    body = render_template('feeds/sitemap.xml', urls=_sitemap_urls())
    response = Response(body, mimetype='application/xml')
    response.headers['Cache-Control'] = f'public, max-age={CACHE_MAX_AGE}'
    return response


@bp.route('/robots.txt')
@org_required
def robots() -> ResponseReturnValue:
    """Where the sitemap is, in the file crawlers look in first."""
    body = render_template('feeds/robots.txt',
                           sitemap_url=org_url(g.org, '/sitemap.xml'))
    response = Response(body, mimetype='text/plain')
    response.headers['Cache-Control'] = f'public, max-age={CACHE_MAX_AGE}'
    return response
