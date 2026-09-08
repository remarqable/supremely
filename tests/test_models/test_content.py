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


def test_a_blank_slug_is_written_from_the_title(app, acme):
    """Nobody should have to type an address for their own post."""
    c = make(app, acme, title='Hello, Sailor!', slug='')
    assert c.slug == 'hello-sailor'


def test_an_accent_is_folded_rather_than_dropped(app, acme):
    """"Café notes" is cafe-notes, not caf-notes."""
    c = make(app, acme, title='Café notes', slug='')
    assert c.slug == 'cafe-notes'


def test_a_slug_that_was_typed_is_left_alone(app, acme):
    c = make(app, acme, title='Hello, Sailor!', slug='my-own-address')
    assert c.slug == 'my-own-address'


def test_editing_a_title_does_not_move_an_existing_address(app, acme):
    """A published item's slug is its public URL. Renaming the item must not
    silently break every link to it."""
    c = make(app, acme, title='First title', slug='')
    assert c.slug == 'first-title'
    with app.test_request_context():
        g.org = acme
        c.title = 'A completely different title'
        c.save()
    assert c.slug == 'first-title'


def test_a_title_with_nothing_usable_still_asks_for_a_slug(app, acme):
    """A title in a non-Latin script derives nothing, so the author is
    asked rather than given an address they cannot read. The message says
    that, rather than complaining about the format of a field they left
    empty."""
    with pytest.raises(ValidationError) as caught, app.test_request_context():
        g.org = acme
        Content(type='article', title='日本語', slug='', org_id=acme.id,
                fields={}, tags=[]).save()
    assert 'no letters or numbers' in caught.value.message


def test_deriving_waits_for_the_type_to_be_settled(app, acme):
    """Uniqueness is per type, so a row that leaves the type to its column
    default must still dedupe against rows of that type. Deriving first
    searched among rows whose type was still None, found nothing taken, and
    handed back a slug the uniqueness check then refused."""
    with app.test_request_context():
        g.org = acme
        first = Content(title='Hello, World!', slug='', org_id=acme.id,
                        fields={}, tags=[])
        first.save()
    assert first.type == 'article'
    assert first.slug == 'hello-world-2'      # the seeded article owns the first


def test_a_derived_slug_finds_a_free_address_instead_of_failing(app, acme):
    """A second post with the same title must not stop on an error about a
    field the author never filled in."""
    first = make(app, acme, title='Weekly update', slug='')
    second = make(app, acme, title='Weekly update', slug='', body='Another.')
    third = make(app, acme, title='Weekly update', slug='', body='A third.')
    assert (first.slug, second.slug, third.slug) == (
        'weekly-update', 'weekly-update-2', 'weekly-update-3')


def test_a_derived_slug_steps_around_the_seeded_content(app, acme):
    """The starter article already owns hello-world."""
    c = make(app, acme, title='Hello, World!', slug='')
    assert c.slug == 'hello-world-2'


def test_a_typed_slug_that_collides_still_says_so(app, acme):
    """Asking for a specific address and being given a different one
    silently is worse than being told it is taken."""
    make(app, acme, title='One', slug='taken-address')
    with pytest.raises(ValidationError), app.test_request_context():
        g.org = acme
        Content(type='article', title='Two', slug='taken-address',
                org_id=acme.id, fields={}, tags=[]).save()


def test_a_derived_page_slug_avoids_the_reserved_ones(app, acme):
    """A page titled "Blog" would sit at /blog, where the article archive
    lives. The author never typed that, so it steps aside rather than
    refusing."""
    page = make(app, acme, type='page', title='Blog', slug='')
    assert page.slug == 'blog-2'

    manage = make(app, acme, type='page', title='Manage', slug='')
    assert manage.slug == 'manage-2'


def test_the_same_slug_can_exist_on_two_types(app, acme):
    """Uniqueness is per type, so deriving must not step around a clash
    that is not a clash."""
    article = make(app, acme, type='article', title='Contact us', slug='')
    page = make(app, acme, type='page', title='Contact us', slug='')
    assert article.slug == 'contact-us'
    assert page.slug == 'contact-us'


def test_a_long_title_gives_a_readable_address(app, acme):
    """A whole sentence of title would make a URL nobody can paste into a
    message. Derived slugs stop at a word, not mid-word."""
    c = make(app, acme, slug='', title=(
        'tet slug this is really long why would you ever make a url this long'))
    assert c.slug == 'tet-slug-this-is-really-long-why-would-you-ever-make-a-url'
    assert len(c.slug) <= 60
    assert not c.slug.endswith('-')


def test_a_typed_slug_keeps_the_column_limit(app, acme):
    """Only derived slugs are shortened. Somebody who types a long one has
    chosen it, and the column allows up to 200."""
    typed = 'a' * 150
    c = make(app, acme, title='Long address', slug=typed)
    assert c.slug == typed


# --- blocks: content that lives inside other content ---------------------------

def block(app, org, parent, **kwargs):
    """A child row, saved and published, ready to be looked for in listings."""
    defaults = {'type': 'recipe', 'title': 'A recipe card', 'body': 'Mix.',
                'org_id': org.id, 'fields': {}, 'tags': [],
                'parent_id': parent.id}
    defaults.update(kwargs)
    with app.test_request_context():
        g.org = org
        child = Content(**defaults)
        child.save()
        child.publish()
        db.session.refresh(child)
    return child


def test_a_block_has_no_address_of_its_own(app, acme):
    parent = make(app, acme, slug='host-article')
    card = block(app, acme, parent, slug='ignored-slug')
    assert card.slug is None                 # not stored unused
    assert card.is_child
    # It is read where it sits, so a link to it goes to its parent.
    assert card.permalink == '/blog/host-article'


def test_a_block_is_in_no_listing_anywhere(app, acme):
    """The regression surface of this whole feature.

    Every standalone listing goes through Content.standalone(), so this is
    one test naming each of them rather than a rule remembered a dozen
    times. A block leaking into an archive is the predictable failure here.
    """
    parent = make(app, acme, slug='host-article')
    with app.test_request_context():
        g.org = acme
        parent.publish()
    card = block(app, acme, parent, title='Leaky card', type='recipe')

    with app.test_request_context():
        g.org = acme
        acme.set_type_settings('recipe', enabled=True)
        category = Category(name='Food', slug='food', org_id=acme.id)
        category.save()
        # Named one by one and asserted together, so a failure says which
        # listing leaked rather than stopping at the first.
        leaked = [name for name, rows in (
            ('standalone', Content.standalone().all()),
            ('of_type', Content.of_type('recipe').all()),
            ('published_query', Content.published_query('recipe').all()),
            ('published_query()', Content.published_query().all()),
            ('visible_query', Content.visible_query('recipe').all()),
            ('feed', Content.feed('recipe', 10)),
            ('with_tag', Content.with_tag('recipe', 'anything').all()),
            ('visible_in_category',
             Content.visible_in_category('recipe', category).all()),
        ) if card in rows]
        assert leaked == []
        assert Content.feed_count('recipe') == 0
        assert dict(Content.count_by_type()).get('recipe') is None
        # ...and it is still there, inside its parent, where it belongs.
        assert card in parent.children


def test_a_block_is_not_found_by_slug_like_a_standalone_row(app, acme):
    """published_by_slug backs every public single, so it is the one that
    turns a guessed address into a page. Asserted with a routable block,
    which has a slug: a block with no slug could not be found by one
    whether or not the rule existed."""
    course = make(app, acme, type='course', title='Intro', slug='intro')
    with app.test_request_context():
        g.org = acme
        course.publish()
    lesson = block(app, acme, course, type='lesson', title='Lesson one',
                   slug='one')
    with app.test_request_context():
        g.org = acme
        acme.set_type_settings('lesson', enabled=True)
        assert lesson.slug == 'one'
        assert Content.published_by_slug('lesson', 'one') is None


def test_a_block_is_not_an_upcoming_event(app, acme):
    """upcoming_event reads through published_query, and the community rail
    draws whatever it returns."""
    from datetime import date, timedelta
    soon = (date.today() + timedelta(days=3)).isoformat()
    parent = make(app, acme, slug='host-article')
    with app.test_request_context():
        g.org = acme
        parent.publish()
    inline = block(app, acme, parent, type='event', title='Inline event',
                   fields={'starts_on': soon})
    with app.test_request_context():
        g.org = acme
        # The rail draws whatever this returns, and it reads the same
        # published_query every listing does.
        assert inline not in Content.published_query('event').all()
        assert Content.upcoming_event() != inline


def test_deleting_a_parent_deletes_its_blocks(app, acme):
    parent = make(app, acme, slug='host-article')
    card = block(app, acme, parent)
    card_id = card.id
    with app.test_request_context():
        g.org = acme
        parent.delete()
        assert db.session.get(Content, card_id) is None


def test_a_block_decides_its_own_visibility(app, acme):
    """Lesson one public and the rest members-only: a free preview for the
    price of a nullable column."""
    course = make(app, acme, type='course', title='Intro', slug='intro')
    with app.test_request_context():
        g.org = acme
        course.publish()
    first = block(app, acme, course, type='lesson', title='Lesson one',
                  slug='one', visibility='public')
    second = block(app, acme, course, type='lesson', title='Lesson two',
                   slug='two', visibility='members')
    with app.test_request_context():
        g.org = acme
        assert first.visible_to_current_visitor()
        assert not second.visible_to_current_visitor()
        # Listing follows the organization's tease-or-hide switch, the same
        # one an archive follows: on, the gated lesson is a locked title;
        # off, it is not there at all. Either way its body is behind
        # visible_to_current_visitor, which the template asks before it
        # draws anything.
        acme.update_settings(gated_teasers=True)
        assert course.visible_children() == [first, second]
        acme.update_settings(gated_teasers=False)
        assert course.visible_children() == [first]


def test_a_routable_block_lives_under_its_parent(app, acme):
    course = make(app, acme, type='course', title='Intro', slug='intro')
    lesson = block(app, acme, course, type='lesson', title='Lesson one',
                   slug='one')
    assert lesson.slug == 'one'          # this one keeps its address
    assert lesson.permalink == '/courses/intro/lessons/one'


def test_promoting_a_block_gives_it_a_page(app, acme):
    """One update, no conversion between shapes. That is the payoff for a
    block being an ordinary content row."""
    parent = make(app, acme, slug='host-article')
    card = block(app, acme, parent, type='recipe', title='Good Bread')
    with app.test_request_context():
        g.org = acme
        acme.set_type_settings('recipe', enabled=True)
        card.parent_id = None
        card.parent = None
        card.save()
        db.session.refresh(card)
        assert card.slug == 'good-bread'         # written from the title
        assert card.permalink == '/recipes/good-bread'
        assert card in Content.of_type('recipe').all()
        assert Content.published_by_slug('recipe', 'good-bread') is card


def test_blocks_go_one_level_deep(app, acme):
    parent = make(app, acme, slug='host-article')
    card = block(app, acme, parent)
    with app.test_request_context():
        g.org = acme
        grandchild = Content(type='recipe', title='Deeper', body='',
                             org_id=acme.id, fields={}, tags=[],
                             parent_id=card.id)
        with pytest.raises(ValidationError):
            grandchild.save()


def test_many_blocks_of_one_type_coexist(app, acme):
    """UNIQUE(org_id, type, slug) with a null slug.

    SQLite and PostgreSQL both count nulls here as distinct, so any number
    of blocks of the same type live in the same organization. CI runs this
    on both engines, which is the only way that claim is worth anything.
    """
    parent = make(app, acme, slug='host-article')
    cards = [block(app, acme, parent, title=f'Card {n}') for n in range(4)]
    assert [card.slug for card in cards] == [None] * 4
    with app.test_request_context():
        g.org = acme
        assert len(parent.children) == 4
        # ...and they keep the order they were added in.
        assert [card.position for card in parent.children] == [0, 1, 2, 3]


def test_two_parents_can_each_hold_a_block_at_the_same_address(app, acme):
    """Two courses each with a lesson "one".

    A block's address is unique inside the item it belongs to, not across
    the organization: one rule over (org, type, slug) would have made the
    second course's first lesson `/courses/b/lessons/lesson-one-2`, or
    refused the save outright if the author typed the slug themselves.
    """
    first = make(app, acme, type='course', title='Course A', slug='a')
    second = make(app, acme, type='course', title='Course B', slug='b')
    one = block(app, acme, first, type='lesson', title='Lesson one', slug='one')
    two = block(app, acme, second, type='lesson', title='Lesson one', slug='one')

    assert one.slug == 'one' and two.slug == 'one'
    assert one.permalink == '/courses/a/lessons/one'
    assert two.permalink == '/courses/b/lessons/one'


def test_one_parent_cannot_hold_two_blocks_at_one_address(app, acme):
    """The rule still bites where it should."""
    course = make(app, acme, type='course', title='Course A', slug='a')
    block(app, acme, course, type='lesson', title='Lesson one', slug='one')
    with app.test_request_context():
        g.org = acme
        clash = Content(type='lesson', title='Another', slug='one',
                        body='', org_id=acme.id, fields={}, tags=[],
                        parent_id=course.id)
        with pytest.raises(ValidationError):
            clash.save()


def test_standalone_rows_still_cannot_share_an_address(app, acme):
    """Folding parent_id into one constraint would have let them: nulls
    never compare equal, so every standalone row would look distinct."""
    make(app, acme, slug='taken')
    with app.test_request_context():
        g.org = acme
        clash = Content(type='article', title='Other', slug='taken', body='',
                        org_id=acme.id, fields={}, tags=[])
        with pytest.raises(ValidationError):
            clash.save()


def test_a_block_inside_a_page_links_to_the_page(app, acme):
    """A page has no archive base to hang a child address from, so there is
    no URL to serve one at. The link goes where the block is actually read
    rather than to a tidy-looking 404."""
    page = make(app, acme, type='page', title='About us', slug='about-us')
    lesson = block(app, acme, page, type='lesson', title='Lesson one',
                   slug='one')
    assert lesson.permalink == '/about-us'


def test_turning_a_type_off_does_not_empty_articles_that_use_it(app, acme):
    """The one place the type gate deliberately does not reach.

    Turning a type off stops it being published as a section: archive,
    listings and routes all go. It does not mean deleting words out of the
    middle of an article somebody wrote, so a block already inside a parent
    keeps rendering there.
    """
    parent = make(app, acme, slug='host-article')
    with app.test_request_context():
        g.org = acme
        parent.publish()
    card = block(app, acme, parent, title='Still here')

    with app.test_request_context():
        g.org = acme
        acme.set_type_settings('recipe', enabled=False)
        assert card in parent.visible_children()
        # ...while the type really is off everywhere it is a section.
        assert Content.published_query('recipe').all() == []
