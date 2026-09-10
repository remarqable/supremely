"""The right-rail Pinned card.

Pinning a post used to change nothing but the order of one group's own
list, so a pinned post was invisible to anyone who never opened that
group. The card gives it a home on the shell, under Upcoming Event.
"""

from datetime import timedelta

from flask import g

from app.extensions import db
from app.models import DiscussionGroup, Post
from app.models.base import utcnow
from tests.conftest import login_as, make_user

ACME = 'http://acme.example.test'
GLOBEX = 'http://globex.example.test'


def rail(response):
    """The right-rail region of a rendered community page."""
    assert response.status_code == 200
    assert b'id="rail"' in response.data, 'page rendered without the rail'
    return response.data.split(b'id="rail"')[1].split(b'</aside>')[0]


def make_post(app, org, title, group_slug='welcome', pinned=True,
              hidden=False, minutes_ago=0):
    """A post written straight to the database, so a test can set the
    flags and the activity time the card sorts on."""
    with app.test_request_context(base_url=ACME):
        g.org = org
        group = DiscussionGroup.query.filter_by(slug=group_slug).one()
        post = Post(org_id=org.id, group_id=group.id, title=title,
                    body='Body.', is_pinned=pinned, is_hidden=hidden,
                    created_by_id=org.memberships[0].user_id,
                    last_activity_at=utcnow() - timedelta(minutes=minutes_ago))
        db.session.add(post)
        db.session.commit()
        return post.id


def unpin_everything(app, org):
    with app.test_request_context(base_url=ACME):
        g.org = org
        for post in Post.query.all():
            post.is_pinned = False
        db.session.commit()


def set_area_visibility(app, org, value):
    with app.test_request_context(base_url=ACME):
        g.org = org
        settings = dict(org.settings or {})
        settings['discussions_visibility'] = value
        org.settings = settings
        db.session.add(org)
        db.session.commit()


def test_the_card_lists_pinned_posts_under_the_event_card(app, client, acme,
                                                          user):
    login_as(client, user)
    region = rail(client.get('/dashboard', base_url=ACME))
    # Both seeded Welcome posts ship pinned.
    assert b'Welcome to the community' in region
    assert b'Introduce yourself' in region
    # Issue #115 asks for it beneath Upcoming Event, and the rail is one
    # ordered stack, so position is the whole requirement.
    assert region.index(b'Upcoming Event') < region.index(
        b'Welcome to the community')


def test_the_card_names_the_group_a_post_came_from(app, client, acme, user):
    # Pinned posts arrive from anywhere in the community, so the card says
    # where each one lives. The group is named after nothing else on the
    # page, so this cannot pass on some other rendering of the same word.
    unpin_everything(app, acme)
    with app.test_request_context(base_url=ACME):
        g.org = acme
        DiscussionGroup(org_id=acme.id, name='Trailhead Planning',
                        slug='trailhead', visibility='members').save()
    make_post(app, acme, 'Where we are headed', group_slug='trailhead')
    login_as(client, user)
    region = rail(client.get('/dashboard', base_url=ACME))
    assert b'Where we are headed' in region
    assert b'Trailhead Planning' in region


def test_an_unpinned_post_stays_out(app, client, acme, user):
    make_post(app, acme, 'Just an ordinary post', pinned=False)
    login_as(client, user)
    assert b'Just an ordinary post' not in rail(
        client.get('/dashboard', base_url=ACME))


def test_the_card_disappears_when_nothing_is_pinned(app, client, acme, user):
    login_as(client, user)
    assert b'Pinned' in rail(client.get('/dashboard', base_url=ACME))
    unpin_everything(app, acme)
    assert b'Pinned' not in rail(client.get('/dashboard', base_url=ACME))


def test_a_hidden_post_stays_out_even_from_a_moderator(app, client, acme,
                                                       user):
    make_post(app, acme, 'Pinned then hidden', hidden=True)
    login_as(client, user)                       # owner: content.moderate
    assert b'Pinned then hidden' not in rail(
        client.get('/dashboard', base_url=ACME))


def test_a_visitor_never_sees_a_title_from_a_gated_group(app, client, acme):
    # 'general' is members-only in every seeded community.
    make_post(app, acme, 'Members only business', group_slug='general')
    region = rail(client.get('/discussions/', base_url=ACME))
    assert b'Members only business' not in region
    # ...while the public group's pinned posts still show.
    assert b'Welcome to the community' in region


def test_a_member_does_see_the_gated_group_title(app, client, acme):
    member = make_user(email='mel@example.com', name='Mel')
    from app.models import Membership
    Membership.add(member.id, acme.id, role='member')
    make_post(app, acme, 'Members only business', group_slug='general')
    login_as(client, member)
    assert b'Members only business' in rail(
        client.get('/dashboard', base_url=ACME))


def test_gating_the_whole_area_empties_the_card_for_visitors(app, client,
                                                             acme):
    # The Welcome group is public, so its pinned posts reach a visitor...
    # /members, not /discussions: a gated area renders its own gate page in
    # the shell, and the question here is only what the rail does.
    assert b'Welcome to the community' in rail(
        client.get('/members', base_url=ACME))
    # ...until the org closes the whole discussions area, which overrides
    # each group's own setting.
    set_area_visibility(app, acme, 'members')
    assert b'Welcome to the community' not in rail(
        client.get('/members', base_url=ACME))


def test_one_community_never_shows_another_ones_pinned_post(app, client, acme,
                                                            globex, user):
    with app.test_request_context(base_url=GLOBEX):
        g.org = globex
        group = DiscussionGroup.query.filter_by(slug='welcome').one()
        db.session.add(Post(org_id=globex.id, group_id=group.id,
                            title='Globex only', body='Body.',
                            is_pinned=True,
                            created_by_id=globex.memberships[0].user_id))
        db.session.commit()
    login_as(client, user)
    assert b'Globex only' not in rail(client.get('/dashboard', base_url=ACME))


def test_the_card_stops_at_three(app, client, acme, user):
    for n in range(4):
        make_post(app, acme, f'Pinned number {n}')
    login_as(client, user)
    region = rail(client.get('/dashboard', base_url=ACME))
    assert region.count(b'Pinned number ') == 3


def test_the_most_recently_active_post_leads(app, client, acme, user):
    unpin_everything(app, acme)
    make_post(app, acme, 'Quiet for a week', minutes_ago=10_080)
    make_post(app, acme, 'Active this minute', minutes_ago=0)
    login_as(client, user)
    region = rail(client.get('/dashboard', base_url=ACME))
    assert region.index(b'Active this minute') < region.index(
        b'Quiet for a week')


def test_a_pinned_title_is_escaped(app, client, acme, user):
    make_post(app, acme, '<script>alert(1)</script>')
    login_as(client, user)
    region = rail(client.get('/dashboard', base_url=ACME))
    assert b'<script>alert(1)</script>' not in region
    assert b'&lt;script&gt;' in region
