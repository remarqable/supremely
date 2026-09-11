"""RSS, Atom, the sitemap and robots.txt.

A feed is read by machines and cached by them, which makes it the easiest
place in a publishing system to leak something gated without anybody
noticing. Most of what is here is about that.
"""

import xml.etree.ElementTree as ET

from flask import g

from app.extensions import db
from app.models import Content, Membership
from tests.conftest import login_as, make_user

ACME = 'http://acme.example.test'
ATOM = '{http://www.w3.org/2005/Atom}'
SITEMAP = '{http://www.sitemaps.org/schemas/sitemap/0.9}'


def _publish(app, org, title, slug, body='The body of it.',
             visibility='public', type_slug='article') -> Content:
    with app.test_request_context(base_url=ACME):
        g.org = org
        content = Content(type=type_slug, title=title, slug=slug, body=body,
                          status='published', visibility=visibility)
        content.save()
        db.session.commit()
        return content


def _xml(client, path, base_url=ACME):
    response = client.get(path, base_url=base_url)
    assert response.status_code == 200, path
    return ET.fromstring(response.get_data(as_text=True))


def _titles(root) -> list[str]:
    rss = [item.findtext('title') for item in root.iter('item')]
    atom = [item.findtext(f'{ATOM}title') for item in root.iter(f'{ATOM}entry')]
    return rss + atom


def test_every_feed_is_well_formed_xml(app, client, acme):
    _publish(app, acme, 'Hello', 'hello')
    for path in ('/feed', '/feed.atom', '/blog/feed', '/blog/feed.atom',
                 '/sitemap.xml'):
        _xml(client, path)


def test_the_content_types_are_the_ones_readers_look_for(app, client, acme):
    _publish(app, acme, 'Hello', 'hello')
    expected = {
        '/feed': 'application/rss+xml',
        '/feed.atom': 'application/atom+xml',
        '/blog/feed': 'application/rss+xml',
        '/blog/feed.atom': 'application/atom+xml',
        '/sitemap.xml': 'application/xml',
    }
    for path, mimetype in expected.items():
        response = client.get(path, base_url=ACME)
        assert response.headers['Content-Type'].startswith(mimetype), path


def test_a_published_item_reaches_the_feed(app, client, acme):
    _publish(app, acme, 'First post', 'first-post')
    assert 'First post' in _titles(_xml(client, '/feed'))
    assert 'First post' in _titles(_xml(client, '/blog/feed'))


def test_a_draft_does_not(app, client, acme):
    """A new organization comes seeded with content, so this asks about
    the draft rather than about the feed being empty."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        Content(type='article', title='Not ready yet', slug='draft',
                body='x', status='draft').save()
        db.session.commit()
    assert 'Not ready yet' not in _titles(_xml(client, '/feed'))


def test_a_gated_body_never_reaches_a_feed(app, client, acme):
    """The whole point. A feed must not be the way around a gate."""
    _publish(app, acme, 'Members only', 'gated',
             body='SECRETBODY only members may read this.',
             visibility='members')

    for path in ('/feed', '/feed.atom', '/blog/feed', '/blog/feed.atom'):
        body = client.get(path, base_url=ACME).get_data(as_text=True)
        assert 'SECRETBODY' not in body, path


def test_a_gated_title_is_teased_the_way_a_list_teases_it(app, client, acme):
    """Teasing is on by default, so an archive shows a locked title. The
    feed says the same thing, because both go through visible_query."""
    _publish(app, acme, 'Members only', 'gated', visibility='members')
    assert 'Members only' in _titles(_xml(client, '/feed'))


def test_with_teasing_off_a_gated_item_is_absent_entirely(app, client, acme):
    acme.update_settings(gated_teasers=False)
    db.session.commit()
    _publish(app, acme, 'Members only', 'gated', visibility='members')
    _publish(app, acme, 'Open to all', 'open')

    titles = _titles(_xml(client, '/feed'))
    assert 'Open to all' in titles
    assert 'Members only' not in titles


def test_a_member_gets_the_body_a_visitor_does_not(app, acme, user):
    _publish(app, acme, 'Members only', 'gated',
             body='SECRETBODY for members.', visibility='members')
    member = make_user(email='ada@example.com')
    Membership.add(member.id, acme.id, role='member')

    visitor = app.test_client().get('/feed', base_url=ACME)
    insider = login_as(app.test_client(), member).get('/feed', base_url=ACME)

    assert 'SECRETBODY' not in visitor.get_data(as_text=True)
    assert 'SECRETBODY' in insider.get_data(as_text=True)


def test_a_members_feed_is_never_stored_by_a_shared_cache(app, acme, user):
    """It can carry titles and bodies a visitor would not be shown, so a
    proxy keeping one would hand it to somebody who could not have asked
    for it."""
    member = make_user(email='ada2@example.com')
    Membership.add(member.id, acme.id, role='member')
    insider = login_as(app.test_client(), member)

    response = insider.get('/feed', base_url=ACME)
    assert response.headers['Cache-Control'] == 'private, no-store'


def test_a_visitors_feed_is_cacheable(app, client, acme):
    response = client.get('/feed', base_url=ACME)
    assert response.headers['Cache-Control'].startswith('public')


def test_a_locked_section_has_no_feed(app, client, acme):
    """A gate is a page and nothing that reads XML can be shown one, so the
    answer is the same 404 a section with no archive gives."""
    acme.set_type_settings('article', visibility='members')
    db.session.commit()
    assert client.get('/blog/feed', base_url=ACME).status_code == 404
    assert client.get('/blog/feed.atom', base_url=ACME).status_code == 404


def test_a_type_that_does_not_exist_has_no_feed(app, client, acme):
    assert client.get('/nosuchtype/feed', base_url=ACME).status_code == 404


def test_one_organizations_feed_holds_only_its_own(app, client, acme, globex):
    _publish(app, acme, 'Acme news', 'acme-news')
    _publish(app, globex, 'Globex news', 'globex-news')

    titles = _titles(_xml(client, '/feed'))
    assert 'Acme news' in titles
    assert 'Globex news' not in titles


def test_a_feed_escapes_what_an_author_typed(app, client, acme):
    """A title is somebody's text and XML has its own syntax to break."""
    _publish(app, acme, 'Tom & Jerry <script>', 'ampersand')
    root = _xml(client, '/feed')          # raises if the escaping is wrong
    assert 'Tom & Jerry <script>' in _titles(root)


# --- Sitemap ------------------------------------------------------------------

def _locations(root) -> list[str]:
    return [url.findtext(f'{SITEMAP}loc') for url in root.iter(f'{SITEMAP}url')]


def test_the_sitemap_lists_public_pages_and_archives(app, client, acme):
    _publish(app, acme, 'Open', 'open')
    locations = _locations(_xml(client, '/sitemap.xml'))

    assert any(loc.endswith('/') for loc in locations)      # the front page
    assert any(loc.endswith('/blog') for loc in locations)
    assert any(loc.endswith('/blog/open') for loc in locations)


def test_the_sitemap_never_lists_a_gated_item(app, client, acme):
    _publish(app, acme, 'Members only', 'gated', visibility='members')
    assert not any('gated' in loc
                   for loc in _locations(_xml(client, '/sitemap.xml')))


def test_the_sitemap_is_the_same_for_a_member(app, acme, user):
    """It is built for crawlers, and a crawler is anonymous. Built from the
    fetcher's own view instead, a member's copy would put members-only
    addresses into a file whose whole purpose is to be fetched by
    strangers and cached."""
    _publish(app, acme, 'Members only', 'gated', visibility='members')
    member = make_user(email='ada3@example.com')
    Membership.add(member.id, acme.id, role='member')

    visitor = _locations(_xml(app.test_client(), '/sitemap.xml'))
    insider = _locations(
        _xml(login_as(app.test_client(), member), '/sitemap.xml'))

    assert visitor == insider
    assert not any('gated' in loc for loc in insider)


def test_a_gated_section_is_not_in_the_sitemap(app, client, acme):
    acme.set_type_settings('article', visibility='members')
    db.session.commit()
    _publish(app, acme, 'Open', 'open')

    assert not any(loc.endswith('/blog')
                   for loc in _locations(_xml(client, '/sitemap.xml')))


def test_robots_names_the_sitemap(app, client, acme):
    response = client.get('/robots.txt', base_url=ACME)
    assert response.status_code == 200
    assert '/sitemap.xml' in response.get_data(as_text=True)


# --- Discovery ------------------------------------------------------------------

def test_the_head_advertises_the_feeds(app, client, acme):
    page = client.get('/', base_url=ACME).get_data(as_text=True)
    assert 'application/rss+xml' in page
    assert 'href="/feed"' in page
    assert 'href="/feed.atom"' in page


def test_an_archive_also_advertises_its_own_feed(app, client, acme):
    _publish(app, acme, 'Open', 'open')
    page = client.get('/blog', base_url=ACME).get_data(as_text=True)
    assert 'href="/blog/feed"' in page
    assert 'href="/blog/feed.atom"' in page


def test_an_item_cannot_take_the_feed_address(app, acme):
    """The feed rule wins in the URL map, so an item slugged "feed" would
    be unreachable rather than merely shadowed. It is refused instead."""
    from app.platform.errors import ValidationError

    with app.test_request_context(base_url=ACME):
        g.org = acme
        for slug in ('feed', 'feed.atom'):
            try:
                Content(type='article', title='Sneaky', slug=slug,
                        body='x', status='published').save()
            except ValidationError:
                continue
            raise AssertionError(f'{slug} was allowed')


def test_the_feed_route_beats_the_item_route(app, client, acme):
    """Both rules are two segments; this is the check that the map orders
    them the way the comment in app/__init__.py says it does."""
    response = client.get('/blog/feed', base_url=ACME)
    assert response.headers['Content-Type'].startswith('application/rss+xml')


# --- Conformance ----------------------------------------------------------------
#
# Checked against the specifications rather than against what a reader
# happens to tolerate: RSS 2.0 (rssboard.org/rss-specification), Atom
# RFC 4287, and the sitemap protocol 0.9 (sitemaps.org/protocol.html).

def test_rss_carries_the_three_channel_elements_it_must(app, client, acme):
    """title, link and description are the only mandatory ones."""
    channel = _xml(client, '/feed').find('channel')
    for element in ('title', 'link', 'description'):
        assert channel.findtext(element), element


def test_every_rss_item_has_a_title_or_a_description(app, client, acme):
    """One or the other is required. A gated item has no description, which
    is exactly why this is worth asserting."""
    _publish(app, acme, 'Members only', 'gated', visibility='members')
    for item in _xml(client, '/feed').iter('item'):
        assert item.findtext('title') or item.findtext('description')


def test_rss_dates_are_rfc_822(app, client, acme):
    import email.utils

    _publish(app, acme, 'Dated', 'dated')
    for item in _xml(client, '/feed').iter('item'):
        stamp = item.findtext('pubDate')
        assert stamp
        # Returns None rather than raising on anything it cannot parse.
        assert email.utils.parsedate_to_datetime(stamp) is not None


def test_rss_says_where_it_lives(app, client, acme):
    """atom:link rel=self: the RSS Advisory Board's best-practice profile,
    and what the W3C feed validator warns about when it is missing."""
    channel = _xml(client, '/feed').find('channel')
    link = channel.find(f'{ATOM}link')
    assert link is not None
    assert link.get('rel') == 'self'
    assert link.get('href', '').endswith('/feed')


def test_atom_has_the_three_elements_a_feed_must_have(app, client, acme):
    """RFC 4287 4.1.1: exactly one each of id, title and updated."""
    root = _xml(client, '/feed.atom')
    for element in ('id', 'title', 'updated'):
        assert len(root.findall(f'{ATOM}{element}')) == 1, element


def test_atom_has_the_three_elements_an_entry_must_have(app, client, acme):
    """RFC 4287 4.1.2. updated used to be written only when the row had
    one, which made an entry without it invalid rather than sparse."""
    _publish(app, acme, 'Dated', 'dated')
    entries = list(_xml(client, '/feed.atom').iter(f'{ATOM}entry'))
    assert entries
    for entry in entries:
        for element in ('id', 'title', 'updated'):
            assert len(entry.findall(f'{ATOM}{element}')) == 1, element


def test_atom_names_an_author_somewhere(app, client, acme):
    """4.1.2 asks for one on every entry unless the feed carries one. Most
    content here has no named author, so the feed carries it."""
    root = _xml(client, '/feed.atom')
    feed_author = root.find(f'{ATOM}author')
    for entry in root.iter(f'{ATOM}entry'):
        assert feed_author is not None or entry.find(f'{ATOM}author') is not None


def test_atom_dates_are_rfc_3339(app, client, acme):
    from datetime import datetime

    _publish(app, acme, 'Dated', 'dated')
    root = _xml(client, '/feed.atom')
    stamps = [root.findtext(f'{ATOM}updated')]
    stamps += [entry.findtext(f'{ATOM}updated')
               for entry in root.iter(f'{ATOM}entry')]
    for stamp in stamps:
        assert stamp and stamp.endswith('Z') and 'T' in stamp
        datetime.fromisoformat(stamp)      # raises if it is not a date-time


def test_an_empty_feed_is_still_valid_and_does_not_move(app, client, acme):
    """A feed with nothing in it still needs a date, and one taken from the
    clock would tell every reader polling it that it had changed."""
    acme.set_type_settings('article', enabled=False)
    db.session.commit()

    root = _xml(client, '/feed.atom')
    assert root.findtext(f'{ATOM}updated')
    again = _xml(client, '/feed.atom')
    assert root.findtext(f'{ATOM}updated') == again.findtext(f'{ATOM}updated')


def test_the_sitemap_uses_the_current_namespace_and_required_element(
        app, client, acme):
    """Protocol 0.9. loc is the only required child of url."""
    root = _xml(client, '/sitemap.xml')
    assert root.tag == f'{SITEMAP}urlset'
    for url in root.iter(f'{SITEMAP}url'):
        assert url.findtext(f'{SITEMAP}loc')


def test_sitemap_lastmod_is_a_w3c_date(app, client, acme):
    from datetime import date

    _publish(app, acme, 'Open', 'open')
    for url in _xml(client, '/sitemap.xml').iter(f'{SITEMAP}url'):
        stamp = url.findtext(f'{SITEMAP}lastmod')
        if stamp:
            date.fromisoformat(stamp)      # raises if it is not one


def test_no_sitemap_url_is_longer_than_the_protocol_allows(app, client, acme):
    for url in _xml(client, '/sitemap.xml').iter(f'{SITEMAP}url'):
        assert len(url.findtext(f'{SITEMAP}loc')) < 2048


# --- Leaks the review found ------------------------------------------------------

def test_a_locked_section_is_absent_from_the_site_wide_feed(app, client, acme):
    """The one that mattered. /blog/feed refused a locked section from the
    start, but /feed filtered each item's own visibility and never the
    organization's lock on the section, so every article title and address
    went out to anybody who asked."""
    _publish(app, acme, 'Behind the lock', 'locked-one')
    acme.set_type_settings('article', visibility='members')
    db.session.commit()

    for path in ('/feed', '/feed.atom'):
        body = client.get(path, base_url=ACME).get_data(as_text=True)
        assert 'Behind the lock' not in body, path
        assert 'locked-one' not in body, path


def test_a_locked_section_is_absent_even_with_teasing_off(app, client, acme):
    """visible_query(None) has no type to ask about, so it fell back to the
    organization's own teasing default and leaked whichever way the switch
    was set."""
    acme.update_settings(gated_teasers=False)
    _publish(app, acme, 'Behind the lock', 'locked-two')
    acme.set_type_settings('article', visibility='members')
    db.session.commit()

    assert 'Behind the lock' not in client.get(
        '/feed', base_url=ACME).get_data(as_text=True)


def test_a_control_character_does_not_take_the_whole_feed_down(app, client, acme):
    """XML 1.0 has no escape for these, so one in a body used to make the
    document unparseable for every subscriber rather than spoiling one
    entry. Titles were already refused them; bodies, category names and the
    organization's description were not."""
    acme.description = 'Sound\x01ing off'
    acme.save()
    _publish(app, acme, 'Ordinary title', 'ctrl',
             body='A body with a \x01 control character in it.')
    db.session.commit()

    for path in ('/feed', '/feed.atom'):
        _xml(client, path)          # raises if the document is malformed


def test_the_sitemap_categories_do_not_depend_on_who_asks(app, acme, user):
    """They came from Category.for_type, which is built on visible_query,
    so a member's sitemap listed a category whose only items were gated --
    and the response carried a public cache header."""
    from app.models.content import Category

    with app.test_request_context(base_url=ACME):
        g.org = acme
        secret = Category(name='Secret roadmap', slug='secret-roadmap').save()
        gated = Content(type='article', title='Members only', slug='cat-gated',
                        body='x', status='published', visibility='members')
        gated.categories = [secret]
        gated.save()
        db.session.commit()

    member = make_user(email='ada4@example.com')
    Membership.add(member.id, acme.id, role='member')

    visitor = _locations(_xml(app.test_client(), '/sitemap.xml'))
    insider = _locations(
        _xml(login_as(app.test_client(), member), '/sitemap.xml'))

    assert visitor == insider
    assert not any('secret-roadmap' in loc for loc in insider)


def test_the_sitemap_lists_standalone_pages(app, client, acme):
    """It walked the types that have archives, so /about and every other
    page was missing from a file that says it holds every public
    address."""
    _publish(app, acme, 'About us', 'about-us', type_slug='page')
    assert any(loc.endswith('/about-us')
               for loc in _locations(_xml(client, '/sitemap.xml')))


def test_a_feed_does_not_cost_a_query_per_item(app, client, acme):
    """Public, unauthenticated, and polled by machines on a schedule. The
    author and the categories of every item were lazy-loaded, and the URL
    builder counted the installation's organizations once per address."""
    from sqlalchemy import event

    for n in range(12):
        _publish(app, acme, f'Item {n}', f'item-{n}')

    seen = []
    engine = db.session.get_bind()

    def count(conn, cursor, statement, *args):
        seen.append(statement)

    event.listen(engine, 'before_cursor_execute', count)
    try:
        client.get('/feed', base_url=ACME)
    finally:
        event.remove(engine, 'before_cursor_execute', count)

    # A fixed handful: the org, the items, and one batch each for the
    # authors and the categories. Well under one per item.
    assert len(seen) < 20, f'{len(seen)} queries'


def test_an_item_titled_feed_can_still_be_saved(app, acme):
    """Reserving the slug broke the derivation that avoids it: an author
    who titled an article "Feed" was refused over a field they never
    filled in, instead of getting feed-2."""
    with app.test_request_context(base_url=ACME):
        g.org = acme
        content = Content(type='article', title='Feed', body='x',
                          status='published')
        content.save()
        assert content.slug != 'feed'
        assert content.slug


def test_a_types_own_tease_setting_reaches_the_site_wide_feed(app, client, acme):
    """Teasing is a per-type answer: an organization may advertise its
    articles and say nothing about its jobs board. The all-types feed had
    no type to put that question to, so it fell back to the organization
    default and published titles a type had asked it to hide."""
    acme.set_type_settings('article', tease=False)
    db.session.commit()
    _publish(app, acme, 'Quiet article', 'quiet', visibility='members')

    archive = client.get('/blog', base_url=ACME).get_data(as_text=True)
    per_type = client.get('/blog/feed', base_url=ACME).get_data(as_text=True)
    site_wide = client.get('/feed', base_url=ACME).get_data(as_text=True)

    assert 'Quiet article' not in archive
    assert 'Quiet article' not in per_type
    assert 'Quiet article' not in site_wide


def test_a_type_may_tease_where_the_organization_does_not(app, client, acme):
    """The other direction of the same override, which the fallback also
    got wrong."""
    acme.update_settings(gated_teasers=False)
    acme.set_type_settings('article', tease=True)
    db.session.commit()
    _publish(app, acme, 'Loud article', 'loud', visibility='members')

    titles = _titles(_xml(client, '/feed'))
    assert 'Loud article' in titles


def test_an_existing_item_cannot_be_renamed_onto_the_feed_address(app, acme):
    """The reservation was briefly asked only when SQLAlchemy said the slug
    had changed, and anything earlier in validate() that runs a query
    autoflushes the pending value first -- so the history it read was
    already empty and an edit walked straight past the rule."""
    from app.platform.errors import ValidationError

    with app.test_request_context(base_url=ACME):
        g.org = acme
        stored = Content(type='article', title='Ordinary', slug='ordinary',
                         body='x', status='published')
        stored.save()
        db.session.commit()

        again = Content.query.filter_by(id=stored.id).first()
        db.session.expire(again)          # what a fresh request would hold
        again.slug = 'feed'
        try:
            again.save()
        except ValidationError:
            return
        raise AssertionError('an edit was allowed onto the feed address')


def test_the_sitemap_cap_is_on_the_document_not_on_each_type(app, acme):
    """It took the limit from every type and sliced the combined list
    afterwards, so a large install materialised types-times-limit rows to
    render a file capped well below that."""
    from app.controllers import feeds

    with app.test_request_context(base_url=ACME):
        g.org = acme
        assert len(feeds._sitemap_urls()) <= feeds.SITEMAP_LIMIT

