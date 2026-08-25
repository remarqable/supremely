from flask import g

from app.extensions import db
from app.models import Category, Post
from tests.conftest import login_as, make_user

ACME = 'http://acme.example.test'


def publish(app, org, slug='hello', title='Hello', body='World.', **kwargs):
    with app.test_request_context():
        g.org = org
        post = Post(title=title, slug=slug, body=body, org_id=org.id, **kwargs)
        post.save()
        post.publish()
        return post.id


def test_listing_and_permalink(app, client, acme, globex):
    publish(app, acme, slug='one', title='Post One')
    publish(app, acme, slug='two', title='Post Two')
    listing = client.get('/posts', base_url=ACME)
    assert b'Post One' in listing.data
    assert b'Post Two' in listing.data

    single = client.get('/posts/one', base_url=ACME)
    assert single.status_code == 200
    assert b'Post One' in single.data


def test_posts_tenant_isolated(app, client, acme, globex):
    publish(app, acme, slug='mine', title='Acme Post')
    publish(app, globex, slug='theirs', title='Globex Post')
    listing = client.get('/posts', base_url=ACME)
    assert b'Acme Post' in listing.data
    assert b'Globex Post' not in listing.data
    assert client.get('/posts/theirs', base_url=ACME).status_code == 404


def test_draft_not_listed(app, client, acme, globex):
    with app.test_request_context():
        g.org = acme
        Post(title='Draft', slug='draft', org_id=acme.id).save()
    assert b'Draft' not in client.get('/posts', base_url=ACME).data
    assert client.get('/posts/draft', base_url=ACME).status_code == 404


def test_member_only_post_hidden_from_anonymous_listing(app, client, acme, globex):
    publish(app, acme, slug='open', title='Open Post')
    publish(app, acme, slug='closed', title='Members Post',
            visibility='members')
    listing = client.get('/posts', base_url=ACME)
    assert b'Open Post' in listing.data
    assert b'Members Post' not in listing.data


def test_member_sees_member_post(app, client, acme, globex, user):
    publish(app, acme, slug='closed', title='Members Post',
            visibility='members')
    login_as(client, user)
    listing = client.get('/posts', base_url=ACME)
    assert b'Members Post' in listing.data
    assert client.get('/posts/closed', base_url=ACME).status_code == 200


def test_category_archive(app, client, acme, globex):
    with app.test_request_context():
        g.org = acme
        cat = Category(name='News', slug='news', org_id=acme.id).save()
        post = Post(title='Categorized', slug='c1', org_id=acme.id)
        post.categories = [cat]
        post.save()
        post.publish()
        Post(title='Uncategorized', slug='c2', org_id=acme.id).save().publish()

    archive = client.get('/posts/category/news', base_url=ACME)
    assert b'Categorized' in archive.data
    assert b'Uncategorized' not in archive.data
    assert client.get('/posts/category/nope', base_url=ACME).status_code == 404


def test_tag_archive(app, client, acme, globex):
    publish(app, acme, slug='tagged', title='Tagged Post', tags=['alpha'])
    publish(app, acme, slug='untagged', title='Untagged Post')
    archive = client.get('/posts/tag/alpha', base_url=ACME)
    assert b'Tagged Post' in archive.data
    assert b'Untagged Post' not in archive.data


def test_manage_post_workflow(app, client, acme, globex, user):
    login_as(client, user)
    response = client.post('/manage/posts/new', base_url=ACME, data={
        'title': 'Via UI', 'slug': 'via-ui', 'body': 'Written in the editor.',
        'visibility': 'public', 'tags': 'release, howto', 'action': 'publish',
    })
    assert response.status_code == 302
    public = client.get('/posts/via-ui', base_url=ACME)
    assert b'Written in the editor.' in public.data
    assert b'#release' in public.data


def test_manage_link_post(app, client, acme, globex, user):
    login_as(client, user)
    client.post('/manage/posts/new?type=link', base_url=ACME, data={
        'title': 'Cool Site', 'slug': 'cool-site', 'body': 'Check it out.',
        'visibility': 'public', 'field_url': 'https://cool.example',
        'field_source': 'a friend', 'action': 'publish',
    })
    public = client.get('/posts/cool-site', base_url=ACME)
    assert public.status_code == 200
    assert b'https://cool.example' in public.data
    assert b'via a friend' in public.data


def test_link_post_rejects_bad_url(app, client, acme, globex, user):
    login_as(client, user)
    response = client.post('/manage/posts/new?type=link', base_url=ACME, data={
        'title': 'Bad', 'slug': 'bad', 'body': '', 'visibility': 'public',
        'field_url': 'javascript:alert(1)', 'action': 'publish',
    })
    assert b'must be an http(s) URL' in response.data


def test_preview_draft(app, client, acme, globex, user):
    with app.test_request_context():
        g.org = acme
        post = Post(title='Sneak Peek', slug='sneak', org_id=acme.id,
                    body='Unreleased.').save()
        post_id = post.id
    login_as(client, user)
    preview = client.get(f'/manage/posts/{post_id}/preview', base_url=ACME)
    assert preview.status_code == 200
    assert b'Unreleased.' in preview.data
    # But the public permalink stays hidden
    assert client.get('/posts/sneak', base_url=ACME).status_code == 404
