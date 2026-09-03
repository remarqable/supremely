import pytest
from flask import g

from app.extensions import db
from app.models import Category, Content
from app.platform.errors import ValidationError


def make(app, org, **kwargs):
    # Slugs avoid the seeded starter content (hello-world, about, …).
    defaults = {'type': 'article', 'title': 'My Article', 'slug': 'my-article',
                'body': 'First **post**.', 'org_id': org.id, 'fields': {}, 'tags': []}
    defaults.update(kwargs)
    with app.test_request_context():
        g.org = org
        content = Content(**defaults)
        content.save()
        db.session.refresh(content)
    return content


def test_create_and_publish(app, acme):
    c = make(app, acme)
    assert c.type == 'article'
    assert c.status == 'draft'
    with app.test_request_context():
        g.org = acme
        c.publish()
    assert c.is_published
    assert c.permalink == '/blog/my-article'


def test_page_permalink_is_root(app, acme):
    page = make(app, acme, type='page', slug='my-about', title='About')
    assert page.permalink == '/my-about'


def test_event_permalink_uses_base(app, acme):
    e = make(app, acme, type='event', slug='meetup', title='Meetup',
             fields={'starts_on': '2026-09-01'})
    assert e.permalink == '/events/meetup'


def test_same_slug_different_types_coexist(app, acme):
    make(app, acme, type='page', slug='widget', title='Widget page')
    make(app, acme, type='article', slug='widget', title='Widget article')
    # unique is per (org, type, slug), so both persist
    assert Content.query.filter_by(slug='widget').count() == 2


def test_duplicate_slug_same_type_rejected(app, acme):
    make(app, acme, type='article', slug='dup')
    with pytest.raises(ValidationError, match='already exists'):
        make(app, acme, type='article', slug='dup', title='Other')


def test_page_reserved_slug_rejected(app, acme):
    with pytest.raises(ValidationError, match='reserved'):
        make(app, acme, type='page', slug='blog')       # a feed type's base
    with pytest.raises(ValidationError, match='reserved'):
        make(app, acme, type='page', slug='manage')     # app route


def test_unknown_type_rejected(app, acme):
    with pytest.raises(ValidationError, match='Unknown content type'):
        make(app, acme, type='nonsense')


def test_structured_fields_validated(app, acme):
    with app.test_request_context():
        g.org = acme
        e = Content(type='event', title='E', slug='e', org_id=acme.id)
        with pytest.raises(ValidationError, match='Date'):
            e.set_structured_fields({})                 # starts_on required
        e.set_structured_fields({'starts_on': '2026-01-02', 'location': 'NYC'})
        assert e.fields == {'starts_on': '2026-01-02', 'location': 'NYC'}


def test_a_summary_is_always_derived_from_the_body(app, acme):
    """The editor used to offer a hand-written excerpt. It no longer does,
    and a value written back when it did is kept but not read, so every
    item summarises the same way with nothing on screen to explain a
    difference."""
    c = make(app, acme, body='Some **bold** words.')
    assert 'Some bold words.' in c.excerpt_or_summary()

    c.excerpt = 'A hand-written summary.'
    c.save()
    assert 'Some bold words.' in c.excerpt_or_summary()
    assert c.excerpt == 'A hand-written summary.'        # kept, not destroyed


def test_a_field_the_type_no_longer_declares_survives_a_save(app, acme):
    """Videos used to ask for a duration and speakers. Saving an item for
    an unrelated reason must not throw away what an organization typed
    into a field that has since been retired."""
    item = make(app, acme, type='recording', title='A talk', slug='a-talk',
                fields={'video_url': 'https://example.com/v/1',
                        'duration_minutes': 67,
                        'speakers': 'Ada Lovelace'})
    with app.test_request_context():
        g.org = acme
        item.set_structured_fields({'video_url': 'https://example.com/v/2'})
        item.save()

    assert item.fields == {'video_url': 'https://example.com/v/2',
                           'duration_minutes': 67,
                           'speakers': 'Ada Lovelace'}


def test_tag_and_category_queries(app, acme):
    with app.test_request_context():
        g.org = acme
        cat = Category(name='News', slug='news', org_id=acme.id).save()
        c = Content(type='article', title='T', slug='t', org_id=acme.id,
                    tags=['release'], fields={})
        c.categories = [cat]
        c.save()
        c.publish()
        assert Content.with_tag('article', 'release').count() == 1
        assert Content.with_tag('article', 'nope').count() == 0
        by_cat = (Content.published_query('article')
                  .filter(Content.categories.contains(cat)).all())
        assert [x.slug for x in by_cat] == ['t']


# --- template is a name, never a path ------------------------------------------------

def _page(org, template):
    """A page row, unsaved: these tests are about validate() itself."""
    return Content(type='page', title='Probe', slug='tmpl-probe', body='x',
                   org_id=org.id, fields={}, tags=[], template=template)


@pytest.mark.parametrize('template', [
    'manage/members',           # renders the member admin on a public URL
    'admin/users',
    'community/discussion-post',
    'layouts/community',
    '../../../etc/passwd',
    'page/../admin/users',
    'Manage/Members',           # the resolver is case sensitive; the rule is not
])
def test_page_template_rejects_anything_path_shaped(app, acme, template):
    """`template` reaches render_site()'s candidate list, which also searches
    app-owned directories. A slash in it pulls a manage/ or admin/ template
    onto a public URL, where an anonymous visitor renders it."""
    with app.test_request_context():
        g.org = acme
        with pytest.raises(ValidationError, match='not a path'):
            _page(acme, template).validate()


@pytest.mark.parametrize('template,stored', [
    ('page', 'page'),
    ('page-wide', 'page-wide'),
    ('landing2', 'landing2'),
    ('  page  ', 'page'),
    ('', None),
    (None, None),
])
def test_page_template_accepts_a_theme_template_name(app, acme, template, stored):
    with app.test_request_context():
        g.org = acme
        page = _page(acme, template)
        page.validate()
        assert page.template == stored
