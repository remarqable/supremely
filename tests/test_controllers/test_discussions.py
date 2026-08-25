"""Phase 5: discussions. Completion test: a small organization uses Supremely
as its primary asynchronous community discussion system."""

from flask import g

from app.extensions import db
from app.models import (Comment, DiscussionPost, Flag, Membership,
                        Notification, PostFollow, Space, User)
from tests.conftest import login_as, make_user

ACME = 'http://acme.example.test'


def make_space(app, org, slug='general', name='General', visibility='members'):
    """Fetch-or-create: new orgs come seeded with a 'general' space, so tests
    reuse it (and can flip its visibility)."""
    with app.test_request_context():
        g.org = org
        existing = Space.query.filter_by(slug=slug).first()
        if existing:
            existing.name = name
            existing.visibility = visibility
            return existing.save()
        return Space(name=name, slug=slug, org_id=org.id,
                     visibility=visibility).save()


def member_client(app, org, email):
    user = make_user(email=email)
    Membership.add(user.id, org.id, role='member')
    client = app.test_client()
    return login_as(client, user), user


def create_post(client, space_slug='general', title='First topic',
                 body='Hello everyone'):
    return client.post(f'/discussions/{space_slug}/new', base_url=ACME,
                       data={'title': title, 'body': body})


def test_reaction_htmx_swaps_bar_only(app, client, acme, globex, user):
    make_space(app, acme)
    owner_client = app.test_client()
    login_as(owner_client, user)
    create_post(owner_client)
    topic = DiscussionPost.query.first()

    # HTMX request gets the reaction-bar fragment, not a full page or redirect
    response = owner_client.post('/discussions/react', base_url=ACME,
                                 data={'target_type': 'post',
                                       'target_id': topic.id, 'emoji': '👍'},
                                 headers={'HX-Request': 'true'})
    assert response.status_code == 200
    assert b'reaction-bar' in response.data
    assert b'<!DOCTYPE html>' not in response.data
    assert '👍 1'.encode() in response.data

    # Non-HTMX still redirects (progressive enhancement)
    plain = owner_client.post('/discussions/react', base_url=ACME,
                              data={'target_type': 'post',
                                    'target_id': topic.id, 'emoji': '👍'})
    assert plain.status_code == 302


def test_full_discussion_flow(app, client, acme, globex, user):
    """Owner creates a space; two members hold a threaded conversation."""
    make_space(app, acme)
    alice_client, alice = member_client(app, acme, 'alice@example.com')
    bob_client, bob = member_client(app, acme, 'bob@example.com')

    # Alice starts a topic
    response = create_post(alice_client, title='Welcome thread',
                            body='Say hi **here**.')
    assert response.status_code == 302
    topic = DiscussionPost.query.filter_by(title='Welcome thread').first()
    assert topic is not None
    assert topic.created_by_id == alice.id
    assert PostFollow.is_following(alice.id, topic.id)   # auto-follow

    # Bob replies
    bob_client.post(f'/discussions/general/{topic.id}/comment', base_url=ACME,
                    data={'body': 'Hi Alice!'})
    reply = Comment.query.filter_by(post_id=topic.id).first()
    assert reply is not None

    # One-level threading: Alice answers Bob's reply
    alice_client.post(f'/discussions/general/{topic.id}/comment', base_url=ACME,
                      data={'body': 'Welcome Bob', 'parent_id': reply.id})
    page = alice_client.get(f'/discussions/general/{topic.id}', base_url=ACME)
    assert b'Hi Alice!' in page.data
    assert b'Welcome Bob' in page.data
    assert db.session.get(DiscussionPost, topic.id).comment_count == 2

    # Alice was notified of Bob's reply (author + follower dedup: one entry)
    notes = Notification.query.filter_by(user_id=alice.id).all()
    assert len(notes) == 1
    assert notes[0].type == 'comment.to_author'

    # Reaction
    bob_client.post('/discussions/react', base_url=ACME, data={
        'target_type': 'post', 'target_id': topic.id, 'emoji': '👍'})
    page = bob_client.get(f'/discussions/general/{topic.id}', base_url=ACME)
    assert '👍 1'.encode() in page.data


def test_second_level_nesting_rejected(app, client, acme, globex, user):
    make_space(app, acme)
    alice_client, alice = member_client(app, acme, 'a2@example.com')
    create_post(alice_client)
    topic = DiscussionPost.query.first()
    alice_client.post(f'/discussions/general/{topic.id}/comment', base_url=ACME,
                      data={'body': 'level 1'})
    first = Comment.query.first()
    alice_client.post(f'/discussions/general/{topic.id}/comment', base_url=ACME,
                      data={'body': 'level 2', 'parent_id': first.id})
    second = Comment.query.filter_by(parent_id=first.id).first()
    response = alice_client.post(f'/discussions/general/{topic.id}/comment',
                                 base_url=ACME,
                                 data={'body': 'level 3',
                                       'parent_id': second.id},
                                 follow_redirects=True)
    assert b'one level' in response.data


def test_anonymous_cannot_see_members_space(app, client, acme, globex):
    make_space(app, acme, visibility='members')
    listing = client.get('/discussions/', base_url=ACME)
    assert b'General' not in listing.data
    assert client.get('/discussions/general', base_url=ACME).status_code == 404


def test_public_space_readable_not_writable(app, client, acme, globex, user):
    make_space(app, acme, visibility='public')
    owner_client = app.test_client()
    login_as(owner_client, user)
    create_post(owner_client, title='Open thread')
    topic = DiscussionPost.query.first()

    anon_view = client.get(f'/discussions/general/{topic.id}', base_url=ACME)
    assert anon_view.status_code == 200
    assert b'Open thread' in anon_view.data
    # Anonymous posting refused (redirects to login)
    response = client.post(f'/discussions/general/{topic.id}/comment',
                           base_url=ACME, data={'body': 'spam'})
    assert response.status_code == 302
    assert Comment.query.count() == 0


def test_lock_stops_replies(app, client, acme, globex, user):
    make_space(app, acme)
    owner_client = app.test_client()
    login_as(owner_client, user)
    create_post(owner_client)
    topic = DiscussionPost.query.first()

    owner_client.post(f'/discussions/general/{topic.id}/lock', base_url=ACME)
    assert db.session.get(DiscussionPost, topic.id).is_locked

    member, _ = member_client(app, acme, 'm@example.com')
    member.post(f'/discussions/general/{topic.id}/comment', base_url=ACME,
                data={'body': 'too late'})
    assert Comment.query.count() == 0


def test_hidden_topic_invisible_to_members(app, client, acme, globex, user):
    make_space(app, acme)
    owner_client = app.test_client()
    login_as(owner_client, user)
    create_post(owner_client, title='Bad topic')
    topic = DiscussionPost.query.first()
    owner_client.post(f'/discussions/general/{topic.id}/hide', base_url=ACME)

    member, _ = member_client(app, acme, 'm4@example.com')
    assert member.get(f'/discussions/general/{topic.id}',
                      base_url=ACME).status_code == 404
    listing = member.get('/discussions/general', base_url=ACME)
    assert b'Bad topic' not in listing.data
    # Moderator still sees it
    assert owner_client.get(f'/discussions/general/{topic.id}',
                            base_url=ACME).status_code == 200


def test_member_cannot_moderate(app, client, acme, globex, user):
    make_space(app, acme)
    owner_client = app.test_client()
    login_as(owner_client, user)
    create_post(owner_client)
    topic = DiscussionPost.query.first()
    member, _ = member_client(app, acme, 'm5@example.com')
    assert member.post(f'/discussions/general/{topic.id}/lock',
                       base_url=ACME).status_code == 403


def test_edit_own_only(app, client, acme, globex, user):
    make_space(app, acme)
    alice_client, alice = member_client(app, acme, 'a6@example.com')
    bob_client, bob = member_client(app, acme, 'b6@example.com')
    create_post(alice_client, title='Mine')
    topic = DiscussionPost.query.first()

    assert bob_client.post(f'/discussions/general/{topic.id}/edit',
                           base_url=ACME,
                           data={'title': 'Hijacked', 'body': 'x'}
                           ).status_code == 403
    alice_client.post(f'/discussions/general/{topic.id}/edit', base_url=ACME,
                      data={'title': 'Mine v2', 'body': 'updated'})
    assert db.session.get(DiscussionPost, topic.id).title == 'Mine v2'


def test_mention_notification(app, client, acme, globex, user):
    make_space(app, acme)
    alice_client, alice = member_client(app, acme, 'alice7@example.com')
    bob_client, bob = member_client(app, acme, 'bob7@example.com')
    create_post(alice_client, title='T')
    topic = DiscussionPost.query.first()
    # Bob mentions alice7 by email local part
    bob_client.post(f'/discussions/general/{topic.id}/comment', base_url=ACME,
                    data={'body': 'ping @alice7 what do you think?'})
    mention = Notification.query.filter_by(user_id=alice.id,
                                           type='mention').first()
    assert mention is not None


def test_follow_notification_and_unread_flow(app, client, acme, globex, user):
    make_space(app, acme)
    alice_client, alice = member_client(app, acme, 'a8@example.com')
    bob_client, bob = member_client(app, acme, 'b8@example.com')
    carol_client, carol = member_client(app, acme, 'c8@example.com')

    create_post(alice_client)
    topic = DiscussionPost.query.first()
    # Carol follows without replying
    carol_client.post(f'/discussions/general/{topic.id}/follow', base_url=ACME)
    bob_client.post(f'/discussions/general/{topic.id}/comment', base_url=ACME,
                    data={'body': 'news!'})

    assert Notification.query.filter_by(user_id=carol.id,
                                        type='comment.followed').count() == 1
    assert Notification.unread_count(carol.id) == 1

    # Navbar badge + notifications page
    page = carol_client.get('/notifications/', base_url=ACME)
    assert b'news!' in page.data
    carol_client.post('/notifications/read-all', base_url=ACME)
    assert Notification.unread_count(carol.id) == 0


def test_flag_and_moderation_queue(app, client, acme, globex, user):
    make_space(app, acme)
    alice_client, alice = member_client(app, acme, 'a9@example.com')
    create_post(alice_client, title='Spammy')
    topic = DiscussionPost.query.first()
    alice_client.post('/discussions/flag', base_url=ACME, data={
        'target_type': 'post', 'target_id': topic.id, 'reason': 'spam'})
    assert Flag.query.filter_by(resolved_at=None).count() == 1

    owner_client = app.test_client()
    login_as(owner_client, user)
    queue = owner_client.get('/manage/flags', base_url=ACME)
    assert b'Spammy' in queue.data
    flag = Flag.query.first()
    owner_client.post(f'/manage/flags/{flag.id}/resolve', base_url=ACME)
    assert Flag.query.filter_by(resolved_at=None).count() == 0


def test_search(app, client, acme, globex, user):
    make_space(app, acme)
    owner_client = app.test_client()
    login_as(owner_client, user)
    create_post(owner_client, title='Deployment woes', body='gunicorn hangs')
    create_post(owner_client, title='Cooking tips', body='use salt')
    results = owner_client.get('/discussions/?q=gunicorn', base_url=ACME)
    assert b'Deployment woes' in results.data
    assert b'Cooking tips' not in results.data


def test_discussions_tenant_isolated(app, client, acme, globex, user):
    make_space(app, acme, slug='general')
    with app.test_request_context():
        g.org = globex
        Space(name='Globex Private', slug='globex-private',
              org_id=globex.id, visibility='public').save()
    owner_client = app.test_client()
    login_as(owner_client, user)
    listing = owner_client.get('/discussions/', base_url=ACME)
    assert b'Globex Private' not in listing.data
    assert owner_client.get('/discussions/globex-private',
                            base_url=ACME).status_code == 404


def test_email_job_enqueued_only_when_configured(app, client, acme, globex, user):
    from app.models import InstallationSetting, Job
    make_space(app, acme)
    alice_client, alice = member_client(app, acme, 'a10@example.com')
    bob_client, bob = member_client(app, acme, 'b10@example.com')
    create_post(alice_client)
    topic = DiscussionPost.query.first()

    bob_client.post(f'/discussions/general/{topic.id}/comment', base_url=ACME,
                    data={'body': 'no email configured'})
    assert Job.query.filter_by(name='notifications.email').count() == 0

    InstallationSetting.set('email.smtp_host', 'smtp.test')
    InstallationSetting.set('email.from_address', 'noreply@test')
    bob_client.post(f'/discussions/general/{topic.id}/comment', base_url=ACME,
                    data={'body': 'now with email'})
    assert Job.query.filter_by(name='notifications.email').count() >= 1
