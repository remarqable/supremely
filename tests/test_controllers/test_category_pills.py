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


def test_a_category_carries_a_body_template(app, client, acme, globex, user):
    category = make_category(app, acme, 'Reviews', 'reviews')
    login_as(client, user)
    client.post(f'/manage/categories/{category.id}',
                data={'name': 'Reviews', 'slug': 'reviews', 'icon': 'star',
                      'body_template': '## TL;DR\n\n## Takeaways\n'},
                base_url=ACME, follow_redirects=True)
    with app.test_request_context():
        g.org = acme
        again = db.session.get(Category, category.id)
        assert again.body_template.startswith('## TL;DR')


def test_the_editor_offers_the_template_to_the_body(app, client, acme, globex,
                                                    user):
    """The skeleton reaches the page as data for the editor to offer, not as
    something already written into the item."""
    category = make_category(app, acme, 'Reviews', 'reviews')
    with app.test_request_context():
        g.org = acme
        category.body_template = '## TL;DR'
        category.save()
    login_as(client, user)
    body = client.get('/manage/content/article/new', base_url=ACME).data.decode()
    assert 'data-template="## TL;DR"' in body
    assert 'pickCategory($event.target)' in body


def test_the_editor_offers_one_category_not_several(app, client, acme, globex,
                                                    user):
    """A category decides the template and the card colour, so the editor
    asks for one. Several topics is what tags are for."""
    make_category(app, acme, 'Reviews', 'reviews')
    login_as(client, user)
    body = client.get('/manage/content/article/new', base_url=ACME).data.decode()
    assert 'type="radio" name="category_id"' in body
    assert 'name="category_ids"' not in body
    assert 'value=""' in body               # the None choice


def test_saving_replaces_the_category_rather_than_adding_one(app, client, acme,
                                                             globex, user):
    first = make_category(app, acme, 'Reviews', 'reviews')
    second = make_category(app, acme, 'Notes', 'notes')
    login_as(client, user)
    client.post('/manage/content/article/new', base_url=ACME, data={
        'title': 'A post', 'slug': 'a-post', 'body': 'Body.',
        'visibility': 'public', 'category_id': first.id, 'action': 'publish'})
    with app.test_request_context():
        g.org = acme
        item = Content.published_by_slug('article', 'a-post')
        assert item.category.slug == 'reviews'
        item_id = item.id
    client.post(f'/manage/content/{item_id}/edit', base_url=ACME, data={
        'title': 'A post', 'slug': 'a-post', 'body': 'Body.',
        'visibility': 'public', 'category_id': second.id, 'action': 'save'})
    with app.test_request_context():
        g.org = acme
        again = db.session.get(Content, item_id)
        assert [c.slug for c in again.categories] == ['notes']


def test_choosing_no_category_clears_it(app, client, acme, globex, user):
    category = make_category(app, acme, 'Reviews', 'reviews')
    login_as(client, user)
    client.post('/manage/content/article/new', base_url=ACME, data={
        'title': 'B post', 'slug': 'b-post', 'body': 'Body.',
        'visibility': 'public', 'category_id': category.id,
        'action': 'publish'})
    with app.test_request_context():
        g.org = acme
        item_id = Content.published_by_slug('article', 'b-post').id
    client.post(f'/manage/content/{item_id}/edit', base_url=ACME, data={
        'title': 'B post', 'slug': 'b-post', 'body': 'Body.',
        'visibility': 'public', 'category_id': '', 'action': 'save'})
    with app.test_request_context():
        g.org = acme
        assert db.session.get(Content, item_id).category is None


def test_a_blank_template_is_stored_as_none(app, acme):
    with app.test_request_context():
        g.org = acme
        category = Category(org_id=acme.id, name='X', slug='x',
                            body_template='   ')
        category.save()
        assert category.body_template is None


def test_another_tenants_category_cannot_be_attached(app, client, acme,
                                                     globex, user):
    """The id comes from a form, so it is not trusted: the lookup is scoped
    and a number belonging to another organization simply finds nothing."""
    theirs = make_category(app, globex, 'Theirs', 'theirs')
    login_as(client, user)
    client.post('/manage/content/article/new', base_url=ACME, data={
        'title': 'C post', 'slug': 'c-post', 'body': 'Body.',
        'visibility': 'public', 'category_id': theirs.id, 'action': 'publish'})
    with app.test_request_context():
        g.org = acme
        item = Content.published_by_slug('article', 'c-post')
        assert item is not None
        assert item.category is None


def test_the_editor_tells_an_author_the_video_directive_exists(app, client,
                                                               acme, globex,
                                                               user):
    """A feature nobody can discover is not shipped. Nothing else in the
    product mentions :::video, so the editor's own hint has to."""
    login_as(client, user)
    body = client.get('/manage/content/article/new', base_url=ACME).data.decode()
    assert ':::video' in body
    assert 'placeholder=' in body
