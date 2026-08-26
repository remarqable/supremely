"""One surface, two modes: community-native pages and the manage-mode toggle.

Manage mode is presentation state — it surfaces controls the user already
has; it must never grant anything.
"""

from flask import g

from app.extensions import db
from app.models import Content, Membership
from tests.conftest import login_as, make_user

ACME = 'http://acme.example.test'
COMMUNITY_MARKER = b'aria-label="Community navigation"'


def make_member(client, acme, email='member@example.com'):
    member = make_user(email=email)
    Membership.add(member.id, acme.id, role='member')
    login_as(client, member)
    return member


def seed_discussion(client):
    client.post('/discussions/general/new', base_url=ACME,
                data={'title': 'Roadmap question', 'body': 'What about X?'})


# --- Community-native pages -------------------------------------------------

def test_members_get_community_templates(app, client, acme, user):
    login_as(client, user)
    seed_discussion(client)
    # Community discussions index: space cards + recent feed cards.
    index = client.get('/discussions/', base_url=ACME)
    assert b'Roadmap question' in index.data
    assert COMMUNITY_MARKER in index.data
    # Space page: the inline composer replaces the old details-form.
    space = client.get('/discussions/general', base_url=ACME)
    assert b'Share something with your community' in space.data
    # Post page: comments heading + reaction bar render.
    with app.test_request_context(base_url=ACME):
        g.org = acme
        from app.models.discussion import DiscussionPost
        post = DiscussionPost.query.filter_by(title='Roadmap question').one()
        url = post.url
    page = client.get(url, base_url=ACME)
    assert page.status_code == 200
    assert b'reaction-bar' in page.data
    assert b'0 comments' in page.data


def test_visitor_discussion_pages_stay_themed(app, client, acme, user):
    login_as(client, user)
    seed_discussion(client)
    anon = app.test_client()
    index = anon.get('/discussions/', base_url=ACME)
    assert index.status_code == 200
    assert COMMUNITY_MARKER not in index.data
    # The seeded General space is members-only, so its posts stay invisible
    # to visitors — the themed page renders without them.
    assert b'Roadmap question' not in index.data


def test_events_archive_new_button_is_permission_gated(app, client, acme, user):
    login_as(client, user)          # owner: content.write
    page = client.get('/events', base_url=ACME)
    assert b'New Event' in page.data

    member_client = app.test_client()
    make_member(member_client, acme)
    page = member_client.get('/events', base_url=ACME)
    assert page.status_code == 200
    assert COMMUNITY_MARKER in page.data
    assert b'New Event' not in page.data


def test_space_composer_posts_into_that_space(app, client, acme, user):
    login_as(client, user)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        from app.models.discussion import Space
        db.session.add(Space(org_id=acme.id, name='Dev', slug='dev',
                             visibility='members', position=2))
        db.session.commit()
    response = client.post('/discussions/dev/new', base_url=ACME,
                           data={'title': 'From the space composer',
                                 'body': 'Composer body.'})
    assert response.status_code == 302
    page = client.get('/discussions/dev', base_url=ACME)
    assert b'From the space composer' in page.data
    general = client.get('/discussions/general', base_url=ACME)
    assert b'From the space composer' not in general.data


# --- Manage mode ------------------------------------------------------------

def test_plain_members_cannot_toggle_manage_mode(app, client, acme):
    make_member(client, acme)
    response = client.post('/manage-mode', base_url=ACME)
    assert response.status_code == 403
    # The pill is not rendered for members either.
    page = client.get('/dashboard', base_url=ACME)
    assert b'/manage-mode' not in page.data


def test_manage_mode_surfaces_moderation_controls(app, client, acme, user):
    login_as(client, user)          # owner
    seed_discussion(client)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        from app.models.discussion import DiscussionPost
        url = DiscussionPost.query.filter_by(title='Roadmap question').one().url

    normal = client.get(url, base_url=ACME)
    assert b'>Pin</button>' not in normal.data

    assert client.post('/manage-mode', base_url=ACME,
                       data={'next': url}).status_code == 302
    managing = client.get(url, base_url=ACME)
    assert b'>Pin</button>' in managing.data
    assert b'>Lock</button>' in managing.data
    assert b'>Hide</button>' in managing.data

    # Toggle back off: controls collapse again.
    client.post('/manage-mode', base_url=ACME)
    assert b'>Pin</button>' not in client.get(url, base_url=ACME).data


def test_manage_mode_session_grants_nothing_to_members(app, client, acme, user):
    """A smuggled manage_mode flag must not surface (or allow) moderation."""
    login_as(client, user)
    seed_discussion(client)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        from app.models.discussion import DiscussionPost
        post = DiscussionPost.query.filter_by(title='Roadmap question').one()
        url, post_id = post.url, post.id

    member_client = app.test_client()
    make_member(member_client, acme)
    with member_client.session_transaction() as session:
        session['manage_mode'] = True           # forged presentation state
    page = member_client.get(url, base_url=ACME)
    assert b'>Pin</button>' not in page.data
    # And the backend refuses the action itself regardless of any UI state.
    response = member_client.post(f'/discussions/general/{post_id}/pin',
                                  base_url=ACME)
    assert response.status_code == 403


# --- Theme token whitelist ---------------------------------------------------

def test_community_tokens_whitelist(app, acme):
    from app.platform.theming import AVAILABLE_THEMES, community_tokens
    with app.test_request_context(base_url=ACME):
        g.org = acme
        theme = acme.theme or 'origin'
        original = AVAILABLE_THEMES[theme].get('community_tokens')
        AVAILABLE_THEMES[theme]['community_tokens'] = {
            'brand-600': '#112233',                      # approved + valid
            'brand-500': 'red',                          # invalid format
            'font-family': '"evil"; } body { display:none', # not whitelisted
        }
        try:
            assert community_tokens() == {'brand-600': '#112233'}
        finally:
            if original is None:
                AVAILABLE_THEMES[theme].pop('community_tokens', None)
            else:
                AVAILABLE_THEMES[theme]['community_tokens'] = original


# --- Announcements + newsletter archive ---------------------------------------

def test_publish_and_view_announcement(app, client, acme, user):
    login_as(client, user)
    response = client.post('/manage/content/announcement/new', base_url=ACME,
                           data={'title': 'Office closed Friday',
                                 'slug': 'office-closed',
                                 'body': 'See you **Monday**.',
                                 'visibility': 'public', 'action': 'publish'})
    assert response.status_code == 302
    archive = client.get('/announcements', base_url=ACME)
    assert archive.status_code == 200
    assert b'Office closed Friday' in archive.data
    # And it lands in the Home feed.
    assert b'Office closed Friday' in client.get('/dashboard', base_url=ACME).data


def test_newsletter_archive_members_only(app, client, acme, user):
    anon = app.test_client()
    assert anon.get('/newsletters', base_url=ACME).status_code == 404
    login_as(client, user)
    page = client.get('/newsletters', base_url=ACME)
    assert page.status_code == 200
    assert b'No newsletters have gone out yet.' in page.data


def test_newsletter_archive_lists_sent_issues(app, client, acme, user):
    from app.models.base import utcnow
    from app.models.newsletter import Delivery
    login_as(client, user)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        post = Content.query.filter_by(type='article').first()
        delivery = Delivery(org_id=acme.id, post_id=post.id, status='done',
                            recipients_total=3, sent_count=3,
                            finished_at=utcnow())
        db.session.add(delivery)
        db.session.commit()
        title = post.title.encode()
    page = client.get('/newsletters', base_url=ACME)
    assert title in page.data
    assert b'Sent ' in page.data
