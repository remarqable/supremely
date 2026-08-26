"""The community home: one blended feed, composer, and right-rail widgets."""

from datetime import date, timedelta

from flask import g

from app.extensions import db
from app.models import Content
from tests.conftest import login_as

ACME = 'http://acme.example.test'


def _publish(app, **kwargs):
    with app.test_request_context(base_url=ACME):
        from app.models import Organization
        g.org = Organization.get_by_slug('acme')
        kwargs.setdefault('fields', {})
        kwargs.setdefault('tags', [])
        content = Content(status='published', visibility='public', **kwargs)
        from app.models.base import utcnow
        content.published_at = utcnow()
        db.session.add(content)
        db.session.commit()
        return content.id


def test_dashboard_requires_membership(client, acme, globex):
    hank = globex.memberships[0].user
    login_as(client, hank)
    assert client.get('/dashboard', base_url=ACME).status_code == 404


def test_feed_mixes_discussions_and_content(app, client, acme, user):
    login_as(client, user)
    response = client.get('/dashboard', base_url=ACME)
    assert response.status_code == 200
    # Seeded org: first article + kickoff event in the feed (posts side).
    assert b'Hello, World!' in response.data
    assert b'Kickoff meetup' in response.data
    # Composer renders; the tab bar is gone (sidebar sections replaced it).
    assert b'Share something with your community' in response.data
    assert b'?tab=' not in response.data




def test_upcoming_event_card(app, client, acme, user):
    login_as(client, user)
    future = (date.today() + timedelta(days=30)).isoformat()
    _publish(app, type='event', title='Community Call', slug='community-call',
             body='x', fields={'starts_on': future, 'location': 'Online'})
    response = client.get('/dashboard', base_url=ACME)
    assert b'Upcoming Event' in response.data
    assert b'Community Call' in response.data
    assert b'View all events' in response.data


def test_past_events_do_not_appear_as_upcoming(app, client, acme, user):
    login_as(client, user)
    # The seeded kickoff event is dated today, so it IS upcoming; replace its
    # date with the past.
    with app.test_request_context(base_url=ACME):
        g.org = acme
        event = Content.query.filter_by(type='event').first()
        event.fields = {'starts_on': '2020-01-01'}
        db.session.commit()
    response = client.get('/dashboard', base_url=ACME)
    assert b'Upcoming Event' not in response.data


def test_composer_posts_into_selected_space(app, client, acme, user):
    login_as(client, user)
    response = client.post('/discussions/general/new', base_url=ACME,
                           data={'title': 'Posted from the composer',
                                 'body': 'Hello from the home feed.'})
    assert response.status_code == 302
    feed = client.get('/dashboard', base_url=ACME)
    assert b'Posted from the composer' in feed.data


def test_sidebar_lists_content_type_sections(app, client, acme, user):
    """The sidebar mirrors the org's feed content types as destinations."""
    login_as(client, user)
    response = client.get('/dashboard', base_url=ACME)
    for base, label in ((b'/blog', b'Blog'), (b'/events', b'Events'),
                        (b'/recordings', b'Recordings'),
                        (b'/podcast', b'Podcast'), (b'/resources', b'Resources')):
        assert b'href="' + base + b'"' in response.data, base
        assert label in response.data, label


def test_members_widget_counts(app, client, acme, user):
    login_as(client, user)
    response = client.get('/dashboard', base_url=ACME)
    assert b'1 members' in response.data


SHELL_MARKER = b'aria-label="Community navigation"'


def test_members_see_community_pages_in_the_shell(client, acme, user):
    login_as(client, user)
    for path in ('/discussions/', '/members', '/events', '/subscribe'):
        response = client.get(path, base_url=ACME, follow_redirects=True)
        assert response.status_code == 200, path
        assert SHELL_MARKER in response.data, path


def test_visitors_see_the_theme_not_the_shell(client, acme):
    for path in ('/discussions/', '/events', '/subscribe'):
        response = client.get(path, base_url=ACME, follow_redirects=True)
        assert response.status_code == 200, path
        assert SHELL_MARKER not in response.data, path


def test_front_page_stays_themed_for_members(client, acme, user):
    login_as(client, user)
    response = client.get('/', base_url=ACME)
    assert response.status_code == 200
    assert SHELL_MARKER not in response.data


def test_preview_stays_themed_for_members(app, client, acme, user):
    login_as(client, user)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        page_id = Content.query.filter_by(type='page', slug='about').first().id
    response = client.get(f'/manage/content/{page_id}/preview', base_url=ACME)
    assert response.status_code == 200
    assert SHELL_MARKER not in response.data
