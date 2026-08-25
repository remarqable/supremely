import pytest
from flask import g

from app.extensions import db
from app.models import Category, Post
from app.platform.errors import ValidationError


def make_post(app, org, **kwargs):
    defaults = dict(title='First Light', slug='first-light',
                    body='First **post**.', org_id=org.id)
    defaults.update(kwargs)
    with app.test_request_context():
        g.org = org
        post = Post(**defaults)
        post.save()
        db.session.refresh(post)
    return post


def test_create_and_publish(app, acme):
    post = make_post(app, acme)
    assert post.type == 'article'
    assert post.status == 'draft'
    with app.test_request_context():
        g.org = acme
        post.publish()
    assert post.is_published
    assert post.permalink == '/posts/first-light'


def test_excerpt_fallback(app, acme):
    post = make_post(app, acme, body='Some **bold** words here.')
    assert 'Some bold words here.' in post.excerpt_or_summary()
    post.excerpt = 'Hand-written.'
    assert post.excerpt_or_summary() == 'Hand-written.'


def test_duplicate_slug_same_org_rejected(app, acme):
    make_post(app, acme)
    with pytest.raises(ValidationError, match='already exists'):
        make_post(app, acme, title='Other')


def test_same_slug_across_orgs_allowed(app, acme, globex):
    make_post(app, acme)
    assert make_post(app, globex).id is not None


def test_unknown_type_rejected(app, acme):
    with pytest.raises(ValidationError, match='Unknown post type'):
        make_post(app, acme, type='nonsense')


def test_link_type_requires_url(app, acme):
    with app.test_request_context():
        g.org = acme
        post = Post(title='L', slug='l', org_id=acme.id, type='link')
        with pytest.raises(ValidationError, match='required'):
            post.set_structured_fields({})


def test_link_type_validates_url(app, acme):
    with app.test_request_context():
        g.org = acme
        post = Post(title='L', slug='l', org_id=acme.id, type='link')
        with pytest.raises(ValidationError, match='http'):
            post.set_structured_fields({'url': 'javascript:alert(1)'})
        post.set_structured_fields({'url': 'https://example.com', 'source': 'HN'})
        assert post.fields == {'url': 'https://example.com', 'source': 'HN'}


def test_categories_and_tags(app, acme):
    with app.test_request_context():
        g.org = acme
        cat = Category(name='News', slug='news', org_id=acme.id).save()
        post = Post(title='T', slug='t', org_id=acme.id,
                    tags=['release', 'howto'])
        post.categories = [cat]
        post.save()
        post.publish()

        assert Post.with_tag('release').count() == 1
        assert Post.with_tag('nope').count() == 0
        by_cat = Post.published_query().filter(
            Post.categories.contains(cat)).all()
        assert [p.slug for p in by_cat] == ['t']
