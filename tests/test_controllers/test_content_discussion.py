"""The Discuss button: one thread per published item.

The thread is an ordinary discussion post that records what it is about, so
content stays editorial and discussion stays conversational — a reference
between the two rather than comments bolted onto Content.
"""
import pytest
from flask import g

from app.extensions import db
from app.models import Content, DiscussionGroup, Membership
from app.models.discussion import Post
from tests.conftest import login_as, make_user

ACME = 'http://acme.example.test'


def publish(app, org, slug='hello', title='Hello', visibility='public'):
    with app.test_request_context():
        g.org = org
        item = Content(type='article', title=title, slug=slug, body='Body.',
                       org_id=org.id, visibility=visibility, fields={}, tags=[])
        item.save()
        item.publish()
        return item.id


def make_group(app, org, slug='general', visibility='public'):
    """Get or create — provisioning already gives a new org some groups."""
    with app.test_request_context():
        g.org = org
        group = DiscussionGroup.query.filter_by(slug=slug).first()
        if group is None:
            group = DiscussionGroup(org_id=org.id, name=slug.title(),
                                    slug=slug, visibility=visibility)
            group.save()
        return group.id


def member_of(app, org, email='m@acme.test'):
    user = make_user(email=email)
    with app.test_request_context():
        g.org = org
        Membership.add(user.id, org.id, role='member')
    return user


def test_a_member_starts_the_thread_and_it_records_the_item(app, client, acme,
                                                            globex):
    make_group(app, acme)
    content_id = publish(app, acme)
    login_as(client, member_of(app, acme))
    response = client.post(f'/discussions/for-content/{content_id}',
                           base_url=ACME)
    assert response.status_code == 302
    with app.test_request_context():
        g.org = acme
        thread = Post.for_content(content_id)
        assert thread is not None
        assert thread.title == 'Hello'
        assert response.headers['Location'].endswith(thread.url)


def test_pressing_it_twice_joins_the_first_thread(app, client, acme, globex):
    make_group(app, acme)
    content_id = publish(app, acme)
    login_as(client, member_of(app, acme))
    first = client.post(f'/discussions/for-content/{content_id}', base_url=ACME)
    second = client.post(f'/discussions/for-content/{content_id}', base_url=ACME)
    assert first.headers['Location'] == second.headers['Location']
    with app.test_request_context():
        g.org = acme
        assert Post.query.filter_by(content_id=content_id).count() == 1


def test_the_article_links_to_the_thread_once_it_exists(app, client, acme,
                                                        globex):
    make_group(app, acme)
    content_id = publish(app, acme)
    user = member_of(app, acme)
    login_as(client, user)
    client.post(f'/discussions/for-content/{content_id}', base_url=ACME)
    with app.test_request_context():
        g.org = acme
        thread_url = Post.for_content(content_id).url
    body = client.get('/blog/hello', base_url=ACME).data.decode()
    assert 'Discuss this' in body
    assert thread_url in body


def test_a_visitor_is_not_offered_the_button(app, client, acme, globex):
    make_group(app, acme)
    publish(app, acme)
    body = client.get('/blog/hello', base_url=ACME).data.decode()
    assert 'Start a discussion' not in body


def test_a_visitor_cannot_start_one(app, client, acme, globex):
    make_group(app, acme)
    content_id = publish(app, acme)
    response = client.post(f'/discussions/for-content/{content_id}',
                           base_url=ACME)
    assert response.status_code in (302, 401, 403)
    with app.test_request_context():
        g.org = acme
        assert Post.for_content(content_id) is None


def test_a_thread_cannot_be_opened_on_a_draft(app, client, acme, globex):
    make_group(app, acme)
    with app.test_request_context():
        g.org = acme
        draft = Content(type='article', title='Draft', slug='draft',
                        body='b', org_id=acme.id, fields={}, tags=[])
        draft.save()
        draft_id = draft.id
    login_as(client, member_of(app, acme))
    response = client.post(f'/discussions/for-content/{draft_id}',
                           base_url=ACME)
    assert response.status_code == 404


def test_deleting_the_item_orphans_the_thread_rather_than_destroying_it(
        app, client, acme, globex):
    make_group(app, acme)
    content_id = publish(app, acme)
    login_as(client, member_of(app, acme))
    client.post(f'/discussions/for-content/{content_id}', base_url=ACME)
    with app.test_request_context():
        g.org = acme
        thread_id = Post.for_content(content_id).id
        db.session.get(Content, content_id).delete()
        db.session.commit()
        survivor = db.session.get(Post, thread_id)
        assert survivor is not None            # the conversation survives
        assert survivor.content_id is None


def test_threads_do_not_leak_across_tenants(app, client, acme, globex):
    make_group(app, acme)
    make_group(app, globex)
    theirs = publish(app, globex, slug='theirs', title='Theirs')
    login_as(client, member_of(app, acme))
    response = client.post(f'/discussions/for-content/{theirs}', base_url=ACME)
    assert response.status_code == 404


@pytest.mark.parametrize('type_slug,expected', [('article', True),
                                                ('page', False)])
def test_the_button_is_on_feed_types_not_pages(app, client, acme, globex,
                                               type_slug, expected):
    make_group(app, acme)
    with app.test_request_context():
        g.org = acme
        item = Content(type=type_slug, title='Thing', slug='thing',
                       body='b', org_id=acme.id, fields={}, tags=[],
                       presentation='community')
        item.save()
        item.publish()
    login_as(client, member_of(app, acme))
    url = '/blog/thing' if type_slug == 'article' else '/thing'
    body = client.get(url, base_url=ACME).data.decode()
    assert ('Start a discussion' in body) is expected
