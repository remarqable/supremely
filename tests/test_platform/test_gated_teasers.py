"""What a tease may show. The community shell serves visitors as well as
members, so every surface that lists gated items has to stop at the title:
never a body, an excerpt, or a structured field."""

from app.extensions import db
from app.models import Content
from app.platform.authz import can_view

ACME = 'http://acme.example.test'


def _publish(org, type_slug, slug=None, **kwargs):
    """Publish a seeded row of this type. Ordered, and addressable by
    slug, so the row picked does not depend on the database."""
    query = Content.query.filter_by(org_id=org.id, type=type_slug)
    if slug is not None:
        query = query.filter_by(slug=slug)
    item = query.order_by(Content.id).first()
    assert item is not None, f'no seeded {type_slug} to publish'
    item.status = 'published'
    for key, value in kwargs.items():
        setattr(item, key, value)
    db.session.add(item)
    db.session.commit()
    return item


def test_gated_event_teases_its_title_but_not_its_date(app, client, acme):
    """The date chip used to render before the lock was worked out, so a
    members-only event announced when it was happening."""
    with app.test_request_context(base_url=ACME):
        from flask import g
        g.org = acme
        _publish(acme, 'event', title='GATED-EVENT', visibility='members',
                 fields={'starts_on': '2026-07-14', 'location': 'THE-VENUE'})

    body = client.get('/events', base_url=ACME).data.decode()
    assert 'GATED-EVENT' in body          # the tease is the point
    assert 'JUL' not in body
    assert 'THE-VENUE' not in body


def test_public_event_still_shows_its_date_and_location(app, client, acme):
    with app.test_request_context(base_url=ACME):
        from flask import g
        g.org = acme
        _publish(acme, 'event', title='OPEN-EVENT', visibility='public',
                 fields={'starts_on': '2026-07-14', 'location': 'THE-VENUE'})

    body = client.get('/events', base_url=ACME).data.decode()
    assert 'JUL' in body
    assert 'THE-VENUE' in body


def test_sidebar_hides_start_here_when_the_about_page_is_gated(app, client, acme):
    with app.test_request_context(base_url=ACME):
        from flask import g
        g.org = acme
        _publish(acme, 'page', slug='about', visibility='members')

    assert '/about' not in client.get('/discussions/', base_url=ACME).data.decode()

    with app.test_request_context(base_url=ACME):
        from flask import g
        g.org = acme
        _publish(acme, 'page', slug='about', visibility='public')

    assert '/about' in client.get('/discussions/', base_url=ACME).data.decode()


def test_a_locked_section_teases_nothing_anywhere(app, client, acme):
    """The archive already gated a locked section without naming items; the
    item URL still named the item."""
    with app.test_request_context(base_url=ACME):
        from flask import g
        g.org = acme
        article = _publish(acme, 'article', title='SECTION-LOCKED-TITLE',
                           visibility='public')
        permalink = article.permalink
        acme.update_settings(section_visibility={'article': 'members'})
        db.session.commit()

    assert 'SECTION-LOCKED-TITLE' not in client.get('/blog', base_url=ACME).data.decode()
    assert 'SECTION-LOCKED-TITLE' not in client.get(permalink, base_url=ACME).data.decode()


def test_can_view_answers_rather_than_raising_without_a_visitor(app, acme):
    """A real row with a real policy: outside a request there is no
    visitor to answer for, so the answer is no rather than a crash."""
    row = Content.query.filter_by(org_id=acme.id, type='article').first()
    row.visibility = 'public'
    assert can_view(row) is False
    assert can_view(object()) is False


def test_can_view_refuses_another_tenants_row(app, acme, globex):
    with app.test_request_context(base_url=ACME):
        from flask import g
        g.org = acme
        foreign = Content(org_id=globex.id, type='article', title='X',
                          slug='foreign-row', body='y', status='published',
                          visibility='public')
        assert can_view(foreign) is False

        own = Content.query.filter_by(org_id=acme.id, type='article').first()
        own.visibility = 'public'
        assert can_view(own) is True


def test_a_locked_page_section_teases_nothing_at_the_page_url(app, client, acme):
    """The other half of the section lock: pages are reached at /<slug>, and
    that path gates separately from the archive."""
    with app.test_request_context(base_url=ACME):
        from flask import g
        g.org = acme
        _publish(acme, 'page', slug='about', title='PAGE-SECTION-LOCKED',
                 visibility='public')
        acme.update_settings(section_visibility={'page': 'members'})
        db.session.commit()

    body = client.get('/about', base_url=ACME).data.decode()
    assert 'PAGE-SECTION-LOCKED' not in body
