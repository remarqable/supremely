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


def test_home_splits_feed_and_published(app, client, acme, user):
    login_as(client, user)
    response = client.get('/dashboard', base_url=ACME)
    assert response.status_code == 200
    # Org column: published content under "Latest from", announcements only
    # in their own card (not duplicated in the list).
    assert b'Latest from Acme' in response.data
    assert b'Hello, World!' in response.data
    assert b'Kickoff meetup' in response.data
    assert response.data.count(b'Welcome to Acme') == 1   # announcement card
    # Feed column: seeded forum posts; New Post trigger; no tabs.
    assert b'Introduce yourself' in response.data
    assert b'New Post' in response.data
    assert b'?tab=' not in response.data


def test_publish_menu_is_permission_gated(app, client, acme, user):
    login_as(client, user)                # owner: content.write
    page = client.get('/dashboard', base_url=ACME)
    assert b'>Publish' in page.data or b'Publish\n' in page.data
    assert b'/manage/content/article/new' in page.data
    assert b'/manage/content/recording/new' in page.data

    from app.models import Membership
    from tests.conftest import make_user
    member = make_user(email='pm@example.com')
    Membership.add(member.id, acme.id, role='member')
    member_client = app.test_client()
    login_as(member_client, member)
    page = member_client.get('/dashboard', base_url=ACME)
    assert b'/manage/content/article/new' not in page.data




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
    for base, label in ((b'/blog', b'Articles'), (b'/events', b'Events'),
                        (b'/announcements', b'Announcements'),
                        (b'/recordings', b'Videos'),
                        (b'/podcast', b'Podcast'), (b'/resources', b'Resources')):
        assert b'href="' + base + b'"' in response.data, base
        assert label in response.data, label


def test_sidebar_groups_and_fixed_items(app, client, acme, user):
    """Community -> Meet -> Learn in order; Start Here, Newsletters archive,
    and Settings present; the public /subscribe page is out of the sidebar."""
    login_as(client, user)
    html = client.get('/dashboard', base_url=ACME).data
    community = html.index(b'>Community</div>')
    meet = html.index(b'>Meet</div>')
    learn = html.index(b'>Learn</div>')
    assert community < meet < learn
    assert b'Start Here' in html and b'href="/about"' in html
    assert b'href="/newsletters"' in html
    assert b'href="/subscribe"' not in html
    assert b'Settings' in html


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


def test_visitors_get_the_shell_on_community_surfaces(client, acme):
    # The shell serves everyone; only the front page (and previews) stay
    # themed. Visitors browse the same layout members use, with gated
    # content teased in place.
    for path in ('/discussions/', '/events', '/subscribe'):
        response = client.get(path, base_url=ACME, follow_redirects=True)
        assert response.status_code == 200, path
        assert SHELL_MARKER in response.data, path
    front = client.get('/', base_url=ACME)
    assert SHELL_MARKER not in front.data


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


# --- Identity chrome ----------------------------------------------------------

def test_shell_navbar_centers_name_and_drops_section_links(client, acme, user):
    login_as(client, user)
    page = client.get('/dashboard', base_url=ACME).data
    assert b'>Acme</a>' in page                    # centered name -> public site
    assert b'href="/manage"' not in page           # sidebar owns navigation
    assert b'/launcher' not in page                # single membership: no switcher


def test_switcher_appears_with_multiple_memberships(app, client, acme, globex, user):
    from app.models import Membership
    Membership.add(user.id, globex.id, role='member')
    login_as(client, user)
    page = client.get('/dashboard', base_url=ACME).data
    assert b'/launcher' in page                    # avatar menu shows it now


def test_console_keeps_full_navbar(client, acme, user):
    login_as(client, user)
    page = client.get('/manage/content/page', base_url=ACME).data
    assert b'href="/dashboard"' in page            # console navbar unchanged
