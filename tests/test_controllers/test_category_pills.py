"""Category pills on content archives.

The pill row filters an archive by category. It is drawn from
Category.for_type, so it offers only categories that actually hold
something the visitor may see of that type.
"""
import pytest
from flask import g

from app.extensions import db
from app.models import Category, Content, Membership
from tests.conftest import login_as, make_user

ACME = 'http://acme.example.test'


def publish(app, org, type_slug='article', slug='hello', title='Hello',
            categories=(), visibility='public'):
    with app.test_request_context():
        g.org = org
        item = Content(type=type_slug, title=title, slug=slug, body='Body.',
                       org_id=org.id, visibility=visibility, fields={}, tags=[])
        item.categories = list(categories)
        item.save()
        item.publish()
        return item.id


def make_category(app, org, name='Strategy', slug='strategy'):
    with app.test_request_context():
        g.org = org
        category = Category(org_id=org.id, name=name, slug=slug)
        category.save()
        return category


def test_consecutive_categories_get_different_tones(app, acme):
    made = [make_category(app, acme, f'C{n}', f'c{n}') for n in range(4)]
    with app.test_request_context():
        g.org = acme
        tones = [c.tone for c in made]
    assert len(set(tones)) == len(tones)        # a row of pills, not one colour
    assert all(0 <= tone < Category.PILL_TONES for tone in tones)


def test_for_type_lists_only_categories_holding_that_type(app, acme):
    strategy = make_category(app, acme, 'Strategy', 'strategy')
    diary = make_category(app, acme, 'Diary', 'diary')
    publish(app, acme, 'article', 'a', 'A', categories=[strategy])
    publish(app, acme, 'event', 'e', 'E', categories=[diary])
    with app.test_request_context():
        g.org = acme
        slugs = [c.slug for c in Category.for_type('article')]
    # `diary` is used only by an event, so the blog must not offer it.
    assert slugs == ['strategy']


def test_for_type_skips_a_category_with_only_drafts(app, acme):
    empty = make_category(app, acme, 'Empty', 'empty')
    with app.test_request_context():
        g.org = acme
        Content(type='article', title='Draft', slug='draft', org_id=acme.id,
                fields={}, tags=[], categories=[empty]).save()
        assert Category.for_type('article') == []


def test_archive_draws_a_pill_per_category(app, client, acme, globex):
    strategy = make_category(app, acme, 'Strategy', 'strategy')
    publish(app, acme, categories=[strategy])
    response = client.get('/blog', base_url=ACME)
    assert response.status_code == 200
    body = response.data.decode()
    assert '/blog/category/strategy' in body
    assert 'Strategy' in body
    # Nothing is filtered yet, so "All" is the current pill.
    assert 'pill-all pill-current' in body


def test_category_page_marks_its_own_pill_current(app, client, acme, globex):
    strategy = make_category(app, acme, 'Strategy', 'strategy')
    publish(app, acme, categories=[strategy])
    response = client.get('/blog/category/strategy', base_url=ACME)
    assert response.status_code == 200
    body = response.data.decode()
    assert 'pill-current' in body
    assert 'aria-current="page"' in body
    # The "All" pill is present as the way back, but is not the current one.
    assert 'pill-all pill-current' not in body


def test_tag_archive_still_renders_after_the_refactor(app, client, acme, globex):
    with app.test_request_context():
        g.org = acme
        item = Content(type='article', title='Tagged', slug='tagged',
                       body='B.', org_id=acme.id, fields={}, tags=['howto'])
        item.save()
        item.publish()
    response = client.get('/blog/tag/howto', base_url=ACME)
    assert response.status_code == 200
    assert b'Tagged' in response.data


def test_pills_do_not_leak_across_tenants(app, client, acme, globex):
    theirs = make_category(app, globex, 'Theirs', 'theirs')
    publish(app, globex, categories=[theirs])
    mine = make_category(app, acme, 'Mine', 'mine')
    publish(app, acme, slug='mine-post', title='Mine', categories=[mine])
    body = client.get('/blog', base_url=ACME).data.decode()
    assert 'Mine' in body
    assert 'theirs' not in body


def test_gated_category_hidden_from_a_visitor_when_teasing_is_off(app, client,
                                                                  acme, globex):
    with app.test_request_context():
        g.org = acme
        acme.settings = {**(acme.settings or {}), 'gated_teasers': False}
        acme.save()
    members_only = make_category(app, acme, 'Inner', 'inner')
    publish(app, acme, slug='gated', title='Gated', categories=[members_only],
            visibility='members')
    body = client.get('/blog', base_url=ACME).data.decode()
    assert 'category/inner' not in body

    member = make_user(email='m@acme.test')
    with app.test_request_context():
        g.org = acme
        Membership.add(member.id, acme.id, role='member')
    login_as(client, member)
    assert 'category/inner' in client.get('/blog', base_url=ACME).data.decode()


def test_every_declared_icon_is_actually_drawn(app):
    """Category.ICONS and the category_icon macro must not drift apart.

    The names live in Python and the drawings in a template, so a name
    added to one and not the other would silently fall back to the generic
    icon. Each icon must render a <path> or <circle> distinct from the
    fallback's.
    """
    with app.app_context():
        macros = (app.jinja_env.get_template('partials/_ui.html')
                  .make_module({'_': lambda s, **k: s}))
        fallback = str(macros.category_icon('definitely-not-an-icon'))
        drawn = {name: str(macros.category_icon(name))
                 for name in Category.ICONS}
    for name, svg in drawn.items():
        assert '<svg' in svg and ('<path' in svg or '<circle' in svg), name
        if name != 'tag':       # 'tag' is itself the fallback drawing
            assert svg != fallback, f'{name} falls through to the fallback'


def test_an_unknown_icon_is_refused(app, acme):
    from app.platform.errors import ValidationError
    with app.test_request_context():
        g.org = acme
        category = Category(org_id=acme.id, name='X', slug='x',
                            icon='<script>alert(1)</script>')
        with pytest.raises(ValidationError):
            category.save()


def test_a_blank_icon_is_stored_as_none(app, acme):
    with app.test_request_context():
        g.org = acme
        category = Category(org_id=acme.id, name='X', slug='x', icon='')
        category.save()
        assert category.icon is None


def test_admin_edits_a_category_name_and_icon(app, client, acme, globex, user):
    category = make_category(app, acme, 'Old', 'old')
    login_as(client, user)
    response = client.post(f'/manage/categories/{category.id}',
                           data={'name': 'New', 'slug': 'new', 'icon': 'book'},
                           base_url=ACME, follow_redirects=True)
    assert response.status_code == 200
    with app.test_request_context():
        g.org = acme
        again = db.session.get(Category, category.id)
        assert (again.name, again.slug, again.icon) == ('New', 'new', 'book')


def test_a_card_without_a_picture_shows_the_category_icon(app, client, acme,
                                                          globex):
    strategy = make_category(app, acme, 'Strategy', 'strategy')
    with app.test_request_context():
        g.org = acme
        strategy.icon = 'lightbulb'
        strategy.save()
    publish(app, acme, categories=[strategy])
    body = client.get('/blog', base_url=ACME).data.decode()
    assert 'band-' in body                      # the tinted panel
    assert 'pill-on-band' in body               # the chip sitting on it
