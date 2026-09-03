"""Phase 5: discussions. Completion test: a small organization uses Supremely
as its primary asynchronous community discussion system."""

from flask import g

from app.extensions import db
from app.models import (
    DiscussionGroup,
    Flag,
    Membership,
    Notification,
    Post,
    PostFollow,
    Reply,
)
from tests.conftest import login_as, make_user

ACME = 'http://acme.example.test'


def make_group(app, org, slug='general', name='General', visibility='members'):
    """Fetch-or-create: new orgs come seeded with a 'general' group, so tests
    reuse it (and can flip its visibility)."""
    with app.test_request_context():
        g.org = org
        existing = DiscussionGroup.query.filter_by(slug=slug).first()
        if existing:
            existing.name = name
            existing.visibility = visibility
            return existing.save()
        return DiscussionGroup(name=name, slug=slug, org_id=org.id,
                     visibility=visibility).save()


def member_client(app, org, email):
    user = make_user(email=email)
    Membership.add(user.id, org.id, role='member')
    client = app.test_client()
    return login_as(client, user), user


def create_post(client, group_slug='general', title='First post',
                 body='Hello everyone'):
    return client.post(f'/discussions/{group_slug}/new', base_url=ACME,
                       data={'title': title, 'body': body})


def test_reaction_htmx_swaps_bar_only(app, client, acme, globex, user):
    make_group(app, acme)
    owner_client = app.test_client()
    login_as(owner_client, user)
    create_post(owner_client)
    post = Post.query.order_by(Post.id.desc()).first()

    # HTMX request gets the reaction-bar fragment, not a full page or redirect
    response = owner_client.post('/discussions/react', base_url=ACME,
                                 data={'target_type': 'post',
                                       'target_id': post.id, 'emoji': '👍'},
                                 headers={'HX-Request': 'true'})
    assert response.status_code == 200
    assert b'reaction-bar' in response.data
    assert b'<!DOCTYPE html>' not in response.data
    assert '👍 1'.encode() in response.data

    # Non-HTMX still redirects (progressive enhancement)
    plain = owner_client.post('/discussions/react', base_url=ACME,
                              data={'target_type': 'post',
                                    'target_id': post.id, 'emoji': '👍'})
    assert plain.status_code == 302


def test_full_discussion_flow(app, client, acme, globex, user):
    """Owner creates a group; two members hold a threaded conversation."""
    make_group(app, acme)
    alice_client, alice = member_client(app, acme, 'alice@example.com')
    bob_client, _bob = member_client(app, acme, 'bob@example.com')

    # Alice starts a post
    response = create_post(alice_client, title='Welcome thread',
                            body='Say hi **here**.')
    assert response.status_code == 302
    post = Post.query.filter_by(title='Welcome thread').first()
    assert post is not None
    assert post.created_by_id == alice.id
    assert PostFollow.is_following(alice.id, post.id)   # auto-follow

    # Bob replies
    bob_client.post(f'/discussions/general/{post.id}/reply', base_url=ACME,
                    data={'body': 'Hi Alice!'})
    reply = Reply.query.filter_by(post_id=post.id).first()
    assert reply is not None

    # One-level threading: Alice answers Bob's reply
    alice_client.post(f'/discussions/general/{post.id}/reply', base_url=ACME,
                      data={'body': 'Welcome Bob', 'parent_id': reply.id})
    page = alice_client.get(f'/discussions/general/{post.id}', base_url=ACME)
    assert b'Hi Alice!' in page.data
    assert b'Welcome Bob' in page.data
    assert db.session.get(Post, post.id).reply_count == 2

    # Alice was notified of Bob's reply (author + follower dedup: one entry)
    notes = Notification.query.filter_by(user_id=alice.id).all()
    assert len(notes) == 1
    assert notes[0].type == 'reply.to_author'

    # Reaction
    bob_client.post('/discussions/react', base_url=ACME, data={
        'target_type': 'post', 'target_id': post.id, 'emoji': '👍'})
    page = bob_client.get(f'/discussions/general/{post.id}', base_url=ACME)
    assert '👍 1'.encode() in page.data


def test_second_level_nesting_rejected(app, client, acme, globex, user):
    make_group(app, acme)
    alice_client, _alice = member_client(app, acme, 'a2@example.com')
    create_post(alice_client)
    post = Post.query.order_by(Post.id.desc()).first()
    alice_client.post(f'/discussions/general/{post.id}/reply', base_url=ACME,
                      data={'body': 'level 1'})
    first = Reply.query.first()
    alice_client.post(f'/discussions/general/{post.id}/reply', base_url=ACME,
                      data={'body': 'level 2', 'parent_id': first.id})
    second = Reply.query.filter_by(parent_id=first.id).first()
    response = alice_client.post(f'/discussions/general/{post.id}/reply',
                                 base_url=ACME,
                                 data={'body': 'level 3',
                                       'parent_id': second.id},
                                 follow_redirects=True)
    assert b'one level' in response.data


def test_members_space_teased_to_anonymous(app, client, acme, globex, user):
    """Tease-don't-hide: the group is listed by name with a lock, but its
    post titles stay gated and a direct hit lands on the gate page."""
    make_group(app, acme, visibility='members')
    owner_client = app.test_client()
    login_as(owner_client, user)
    create_post(owner_client, title='Secret plans')

    listing = client.get('/discussions/', base_url=ACME)
    assert b'General' in listing.data            # teased by name
    assert b'Members only' in listing.data       # with a lock badge
    assert b'Secret plans' not in listing.data   # post titles stay gated

    gate = client.get('/discussions/general', base_url=ACME)
    assert gate.status_code == 200
    assert b'Secret plans' not in gate.data
    assert b'Members only' in gate.data
    assert b'/auth/login' in gate.data

    # The post URL gates on the group without confirming the post exists.
    post = Post.query.order_by(Post.id.desc()).first()
    gate = client.get(f'/discussions/general/{post.id}', base_url=ACME)
    assert gate.status_code == 200
    assert b'Secret plans' not in gate.data


def set_area(app, org, value):
    with app.test_request_context():
        g.org = org
        org.update_settings(discussions_visibility=value)


def test_area_members_gates_the_whole_area(app, client, acme, globex, user):
    """discussions_visibility='members': one gate for the area — even public
    groups and their names are withheld from visitors; members unaffected."""
    make_group(app, acme, visibility='public')
    owner_client = app.test_client()
    login_as(owner_client, user)
    create_post(owner_client, title='Open thread')
    set_area(app, acme, 'members')

    listing = client.get('/discussions/', base_url=ACME)
    assert listing.status_code == 200
    assert b'Members only' in listing.data
    assert b'Open thread' not in listing.data
    group_page = client.get('/discussions/general', base_url=ACME)
    assert group_page.status_code == 200
    assert b'Open thread' not in group_page.data

    member_view = owner_client.get('/discussions/general', base_url=ACME)
    assert b'Open thread' in member_view.data


def test_area_public_opens_members_groups(app, client, acme, globex, user):
    """discussions_visibility='public': the whole area is public, group
    settings notwithstanding."""
    make_group(app, acme, visibility='members')
    owner_client = app.test_client()
    login_as(owner_client, user)
    create_post(owner_client, title='Open to all now')
    set_area(app, acme, 'public')

    page = client.get('/discussions/general', base_url=ACME)
    assert page.status_code == 200
    assert b'Open to all now' in page.data
    listing = client.get('/discussions/', base_url=ACME)
    assert b'Open to all now' in listing.data


def test_manage_group_lock_toggle(app, client, acme, globex, user):
    group = make_group(app, acme, visibility='members')
    group_id = group.id
    login_as(client, user)                       # acme's owner
    response = client.post(f'/manage/discussions/{group_id}/visibility',
                           base_url=ACME)
    assert response.status_code == 302
    with app.test_request_context():
        g.org = acme
        assert db.session.get(DiscussionGroup, group_id).visibility == 'public'
    client.post(f'/manage/discussions/{group_id}/visibility', base_url=ACME)
    with app.test_request_context():
        g.org = acme
        assert db.session.get(DiscussionGroup,
                              group_id).visibility == 'members'


def test_manage_group_reorder(app, client, acme, globex, user):
    """An admin can move a group up or down; groups that share a position
    (seeded together) are renumbered so the move is visible."""
    general = make_group(app, acme, slug='general', name='General')
    help_ = make_group(app, acme, slug='help', name='Help')
    other = make_group(app, globex, slug='other', name='Other')
    general_id, help_id, other_id = general.id, help_.id, other.id
    with app.test_request_context():
        g.org = globex
        db.session.expunge(db.session.get(DiscussionGroup, other_id))  # requests never share an identity map

    def order():
        with app.test_request_context():
            g.org = acme
            return [x.slug for x in DiscussionGroup.in_order()]

    with app.test_request_context():
        g.org = acme
        # Two groups tied on position, so name decides: General before Help.
        for group_id in (general_id, help_id):
            db.session.get(DiscussionGroup, group_id).position = 0
        db.session.commit()
    assert order()[:2] == ['general', 'help']
    login_as(client, user)                       # acme's owner
    response = client.post(f'/manage/discussions/{help_id}/move',
                           base_url=ACME, data={'direction': 'up'})
    assert response.status_code == 302
    assert order()[:2] == ['help', 'general']
    # Moving the top group up again is a no-op, not an error.
    client.post(f'/manage/discussions/{help_id}/move', base_url=ACME,
                data={'direction': 'up'})
    assert order()[:2] == ['help', 'general']
    client.post(f'/manage/discussions/{help_id}/move', base_url=ACME,
                data={'direction': 'down'})
    assert order()[:2] == ['general', 'help']
    # Another tenant's group is out of reach.
    response = client.post(f'/manage/discussions/{other_id}/move',
                           base_url=ACME, data={'direction': 'up'})
    assert response.status_code == 404
    page = client.get('/manage/discussions', base_url=ACME)
    assert b'Move up' in page.data and b'Move down' in page.data


def test_manage_area_visibility_switch(app, client, acme, globex, user):
    login_as(client, user)                       # acme's owner
    response = client.post('/manage/discussions', base_url=ACME,
                           data={'area_visibility': 'members'})
    assert response.status_code == 302
    assert acme.setting('discussions_visibility') == 'members'
    # Unknown values are ignored, not stored.
    client.post('/manage/discussions', base_url=ACME,
                data={'area_visibility': 'bogus'})
    assert acme.setting('discussions_visibility') == 'members'


def test_teasers_off_hides_gated_groups(app, client, acme, globex, user):
    """The org's tease switch off: gated groups vanish from the listing and
    their pages degrade to login redirect / 404 instead of the gate."""
    make_group(app, acme, visibility='members')
    with app.test_request_context():
        g.org = acme
        acme.update_settings(gated_teasers=False)

    listing = client.get('/discussions/', base_url=ACME)
    assert listing.status_code == 200
    assert b'General' not in listing.data
    response = client.get('/discussions/general', base_url=ACME)
    assert response.status_code == 302
    assert '/auth/login' in response.headers['Location']

    outsider_client = app.test_client()
    login_as(outsider_client, make_user(email='out2@example.com'))
    assert outsider_client.get('/discussions/general',
                               base_url=ACME).status_code == 404


def test_members_space_search_leaks_nothing(app, client, acme, globex, user):
    make_group(app, acme, visibility='members')
    owner_client = app.test_client()
    login_as(owner_client, user)
    create_post(owner_client, title='Secret plans', body='Hidden agenda')
    results = client.get('/discussions/?q=Secret', base_url=ACME)
    assert b'Secret plans' not in results.data


def test_public_space_readable_not_writable(app, client, acme, globex, user):
    make_group(app, acme, visibility='public')
    owner_client = app.test_client()
    login_as(owner_client, user)
    create_post(owner_client, title='Open thread')
    post = Post.query.order_by(Post.id.desc()).first()

    anon_view = client.get(f'/discussions/general/{post.id}', base_url=ACME)
    assert anon_view.status_code == 200
    assert b'Open thread' in anon_view.data
    # Anonymous posting refused (redirects to login)
    response = client.post(f'/discussions/general/{post.id}/reply',
                           base_url=ACME, data={'body': 'spam'})
    assert response.status_code == 302
    assert Reply.query.count() == 0


def test_lock_stops_replies(app, client, acme, globex, user):
    make_group(app, acme)
    owner_client = app.test_client()
    login_as(owner_client, user)
    create_post(owner_client)
    post = Post.query.order_by(Post.id.desc()).first()

    owner_client.post(f'/discussions/general/{post.id}/lock', base_url=ACME)
    assert db.session.get(Post, post.id).is_locked

    member, _ = member_client(app, acme, 'm@example.com')
    member.post(f'/discussions/general/{post.id}/reply', base_url=ACME,
                data={'body': 'too late'})
    assert Reply.query.count() == 0


def test_hidden_topic_invisible_to_members(app, client, acme, globex, user):
    make_group(app, acme)
    owner_client = app.test_client()
    login_as(owner_client, user)
    create_post(owner_client, title='Bad post')
    post = Post.query.order_by(Post.id.desc()).first()
    owner_client.post(f'/discussions/general/{post.id}/hide', base_url=ACME)

    member, _ = member_client(app, acme, 'm4@example.com')
    assert member.get(f'/discussions/general/{post.id}',
                      base_url=ACME).status_code == 404
    listing = member.get('/discussions/general', base_url=ACME)
    assert b'Bad post' not in listing.data
    # Moderator still sees it
    assert owner_client.get(f'/discussions/general/{post.id}',
                            base_url=ACME).status_code == 200


def test_member_cannot_moderate(app, client, acme, globex, user):
    make_group(app, acme)
    owner_client = app.test_client()
    login_as(owner_client, user)
    create_post(owner_client)
    post = Post.query.order_by(Post.id.desc()).first()
    member, _ = member_client(app, acme, 'm5@example.com')
    assert member.post(f'/discussions/general/{post.id}/lock',
                       base_url=ACME).status_code == 403


def test_edit_own_only(app, client, acme, globex, user):
    make_group(app, acme)
    alice_client, _alice = member_client(app, acme, 'a6@example.com')
    bob_client, _bob = member_client(app, acme, 'b6@example.com')
    create_post(alice_client, title='Mine')
    post = Post.query.order_by(Post.id.desc()).first()

    assert bob_client.post(f'/discussions/general/{post.id}/edit',
                           base_url=ACME,
                           data={'title': 'Hijacked', 'body': 'x'}
                           ).status_code == 403
    alice_client.post(f'/discussions/general/{post.id}/edit', base_url=ACME,
                      data={'title': 'Mine v2', 'body': 'updated'})
    assert db.session.get(Post, post.id).title == 'Mine v2'


def test_mention_notification(app, client, acme, globex, user):
    make_group(app, acme)
    alice_client, alice = member_client(app, acme, 'alice7@example.com')
    bob_client, _bob = member_client(app, acme, 'bob7@example.com')
    create_post(alice_client, title='T')
    post = Post.query.order_by(Post.id.desc()).first()
    # Bob mentions alice7 by email local part
    bob_client.post(f'/discussions/general/{post.id}/reply', base_url=ACME,
                    data={'body': 'ping @alice7 what do you think?'})
    mention = Notification.query.filter_by(user_id=alice.id,
                                           type='mention').first()
    assert mention is not None


def test_follow_notification_and_unread_flow(app, client, acme, globex, user):
    make_group(app, acme)
    alice_client, _alice = member_client(app, acme, 'a8@example.com')
    bob_client, _bob = member_client(app, acme, 'b8@example.com')
    carol_client, carol = member_client(app, acme, 'c8@example.com')

    create_post(alice_client)
    post = Post.query.order_by(Post.id.desc()).first()
    # Carol follows without replying
    carol_client.post(f'/discussions/general/{post.id}/follow', base_url=ACME)
    bob_client.post(f'/discussions/general/{post.id}/reply', base_url=ACME,
                    data={'body': 'news!'})

    assert Notification.query.filter_by(user_id=carol.id,
                                        type='reply.followed').count() == 1
    assert Notification.unread_count(carol.id) == 1

    # Navbar badge + notifications page
    page = carol_client.get('/notifications/', base_url=ACME)
    assert b'news!' in page.data
    carol_client.post('/notifications/read-all', base_url=ACME)
    assert Notification.unread_count(carol.id) == 0


def test_flag_and_moderation_queue(app, client, acme, globex, user):
    make_group(app, acme)
    alice_client, _alice = member_client(app, acme, 'a9@example.com')
    create_post(alice_client, title='Spammy')
    post = Post.query.order_by(Post.id.desc()).first()
    alice_client.post('/discussions/flag', base_url=ACME, data={
        'target_type': 'post', 'target_id': post.id, 'reason': 'spam'})
    assert Flag.query.filter_by(resolved_at=None).count() == 1

    owner_client = app.test_client()
    login_as(owner_client, user)
    queue = owner_client.get('/manage/flags', base_url=ACME)
    assert b'Spammy' in queue.data
    flag = Flag.query.first()
    owner_client.post(f'/manage/flags/{flag.id}/resolve', base_url=ACME)
    assert Flag.query.filter_by(resolved_at=None).count() == 0


def test_search(app, client, acme, globex, user):
    make_group(app, acme)
    owner_client = app.test_client()
    login_as(owner_client, user)
    create_post(owner_client, title='Deployment woes', body='gunicorn hangs')
    create_post(owner_client, title='Cooking tips', body='use salt')
    results = owner_client.get('/discussions/?q=gunicorn', base_url=ACME)
    assert b'Deployment woes' in results.data
    assert b'Cooking tips' not in results.data


def test_discussions_tenant_isolated(app, client, acme, globex, user):
    make_group(app, acme, slug='general')
    with app.test_request_context():
        g.org = globex
        DiscussionGroup(name='Globex Private', slug='globex-private',
              org_id=globex.id, visibility='public').save()
    owner_client = app.test_client()
    login_as(owner_client, user)
    listing = owner_client.get('/discussions/', base_url=ACME)
    assert b'Globex Private' not in listing.data
    assert owner_client.get('/discussions/globex-private',
                            base_url=ACME).status_code == 404


def test_email_job_enqueued_only_when_configured(app, client, acme, globex, user):
    from app.models import InstallationSetting, Job
    make_group(app, acme)
    alice_client, _alice = member_client(app, acme, 'a10@example.com')
    bob_client, _bob = member_client(app, acme, 'b10@example.com')
    create_post(alice_client)
    post = Post.query.order_by(Post.id.desc()).first()

    bob_client.post(f'/discussions/general/{post.id}/reply', base_url=ACME,
                    data={'body': 'no email configured'})
    assert Job.query.filter_by(name='notifications.email').count() == 0

    InstallationSetting.set('email.smtp_host', 'smtp.test')
    InstallationSetting.set('email.from_address', 'noreply@test')
    bob_client.post(f'/discussions/general/{post.id}/reply', base_url=ACME,
                    data={'body': 'now with email'})
    assert Job.query.filter_by(name='notifications.email').count() >= 1


# --- Directory / dense-list layout ------------------------------------------

def test_index_is_a_group_directory(app, client, acme, globex, user):
    make_group(app, acme)
    owner_client = app.test_client()
    login_as(owner_client, user)
    create_post(owner_client, title='Latest thing', body='hello')
    page = owner_client.get('/discussions/', base_url=ACME)
    # Group row with post count and last-activity meta; no composer here.
    assert b'General' in page.data
    assert b'3 posts' in page.data
    assert b'Latest thing' in page.data          # last-activity line
    assert b'New Post' in page.data              # modal trigger


def test_reply_sort_toggle(app, client, acme, globex, user):
    make_group(app, acme)
    owner_client = app.test_client()
    login_as(owner_client, user)
    create_post(owner_client, title='Sortable', body='x')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        post = Post.query.filter_by(title='Sortable').one()
        url = post.url
    owner_client.post(f'{url}/reply', base_url=ACME, data={'body': 'first reply'})
    owner_client.post(f'{url}/reply', base_url=ACME, data={'body': 'second reply'})

    oldest = owner_client.get(url, base_url=ACME).data
    assert oldest.index(b'first reply') < oldest.index(b'second reply')
    newest = owner_client.get(f'{url}?sort=newest', base_url=ACME).data
    assert newest.index(b'second reply') < newest.index(b'first reply')


def test_latest_in_group_rail(app, client, acme, globex, user):
    make_group(app, acme)
    owner_client = app.test_client()
    login_as(owner_client, user)
    create_post(owner_client, title='Rail neighbor', body='x')
    create_post(owner_client, title='Current post', body='y')
    with app.test_request_context(base_url=ACME):
        g.org = acme
        url = Post.query.filter_by(title='Current post').one().url
    page = owner_client.get(url, base_url=ACME).data
    assert b'Latest in General' in page
    assert b'Rail neighbor' in page


def test_an_over_long_post_can_still_be_moderated(app, client, acme, user):
    """The cap on a body must not make the worst posts unmoderatable: a
    pasted hundred-kilobyte dump is exactly the row someone needs to hide,
    and re-validating it on a flag change refused the save."""
    from app.models.discussion import BODY_MAX, DiscussionGroup, Post

    with app.test_request_context(base_url=ACME):
        g.org = acme
        group = DiscussionGroup.query.filter_by(org_id=acme.id).first()
        spam = Post(org_id=acme.id, group_id=group.id, title='Spam',
                    body='x' * (BODY_MAX + 5_000), created_by_id=user.id)
        db.session.add(spam)
        db.session.commit()
        post_id, slug = spam.id, group.slug

    login_as(client, user)
    for action in ('lock', 'pin', 'hide'):
        response = client.post(f'/discussions/{slug}/{post_id}/{action}',
                               base_url=ACME)
        assert response.status_code == 302, action

    db.session.expire_all()
    assert db.session.get(Post, post_id).is_hidden is True
