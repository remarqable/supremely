"""One surface, plus a console: community-native pages and inline controls.

An inline control renders whenever can() allows it. One a member could never
use is marked with .btn-admin rather than hidden, so an admin can see what a
member does not. The marking is presentation only: the backend enforces every
action regardless of what the UI drew.
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


def admin_control(html, label):
    """The markup of the button whose visible text starts with `label`.

    Asserting the page merely contains "btn-admin" somewhere would still pass
    when one control quietly lost its marking, so each is checked on its own.
    """
    import re
    for m in re.finditer(r'<button[^>]*>(.*?)</button>', html, re.S):
        text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        if text.startswith(label):
            return m.group(0)
    raise AssertionError(f'no button labelled {label!r}')


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
    # Group page: the New Post modal replaces the old details-form.
    space = client.get('/discussions/general', base_url=ACME)
    assert b'New Post' in space.data
    # Post page: comments heading + reaction bar render.
    with app.test_request_context(base_url=ACME):
        g.org = acme
        from app.models.discussion import Post
        post = Post.query.filter_by(title='Roadmap question').one()
        url = post.url
    page = client.get(url, base_url=ACME)
    assert page.status_code == 200
    assert b'reaction-bar' in page.data
    assert b'0 replies' in page.data


def test_visitor_discussion_pages_use_the_shell(app, client, acme, user):
    login_as(client, user)
    seed_discussion(client)
    anon = app.test_client()
    index = anon.get('/discussions/', base_url=ACME)
    assert index.status_code == 200
    assert COMMUNITY_MARKER in index.data
    # The seeded General space is members-only: teased by name with a lock,
    # but its post titles stay invisible to visitors.
    assert b'Roadmap question' not in index.data
    assert b'Members only' in index.data


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
        from app.models.discussion import DiscussionGroup
        db.session.add(DiscussionGroup(org_id=acme.id, name='Dev', slug='dev',
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

def test_moderation_controls_need_no_toggle(app, client, acme, user):
    """They used to appear only after flipping manage mode. A moderator now
    sees them on first load, and they carry the admin marking."""
    login_as(client, user)          # owner
    seed_discussion(client)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        from app.models.discussion import Post
        url = Post.query.filter_by(title='Roadmap question').one().url

    html = client.get(url, base_url=ACME).get_data(as_text=True)
    # Every one of them, individually: a page-wide check would still pass if
    # one control quietly lost its marking.
    for label in ('Pin', 'Lock', 'Hide'):
        control = admin_control(html, label)
        assert 'btn-admin' in control, label
        assert 'sr-only' in control, label      # colour is not the only signal


def test_a_plain_member_sees_no_moderation_controls(app, client, acme, user):
    """The guarantee the old forged-session test protected: presentation is not
    permission. A member sees none of it, and the backend refuses anyway."""
    login_as(client, user)
    seed_discussion(client)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        from app.models.discussion import Post
        post = Post.query.filter_by(title='Roadmap question').one()
        url, post_id = post.url, post.id

    member_client = app.test_client()
    make_member(member_client, acme)
    page = member_client.get(url, base_url=ACME).data
    assert b'>Pin</button>' not in page
    assert b'>Lock</button>' not in page
    assert b'btn-admin' not in page

    response = member_client.post(f'/discussions/general/{post_id}/pin',
                                  base_url=ACME)
    assert response.status_code == 403


def test_your_own_content_controls_are_not_marked_admin_only(app, client, acme):
    """Editing or deleting your own post is not an admin action, so it must not
    carry the marking. A member on their own post sees plain controls."""
    member_client = app.test_client()
    make_member(member_client, acme)
    member_client.post('/discussions/general/new', base_url=ACME,
                       data={'title': 'My own thread', 'body': 'Mine.'})
    with app.test_request_context(base_url=ACME):
        g.org = acme
        from app.models.discussion import Post
        url = Post.query.filter_by(title='My own thread').one().url

    page = member_client.get(url, base_url=ACME).data
    assert b'>Delete</button>' in page           # theirs to delete
    assert b'btn-admin' not in page              # but nothing admin-only


def test_the_shell_links_to_the_console_for_an_admin(app, client, acme, user):
    """The console had no entry point from the community shell: the header
    button toggled a mode instead of going there."""
    login_as(client, user)
    page = client.get('/dashboard', base_url=ACME).data
    assert b'href="/manage/"' in page
    assert b'/manage-mode' not in page           # the toggle is gone


def test_the_console_link_is_hidden_from_a_plain_member(app, client, acme):
    make_member(client, acme)
    page = client.get('/dashboard', base_url=ACME).data
    assert b'href="/manage/"' not in page


def test_the_removed_toggle_route_is_gone(app, client, acme, user):
    """405, not 404: with the POST route deleted, the public site's /<seg>
    content route claims the path for GET, so a POST is method-not-allowed."""
    login_as(client, user)
    assert client.post('/manage-mode', base_url=ACME).status_code == 405


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
    gate = anon.get('/newsletters', base_url=ACME)
    assert gate.status_code == 200                     # the gate, not a 404
    assert b'Members only' in gate.data
    assert b'No newsletters have gone out yet.' not in gate.data
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
        delivery = Delivery(org_id=acme.id, content_id=post.id, status='done',
                            recipients_total=3, sent_count=3,
                            finished_at=utcnow())
        db.session.add(delivery)
        db.session.commit()
        title = post.title.encode()
    page = client.get('/newsletters', base_url=ACME)
    assert title in page.data
    assert b'Sent ' in page.data

